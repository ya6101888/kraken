"""
KRAKEN Engine — Центральный оркестратор сбора сигналов.

Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.3 engine.py
Версия: v5.3.8 (SRE 5.0 CORE SYNCHRONIZED — RAW observado — AUTOMATIC FLUSH)
Дата/Время стабилизации: 2026-05-30 20:30:00 UTC
"""

import sys
import re
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.signal import (
    RawMessageWithTrace,
    SanitizedMessage,
    ApprovedSignal,
    MarketSegment,
    SourceType,
    GeoFocus
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
                        except Exception:
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
        
        # 5. Прямой вызов OpenAI Клиента (Абсолютная плоская матрица v2.1)
        approved = []
        if sanitized:
            try:
                channel_meta = {c.channel_id: c for c in channels}
                
                from clients.openai_client import OpenAIClient
                ai_client = OpenAIClient()
                
                texts_to_analyze = [msg.cleaned_content for msg in sanitized]
                ai_response = await ai_client.classify_batch(texts_to_analyze)
                
                # ТОТАЛЬНЫЙ КВАНТОСКОПИЧЕСКИЙ ЛОГ — ВЫБИВАЕМ ПРОБКУ КЭША ДОКЕРА
                sys.stdout.write(f"🔮 [RAW AI RESPONSE] Получен пакет: {ai_response}\n")
                sys.stdout.flush()
                
                if ai_response and "signals" in ai_response:
                    for idx, raw_signal in enumerate(ai_response["signals"]):
                        try:
                            src_msg = sanitized[min(idx, len(sanitized)-1)]
                            meta = channel_meta.get(src_msg.channel_id)
                            
                            st_val = meta.source_type if meta else SourceType.PRIVATE
                            tier_val = int(meta.tier) if meta else 3
                            
                            seg_val = str(raw_signal.get("market_segment", "SECONDARY")).upper()
                            if seg_val not in MarketSegment.__members__:
                                seg_val = "SECONDARY"
                                
                            geo_val = str(raw_signal.get("geo_focus", "ROSTOV_CITY")).upper()
                            if geo_val not in GeoFocus.__members__:
                                geo_val = "ROSTOV_CITY"
                                
                            if not raw_signal.get("is_approved", True):
                                continue
                                
                            obj_data = raw_signal.get("object")
                            if not isinstance(obj_data, dict):
                                obj_data = raw_signal
                            
                            signal_obj = ApprovedSignal(
                                signal_id=f"SIG_{src_msg.channel_id.replace('@', '')}_{src_msg.message_id}",
                                trace_id=trace_id,
                                channel_id=src_msg.channel_id,
                                channel_name=src_msg.channel_name,
                                message_id=src_msg.message_id,
                                market_segment=MarketSegment(seg_val),
                                segment_confidence=float(raw_signal.get("segment_confidence", 0.95)),
                                source_type=SourceType(st_val),
                                source_tier=tier_val,
                                geo_focus=GeoFocus(geo_val),
                                
                                price=obj_data.get("price") if obj_data.get("price") is not None else raw_signal.get("price"),
                                address=obj_data.get("address") if obj_data.get("address") is not None else raw_signal.get("address"),
                                rooms=obj_data.get("rooms") if obj_data.get("rooms") is not None else raw_signal.get("rooms"),
                                area=obj_data.get("area") if obj_data.get("area") is not None else raw_signal.get("area"),
                                floor=str(obj_data.get("floor")) if obj_data.get("floor") is not None else (str(raw_signal.get("floor")) if raw_signal.get("floor") is not None else None),
                                developer=obj_data.get("developer") if obj_data.get("developer") is not None else raw_signal.get("developer"),
                                completion_date=obj_data.get("completion_date") if obj_data.get("completion_date") is not None else raw_signal.get("completion_date"),
                                phone_number=obj_data.get("phone_number") if obj_data.get("phone_number") is not None else raw_signal.get("phone_number"),
                                
                                original_content=src_msg.content,
                                cleaned_content=src_msg.cleaned_content,
                                relevance_score=float(raw_signal.get("relevance_score", 0.85)),
                                collected_at=src_msg.collected_at,
                                is_approved=True,
                                wf06_used_at=None
                            )
                            approved.append(signal_obj)
                        except Exception as inner_e:
                            sys.stdout.write(f"❌ [VALIDATION CRASH] Ошибка Pydantic ядра v5.3.8: {inner_e} | RAW JSON: {raw_signal}\n")
                            sys.stdout.flush()
            except Exception as e:
                sys.stdout.write(f"❌ Критический сбой слоя ИИ-Файрволла v2.1: {e}\n")
                sys.stdout.flush()
                stats["errors"].append(f"AI error: {e}")

        # Слой финальной гигиены данных перед отгрузкой — ПРЯМОЙ СЛИВ ПО ТЗ
        final_approved = approved
        stats["signals_approved"] = len(final_approved)
        
        # 6. Запись в Google Sheets по плоской матрице с автоматическим выталкиванием
        if final_approved:
            await self._write_signals(final_approved)
        
        self.total_cycles += 1
        self.total_signals += len(final_approved)
        self.last_success = datetime.now()
        
        duration = (datetime.now() - started_at).total_seconds()
        sys.stdout.write(f"[{datetime.now().isoformat()}] ✅ Cycle {trace_id}: {len(messages)} collected, {len(sanitized)} sanitized, {len(final_approved)} approved ({duration:.1f}s)\n")
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
        except Exception:
            return []
    
    async def _write_signals(self, signals: List[ApprovedSignal]):
        try:
            from core.storage_writer import StorageWriter
            if not hasattr(self, '_writer') or self._writer is None:
                self._writer = StorageWriter(self._gsheets_client)
            
            await self._writer.write_signals(signals)
            if hasattr(self._writer, 'flush'):
                await self._writer.flush()
            elif hasattr(self._writer, '_buffer') and hasattr(self._writer, 'flush_buffer'):
                await self._writer.flush_buffer()
                
            sys.stdout.write(f"[{datetime.now().isoformat()}] 📊 [Engine Flush] Принудительно вытолкнули {len(signals)} записей в Sheets.\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stdout.write(f"[{datetime.now().isoformat()}] ⚠️ [Sheets Write Exception] {e}\n")
            sys.stdout.flush()
    
    def get_stats(self) -> dict:
        return {
            "total_cycles": self.total_cycles,
            "total_signals": self.total_signals,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "channels_tracked": len(self.last_processed)
        }