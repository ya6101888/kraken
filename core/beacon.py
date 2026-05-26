"""
KRAKEN Beacon — Мониторинг и алертинг.

Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.7 beacon.py
Версия: v5.2.3

Принципы:
- Health checks каждые 30 секунд (Telegram, Google Sheets, сессия)
- Watcher Node: следит за Engine
- Rate limiter: 1 алерт в 5 минут на тип ошибки
- Метрики сессии: возраст, количество переподключений
- Алерты в Telegram через BotClient
"""

import os
import sys
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

env_path = Path("/app/secrets/.env")
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv(Path(__file__).parent.parent.parent / "secrets" / ".env")


class Beacon:
    """
    Мониторинг и алертинг KRAKEN.
    
    Использование:
        beacon = Beacon(engine, channel_manager, gsheets_client)
        await beacon.start()
    """
    
    def __init__(self, engine=None, channel_manager=None, gsheets_client=None):
        """
        Args:
            engine: Engine для проверки состояния
            channel_manager: ChannelManager для статистики каналов
            gsheets_client: GoogleSheetsClient для health-check
        """
        self._engine = engine
        self._channel_manager = channel_manager
        self._gsheets_client = gsheets_client
        
        self.health_status: Dict[str, str] = {
            "telegram": "UNKNOWN",
            "sheets": "UNKNOWN",
            "session": "UNKNOWN",
            "engine": "UNKNOWN"
        }
        
        self.reconnect_count: int = 0
        self.start_time: Optional[datetime] = None
        
        # Задачи
        self._health_check_task: Optional[asyncio.Task] = None
        self._watcher_task: Optional[asyncio.Task] = None
        self._stop_event: asyncio.Event = asyncio.Event()
    
    @property
    def bot(self):
        """Ленивая инициализация BotClient."""
        if not hasattr(self, '_bot'):
            from clients.bot_client import BotClient
            self._bot = BotClient()
        return self._bot
    
    # ===== 4.7.1. HEALTH CHECKS =====
    
    async def health_check_loop(self):
        """
        Проверяет состояние всех компонентов каждые 30 секунд.
        
        Проверки:
        - Telegram: client.get_me()
        - Google Sheets: health_check()
        - .session файл: существует ли?
        - Engine: был ли успешный цикл за последний час?
        """
        while not self._stop_event.is_set():
            try:
                # 1. Проверка Telegram
                try:
                    from clients.telegram_client import TelegramClientManager
                    client = await TelegramClientManager.get_instance()
                    if client.is_connected():
                        await client.get_me()
                        self.health_status["telegram"] = "OK"
                    else:
                        self.health_status["telegram"] = "DISCONNECTED"
                except Exception as e:
                    self.health_status["telegram"] = f"FAIL: {type(e).__name__}"
                
                # 2. Проверка Google Sheets
                if self._gsheets_client:
                    try:
                        ok = self._gsheets_client.health_check()
                        self.health_status["sheets"] = "OK" if ok else "FAIL"
                    except Exception:
                        self.health_status["sheets"] = "FAIL"
                
                # 3. Проверка .session файла
                session_path = Path("/app/sessions/kraken.session")
                if session_path.exists():
                    age_days = (datetime.now() - datetime.fromtimestamp(
                        session_path.stat().st_mtime
                    )).days
                    self.health_status["session"] = f"OK (age={age_days}d)"
                else:
                    self.health_status["session"] = "MISSING"
                
                # 4. Проверка Engine
                if self._engine and self._engine.last_success:
                    since_last = (datetime.now() - self._engine.last_success).total_seconds()
                    if since_last < 3600:
                        self.health_status["engine"] = f"OK (last={since_last:.0f}s ago)"
                    else:
                        self.health_status["engine"] = f"STALE (last={since_last:.0f}s ago)"
                else:
                    self.health_status["engine"] = "NO_CYCLES"
                
            except Exception as e:
                print(f"⚠️ Health check error: {e}")
            
            await asyncio.sleep(30)
    
    # ===== 4.7.2. WATCHER NODE =====
    
    async def watcher_loop(self):
        """
        Следит за Engine и отправляет алерты при проблемах.
        
        Проверяет каждые 60 секунд:
        - Был ли успешный цикл за последний час?
        - Не превышен ли лимит ошибок?
        """
        while not self._stop_event.is_set():
            try:
                if self._engine:
                    # Проверка: нет успешных циклов больше часа
                    if self._engine.last_success:
                        since_last = (datetime.now() - self._engine.last_success).total_seconds()
                        
                        if since_last > 3600:
                            await self.bot.send_alert_with_fallback(
                                severity="WARNING",
                                message=f"No successful cycles for {since_last:.0f}s",
                                error_type="ENGINE_STALE"
                            )
                    
                    # Проверка возраста сессии (> 25 дней → предупреждение)
                    session_path = Path("/app/sessions/kraken.session")
                    if session_path.exists():
                        age_days = (datetime.now() - datetime.fromtimestamp(
                            session_path.stat().st_mtime
                        )).days
                        
                        max_age = int(os.getenv("BEACON_ALERT_ON_SESSION_AGE_DAYS", "30"))
                        if age_days >= max_age:
                            await self.bot.send_alert_with_fallback(
                                severity="WARNING",
                                message=f"Session age is {age_days} days (limit={max_age})",
                                error_type="SESSION_OLD"
                            )
                
            except Exception as e:
                print(f"⚠️ Watcher error: {e}")
            
            await asyncio.sleep(60)
    
    # ===== 4.7.4. МЕТРИКИ СЕССИИ =====
    
    def get_session_metrics(self) -> dict:
        """
        Возвращает метрики файла сессии.
        
        Returns:
            Словарь с возрастом сессии, размером файла, наличием бэкапа.
        """
        session_path = Path("/app/sessions/kraken.session")
        backup_path = Path("/app/sessions/kraken.session.bak")
        
        if not session_path.exists():
            return {"exists": False}
        
        return {
            "exists": True,
            "age_days": (datetime.now() - datetime.fromtimestamp(
                session_path.stat().st_mtime
            )).days,
            "size_bytes": session_path.stat().st_size,
            "backup_exists": backup_path.exists()
        }
    
    # ===== 4.7.7. АЛЕРТ =====
    
    async def alert(
        self,
        severity: str,
        message: str,
        error_type: Optional[str] = None,
        trace_id: Optional[str] = None
    ):
        """
        Отправляет алерт в Telegram.
        
        Args:
            severity: FATAL / CRITICAL / WARNING / INFO / RECOVERED
            message: Текст сообщения
            error_type: Тип ошибки для rate limiting
            trace_id: ID цикла для отладки
        """
        await self.bot.send_alert_with_fallback(
            severity=severity,
            message=message,
            error_type=error_type,
            trace_id=trace_id
        )
    
    # ===== ЗАПУСК / ОСТАНОВКА =====
    
    async def start(self):
        """Запускает все фоновые задачи Beacon."""
        self.start_time = datetime.now()
        
        self._health_check_task = asyncio.create_task(self.health_check_loop())
        self._watcher_task = asyncio.create_task(self.watcher_loop())
        
        print("🚨 Beacon started (health check every 30s, watcher every 60s)")
        
        # Отправляем алерт о старте
        await self.alert(
            severity="INFO",
            message="KRAKEN Beacon started",
            error_type="STARTUP"
        )
    
    async def stop(self):
        """Останавливает все фоновые задачи Beacon."""
        self._stop_event.set()
        
        for task in [self._health_check_task, self._watcher_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        print("🚨 Beacon stopped")
    
    # ===== СТАТИСТИКА =====
    
    def get_stats(self) -> dict:
        """Возвращает полную статистику Beacon."""
        uptime = (datetime.now() - self.start_time).total_seconds() if self.start_time else 0
        
        return {
            "uptime_seconds": uptime,
            "health_status": self.health_status,
            "session": self.get_session_metrics(),
            "reconnect_count": self.reconnect_count,
            "engine": self._engine.get_stats() if self._engine else {},
            "channels": self._channel_manager.get_stats() if self._channel_manager else {}
        }