"""
KRAKEN Telegram Client — Singleton-менеджер MTProto-соединения.

Фаза 3: ИНТЕГРАЦИИ
Модуль: 3.1 telegram_client.py
Версия: v5.2.3

Принципы:
- ОДИН клиент на всё приложение (Singleton)
- Потокобезопасность через asyncio.Lock
- Автоматический reconnect при обрыве
- Уважение FloodWait от Telegram
"""

import os
import asyncio
import sys
from pathlib import Path
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    AuthKeyInvalidError,
)

# Загрузка .env
env_path = Path("/app/secrets/.env")
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv(Path(__file__).parent.parent.parent / "secrets" / ".env")


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
                session_path = os.getenv("KRAKEN_SESSION_PATH", "/app/sessions/kraken.session")
                if session_path.startswith("/app/") and not Path("/app").exists():
                    session_path = "/app" + session_path[4:]
                
                api_id = int(os.getenv("TG_API_ID", "0"))
                api_hash = os.getenv("TG_API_HASH", "")
                
                if not api_id or not api_hash:
                    raise ValueError(
                        "TG_API_ID или TG_API_HASH не заданы в .env! "
                        "Проверь /app/secrets/.env"
                    )
                
                proxy = None
                proxy_type = os.getenv("TG_PROXY_TYPE", "").lower()
                proxy_ip = os.getenv("TG_PROXY_IP", "")
                proxy_port = os.getenv("TG_PROXY_PORT", "")
                
                if proxy_type and proxy_ip and proxy_port:
                    proxy = {
                        'proxy_type': proxy_type,
                        'addr': proxy_ip,
                        'port': int(proxy_port),
                    }
                    proxy_user = os.getenv("TG_PROXY_USER", "")
                    proxy_pass = os.getenv("TG_PROXY_PASS", "")
                    if proxy_user:
                        proxy['username'] = proxy_user
                        proxy['password'] = proxy_pass
                    print(f"🛡️ Proxy configured: {proxy_type}://{proxy_ip}:{proxy_port}")
                
                cls._instance = TelegramClient(
                    session=session_path,
                    api_id=api_id,
                    api_hash=api_hash,
                    proxy=proxy,
                    connection_retries=3,
                    auto_reconnect=True,
                    timeout=30
                )
                print(f"🔑 TelegramClient singleton created")
                print(f"   Session: {session_path}")
                print(f"   API_ID: {api_id}")
            
            return cls._instance
    
    @classmethod
    async def init_client(cls) -> TelegramClient:
        client = await cls.get_instance()
        
        if cls._initialized:
            print("✅ Client already initialized")
            return client
        
        print("🔄 Connecting to Telegram...")
        await client.connect()
        
        if not await client.is_user_authorized():
            phone = os.getenv("TG_PHONE_NUMBER", "")
            password = os.getenv("TG_2FA_PASSWORD", "")
            print(f"📱 Starting new session for {phone}")
            await client.start(phone=phone, password=password or None)
            print("✅ New session created and authorized")
        else:
            me = await client.get_me()
            print(f"✅ Session loaded: @{me.username or me.first_name}")
        
        cls._initialized = True
        return client
    
    @classmethod
    async def disconnect(cls):
        if cls._instance and cls._initialized:
            print("🛑 Disconnecting from Telegram...")
            await cls._instance.disconnect()
            cls._initialized = False
            print("✅ Disconnected")
    
    @classmethod
    async def reconnect(cls) -> bool:
        if not cls._instance:
            print("❌ Cannot reconnect: no instance")
            return False
        
        try:
            print("🔄 Reconnecting...")
            await cls._instance.connect()
            
            if await cls._instance.is_user_authorized():
                print("✅ Reconnected successfully")
                cls._initialized = True
                return True
            else:
                print("❌ Reconnect failed: not authorized")
                return False
        except AuthKeyInvalidError:
            print("💀 FATAL: AuthKey invalid — session is dead")
            return False
        except Exception as e:
            print(f"❌ Reconnect error: {e}")
            return False
    
    # ===== 3.1.4. HEARTBEAT =====
    
    @classmethod
    async def heartbeat_loop(cls, stop_event: asyncio.Event = None):
        consecutive_failures = 0
        max_failures = 3
        print("💓 Heartbeat loop started (every 300s)")
        while True:
            if stop_event and stop_event.is_set():
                print("💓 Heartbeat stopped")
                break
            await asyncio.sleep(300)
            try:
                client = await cls.get_instance()
                if not client.is_connected():
                    raise ConnectionError("Not connected")
                me = await client.get_me()
                consecutive_failures = 0
                print(f"💓 Heartbeat OK: user_id={me.id}")
            except Exception as e:
                consecutive_failures += 1
                print(f"⚠️ Heartbeat failed ({consecutive_failures}/{max_failures}): {e}")
                if consecutive_failures >= max_failures:
                    print("🔄 Starting auto-reconnect...")
                    success = await cls.reconnect()
                    if success:
                        consecutive_failures = 0
                    else:
                        print("❌ Reconnect failed, will retry next heartbeat")
                        consecutive_failures = 0
    
    # ===== 3.1.5. GET MESSAGES FAST =====
    
    @staticmethod
    async def get_messages_fast(client: TelegramClient, channel_id: str, limit: int = 50, offset_id: int = None):
        try:
            messages = await client.get_messages(channel_id, limit=limit, offset_id=offset_id)
            return messages
        except FloodWaitError as e:
            raise e
        except Exception as e:
            print(f"❌ Error fetching from {channel_id}: {e}")
            return []
    
    # ===== 3.1.6. FLOODWAIT HANDLER =====
    
    @staticmethod
    async def handle_floodwait(error: FloodWaitError):
        import random
        wait_seconds = error.seconds
        jitter = random.uniform(0, wait_seconds * 0.3)
        delay = wait_seconds + jitter
        print(f"🌊 FloodWait: Telegram says wait {wait_seconds}s, waiting {delay:.1f}s (with jitter)")
        await asyncio.sleep(delay)
        return wait_seconds > 30
    
    # ===== 3.1.8. AUTH REVOKED HANDLER =====
    
    @staticmethod
    async def handle_auth_revoked():
        print("💀 FATAL: Session dead (AuthKeyInvalid)")
        try:
            from clients.bot_client import BotClient
            bot = BotClient()
            await bot.send_alert(
                severity="FATAL",
                error_type="AUTH_REVOKED",
                message="Session revoked, need manual bootstrap."
            )
        except Exception as e:
            print(f"⚠️ Could not send Beacon alert: {e}")
        print("🛑 Exiting with code 1")
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
        print("🚀 TelegramLifespan STARTUP")
        self.client = await TelegramClientManager.init_client()
        self._heartbeat_task = asyncio.create_task(
            TelegramClientManager.heartbeat_loop(self._stop_event)
        )
        return self.client
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        print("🛑 TelegramLifespan SHUTDOWN")
        self._stop_event.set()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        await TelegramClientManager.disconnect()
        print("✅ TelegramLifespan SHUTDOWN complete")