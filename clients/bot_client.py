"""
KRAKEN Beacon Bot Client — Отправка алертов в Telegram.

Фаза 3: ИНТЕГРАЦИИ
Модуль: 3.4 bot_client.py
Версия: v5.2.3

Принципы:
- Отправка алертов через Telegram Bot API
- Rate limiter: не чаще 1 алерта одного типа в 5 минут
- Форматирование Markdown V2
- Fallback: запись в локальный лог при ошибке API
"""

import os
import json
import time
import requests
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Optional
from dotenv import load_dotenv

# Загрузка .env
env_path = Path("/opt/kraken/secrets/.env")
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv(Path(__file__).parent.parent.parent / "secrets" / ".env")


class RateLimiter:
    """
    Ограничитель частоты алертов.
    
    Не позволяет отправлять больше 1 алерта одного типа
    в течение заданного интервала (по умолчанию 5 минут).
    
    Зачем: если KRAKEN зациклится на ошибке, он не заспамит
    Telegram сотнями одинаковых сообщений.
    """
    
    def __init__(self, limit_seconds: int = 300):
        self.limit_seconds = limit_seconds
        self.last_alert_time: dict[str, float] = defaultdict(float)
    
    def can_send(self, error_type: str) -> bool:
        """
        Проверяет, можно ли отправить алерт данного типа.
        
        Returns:
            True если прошло достаточно времени с прошлого алерта.
        """
        now = time.time()
        last = self.last_alert_time.get(error_type, 0)
        
        if now - last >= self.limit_seconds:
            self.last_alert_time[error_type] = now
            return True
        return False


class BotClient:
    """
    Клиент для отправки алертов через Telegram Bot API.
    
    Использование:
        bot = BotClient()
        await bot.send_alert(severity="WARNING", message="FloodWait 30s")
    """
    
    def __init__(self):
        self.token = os.getenv("BEACON_BOT_TOKEN", "")
        self.chat_id = os.getenv("BEACON_CHAT_ID", "")
        self.enabled = os.getenv("BEACON_ENABLED", "TRUE").upper() == "TRUE"
        self.rate_limiter = RateLimiter(
            int(os.getenv("BEACON_RATE_LIMIT_PER_ERROR_MINUTES", "5")) * 60
        )
        
        if self.token and self.chat_id:
            self.api_url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            print(f"🤖 Beacon bot initialized (chat_id={self.chat_id[:5]}...)")
        else:
            print("⚠️ Beacon bot token or chat_id not set — alerts disabled")
            self.api_url = None
    
    @property
    def is_available(self) -> bool:
        """Доступен ли Bot API."""
        return self.api_url is not None and self.enabled
    
    # ===== 3.4.4. ФОРМАТИРОВАНИЕ =====
    
    def _format_message(
        self,
        severity: str,
        message: str,
        trace_id: Optional[str] = None,
        error_type: Optional[str] = None
    ) -> str:
        """
        Форматирует сообщение для отправки в Telegram.
        
        Использует Markdown V2 для жирного текста и моноширинного кода.
        """
        # Иконки для уровней серьёзности
        icons = {
            "FATAL": "💀",
            "CRITICAL": "🔴",
            "WARNING": "🟡",
            "INFO": "🔵",
            "RECOVERED": "🟢"
        }
        icon = icons.get(severity, "📢")
        
        # Экранирование спецсимволов Markdown V2
        message_escaped = message.replace("-", "\\-").replace(".", "\\.")
        
        text = f"{icon} *{severity}*"
        if error_type:
            text += f" \\({error_type}\\)"
        text += f"\n{message_escaped}"
        
        if trace_id:
            # Моноширинный текст для TraceID
            text += f"\n`{trace_id}`"
        
        return text
    
    # ===== 3.4.2. ОТПРАВКА АЛЕРТА =====
    
    async def send_alert(
        self,
        severity: str,
        message: str,
        error_type: Optional[str] = None,
        trace_id: Optional[str] = None
    ) -> bool:
        """
        Отправляет алерт в Telegram.
        
        Args:
            severity: Серьёзность (FATAL/CRITICAL/WARNING/INFO/RECOVERED)
            message: Текст сообщения
            error_type: Тип ошибки (для rate limiting)
            trace_id: ID цикла (для отладки)
        
        Returns:
            True если отправлено успешно.
        """
        # Проверка доступности
        if not self.is_available:
            print(f"📢 Alert (muted): [{severity}] {message}")
            return False
        
        # Rate limiting
        rate_key = error_type or severity
        if not self.rate_limiter.can_send(rate_key):
            print(f"⏸️ Rate limited: skipping [{severity}] {message[:50]}")
            return False
        
        # Отправка
        payload = {
            "chat_id": self.chat_id,
            "text": self._format_message(severity, message, trace_id, error_type),
            "parse_mode": "MarkdownV2"
        }
        
        try:
            response = requests.post(
                self.api_url,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                print(f"📤 Alert sent: [{severity}] {message[:50]}")
                return True
            else:
                print(f"❌ Bot API error {response.status_code}: {response.text[:100]}")
                return False
                
        except requests.Timeout:
            print("❌ Bot API timeout")
            return False
        except Exception as e:
            print(f"❌ Bot API error: {e}")
            return False
    
    # ===== 3.4.5. FALLBACK В ЛОГ =====
    
    async def send_alert_with_fallback(
        self,
        severity: str,
        message: str,
        error_type: Optional[str] = None,
        trace_id: Optional[str] = None
    ):
        """
        Отправляет алерт, при ошибке пишет в локальный лог.
        """
        success = await self.send_alert(severity, message, error_type, trace_id)
        
        if not success:
            # Fallback: локальный лог
            log_dir = Path("/opt/kraken/logs")
            log_dir.mkdir(parents=True, exist_ok=True)
            
            log_file = log_dir / "alerts.log"
            timestamp = datetime.now().isoformat()
            log_line = f"{timestamp} | {severity:8s} | {error_type or '':20s} | {message}\n"
            
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(log_line)
            
            print(f"📝 Alert saved to local log: {log_file}")
    
    # ===== ОТПРАВКА СТАТУСА ЦИКЛА =====
    
    async def send_cycle_summary(
        self,
        trace_id: str,
        messages_collected: int,
        signals_approved: int,
        errors: int = 0,
        floodwait_seconds: int = 0
    ):
        """
        Отправляет сводку после завершения цикла сбора.
        """
        parts = [
            f"📊 Cycle complete",
            f"Messages: {messages_collected}",
            f"Approved: {signals_approved}",
        ]
        
        if errors > 0:
            parts.append(f"Errors: {errors}")
        if floodwait_seconds > 0:
            parts.append(f"FloodWait: {floodwait_seconds}s")
        
        message = " | ".join(parts)
        await self.send_alert(
            severity="INFO",
            message=message,
            trace_id=trace_id
        )