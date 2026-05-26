"""
KRAKEN Channel Manager — Управление реестром каналов.

Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.2 channel_manager.py
Версия: v5.2.3

Принципы:
- Загрузка каналов из Google Sheets (tg_channels)
- Кэширование на 1 час
- Дифференциация по tier (Tier 1 — каждый цикл, Tier 4 — каждый 8-й)
- Бан каналов при ошибках
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional, Dict

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.signal import ChannelRegistryEntry, ChannelStatus, ChannelTier


# ===== КОНСТАНТЫ =====

# Частота опроса: Tier 1 → каждый цикл, Tier 2 → каждый 2-й, Tier 3 → каждый 4-й, Tier 4 → каждый 8-й
# Tier 5 (спам) — исключён из сбора
TIER_FREQUENCY: Dict[int, int] = {
    1: 1,   # Каждый цикл (каждые 15 минут)
    2: 2,   # Каждые 30 минут
    3: 4,   # Каждый час
    4: 8,   # Каждые 2 часа
    5: 0,   # Никогда (спам)
}


class ChannelManager:
    """
    Управляет реестром каналов для сбора сигналов.
    
    Загружает список из Google Sheets, кэширует на час,
    и отдаёт нужные каналы в зависимости от tier и номера цикла.
    
    Использование:
        mgr = ChannelManager(gsheets_client)
        channels = mgr.get_channels_for_cycle(cycle_counter=42)
    """
    
    def __init__(self, gsheets_client=None):
        """
        Args:
            gsheets_client: GoogleSheetsClient для загрузки каналов.
                           Если None — будет создан при первом использовании.
        """
        self._gsheets_client = gsheets_client
        self.channels_cache: List[ChannelRegistryEntry] = []
        self.last_load: Optional[datetime] = None
        self.banned_cache: set = set()
        self.cache_ttl_seconds: int = 3600  # 1 час
    
    # ===== 4.2.1. ЗАГРУЗКА КАНАЛОВ =====
    
    async def load_channels(self) -> List[ChannelRegistryEntry]:
        """
        Загружает реестр каналов из Google Sheets.
        
        Returns:
            Список ВСЕХ каналов из листа tg_channels.
        """
        if self._gsheets_client is None:
            from clients.gsheets_client import GoogleSheetsClient
            self._gsheets_client = GoogleSheetsClient()
        
        try:
            rows = self._gsheets_client.load_channels()
            
            self.channels_cache = []
            for row in rows:
                try:
                    channel = ChannelRegistryEntry(
                        channel_id=row.get("channel_id", ""),
                        title=row.get("title", ""),
                        source_type=row.get("source_type", "NEWS"),
                        tier=row.get("tier", 3),
                        geo_focus=row.get("geo_focus", "ROSTOV_CITY"),
                        status=row.get("status", "ACTIVE"),
                        last_scan=row.get("last_scan") if row.get("last_scan") else None,
                        subscribers=row.get("subscribers"),
                        avg_reach=row.get("avg_reach"),
                        engagement=row.get("engagement"),
                        citation_index=row.get("citation_index"),
                        content_quality=row.get("content_quality"),
                        fraud_signs=row.get("fraud_signs")
                    )
                    self.channels_cache.append(channel)
                except Exception as e:
                    print(f"⚠️ Skipping invalid channel row: {e}")
            
            self.last_load = datetime.now()
            print(f"✅ Loaded {len(self.channels_cache)} channels from registry")
            return self.channels_cache
            
        except Exception as e:
            print(f"❌ Failed to load channels: {e}")
            # Если есть кэш — используем его
            if self.channels_cache:
                print(f"⚠️ Using cached channels ({len(self.channels_cache)} entries)")
            return self.channels_cache
    
    # ===== 4.2.2. АКТИВНЫЕ КАНАЛЫ =====
    
    def get_active_channels(self) -> List[ChannelRegistryEntry]:
        """
        Возвращает только каналы со статусом ACTIVE и не из banned_cache.
        """
        return [
            ch for ch in self.channels_cache
            if ch.status == "ACTIVE" and ch.channel_id not in self.banned_cache
        ]
    
    # ===== 4.2.3. ДИФФЕРЕНЦИАЦИЯ ПО TIER =====
    
    def get_channels_for_cycle(self, cycle_counter: int) -> List[ChannelRegistryEntry]:
        """
        Возвращает каналы для конкретного цикла сбора.
        
        Логика:
        - Tier 1: каждый цикл (cycle_counter % 1 == 0 → всегда)
        - Tier 2: каждый 2-й цикл
        - Tier 3: каждый 4-й цикл
        - Tier 4: каждый 8-й цикл
        - Tier 5: никогда (спам)
        
        Args:
            cycle_counter: Номер текущего цикла (1, 2, 3...)
        
        Returns:
            Список каналов, которые нужно опросить в этом цикле.
        """
        active = self.get_active_channels()
        selected = []
        
        for ch in active:
            freq = TIER_FREQUENCY.get(ch.tier, 0)
            if freq == 0:
                continue  # Tier 5 — никогда
            if cycle_counter % freq == 0:
                selected.append(ch)
        
        print(f"📡 Cycle #{cycle_counter}: selected {len(selected)}/{len(active)} channels "
              f"(T1={sum(1 for c in selected if c.tier==1)}, "
              f"T2={sum(1 for c in selected if c.tier==2)}, "
              f"T3={sum(1 for c in selected if c.tier==3)}, "
              f"T4={sum(1 for c in selected if c.tier==4)})")
        
        return selected
    
    # ===== 4.2.4. БАН КАНАЛА =====
    
    async def ban_channel(self, channel_id: str, reason: str = "Unknown"):
        """
        Банит канал: добавляет в локальный кэш и обновляет Google Sheets.
        
        Args:
            channel_id: ID канала (@username)
            reason: Причина бана (для логов)
        """
        if channel_id in self.banned_cache:
            return
        
        self.banned_cache.add(channel_id)
        print(f"🚫 Channel banned: {channel_id} ({reason})")
        
        # Обновляем статус в Google Sheets
        if self._gsheets_client:
            try:
                # Обновляем поле status в кэше
                for ch in self.channels_cache:
                    if ch.channel_id == channel_id:
                        ch.status = "BANNED"
                        break
            except Exception as e:
                print(f"⚠️ Could not update channel status: {e}")
    
    # ===== 4.2.6. КЭШИРОВАНИЕ =====
    
    async def ensure_fresh_cache(self) -> List[ChannelRegistryEntry]:
        """
        Проверяет актуальность кэша и обновляет при необходимости.
        
        Если с момента последней загрузки прошло больше часа —
        перезагружает каналы из Google Sheets.
        
        Returns:
            Актуальный список каналов.
        """
        if self.last_load is None:
            print("📡 Cache empty, loading channels...")
            return await self.load_channels()
        
        age = (datetime.now() - self.last_load).total_seconds()
        if age > self.cache_ttl_seconds:
            print(f"📡 Cache expired ({age:.0f}s old), reloading...")
            return await self.load_channels()
        
        print(f"📡 Using cached channels ({len(self.channels_cache)} entries, {age:.0f}s old)")
        return self.channels_cache
    
    # ===== СТАТИСТИКА =====
    
    def get_stats(self) -> dict:
        """Возвращает статистику по каналам."""
        active = self.get_active_channels()
        by_tier = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for ch in active:
            by_tier[ch.tier] = by_tier.get(ch.tier, 0) + 1
        
        return {
            "total_loaded": len(self.channels_cache),
            "active": len(active),
            "banned": len(self.banned_cache),
            "by_tier": by_tier,
            "last_load": self.last_load.isoformat() if self.last_load else None
        }