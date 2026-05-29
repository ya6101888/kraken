import re
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator

# ==========================================
# 1. ENUMS (Бизнес-контур стандарта v1.2)
# ==========================================

class MarketSegment(str, Enum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    RENT = "RENT"
    INVEST = "INVEST"
    PRO = "PRO"

class SourceType(str, Enum):
    ANALYTIC = "ANALYTIC"
    DEVELOPER = "DEVELOPER"
    NEWS = "NEWS"
    AGENCY = "AGENCY"
    PRIVATE = "PRIVATE"

class GeoFocus(str, Enum):
    ROSTOV_CITY = "ROSTOV_CITY"
    ROSTOV_REGION = "ROSTOV_REGION"
    SOUTHERN_FEDERAL_DISTRICT = "SOUTHERN_FEDERAL_DISTRICT"
    FEDERAL = "FEDERAL"

# ==========================================
# 2. ВЛОЖЕННЫЕ МОДЕЛИ (Компоненты ДЕТАЛИ)
# ==========================================

class SourcePassport(BaseModel):
    """Паспорт канала-источника (Проброс метаданных из tg_channels)"""
    source_type: SourceType
    source_tier: int = Field(..., ge=1, le=5)

class ObjectDetails(BaseModel):
    """Детальные характеристики объекта недвижимости"""
    price: Optional[int] = None
    address: Optional[str] = Field(None, max_length=500)
    rooms: Optional[int] = Field(None, ge=1, le=10)
    area: Optional[float] = Field(None, ge=1.0, le=1000.0)
    floor: Optional[str] = None
    developer: Optional[str] = Field(None, max_length=200)
    completion_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}$")

    @field_validator("price", mode="before")
    @classmethod
    def scale_price_to_rubles(cls, v):
        """
        Хирургический валидатор: перехватывает кривой JSON от ИИ
        и масштабирует рубли (например: 4300 -> 4300000, '9.5 млн' -> 9500000)
        """
        if v is None:
            return None
            
        if isinstance(v, str):
            # Зачищаем строку от мусора
            v_clean = v.replace(",", ".").lower().strip()
            nums = re.findall(r"[-+]?\d*\.\d+|\d+", v_clean)
            if not nums:
                return None
            val = float(nums[0])
            
            if "млн" in v_clean:
                return int(val * 1_000_000)
            if "тр" in v_clean or val < 100000:
                return int(val * 1000)
            return int(val)
            
        # Защита от деления/обрезания на стороне ИИ (если прилетело 4300 интом)
        if isinstance(v, (int, float)) and v < 100000:
            return int(v * 1000)
            
        return int(v)

# ==========================================
# 3. КОРНЕВОЙ СУВЕРЕННЫЙ DTO СТАНДАРТА SRE 5.0
# ==========================================

class ApprovedSignal(BaseModel):
    """Монолитный контракт данных Кракена v1.2 (Запись таблицы)"""
    signal_id: str = Field(..., pattern=r"^SIG_[a-f0-9]{8}_\d+$")
    trace_id: str = Field(..., pattern=r"^KRAKEN_.*")
    channel_name: str
    message_id: int = Field(..., ge=1)
    
    # Вложенные узлы DTO v1.2
    classification: MarketSegment
    segment_confidence: float = Field(..., ge=0.0, le=1.0)
    source: SourcePassport
    geo: GeoFocus
    priority_score: float = Field(0.0, ge=0.0, le=4.0)
    object_data: ObjectDetails
    
    # Текстовые хранилища и семантика
    original_content: str
    cleaned_content: str
    relevance_score: float = Field(..., ge=0.70)
    collected_at: datetime
    is_approved: bool = True
    wf06_used_at: Optional[datetime] = None

    @model_validator(mode="after")
    def calculate_priority_score(self) -> "ApprovedSignal":
        """
        Автоматический математический расчёт индекса приоритета.
        Учитывает уверенность ИИ и Tier источника (Tier 1 весит больше, чем Tier 5).
        """
        # Вес тира: Tier 1 = 1.0, Tier 5 = 0.2
        tier_weight = (6 - self.source.source_tier) / 5.0
        base_score = self.segment_confidence * tier_weight * 4.0
        self.priority_score = round(max(0.0, min(4.0, base_score)), 2)
        return self


# ==========================================
# 4. ТЕХНИЧЕСКИЕ МОДЕЛИ ДЛЯ ЛОГОВ (Для совместимости)
# ==========================================

class MiningCycleLog(BaseModel):
    """Модель лога для листа tg_mining_log"""
    trace_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    messages_collected: int = 0
    messages_after_harvester: int = 0
    signals_approved: int = 0
    errors: Optional[list] = None
    floodwait_seconds: Optional[int] = None