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
                # ===== 3.1.4. HEARTBEAT =====
    
    @classmethod
    async def heartbeat_loop(cls, stop_event: asyncio.Event = None):
        """
        Проверяет соединение каждые 5 минут.
        
        Если соединение живо — логирует успех.
        Если 3 ошибки подряд — запускает auto_reconnect().
        """
        consecutive_failures = 0
        max_failures = 3
        
        print("💓 Heartbeat loop started (every 300s)")
        
        while True:
            if stop_event and stop_event.is_set():
                print("💓 Heartbeat stopped")
                break
            
            await asyncio.sleep(300)  # 5 минут
            
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
                        consecutive_failures = 0  # Сбрасываем, пробуем дальше
    
    # ===== 3.1.5. GET MESSAGES FAST =====
    
    @staticmethod
    async def get_messages_fast(
        client: TelegramClient,
        channel_id: str,
        limit: int = 50,
        offset_id: int = None
    ):
        """
        Быстрый сбор сообщений из канала без оверхеда авторизации.
        
        Args:
            client: Экземпляр TelegramClient (из get_instance())
            channel_id: ID канала (например, '@forumrostov')
            limit: Сколько сообщений собрать (макс 50)
            offset_id: С какого ID начать (None = самые новые)
        
        Returns:
            Список сообщений или пустой список при ошибке.
        """
        try:
            messages = await client.get_messages(
                channel_id,
                limit=limit,
                offset_id=offset_id
            )
            return messages
        except FloodWaitError as e:
            # Пробрасываем выше для обработки в main loop
            raise e
        except Exception as e:
            print(f"❌ Error fetching from {channel_id}: {e}")
            return []
    
    # ===== 3.1.6. FLOODWAIT HANDLER =====
    
    @staticmethod
    async def handle_floodwait(error: FloodWaitError):
        """
        Обрабатывает FloodWait от Telegram.
        
        ЗОЛОТОЕ ПРАВИЛО: уважать wait_seconds от Telegram.
        Добавляем jitter (0-30%), чтобы несколько экземпляров KRAKEN
        не начали стучаться одновременно.
        
        Returns:
            True если нужно пропустить следующий цикл сбора.
        """
        import random
        wait_seconds = error.seconds
        jitter = random.uniform(0, wait_seconds * 0.3)
        delay = wait_seconds + jitter
        
        print(f"🌊 FloodWait: Telegram says wait {wait_seconds}s, "
              f"waiting {delay:.1f}s (with jitter)")
        await asyncio.sleep(delay)
        
        # Если ждали больше 30 секунд — пропускаем следующий цикл
        return wait_seconds > 30
    
    # ===== 3.1.8. AUTH REVOKED HANDLER =====
    
    @staticmethod
    async def handle_auth_revoked():
        """
        Обрабатывает фатальную ошибку: сессия умерла (AuthKeyInvalid).
        
        Отправляет BEACON FATAL и завершает процесс с exit(1).
        Контейнер упадёт, Docker перезапустит его.
        """
        print("💀 FATAL: Session dead (AuthKeyInvalid)")
        
        # Отправляем алерт в Beacon (если доступен)
        try:
            from src.clients.bot_client import BotClient
            bot = BotClient()
            await bot.send_alert(
                severity="FATAL",
                error_type="AUTH_REVOKED",
                message="Session revoked, need manual bootstrap. "
                        "Run bootstrap_session_v3.py on server."
            )
        except Exception as e:
            print(f"⚠️ Could not send Beacon alert: {e}")
        
        print("🛑 Exiting with code 1")
        sys.exit(1)


# ===== 3.1.3 & 3.1.9. LIFESPAN MANAGER =====

class TelegramLifespan:
    """
    Менеджер жизненного цикла Telegram-клиента.
    
    Используется в FastAPI lifespan или в main.py как контекстный менеджер.
    
    Пример использования:
        async with TelegramLifespan() as client:
            # работаем с client
            pass
        # здесь client уже disconnect'нут
    """
    
    def __init__(self):
        self.client: TelegramClient | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event = asyncio.Event()
    
    async def __aenter__(self) -> TelegramClient:
        """STARTUP: вызывается ОДИН раз при старте."""
        print("🚀 TelegramLifespan STARTUP")
        
        # Инициализируем клиент (connect + start)
        self.client = await TelegramClientManager.init_client()
        
        # Запускаем heartbeat в фоне
        self._heartbeat_task = asyncio.create_task(
            TelegramClientManager.heartbeat_loop(self._stop_event)
        )
        
        return self.client
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """SHUTDOWN: вызывается ОДИН раз при остановке."""
        print("🛑 TelegramLifespan SHUTDOWN")
        
        # Останавливаем heartbeat
        self._stop_event.set()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        # Отключаем клиент
        await TelegramClientManager.disconnect()
        
        print("✅ TelegramLifespan SHUTDOWN complete")
        return False
    # ===== 3.1.4. HEARTBEAT =====
    
    @classmethod
    async def heartbeat_loop(cls, stop_event: asyncio.Event = None):
        """
        Проверяет соединение каждые 5 минут.
        
        Если соединение живо — логирует успех.
        Если 3 ошибки подряд — запускает auto_reconnect().
        """
        consecutive_failures = 0
        max_failures = 3
        
        print("💓 Heartbeat loop started (every 300s)")
        
        while True:
            if stop_event and stop_event.is_set():
                print("💓 Heartbeat stopped")
                break
            
            await asyncio.sleep(300)  # 5 минут
            
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
                        consecutive_failures = 0  # Сбрасываем, пробуем дальше
    
    # ===== 3.1.5. GET MESSAGES FAST =====
    
    @staticmethod
    async def get_messages_fast(
        client: TelegramClient,
        channel_id: str,
        limit: int = 50,
        offset_id: int = None
    ):
        """
        Быстрый сбор сообщений из канала без оверхеда авторизации.
        
        Args:
            client: Экземпляр TelegramClient (из get_instance())
            channel_id: ID канала (например, '@forumrostov')
            limit: Сколько сообщений собрать (макс 50)
            offset_id: С какого ID начать (None = самые новые)
        
        Returns:
            Список сообщений или пустой список при ошибке.
        """
        try:
            messages = await client.get_messages(
                channel_id,
                limit=limit,
                offset_id=offset_id
            )
            return messages
        except FloodWaitError as e:
            # Пробрасываем выше для обработки в main loop
            raise e
        except Exception as e:
            print(f"❌ Error fetching from {channel_id}: {e}")
            return []
    
    # ===== 3.1.6. FLOODWAIT HANDLER =====
    
    @staticmethod
    async def handle_floodwait(error: FloodWaitError):
        """
        Обрабатывает FloodWait от Telegram.
        
        ЗОЛОТОЕ ПРАВИЛО: уважать wait_seconds от Telegram.
        Добавляем jitter (0-30%), чтобы несколько экземпляров KRAKEN
        не начали стучаться одновременно.
        
        Returns:
            True если нужно пропустить следующий цикл сбора.
        """
        import random
        wait_seconds = error.seconds
        jitter = random.uniform(0, wait_seconds * 0.3)
        delay = wait_seconds + jitter
        
        print(f"🌊 FloodWait: Telegram says wait {wait_seconds}s, "
              f"waiting {delay:.1f}s (with jitter)")
        await asyncio.sleep(delay)
        
        # Если ждали больше 30 секунд — пропускаем следующий цикл
        return wait_seconds > 30
    
    # ===== 3.1.8. AUTH REVOKED HANDLER =====
    
    @staticmethod
    async def handle_auth_revoked():
        """
        Обрабатывает фатальную ошибку: сессия умерла (AuthKeyInvalid).
        
        Отправляет BEACON FATAL и завершает процесс с exit(1).
        Контейнер упадёт, Docker перезапустит его.
        """
        print("💀 FATAL: Session dead (AuthKeyInvalid)")
        
        # Отправляем алерт в Beacon (если доступен)
        try:
            from src.clients.bot_client import BotClient
            bot = BotClient()
            await bot.send_alert(
                severity="FATAL",
                error_type="AUTH_REVOKED",
                message="Session revoked, need manual bootstrap. "
                        "Run bootstrap_session_v3.py on server."
            )
        except Exception as e:
            print(f"⚠️ Could not send Beacon alert: {e}")
        
        print("🛑 Exiting with code 1")
        sys.exit(1)


# ===== 3.1.3 & 3.1.9. LIFESPAN MANAGER =====

class TelegramLifespan:
    """
    Менеджер жизненного цикла Telegram-клиента.
    
    Используется в FastAPI lifespan или в main.py как контекстный менеджер.
    
    Пример использования:
        async with TelegramLifespan() as client:
            # работаем с client
            pass
        # здесь client уже disconnect'нут
    """
    
    def __init__(self):
        self.client: TelegramClient | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event = asyncio.Event()
    
    async def __aenter__(self) -> TelegramClient:
        """STARTUP: вызывается ОДИН раз при старте."""
        print("🚀 TelegramLifespan STARTUP")
        
        # Инициализируем клиент (connect + start)
        self.client = await TelegramClientManager.init_client()
        
        # Запускаем heartbeat в фоне
        self._heartbeat_task = asyncio.create_task(
            TelegramClientManager.heartbeat_loop(self._stop_event)
        )
        
        return self.client
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """SHUTDOWN: вызывается ОДИН раз при остановке."""
        print("🛑 TelegramLifespan SHUTDOWN")
        
        # Останавливаем heartbeat
        self._stop_event.set()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        # Отключаем клиент
        await TelegramClientManager.disconnect()
        
        print("✅ TelegramLifespan SHUTDOWN complete")    