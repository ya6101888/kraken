"""
KRAKEN Telegram Client — Singleton-менеджер MTProto-соединения.

Фаза 3: ИНТЕГРАЦИИ
Модуль: 3.1 telegram_client.py
Версия: v5.4.0 (SRE 5.0 COMPLIANT — STEALTH READY)

Принципы:
- ОДИН клиент на всё приложение (Singleton)
- Потокобезопасность через asyncio.Lock
- Конфигурация СТРОГО через core.config.settings (Закон Params V)
- Уважение FloodWait от Telegram
"""

import asyncio
import sys
from pathlib import Path
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    AuthKeyInvalidError,
)
from core.config import settings  # Золотой стандарт конфигурации


class TelegramClientManager:
    """
    Singleton-менеджер для TelegramClient.
    """
    
    _instance: TelegramClient | None = None
    _lock: asyncio.Lock = asyncio.Lock()
    _initialized: bool = False
    
    @classmethod
    async def get_instance(cls) -> TelegramClient:
        async with cls._lock:
            if cls._instance is None:
                # Тянем строго валидированные параметры из Pydantic Settings
                session_path = settings.KRAKEN_SESSION_PATH
                api_id = settings.TG_API_ID
                api_hash = settings.TG_API_HASH
                
                if not api_id or not api_hash:
                    raise ValueError(
                        "TG_API_ID или TG_API_HASH не заданы в .env! "
                        "Проверь /app/secrets/.env через настройки Pydantic."
                    )
                
                # Настройка прокси через единый источник правды
                proxy = None
                if settings.PROXY_ENABLED and settings.PROXY_HTTP_URL:
                    from urllib.parse import urlparse
                    parsed_url = urlparse(settings.PROXY_HTTP_URL)
                    
                    proxy = {
                        'proxy_type': settings.PROXY_TELETHON_TYPE.lower(),
                        'addr': parsed_url.hostname,
                        'port': parsed_url.port,
                    }
                    if parsed_url.username:
                        proxy['username'] = parsed_url.username
                        proxy['password'] = parsed_url.password
                        
                    sys.stdout.write(f"🛡️ Proxy configured via Settings: {proxy['proxy_type']}://{proxy['addr']}:{proxy['port']}\n")
                    sys.stdout.flush()
                
                cls._instance = TelegramClient(
                    session=session_path,
                    api_id=api_id,
                    api_hash=api_hash,
                    proxy=proxy,
                    connection_retries=3,
                    auto_reconnect=True,
                    timeout=30
                )
                sys.stdout.write(f"🔑 TelegramClient singleton created | Session: {session_path} | API_ID: {api_id}\n")
                sys.stdout.flush()
            
            return cls._instance
    
    @classmethod
    async def init_client(cls) -> TelegramClient:
        client = await cls.get_instance()
        
        if cls._initialized:
            sys.stdout.write("✅ Client already initialized\n")
            sys.stdout.flush()
            return client
        
        sys.stdout.write("🔄 Connecting to Telegram...\n")
        sys.stdout.flush()
        await client.connect()
        
        if not await client.is_user_authorized():
            phone = settings.TG_PHONE_NUMBER
            password = settings.TG_2FA_PASSWORD
            sys.stdout.write(f"📱 Starting new session for {phone}\n")
            sys.stdout.flush()
            await client.start(phone=phone, password=password or None)
            sys.stdout.write("✅ New session created and authorized\n")
            sys.stdout.flush()
        else:
            me = await client.get_me()
            sys.stdout.write(f"✅ Session loaded: @{me.username or me.first_name}\n")
            sys.stdout.flush()
        
        cls._initialized = True
        return client
    
    @classmethod
    async def disconnect(cls):
        if cls._instance and cls._initialized:
            sys.stdout.write("🛑 Disconnecting from Telegram...\n")
            sys.stdout.flush()
            await cls._instance.disconnect()
            cls._initialized = False
            sys.stdout.write("✅ Disconnected\n")
            sys.stdout.flush()
    
    @classmethod
    async def reconnect(cls) -> bool:
        if not cls._instance:
            sys.stdout.write("❌ Cannot reconnect: no instance\n")
            sys.stdout.flush()
            return False
        
        try:
            sys.stdout.write("🔄 Reconnecting...\n")
            sys.stdout.flush()
            await cls._instance.connect()
            
            if await cls._instance.is_user_authorized():
                sys.stdout.write("✅ Reconnected successfully\n")
                sys.stdout.flush()
                cls._initialized = True
                return True
            else:
                sys.stdout.write("❌ Reconnect failed: not authorized\n")
                sys.stdout.flush()
                return False
        except AuthKeyInvalidError:
            sys.stdout.write("💀 FATAL: AuthKey invalid — session is dead\n")
            sys.stdout.flush()
            return False
        except Exception as e:
            sys.stdout.write(f"❌ Reconnect error: {e}\n")
            sys.stdout.flush()
            return False
    
    # ===== 3.1.4. HEARTBEAT =====
    
    @classmethod
    async def heartbeat_loop(cls, stop_event: asyncio.Event = None):
        consecutive_failures = 0
        max_failures = 3
        sys.stdout.write("💓 Heartbeat loop started (every 300s)\n")
        sys.stdout.flush()
        while True:
            if stop_event and stop_event.is_set():
                sys.stdout.write("💓 Heartbeat stopped\n")
                sys.stdout.flush()
                break
            await asyncio.sleep(settings.KRAKEN_HEARTBEAT_INTERVAL_SECONDS)
            try:
                client = await cls.get_instance()
                if not client.is_connected():
                    raise ConnectionError("Not connected")
                me = await client.get_me()
                consecutive_failures = 0
                sys.stdout.write(f"💓 Heartbeat OK: user_id={me.id}\n")
                sys.stdout.flush()
            except Exception as e:
                consecutive_failures += 1
                sys.stdout.write(f"⚠️ Heartbeat failed ({consecutive_failures}/{max_failures}): {e}\n")
                sys.stdout.flush()
                if consecutive_failures >= max_failures:
                    sys.stdout.write("🔄 Starting auto-reconnect...\n")
                    sys.stdout.flush()
                    success = await cls.reconnect()
                    if success:
                        consecutive_failures = 0
                    else:
                        sys.stdout.write("❌ Reconnect failed, will retry next heartbeat\n")
                        sys.stdout.flush()
                        consecutive_failures = 0
    
    # ===== 3.1.5. GET MESSAGES FAST =====

    @staticmethod
    async def get_messages_fast(client: TelegramClient, channel_id: str, limit: int = 10, min_id: int = 0):
        try:
            # Курсор движется строго вперед от min_id по ТЗ Stealth Mode
            messages = await client.get_messages(channel_id, limit=limit, min_id=min_id)
            return messages
            
        except FloodWaitError as e:
            raise e
        except Exception as e:
            sys.stdout.write(f"❌ Error fetching from {channel_id}: {e}\n")
            sys.stdout.flush()
            return []
    
    # ===== 3.1.6. FLOODWAIT HANDLER =====
    
    @staticmethod
    async def handle_floodwait(error: FloodWaitError):
        import random
        wait_seconds = error.seconds
        jitter = random.uniform(0, wait_seconds * 0.3)
        delay = wait_seconds + jitter
        sys.stdout.write(f"🌊 FloodWait: Telegram says wait {wait_seconds}s, waiting {delay:.1f}s (with jitter)\n")
        sys.stdout.flush()
        await asyncio.sleep(delay)
        return wait_seconds > settings.TG_FLOODWAIT_MAX_WAIT_SECONDS
    
    # ===== 3.1.8. AUTH REVOKED HANDLER =====
    
    @staticmethod
    async def handle_auth_revoked():
        sys.stdout.write("💀 FATAL: Session dead (AuthKeyInvalid)\n")
        sys.stdout.flush()
        try:
            from clients.bot_client import BotClient
            bot = BotClient()
            await bot.send_alert(
                severity="FATAL",
                error_type="AUTH_REVOKED",
                message="Session revoked, need manual bootstrap."
            )
        except Exception as e:
            sys.stdout.write(f"⚠️ Could not send Beacon alert: {e}\n")
            sys.stdout.flush()
        sys.stdout.write("🛑 Exiting with code 1\n")
        sys.stdout.flush()
        sys.exit(1)


# ===== 3.1.3 & 3.1.9. LIFESPAN MANAGER =====

class TelegramLifespan:
    """
    Менеджер жизненного цикла Telegram-клиента.
    """
    
    def __init__(self):
        self.client: TelegramClient | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event = asyncio.Event()
    
    async def __aenter__(self) -> TelegramClient:
        sys.stdout.write("🚀 TelegramLifespan STARTUP\n")
        sys.stdout.flush()
        self.client = await TelegramClientManager.init_client()
        self._heartbeat_task = asyncio.create_task(
            TelegramClientManager.heartbeat_loop(self._stop_event)
        )
        return self.client
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        sys.stdout.write("🛑 TelegramLifespan SHUTDOWN\n")
        sys.stdout.flush()
        self._stop_event.set()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        await TelegramClientManager.disconnect()
        sys.stdout.write("✅ TelegramLifespan SHUTDOWN complete\n")
        sys.stdout.flush()