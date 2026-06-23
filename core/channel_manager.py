"""
KRAKEN Channel Manager — Управление реестром каналов.
Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.2 channel_manager.py
Версия: v5.3.3 (SRE 5.0 CORE DIAGNOSTIC PATCH)
Дата/Время стабилизации: 2026-06-23 14:15:00 UTC
"""

import sys
import re
import traceback
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.signal import (
    ChannelRegistryEntry, 
    ChannelStatus, 
    SourceType, 
    GeoFocus
)

TIER_FREQUENCY: Dict[int, int] = {1: 1, 2: 2, 3: 4, 4: 8, 5: 0}

class ChannelManager:
    def __init__(self, gsheets_client=None):
        self._gsheets_client = gsheets_client
        self.channels_cache: List[ChannelRegistryEntry] = []
        self.last_load: Optional[datetime] = None
        self.banned_cache: set = set()
        self.cache_ttl_seconds: int = 3600
    
    async def load_channels(self) -> List[ChannelRegistryEntry]:
        """Загружает реестр с громкой SRE-диагностикой."""
        if self._gsheets_client is None:
            from clients.gsheets_client import GoogleSheetsClient
            self._gsheets_client = GoogleSheetsClient()
        
        try:
            rows = self._gsheets_client.load_channels()
            if rows is None:
                raise ValueError("Google Sheets вернул пустой ответ (None)")
            
            new_cache = []
            for row in rows:
                try:
                    raw_id = str(row.get("channel_id", "")).strip()
                    if not raw_id: continue
                    
                    raw_tier = row.get("tier", 3)
                    tier_val = int(re.findall(r'\d+', str(raw_tier))[0]) if isinstance(raw_tier, str) and re.findall(r'\d+', raw_tier) else int(raw_tier)
                    
                    st_val = str(row.get("source_type", "NEWS")).upper().strip()
                    st_val = st_val if st_val in SourceType.__members__ else "NEWS"
                    
                    geo_val = str(row.get("geo_focus", "ROSTOV_CITY")).upper().strip()
                    geo_val = geo_val if geo_val in GeoFocus.__members__ else "ROSTOV_CITY"
                    
                    status_val = str(row.get("status", "ACTIVE")).upper().strip()
                    
                    channel = ChannelRegistryEntry(
                        channel_id=raw_id,
                        title=str(row.get("title", "")).strip(),
                        source_type=SourceType(st_val),
                        tier=tier_val,
                        geo_focus=GeoFocus(geo_val),
                        status=ChannelStatus(status_val),
                        last_scan=row.get("last_scan") if row.get("last_scan") else None,
                        subscribers=int(row.get("subscribers")) if row.get("subscribers") else None
                    )
                    new_cache.append(channel)
                except Exception as row_e:
                    sys.stdout.write(f"⚠️ [SRE ТИПЫ] Пропущен канал: {row_e}\n")
            
            self.channels_cache = new_cache
            self.last_load = datetime.now()
            sys.stdout.write(f"✅ Loaded {len(self.channels_cache)} channels\n")
            return self.channels_cache
            
        except Exception:
            sys.stderr.write(f"❌ [CRITICAL] Failed to load channels!\n{traceback.format_exc()}\n")
            return self.channels_cache
            
    # Методы get_active_channels, get_channels_for_cycle, ban_channel, ensure_fresh_cache без изменений...


class ChannelManager:
    """
    Управляет реестром каналов для сбора сигналов.
    Загружает список из Google Sheets, кэширует на час,
    обеспечивает безопасность типов и отказоустойчивость.
    """
    
    def __init__(self, gsheets_client=None):
        self._gsheets_client = gsheets_client
        self.channels_cache: List[ChannelRegistryEntry] = []
        self.last_load: Optional[datetime] = None
        self.banned_cache: set = set()
        self.cache_ttl_seconds: int = 3600  # 1 час
    
    # ===== 4.2.1. ЗАГРУЗКА КАНАЛОВ И ВАЛИДАЦИЯ ТИПОВ =====
    
    async def load_channels(self) -> List[ChannelRegistryEntry]:
        """Загружает реестр каналов из Google Sheets с жесткой нормализацией типов."""
        if self._gsheets_client is None:
            from clients.gsheets_client import GoogleSheetsClient
            self._gsheets_client = GoogleSheetsClient()
        
        try:
            rows = self._gsheets_client.load_channels()
            
            new_cache = []
            for row in rows:
                try:
                    raw_id = str(row.get("channel_id", "")).strip()
                    if not raw_id:
                        continue
                        
                    # ГВАРДЕЙСКИЙ НАКАТ ТИПОВ: Выжигаем строки из tier
                    raw_tier = row.get("tier", 3)
                    if isinstance(raw_tier, str):
                        digits = re.findall(r'\d+', raw_tier)
                        tier_val = int(digits[0]) if digits else 3
                    else:
                        tier_val = int(raw_tier)
                        
                    # Безопасная нормализация Enum-строк из таблицы
                    st_val = str(row.get("source_type", "NEWS")).upper().strip()
                    if st_val not in SourceType.__members__:
                        st_val = "NEWS"
                        
                    geo_val = str(row.get("geo_focus", "ROSTOV_CITY")).upper().strip()
                    if geo_val not in GeoFocus.__members__:
                        geo_val = "ROSTOV_CITY"
                        
                    status_val = str(row.get("status", "ACTIVE")).upper().strip()
                    
                    channel = ChannelRegistryEntry(
                        channel_id=raw_id,
                        title=str(row.get("title", "")).strip(),
                        source_type=SourceType(st_val),
                        tier=tier_val,
                        geo_focus=GeoFocus(geo_val),
                        status=ChannelStatus(status_val),
                        last_scan=row.get("last_scan") if row.get("last_scan") else None,
                        subscribers=int(row.get("subscribers")) if row.get("subscribers") else None
                    )
                    new_cache.append(channel)
                except Exception as row_e:
                    sys.stdout.write(f"⚠️ [SRE ТИПЫ] Пропущен невалидный канал в таблице: {row_e}\n")
                    sys.stdout.flush()
            
            self.channels_cache = new_cache
            self.last_load = datetime.now()
            sys.stdout.write(f"✅ Loaded {len(self.channels_cache)} channels from registry\n")
            sys.stdout.flush()
            return self.channels_cache
            
        except Exception as e:
            sys.stdout.write(f"❌ Failed to load channels: {e}\n")
            sys.stdout.flush()
            return self.channels_cache
    
    # ===== 4.2.2. АКТИВНЫЕ КАНАЛЫ =====
    
    def get_active_channels(self) -> List[ChannelRegistryEntry]:
        """Возвращает только каналы со статусом ACTIVE и не из banned_cache."""
        return [
            ch for ch in self.channels_cache
            if ch.status == ChannelStatus.ACTIVE and ch.channel_id not in self.banned_cache
        ]
    
    # ===== 4.2.3. ДИФФЕРЕНЦИАЦИЯ ПО TIER (БЕЗ СТРОКОВЫХ ОШИБОК) =====
    
    def get_channels_for_cycle(self, cycle_counter: int) -> List[ChannelRegistryEntry]:
        """Возвращает каналы для конкретного цикла сбора."""
        active = self.get_active_channels()
        selected = []
        
        for ch in active:
            # ch.tier гарантированно int благодаря load_channels()
            freq = TIER_FREQUENCY.get(ch.tier, 0)
            if freq == 0:
                continue  # Tier 5 — спам, пропускаем
            if cycle_counter % freq == 0:
                selected.append(ch)
        
        sys.stdout.write(
            f"📡 Cycle #{cycle_counter}: selected {len(selected)}/{len(active)} channels "
            f"(T1={sum(1 for c in selected if c.tier==1)}, "
            f"T2={sum(1 for c in selected if c.tier==2)}, "
            f"T3={sum(1 for c in selected if c.tier==3)}, "
            f"T4={sum(1 for c in selected if c.tier==4)})\n"
        )
        sys.stdout.flush()
        return selected
    
    # ===== 4.2.4. ПЕРСИСТЕНТНЫЙ БАН КАНАЛА =====
    
    async def ban_channel(self, channel_id: str, reason: str = "Unknown"):
        """Банит канал локально и синхронизирует статус напрямую в Google Sheets."""
        if channel_id in self.banned_cache:
            return
        
        self.banned_cache.add(channel_id)
        sys.stdout.write(f"🚫 Channel banned: {channel_id} ({reason})\n")
        sys.stdout.flush()
        
        # Обновляем статус локально
        for ch in self.channels_cache:
            if ch.channel_id == channel_id:
                ch.status = ChannelStatus.BANNED
                break
                
        # ЖЕСТКАЯ ПЕРСИСТЕНТНОСТЬ: Пишем обратно в Google Sheets
        if self._gsheets_client and hasattr(self._gsheets_client, 'update_channel_status'):
            try:
                await self._gsheets_client.update_channel_status(channel_id, "BANNED")
            except Exception as e:
                sys.stdout.write(f"⚠️ Could not update remote channel status in Sheets: {e}\n")
                sys.stdout.flush()
    
    # ===== 4.2.6. АВТОМАТИЧЕСКИЙ СБРОС КЭША =====
    
    async def ensure_fresh_cache(self) -> List[ChannelRegistryEntry]:
        """Проверяет актуальность кэша и обновляет при необходимости."""
        if self.last_load is None:
            return await self.load_channels()
        
        age = (datetime.now() - self.last_load).total_seconds()
        if age > self.cache_ttl_seconds:
            return await self.load_channels()
            
        return self.channels_cache
    
    def get_stats(self) -> dict:
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