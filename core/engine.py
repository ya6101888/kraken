"""
KRAKEN Engine — Центральный оркестратор сбора сигналов.

Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.3 engine.py
Версия: v5.2.4 (SRE 5.0 CORE SYNCHRONIZED)
Дата/Время стабилизации: 2026-05-29 20:15:00 UTC
"""

import sys
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.signal import (
    RawMessageWithTrace,
    SanitizedMessage,
    ApprovedSignal
)


class Engine:
    """Центральный оркестратор цикла сбора сигналов."""
    
    def __init__(self, channel_manager=None, gsheets_client=None):
        self._channel_manager = channel_manager
        self._gsheets_client = gsheets_client
        self.last_processed: Dict[str, int] = {}
        self.last_success: Optional[datetime] = None
        self.total_cycles: int = 0
        self.total_signals: int = 0
    
    async def ensure_connection(self) -> bool:
        from clients.telegram_client import TelegramClientManager
        client = await TelegramClientManager.get_instance()
        if not client.is_connected():
            sys.stdout.write(f"[{datetime.now().isoformat()}] ⚠️ Socket closed, attempting reconnect...\n")
            sys.stdout.flush()
            success = await TelegramClientManager.reconnect()
            if not success:
                sys.stdout.write(f"[{datetime.now().isoformat()}] ❌ Reconnect failed\n")
                sys.stdout.flush()
                return False
        return True
    
    async def get_channels(self, cycle_counter: int) -> List:
        if self._channel_manager is None:
            from core.channel_manager import ChannelManager
            self._channel_manager = ChannelManager(self._gsheets_client)
        await self._channel_manager.ensure_fresh_cache()
        return self._channel_manager.get_channels_for_cycle(cycle_counter)
    
    async def fetch_all_messages(self, channels: List, trace_id: str) -> List[RawMessageWithTrace]:
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
                    self.last_processed[channel_id] = messages[0].id
                    
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
                            pass
                
            except Exception as e:
                if "FloodWait" in type(e).__name__:
                    break
        
        sys.stdout.write(f"[{datetime.now().isoformat()}] 📩 Collected {len(all_messages)} messages from {len(channels)} channels\n")
        sys.stdout.flush()
        return all_messages
    
    async def run_cycle(self, trace_id: str) -> dict:
        started_at = datetime.now()
        stats = {
            "trace_id": trace_id,
            "started_at": started_at,
            "messages_collected": 0,
            "messages_after_harvester": 0,
            "signals_approved": 0,
            "errors": []
        }
        
        if not await self.ensure_connection():
            stats["errors"].append("Connection failed")
            return stats
        
        channels = await self.get_channels(self.total_cycles + 1)
        if not channels:
            return stats
            
        try:
            messages = await self.fetch_all_messages(channels, trace_id)
        except Exception as e:
            stats["errors"].append(f"Fetch error: {e}")
            messages = []        
                
        stats["messages_collected"] = len(messages)
        
        # 4. Harvester
        sanitized = await self._run_harvester(messages, trace_id)
        stats["messages_after_harvester"] = len(sanitized)
        
        # 5. Прямой вызов OpenAI Клиента (В обход поломанного ai_firewall.py)
        approved = []
        if sanitized:
            try:
                from clients.openai_client import OpenAIClient
                ai_client = OpenAIClient()
                
                # По закону SRE скармливаем ИИ исключительно строки cleaned_content
                texts_to_analyze = [msg.cleaned_content for msg in sanitized]
                ai_response = await ai_client.classify_batch(texts_to_analyze)
                
                if ai_response and "signals" in ai_response:
                    # Маппим сырой JSON от OpenAI обратно в строгие Pydantic модели ApprovedSignal
                    for idx, raw_signal in enumerate(ai_response["signals"]):
                        try:
                            # Привязываем метаданные исходного сообщения к вердикту ИИ
                            src_msg = sanitized[min(idx, len(sanitized)-1)]
                            
                            # Сборка вложенной структуры v1.2
                            signal_obj = ApprovedSignal(
                                signal_id=f"SIG_{src_msg.channel_id}_{src_msg.message_id}",
                                trace_id=trace_id,
                                channel_name=src_msg.channel_name,
                                message_id=src_msg.message_id,
                                classification=raw_signal.get("classification", "RESIDENTIAL"),
                                segment_confidence=float(raw_signal.get("segment_confidence", 0.9)),
                                source={
                                    "source_type": "TELEGRAM",
                                    "source_tier": "TIER_1"
                                },
                                geo=raw_signal.get("geo", "ROSTOV"),
                                priority_score=int(raw_signal.get("priority_score", 5)),
                                object_data={
                                    "price": raw_signal.get("object_data", {}).get("price"),
                                    "address": raw_signal.get("object_data", {}).get("address"),
                                    "rooms": raw_signal.get("object_data", {}).get("rooms"),
                                    "area": raw_signal.get("object_data", {}).get("area"),
                                    "floor": raw_signal.get("object_data", {}).get("floor"),
                                    "developer": raw_signal.get("object_data", {}).get("developer"),
                                    "completion_date": raw_signal.get("object_data", {}).get("completion_date")
                                },
                                original_content=src_msg.content,
                                cleaned_content=src_msg.cleaned_content,
                                relevance_score=float(raw_signal.get("relevance_score", 0.85)),
                                collected_at=src_msg.collected_at,
                                is_approved=True,
                                wf06_used_at=datetime.now().isoformat()
                            )
                            approved.append(signal_obj)
                        except Exception as inner_e:
                            sys.stdout.write(f"[{datetime.now().isoformat()}] ⚠️ Сбой маппинга сигнала ApprovedSignal: {inner_e}\n")
                            sys.stdout.flush()
            except Exception as e:
                sys.stdout.write(f"[{datetime.now().isoformat()}] ❌ Критический сбой слоя ИИ-Файрволла: {e}\n")
                sys.stdout.flush()
                stats["errors"].append(f"AI error: {e}")

        stats["signals_approved"] = len(approved)
        
        # 6. Запись в Google Sheets
        if approved:
            await self._write_signals(approved)
        
        self.total_cycles += 1
        self.total_signals += len(approved)
        self.last_success = datetime.now()
        
        duration = (datetime.now() - started_at).total_seconds()
        sys.stdout.write(f"[{datetime.now().isoformat()}] ✅ Cycle {trace_id}: {len(messages)} collected, {len(sanitized)} sanitized, {len(approved)} approved ({duration:.1f}s)\n")
        sys.stdout.flush()
        
        stats["finished_at"] = datetime.now()
        stats["duration_seconds"] = duration
        return stats
    
    async def _run_harvester(self, messages: List[RawMessageWithTrace], trace_id: str) -> List[SanitizedMessage]:
        try:
            from core.harvester import Harvester
            if not hasattr(self, '_harvester'):
                self._harvester = Harvester()
            return await self._harvester.process(messages)
        except Exception as e:
            sys.stdout.write(f"[{datetime.now().isoformat()}] ⚠️ Harvester error: {e}\n")
            sys.stdout.flush()
            return []
    
    async def _write_signals(self, signals: List[ApprovedSignal]):
        try:
            from core.storage_writer import StorageWriter
            if not hasattr(self, '_writer') or self._writer is None:
                self._writer = StorageWriter(self._gsheets_client)
            await self._writer.write_signals(signals)
        except Exception as e:
            sys.stdout.write(f"[{datetime.now().isoformat()}] ⚠️ StorageWriter failed to save signals: {e}\n")
            sys.stdout.flush()
    
    def get_stats(self) -> dict:
        return {
            "total_cycles": self.total_cycles,
            "total_signals": self.total_signals,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "channels_tracked": len(self.last_processed)
        }