"""
KRAKEN Beacon Bot Client — Отправка алертов в Telegram.

Фаза 3: ИНТЕГРАЦИИ
Модуль: 3.4 bot_client.py
Версия: v5.2.3 (GOLDEN ASSEMBLY)

Принципы:
- Полностью асинхронный HTTP-движок (httpx)
- Трафик инкапсулирован в Squid Proxy (192.168.0.1:3128)
- Rate limiter: не чаще 1 алерта одного типа в 5 минут
- Форматирование Markdown V2 с экранированием спецсимволов
- Fallback: запись в локальный лог при ошибке API без падений
"""

import os
import json
import time
import asyncio
import httpx
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Optional
from dotenv import load_dotenv

# Загрузка .env
env_path = Path("/app/secrets/.env")
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv(Path(__file__).parent.parent.parent / "secrets" / ".env")


class RateLimiter:
    """
    Ограничитель частоты алертов.
    
    Не позволяет отправлять больше 1 алерта одного типа
    в течение заданного интервала (по умолчанию 5 минут).
    """
    
    def __init__(self, limit_seconds: int = 300):
        self.limit_seconds = limit_seconds
        self.last_alert_time: dict[str, float] = defaultdict(float)
    
    def can_send(self, error_type: str) -> bool:
        """Проверяет, можно ли отправить алерт данного типа."""
        now = time.time()
        last = self.last_alert_time.get(error_type, 0)
        
        if now - last >= self.limit_seconds:
            self.last_alert_time[error_type] = now
            return True
        return False


class BotClient:
    """
    Клиент для отправки алертов через Telegram Bot API.
    Работает строго через HTTPS прокси-туннель.
    """
    
    def __init__(self):
        self.token = os.getenv("BEACON_BOT_TOKEN", "")
        self.chat_id = os.getenv("BEACON_CHAT_ID", "")
        self.enabled = os.getenv("BEACON_ENABLED", "TRUE").upper() == "TRUE"
        self.rate_limiter = RateLimiter(
            int(os.getenv("BEACON_RATE_LIMIT_PER_ERROR_MINUTES", "5")) * 60
        )
        
        # Подтягиваем параметры нашего Squid-прокси
        proxy_ip = os.getenv("TG_PROXY_IP", "192.168.0.1")
        proxy_port = os.getenv("TG_PROXY_PORT", "3128")
        self.proxy_url = f"http://{proxy_ip}:{proxy_port}"
        
        if self.token and self.chat_id:
            self.api_url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            # Ленивая сборка асинхронного клиента httpx с проксированием
            self._client = httpx.AsyncClient(proxies=self.proxy_url, timeout=10.0)
            print(f"🤖 Beacon bot initialized (chat_id={self.chat_id[:5]}... via proxy)")
        else:
            print("⚠️ Beacon bot token or chat_id not set — alerts disabled")
            self.api_url = None
            self._client = None
    
    @property
    def is_available(self) -> bool:
        """Доступен ли Bot API."""
        return self.api_url is not None and self.enabled
    
    # ===== 3.4.4. ФОРМАТИРОВАНИЕ СУЩНОСТЕЙ =====
    
    def _format_message(
        self,
        severity: str,
        message: str,
        trace_id: Optional[str] = None,
        error_type: Optional[str] = None
    ) -> str:
        """Форматирует сообщение с соблюдением синтаксиса Markdown V2."""
        icons = {
            "FATAL": "💀",
            "CRITICAL": "🔴",
            "WARNING": "🟡",
            "INFO": "🔵",
            "RECOVERED": "🟢"
        }
        icon = icons.get(severity, "📢")
        
        # Экранирование обязательных спецсимволов Markdown V2, чтобы API не выплевывало 400 Bad Request
        escaped_msg = (
            message.replace("-", "\\-")
                   .replace(".", "\\.")
                   .replace("!", "\\!")
                   .replace("(", "\\(")
                   .replace(")", "\\)")
                   .replace("[", "\\[")
                   .replace("]", "\\]")
                   .replace("{", "\\{")
                   .replace("}", "\\}")
        )
        
        text = f"{icon} *{severity}*"
        if error_type:
            text += f" \\({error_type}\\)"
        text += f"\n{escaped_msg}"
        
        if trace_id:
            text += f"\n`{trace_id}`"
        
        return text
    
    # ===== 3.4.2. АСИНХРОННАЯ ОТПРАВКА =====
    
    async def send_alert(
        self,
        severity: str,
        message: str,
        error_type: Optional[str] = None,
        trace_id: Optional[str] = None
    ) -> bool:
        """Отправляет алерт через httpx."""
        if not self.is_available:
            print(f"📢 Alert (muted): [{severity}] {message}")
            return False
        
        rate_key = error_type or severity
        if not self.rate_limiter.can_send(rate_key):
            print(f"⏸️ Rate limited: skipping [{severity}] {message[:50]}")
            return False
        
        payload = {
            "chat_id": self.chat_id,
            "text": self._format_message(severity, message, trace_id, error_type),
            "parse_mode": "MarkdownV2"
        }
        
        try:
            response = await self._client.post(self.api_url, json=payload)
            
            if response.status_code == 200:
                print(f"📤 Alert sent: [{severity}] {message[:50]}")
                return True
            else:
                print(f"❌ Bot API error {response.status_code}: {response.text[:100]}")
                return False
                
        except httpx.TimeoutException:
            print("❌ Bot API timeout via proxy")
            return False
        except Exception as e:
            print(f"❌ Bot API execution failure: {e}")
            return False
    
    # ===== 3.4.5. AMORTIZATION LAYER (FALLBACK) =====
    
    async def send_alert_with_fallback(
        self,
        severity: str,
        message: str,
        error_type: Optional[str] = None,
        trace_id: Optional[str] = None
    ):
        """Отправляет алерт, при аварии пишет в изолированный alerts.log хоста."""
        success = await self.send_alert(severity, message, error_type, trace_id)
        
        if not success:
            try:
                log_dir = Path("/app/logs")
                log_dir.mkdir(parents=True, exist_ok=True)
                
                log_file = log_dir / "alerts.log"
                timestamp = datetime.now().isoformat()
                log_line = f"{timestamp} | {severity:8s} | {error_type or '':20s} | {message}\n"
                
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(log_line)
                
                print(f"📝 Alert saved to local log: {log_file}")
            except Exception as le:
                print(f"💥 Critical Failure: Cannot write to log file: {le}")
    
    # ===== ОТПРАВКА СТАТУСА ЦИКЛА =====
    
    async def send_cycle_summary(
        self,
        trace_id: str,
        messages_collected: int,
        signals_approved: int,
        errors: int = 0,
        floodwait_seconds: int = 0
    ):
        """Отправляет структурированную сводку по результатам добычи."""
        parts = [
            f"📊 *Cycle complete*",
            f"Collected: {messages_collected}",
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