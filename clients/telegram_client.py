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
    # Fallback для локальной разработки (Windows)
    load_dotenv(Path(__file__).parent.parent.parent / "secrets" / ".env")


class TelegramClientManager:
    """
    Singleton-менеджер для TelegramClient.
    
    Гарантирует, что во всём приложении существует только ОДИН экземпляр
    клиента Telegram. Это предотвращает двойные подключения и снижает
    риск бана за подозрительную активность.
    
    Использование:
        client = await TelegramClientManager.get_instance()
        await TelegramClientManager.init_client()
    """
    
    _instance: TelegramClient | None = None
    _lock: asyncio.Lock = asyncio.Lock()
    _initialized: bool = False
    
    @classmethod
    async def get_instance(cls) -> TelegramClient:
        """
        Возвращает единственный экземпляр TelegramClient.
        
        При первом вызове создаёт клиент с параметрами из .env.
        При последующих вызовах возвращает уже существующий.
        
        Потокобезопасность: asyncio.Lock гарантирует, что даже при
        одновременном вызове из нескольких корутин экземпляр создастся
        только ОДИН раз.
        """
        async with cls._lock:
            if cls._instance is None:
                session_path = os.getenv(
                    "KRAKEN_SESSION_PATH",
                    "/app/sessions/kraken.session"
                )
                # Корректировка пути для запуска вне Docker
                if session_path.startswith("/app/") and not Path("/app").exists():
                    session_path = "/app" + session_path[4:]
                
                api_id = int(os.getenv("TG_API_ID", "0"))
                api_hash = os.getenv("TG_API_HASH", "")
                
                if not api_id or not api_hash:
                    raise ValueError(
                        "TG_API_ID или TG_API_HASH не заданы в .env! "
                        "Проверь /app/secrets/.env"
                    )
                
                # Настройка прокси (если задан в .env)
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
        """
        Инициализирует клиент: подключает и авторизует.
        
        Если .session файл существует и валиден — использует его.
        Если нет — запускает процедуру start() с телефоном и 2FA.
        """
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
        """Отключает клиент от Telegram."""
        if cls._instance and cls._initialized:
            print("🛑 Disconnecting from Telegram...")
            await cls._instance.disconnect()
            cls._initialized = False
            print("✅ Disconnected")
    
    @classmethod
    async def reconnect(cls) -> bool:
        """
        Переподключает клиент после обрыва соединения.
        
        Returns:
            True если переподключение успешно, False если нет.
        """
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
    