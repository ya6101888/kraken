"""
KRAKEN Engine — Центральный оркестратор сбора сигналов.

Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.3 engine.py
Версия: v5.2.3 (GOLDEN ASSEMBLY)

Принципы:
- Получает каналы от ChannelManager
- Собирает сообщения через TelegramClient
- Передаёт в Harvester → AIFirewall → StorageWriter
- Сохраняет last_processed_id для каждого канала
- Обрабатывает FloodWait и ошибки соединения
"""

import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.signal import (
    RawTelegramMessage,
    RawMessageWithTrace,
    SanitizedMessage,
    ApprovedSignal,
    MiningCycleLog,
    GoogleSheetsRow
)


class Engine:
    """
    Центральный оркестратор цикла сбора сигналов.
    
    Использование:
        engine = Engine(channel_manager, gsheets_client)
        await engine.run_cycle(trace_id)
    """
    
    def __init__(self, channel_manager=None, gsheets_client=None):
        """
        Args:
            channel_manager: ChannelManager (будет создан при первом использовании)
            gsheets_client: GoogleSheetsClient (будет создан при первом использовании)
        """
        self._channel_manager = channel_manager
        self._gsheets_client = gsheets_client
        
        # Хранит последний обработанный message_id для каждого канала
        self.last_processed: Dict[str, int] = {}
        
        # Время последнего успешного цикла (для Beacon)
        self.last_success: Optional[datetime] = None
        
        # Статистика
        self.total_cycles: int = 0
        self.total_signals: int = 0
    
    # ===== 4.3.1. ПРОВЕРКА СОЕДИНЕНИЯ =====
    
    async def ensure_connection(self) -> bool:
        """
        Проверяет, что Telegram-клиент подключён.
        
        Если соединение потеряно — пытается переподключиться.
        
        Returns:
            True если соединение активно.
        """
        from clients.telegram_client import TelegramClientManager
        
        client = await TelegramClientManager.get_instance()
        
        if not client.is_connected():
            print("⚠️ Socket closed, attempting reconnect...")
            success = await TelegramClientManager.reconnect()
            if not success:
                print("❌ Reconnect failed")
                return False
        
        return True
    
    # ===== 4.3.2. ПОЛУЧЕНИЕ КАНАЛОВ =====
    
    async def get_channels(self, cycle_counter: int) -> List:
        """
        Получает список каналов для текущего цикла.
        
        Args:
            cycle_counter: Номер цикла (для дифференциации по tier)
        """
        if self._channel_manager is None:
            from core.channel_manager import ChannelManager
            self._channel_manager = ChannelManager(self._gsheets_client)
        
        await self._channel_manager.ensure_fresh_cache()
        return self._channel_manager.get_channels_for_cycle(cycle_counter)
    
    # ===== 4.3.3. СБОР СООБЩЕНИЙ =====
    
    async def fetch_all_messages(
        self,
        channels: List,
        trace_id: str
    ) -> List[RawMessageWithTrace]:
        """
        Собирает сообщения из всех переданных каналов.
        
        Для каждого канала:
        1. Определяет offset_id (последний обработанный)
        2. Вызывает get_messages_fast()
        3. Сохраняет новый last_processed_id
        
        Args:
            channels: Список каналов для опроса
            trace_id: ID текущего цикла
        
        Returns:
            Список сообщений с trace_id.
        """
        from clients.telegram_client import TelegramClientManager
        
        client = await TelegramClientManager.get_instance()
        all_messages: List[RawMessageWithTrace] = []
        
        for channel in channels:
            channel_id = channel.channel_id
            offset_id = self.last_processed.get(channel_id, 0)
            
            try:
                messages = await TelegramClientManager.get_messages_fast(
                    client=client,
                    channel_id=channel_id,
                    limit=50,
                    offset_id=offset_id
                )
                
                if messages:
                    # Сохраняем ID последнего сообщения
                    self.last_processed[channel_id] = messages[0].id
                    
                    # Конвертируем в RawMessageWithTrace
                    for msg in messages:
                        try:
                            raw = RawMessageWithTrace(
                                message_id=msg.id,
                                channel_id=channel_id,
                                channel_name=getattr(msg.chat, 'title', channel.title) if msg.chat else channel.title,
                                content=msg.text or "",
                                date=msg.date.replace(tzinfo=None) if msg.date else datetime.now(),
                                from_id=msg.sender_id if msg.sender_id else None,
                                views=getattr(msg, 'views', None),
                                trace_id=trace_id,
                                collected_at=datetime.now()
                            )
                            all_messages.append(raw)
                        except Exception as e:
                            print(f"⚠️ Skipping message {msg.id} from {channel_id}: {e}")
                
            except Exception as e:
                error_name = type(e).__name__
                
                if "FloodWait" in error_name:
                    # FloodWait — останавливаем сбор, но не падаем
                    print(f"🌊 FloodWait from {channel_id}: {e}")
                    break
                
                print(f"❌ Error fetching from {channel_id}: {e}")
        
        print(f"📩 Collected {len(all_messages)} messages from {len(channels)} channels")
        return all_messages
    
    # ===== ЗАПУСК ПОЛНОГО ЦИКЛА =====
    
    async def run_cycle(self, trace_id: str) -> dict:
        """
        Выполняет ПОЛНЫЙ цикл сбора сигналов.
        """
        started_at = datetime.now()
        stats = {
            "trace_id": trace_id,
            "started_at": started_at,
            "messages_collected": 0,
            "messages_after_harvester": 0,
            "signals_approved": 0,
            "errors": []
        }
        
        # 1. Проверка соединения
        if not await self.ensure_connection():
            stats["errors"].append("Connection failed")
            return stats
        
        # 2. Получение каналов
        channels = await self.get_channels(self.total_cycles + 1)
        if not channels:
            print("⚠️ No channels to process")
            return stats
            
        # 3. Сбор сообщений
        try:
            messages = await self.fetch_all_messages(channels, trace_id)
        except Exception as e:
            # Наш амортизатор: сохраняем то, что fetch_all_messages успел собрать до ошибки
            stats["errors"].append(f"Fetch error: {e}")
            messages = []        
                
        stats["messages_collected"] = len(messages)
        
        # 4. Harvester (очистка и дедупликация)
        sanitized = await self._run_harvester(messages, trace_id)
        stats["messages_after_harvester"] = len(sanitized)
        
        # 5. AI Firewall (классификация)
        approved = await self._run_firewall(sanitized)
        stats["signals_approved"] = len(approved)
        
        # 6. Запись в Google Sheets
        if approved:
            await self._write_signals(approved)
        
        # 7. Логирование
        self.total_cycles += 1
        self.total_signals += len(approved)
        self.last_success = datetime.now()
        
        finished_at = datetime.now()
        duration = (finished_at - started_at).total_seconds()
        
        print(f"✅ Cycle {trace_id}: {len(messages)} collected, "
              f"{len(sanitized)} sanitized, {len(approved)} approved "
              f"({duration:.1f}s)")
        
        stats["finished_at"] = finished_at
        stats["duration_seconds"] = duration
        
        return stats
    
    # ===== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ =====
    
    async def _run_harvester(self, messages: List[RawMessageWithTrace], trace_id: str) -> List[SanitizedMessage]:
        """Пропускает сообщения через Harvester."""
        try:
            from core.harvester import Harvester
            if not hasattr(self, '_harvester'):
                self._harvester = Harvester()
            return await self._harvester.process(messages)
        except ImportError:
            print("⚠️ Harvester not available, returning raw messages")
            return []
    
    async def _run_firewall(self, messages: List[SanitizedMessage]) -> List[ApprovedSignal]:
        """Пропускает сообщения через AIFirewall."""
        if not messages:
            return []
        
        try:
            from core.ai_firewall import AIFirewall
            if not hasattr(self, '_firewall'):
                self._firewall = AIFirewall()
            return await self._firewall.classify_batch(messages)
        except ImportError:
            print("⚠️ AIFirewall not available, returning empty")
            return []
    
    async def _write_signals(self, signals: List[ApprovedSignal]):
        """
        Записывает сигналы в Google Sheets через StorageWriter.
        """
        if not signals:
            return
        
        try:
            from core.storage_writer import StorageWriter
            if not hasattr(self, '_writer') or self._writer is None:
                # Безопасная ленивая инициализация: если gsheets_client=None, StorageWriter сам возьмет данные из конфига
                self._writer = StorageWriter(self._gsheets_client)
            await self._writer.write_signals(signals)
        except Exception as e:
            print(f"⚠️ StorageWriter failed to save signals: {e}")
    
    # ===== СТАТИСТИКА =====
    
    def get_stats(self) -> dict:
        """Возвращает статистику Engine."""
        return {
            "total_cycles": self.total_cycles,
            "total_signals": self.total_signals,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "channels_tracked": len(self.last_processed)
        }