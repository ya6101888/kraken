"""
KRAKEN Data Contracts — Модели данных для всех этапов обработки сигналов.

Фаза 2: DATA CONTRACTS
Версия: v5.2.3 (ПОЛНАЯ СБОРКА, ПАТЧ PYTHON 3.13 + PEP 604)
"""

# ===== 2.1.1. ИМПОРТЫ =====
from datetime import datetime
from typing import Literal, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator
import re


# ===== 2.1.2. LITERAL-ТИПЫ (Классификаторы) =====

SignalStatus = Literal["PENDING", "APPROVED", "REJECTED", "DUPLICATE", "ERROR"]

RejectReason = Literal[
    "IRRELEVANT_LOCATION", "SPAM", "TOO_SHORT", "TOO_LONG",
    "NO_MEANING", "OLD_MESSAGE", "AI_ERROR"
]

MarketSegment = Literal["PRIMARY", "SECONDARY", "RENT", "INVEST", "PRO"]

GeoFocus = Literal["ROSTOV_CITY", "ROSTOV_REGION", "SOUTHERN_DISTRICT", "FEDERAL"]

ChannelSourceType = Literal["ANALYTIC", "DEVELOPER", "NEWS", "AGENCY", "PRIVATE"]

ChannelStatus = Literal["ACTIVE", "TESTING", "BANNED"]

ChannelTier = Literal[1, 2, 3, 4, 5]

AlertSeverity = Literal["FATAL", "CRITICAL", "WARNING", "INFO", "RECOVERED"]

AIDecision = Literal["RELEVANT", "IRRELEVANT", "UNCERTAIN", "ERROR"]

WF06ReadStatus = Literal["NOT_USED", "USED", "ARCHIVED"]

Environment = Literal["DEV", "STAGING", "PRODUCTION", "DISASTER"]

ObjectFieldStatus = Literal["PRESENT", "MISSING"]

SignalSource = Literal["TELEGRAM_CHANNEL", "TELEGRAM_SUPERGROUP", "TELEGRAM_CHAT", "TELEGRAM_BOT"]


# ===== 2.1.3. ObjectData =====

class ObjectData(BaseModel):
    """Параметры объекта недвижимости, извлечённые AI."""
    model_config = ConfigDict(extra="ignore")
    
    price: int | None = Field(default=None, ge=0, le=1_000_000_000)
    address: str | None = Field(default=None, max_length=500)
    rooms: int | None = Field(default=None, ge=1, le=10)
    area: float | None = Field(default=None, ge=1.0, le=1000.0)
    floor: str | None = Field(default=None, max_length=50)
    developer: str | None = Field(default=None, max_length=200)
    completion_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")

    @field_validator("completion_date")
    @classmethod
    def validate_completion_date(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            year, month = v.split("-")
            if not (1 <= int(month) <= 12):
                raise ValueError(f"Месяц должен быть 01-12, получено: {month}")
        except (ValueError, AttributeError):
            raise ValueError(f"Неверный формат даты: {v}. Ожидается ГГГГ-ММ")
        return v


# ===== 2.1.4. RawTelegramMessage =====

class RawTelegramMessage(BaseModel):
    """Сырое сообщение из Telegram."""
    model_config = ConfigDict(extra="forbid")
    
    message_id: int = Field(ge=1)
    channel_id: str = Field(min_length=1, max_length=100)
    channel_name: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=10000)
    date: datetime
    from_id: int | None = Field(default=None)
    views: int | None = Field(default=None, ge=0)


# ===== 2.1.5. RawMessageWithTrace =====

class RawMessageWithTrace(RawTelegramMessage):
    """Сообщение с trace_id."""
    trace_id: str = Field(pattern=r"^KRAKEN_\d{8}_\d{6}_[a-f0-9]{8}$")
    collected_at: datetime = Field(default_factory=datetime.now)


# ===== 2.1.6. SanitizedMessage =====

class SanitizedMessage(RawMessageWithTrace):
    """Сообщение после Harvester."""
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    cleaned_content: str = Field(max_length=4000)
    is_rejected: bool = Field(default=False)
    reject_reason: RejectReason | None = Field(default=None)


# ===== 2.1.7. ClassificationResult + BatchAIResponse =====

class ClassificationResult(BaseModel):
    """Результат AI-классификации одного сообщения."""
    model_config = ConfigDict(extra="ignore")
    
    message_index: int = Field(ge=0, le=19)
    relevance_score: float = Field(ge=0.0, le=1.0)
    market_segment: MarketSegment | None = Field(default=None)
    geo_focus: GeoFocus | None = Field(default=None)
    object_data: ObjectData = Field(default_factory=ObjectData)


class BatchAIResponse(BaseModel):
    """Ответ GPT на батч сообщений."""
    model_config = ConfigDict(extra="ignore")
    
    results: list[ClassificationResult] = Field(min_length=1, max_length=20)

    @field_validator("results")
    @classmethod
    def validate_message_indices(cls, v: list[ClassificationResult]) -> list[ClassificationResult]:
        indices = [r.message_index for r in v]
        expected = list(range(len(v)))
        if indices != expected:
            raise ValueError(f"Индексы должны идти по порядку 0..{len(v)-1}, получено: {indices}")
        return v


# ===== 2.1.8. ApprovedSignal =====

class ApprovedSignal(SanitizedMessage):
    """Сообщение, прошедшее AI-фильтр."""
    relevance_score: float = Field(ge=0.0, le=1.0)
    is_approved: bool = Field(default=True)
    approved_at: datetime = Field(default_factory=datetime.now)
    signal_id: str = Field(default="")
    market_segment: MarketSegment | None = Field(default=None)
    geo_focus: GeoFocus | None = Field(default=None)
    object_data: ObjectData = Field(default_factory=ObjectData)

    @field_validator("signal_id")
    @classmethod
    def validate_signal_id_format(cls, v: str) -> str:
        if v and not re.match(r"^SIG_[a-f0-9]{8}_\d+$", v):
            raise ValueError(f"Неверный формат signal_id: {v}")
        return v
    
    def model_post_init(self, __context):
        if not self.signal_id:
            short_trace = self.trace_id.split("_")[-1][:8] if "_" in self.trace_id else self.trace_id[:8]
            object.__setattr__(self, "signal_id", f"SIG_{short_trace}_{self.message_id}")


# ===== 2.1.9. GoogleSheetsRow =====

class GoogleSheetsRow(BaseModel):
    """Плоская модель для записи в Google Sheets."""
    model_config = ConfigDict(extra="forbid")
    
    signal_id: str
    trace_id: str
    channel_name: str
    message_id: int
    content: str | None = None
    relevance_score: float
    market_segment: str | None = None
    geo_focus: str | None = None
    price: int | None = None
    rooms: int | None = None
    area: float | None = None
    floor: str | None = None
    address: str | None = None
    developer: str | None = None
    completion_date: str | None = None
    collected_at: datetime
    wf06_used_at: datetime | None = None
    
    @classmethod
    def from_approved_signal(cls, signal: ApprovedSignal) -> "GoogleSheetsRow":
        return cls(
            signal_id=signal.signal_id,
            trace_id=signal.trace_id,
            channel_name=signal.channel_name,
            message_id=signal.message_id,
            content=signal.cleaned_content,
            relevance_score=signal.relevance_score,
            market_segment=signal.market_segment,
            geo_focus=signal.geo_focus,
            price=signal.object_data.price,
            rooms=signal.object_data.rooms,
            area=signal.object_data.area,
            floor=signal.object_data.floor,
            address=signal.object_data.address,
            developer=signal.object_data.developer,
            completion_date=signal.object_data.completion_date,
            collected_at=signal.collected_at,
            wf06_used_at=None
        )


# ===== 2.1.10. ChannelRegistryEntry =====

class ChannelRegistryEntry(BaseModel):
    """Один канал в реестре tg_channels."""
    model_config = ConfigDict(extra="forbid")
    
    channel_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    source_type: ChannelSourceType
    tier: ChannelTier
    geo_focus: GeoFocus
    status: ChannelStatus = Field(default="ACTIVE")
    last_scan: datetime | None = Field(default=None)
    subscribers: int | None = Field(default=None, ge=0)
    avg_reach: int | None = Field(default=None, ge=0)
    engagement: float | None = Field(default=None, ge=0.0, le=100.0)
    citation_index: float | None = Field(default=None, ge=0.0)
    content_quality: float | None = Field(default=None, ge=0.0, le=10.0)
    fraud_signs: list[str] | None = Field(default=None)


# ===== 2.1.11. MiningCycleLog =====

class MiningCycleLog(BaseModel):
    """Запись в логе цикла сбора."""
    model_config = ConfigDict(extra="forbid")
    
    trace_id: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    messages_collected: int = Field(ge=0)
    messages_after_harvester: int = Field(ge=0)
    signals_approved: int = Field(ge=0)
    errors: list[dict[str, Any]] | None = None
    floodwait_seconds: int | None = Field(default=None, ge=0)


# ===== 2.1.12. DLQEntry =====

class DLQEntry(BaseModel):
    """Запись в Dead Letter Queue."""
    model_config = ConfigDict(extra="forbid")
    
    timestamp: datetime = Field(default_factory=datetime.now)
    trace_id: str
    signal: ApprovedSignal
    error: str
    retry_count: int = Field(default=0, ge=0, le=3)


# ===== 2.1.13. BeaconAlert =====

class BeaconAlert(BaseModel):
    """Алерт мониторинга."""
    model_config = ConfigDict(extra="forbid")
    
    severity: AlertSeverity
    error_type: str | None = None
    trace_id: str | None = None
    message: str = Field(min_length=1, max_length=1000)
    timestamp: datetime = Field(default_factory=datetime.now)


# ===== 2.1.16. УТИЛИТЫ НОРМАЛИЗАЦИИ =====

def normalize_price(raw_price: str) -> int | None:
    """Извлекает цену в рублях."""
    if not raw_price:
        return None
    cleaned = re.sub(r'\s', '', raw_price.lower())
    if 'млн' in cleaned:
        num = re.search(r'(\d+(?:[.,]\d+)?)', cleaned)
        if num:
            return int(float(num.group(1).replace(',', '.')) * 1_000_000)
    if 'тыс' in cleaned or 'к' in cleaned:
        num = re.search(r'(\d+(?:[.,]\d+)?)', cleaned)
        if num:
            return int(float(num.group(1).replace(',', '.')) * 1_000)
    num = re.search(r'(\d+(?:[.,]\d+)?)', cleaned)
    if num:
        return int(float(num.group(1).replace(',', '.')))
    return None


def normalize_rooms(raw_rooms: str) -> int | None:
    """Извлекает количество комнат."""
    if not raw_rooms:
        return None
    low = raw_rooms.lower()
    mapping = {
        'студия': 1, 'студию': 1,
        'двушка': 2, '2-к': 2, '2к': 2, 'двухкомнатная': 2,
        'трёшка': 3, 'трешка': 3, '3-к': 3, '3к': 3, 'трёхкомнатная': 3, 'трехкомнатная': 3,
        'четырехкомнатная': 4, '4-к': 4, '4к': 4,
        'пятикомнатная': 5, '5-к': 5, '5к': 5,
    }
    for key, value in mapping.items():
        if key in low:
            return value
    num = re.search(r'(\d+)', raw_rooms)
    if num:
        return int(num.group(1))
    return None  