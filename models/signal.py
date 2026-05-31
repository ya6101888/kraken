"""
KRAKEN Data Models — ДНК структуры данных.

Фаза 2: МОДЕЛИ ДАННЫХ
Модуль: 2.1 signal.py
Версия: v2.2.7 (SRE 5.0 CANONICAL — INTELLIGENT PRICE NORMALIZATION & AREA EXPANDED)
Дата стабилизации: 2026-05-31 17:15:00 UTC
"""

import re
from enum import Enum
from datetime import datetime
from typing import List, Optional, Any
from pydantic import BaseModel, Field, model_validator


# ==========================================
# 1. IMMUTABLE ENUMS (СТРОГИЙ САНКЦИОНИРОВАННЫЙ СЛОЙ)
# ==========================================

class MarketSegment(str, Enum):
    PRIMARY = "PRIMARY"      # Первичный рынок
    SECONDARY = "SECONDARY"  # Вторичный рынок
    RENT = "RENT"            # Аренда
    INVEST = "INVEST"        # Инвестиции
    PRO = "PRO"              # Профессиональный B2B
    NULL = "NULL"            # Дефолтный сброс


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


class RejectReason(str, Enum):
    DUPLICATE = "DUPLICATE"
    SHORT_TEXT = "SHORT_TEXT"
    MUTED_KEYWORD = "MUTED_KEYWORD"
    NO_DATA = "NO_DATA"
    SPAM = "SPAM"


# ==========================================
# 2. СИСТЕМНЫЕ СТРУКТУРЫ ТРАНЗИТА (ПОТОК ДАННЫХ)
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
    reject_reason: Optional[RejectReason] = Field(default=None)


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


# ==========================================
# 3. КАНОНИЧЕСКИЙ ПЛОСКИЙ СИГНАЛ v2.1 (25 СТОЛБЦОВ ТЗ)
# ==========================================

class ApprovedSignal(BaseModel):
    """Канонический плоский DTO Сигнала SRE 5.0 под спецификацию матрицы v2.1."""
    signal_id: str = Field(pattern=r"^SIG_[A-Za-z0-9_–\-]+_\d+$")
    trace_id: str = Field(pattern=r"^KRAKEN_.*")
    channel_id: str = Field(min_length=1, max_length=100)
    channel_name: str = Field(min_length=1, max_length=200)
    message_id: int = Field(ge=1)
    market_segment: MarketSegment = Field(default=MarketSegment.SECONDARY)
    segment_confidence: float = Field(default=0.95, ge=0.00, le=1.00)
    source_type: SourceType = Field(default=SourceType.AGENCY)
    source_tier: int = Field(default=3, ge=1, le=5)
    geo_focus: GeoFocus = Field(default=GeoFocus.ROSTOV_CITY)
    priority_score: float = Field(default=0.0)
    
    # Плоская бизнес-матрица — Защита от дробных цен ИИ через float тип на входе
    price: Optional[float] = Field(default=None, ge=0)
    address: Optional[str] = Field(default=None, max_length=500)
    rooms: Optional[int] = Field(default=None, ge=0, le=10) # ge=0 разрешает студии!
    
    # ИСПРАВЛЕНО: Расширен лимит до 10000.0 для загородных усадеб и участков ИЖС ЮФО
    area: Optional[float] = Field(default=None, ge=1.0, le=10000.0)
    
    floor: Optional[str] = Field(default=None, max_length=50)
    developer: Optional[str] = Field(default=None, max_length=200)
    completion_date: Optional[str] = Field(default=None)
    phone_number: Optional[str] = Field(default=None, max_length=50)
    
    # Текстовые контейнеры и логистика
    original_content: str = Field(max_length=10000)
    cleaned_content: str = Field(max_length=4000)
    relevance_score: float = Field(default=0.85, ge=0.00, le=1.00)
    collected_at: datetime = Field(default_factory=datetime.now)
    is_approved: bool = Field(default=True)
    wf06_used_at: Optional[datetime] = None

    @model_validator(mode="after")
    def compute_and_normalize_canon(self) -> 'ApprovedSignal':
        # 1. Валидация маски даты сдачи (Strict ISO Month: YYYY-MM)
        if self.completion_date:
            if not re.match(r'^\d{4}-\d{2}$', str(self.completion_date)):
                self.completion_date = None
                
        # 2. ИНТЕЛЛЕКТУАЛЬНЫЙ СЛОЙ ВЫПРАВЛЕНИЯ ЦЕН SRE 5.0 (Защита аренды и коротких миллионов)
        if self.price and self.price > 0:
            if self.market_segment == MarketSegment.RENT:
                # Аренда: защищаем от превращения тысяч в миллионы
                if self.price < 1000:
                    self.price *= 1000  # Например, если ИИ вернул "25" вместо "25000"
            else:
                # Продажа/Инвестиции (PRIMARY, SECONDARY, INVEST)
                if self.price < 100_000:
                    if self.price <= 10_000:
                        # Кейсы риелторов: "4,6 млн" -> 4.6 -> 4600000 или "4200" -> 4200000
                        self.price = self.price * 1_000_000 if self.price < 100 else self.price * 1000
                    else:
                        # Если цена между 10 000 и 100 000 на продажу — это аномалия, оставляем инт
                        pass
                        
            self.price = int(round(self.price))

        # 3. Расчет priority_score по канонической SRE-формуле ТЗ v2.1
        source_bonus = (5 - self.source_tier) * 0.4
        
        geo_bonuses = {
            GeoFocus.ROSTOV_CITY: 0.4,
            GeoFocus.ROSTOV_REGION: 0.2,
            GeoFocus.SOUTHERN_FEDERAL_DISTRICT: 0.1,
            GeoFocus.FEDERAL: 0.0
        }
        geo_bonus = geo_bonuses.get(self.geo_focus, 0.0)
        
        segment_bonuses = {
            MarketSegment.PRIMARY: 0.3,
            MarketSegment.SECONDARY: 0.2,
            MarketSegment.INVEST: 0.3,
            MarketSegment.RENT: 0.1,
            MarketSegment.PRO: 0.0
        }
        segment_bonus = segment_bonuses.get(self.market_segment, 0.0)
        
        self.priority_score = round(source_bonus + geo_bonus + segment_bonus, 2)
        return self


class GoogleSheetsRow(ApprovedSignal):
    """Модель-наследник для совместимости с буферным слоем BufferedWriter. Полный проброс channel_id."""
    pass


class BatchAIResponse(BaseModel):
    """Служебный контейнер для валидации пакетных ответов OpenAI API под Матрицу v2.1."""
    signals: List[Any] = Field(default_factory=list)


ApprovedSignalV2 = ApprovedSignal