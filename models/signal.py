"""
KRAKEN Data Models — ДНК структуры данных.

Фаза 2: МОДЕЛИ ДАННЫХ
Модуль: 2.1 signal.py
Версия: v2.1 GOLDEN MASTER (SRE 5.0 Canon)
"""

import os
import re
from enum import Enum
from datetime import datetime
from typing import List, Optional, Any
from pydantic import BaseModel, Field, model_validator


# ==========================================
# 1. IMMUTABLE ENUMS (ГЕО, СЕГМЕНТЫ, СТАТУСЫ И ОТК)
# ==========================================

class MarketSegment(str, Enum):
    PRIMARY = "PRIMARY"      # Новостройки
    SECONDARY = "SECONDARY"  # Вторичка
    RENT = "RENT"            # Аренда
    INVEST = "INVEST"        # Инвестиции
    PRO = "PRO"              # Проф. аналитика ЮФО
    NULL = "NULL"            # Не определен

class GeoFocus(str, Enum):
    ROSTOV_CITY = "ROSTOV_CITY"
    ROSTOV_REGION = "ROSTOV_REGION"
    SOUTHERN_FEDERAL_DISTRICT = "SOUTHERN_FEDERAL_DISTRICT"
    FEDERAL = "FEDERAL"
    NULL = "NULL"

class SourceType(str, Enum):
    ANALYTIC = "ANALYTIC"
    DEVELOPER = "DEVELOPER"
    NEWS = "NEWS"
    AGENCY = "AGENCY"
    PRIVATE = "PRIVATE"

class ChannelStatus(str, Enum):
    ACTIVE = "ACTIVE"
    TESTING = "TESTING"
    BANNED = "BANNED"

class ChannelTier(int, Enum):
    TIER_1 = 1
    TIER_2 = 2
    TIER_3 = 3
    TIER_4 = 4
    TIER_5 = 5

class RejectReason(str, Enum):
    """Причины отбраковки сообщения спам-фильтром Harvester."""
    DUPLICATE = "DUPLICATE"
    SHORT_TEXT = "SHORT_TEXT"
    MUTED_KEYWORD = "MUTED_KEYWORD"
    NO_DATA = "NO_DATA"
    SPAM = "SPAM"


# ==========================================
# 2. СТАРЫЕ МОДЕЛИ (ПОЛНАЯ ОБРАТНАЯ СОВМЕСТИМОСТЬ)
# ==========================================

class RawTelegramMessage(BaseModel):
    """Сырое сообщение из Telegram."""
    message_id: int = Field(ge=1)
    channel_id: str = Field(min_length=1, max_length=100)
    channel_name: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=10000)
    date: datetime
    from_id: Optional[int] = Field(default=None)
    views: Optional[int] = Field(default=None, ge=0)

class RawMessageWithTrace(RawTelegramMessage):
    """Сообщение со сквозным trace_id."""
    trace_id: str = Field(pattern=r"^KRAKEN_\d{8}_\d{6}_[a-f0-9]{8}$")
    collected_at: datetime = Field(default_factory=datetime.now)

class SanitizedMessage(RawMessageWithTrace):
    """Сообщение после обработки подсистемой Harvester."""
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    cleaned_content: str = Field(max_length=4000)
    is_rejected: bool = Field(default=False)
    reject_reason: Optional[str] = Field(default=None)

class ChannelRegistryEntry(BaseModel):
    """Канал в реестре конфигурации tg_channels."""
    channel_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    source_type: SourceType = Field(default=SourceType.NEWS)
    tier: int = Field(default=3, ge=1, le=5)
    geo_focus: GeoFocus = Field(default=GeoFocus.ROSTOV_CITY)
    status: ChannelStatus = Field(default=ChannelStatus.ACTIVE)
    last_scan: Optional[datetime] = None

class MiningCycleLog(BaseModel):
    """Лог цикла сбора данных для листа tg_mining_log."""
    trace_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    messages_collected: int = 0
    messages_after_harvester: int = 0
    signals_approved: int = 0
    errors: Optional[List[str]] = None
    floodwait_seconds: Optional[int] = None

class GoogleSheetsRow(BaseModel):
    """
    Плоская модель для записи в Google Sheets.
    Полная совместимость со старыми вызовами + расширение до 23 колонок v1.2.
    """
    signal_id: str = ""
    trace_id: str = ""
    channel_name: str = ""
    message_id: int = 0
    content: Optional[str] = None
    relevance_score: float = 0.0
    market_segment: Optional[str] = None
    geo_focus: Optional[str] = None
    price: Optional[int] = None
    rooms: Optional[int] = None
    area: Optional[float] = None
    floor: Optional[str] = None
    address: Optional[str] = None
    developer: Optional[str] = None
    completion_date: Optional[str] = None
    collected_at: datetime = Field(default_factory=datetime.now)
    is_approved: bool = True
    wf06_used_at: Optional[datetime] = None
    
    # Новые поля контракта v1.2
    classification: Optional[str] = None
    segment_confidence: float = 1.0
    source_type: str = "AGENCY"
    source_tier: int = 3
    priority_score: float = 0.0
    object_price: Optional[int] = None
    object_address: Optional[str] = None
    object_rooms: Optional[int] = None
    object_area: Optional[float] = None
    object_floor: Optional[str] = None
    object_developer: Optional[str] = None
    object_completion_date: Optional[str] = None
    original_content: str = ""
    cleaned_content: str = ""

class BatchAIResponse(BaseModel):
    """Служебный контейнер для валидации ответов от OpenAI Client."""
    results: List[Any] = Field(default_factory=list)


# ==========================================
# 3. НОВЫЕ DTO v1.2 (ВЛОЖЕННЫЕ СТРУКТУРЫ)
# ==========================================

class SourcePassport(BaseModel):
    """Паспорт источника сигнала."""
    source_type: SourceType = Field(default=SourceType.AGENCY)
    source_tier: int = Field(default=3, ge=1, le=5)

class ObjectDetails(BaseModel):
    """Детальные метрики физического объекта недвижимости."""
    price: Optional[int] = Field(default=None)
    address: Optional[str] = Field(default=None)
    rooms: Optional[int] = Field(default=None)
    area: Optional[float] = Field(default=None)
    floor: Optional[str] = Field(default=None)
    developer: Optional[str] = Field(default=None)
    completion_date: Optional[str] = Field(default=None)

class ApprovedSignalV2(BaseModel):
    """Каноничный DTO Сигнала SRE 5.0 v1.2 с вложенными паспортами."""
    signal_id: str
    trace_id: str
    channel_name: str
    message_id: int
    classification: MarketSegment = Field(default=MarketSegment.PRIMARY)
    segment_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: SourcePassport = Field(default_factory=SourcePassport)
    geo: GeoFocus = Field(default=GeoFocus.ROSTOV_CITY)
    object_data: ObjectDetails = Field(default_factory=ObjectDetails)
    original_content: str
    cleaned_content: str
    relevance_score: float = Field(ge=0.0, le=1.0)
    collected_at: datetime = Field(default_factory=datetime.now)
    is_approved: bool = Field(default=True)
    wf06_used_at: Optional[datetime] = None
    priority_score: float = Field(default=0.0)

    @model_validator(mode="after")
    def calculate_sre_metrics(self) -> 'ApprovedSignalV2':
        # 1. Рассчет приоритета по SRE-формуле
        confidence = self.segment_confidence
        tier = self.source.source_tier
        raw_score = confidence * ((6.0 - tier) / 5.0) * 4.0
        self.priority_score = max(0.0, min(4.0, round(raw_score, 2)))
        
        # 2. Интеллектуальное масштабирование цен в рубли
        if self.object_data and self.object_data.price:
            p = self.object_data.price
            if p < 100_000:
                self.object_data.price = p * 1000 if p > 10_000 else p * 1000000
        return self


# Глобальный алиас совместимости типов (Закон Системы)
ApprovedSignal = ApprovedSignalV2