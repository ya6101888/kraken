"""
KRAKEN Data Contracts — Модели данных для всех этапов обработки сигналов.

Фаза 2: DATA CONTRACTS
Версия: v5.2.3 (ПАТЧ СТАБИЛЬНОСТИ СЕГМЕНТОВ)
"""

# ===== 2.1.1. ИМПОРТЫ =====
from datetime import datetime
from typing import Optional, Literal, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator
import re


# ===== 2.1.2. LITERAL-ТИПЫ (Классификаторы) =====

SignalStatus = Literal["PENDING", "APPROVED", "REJECTED", "DUPLICATE", "ERROR"]

RejectReason = Literal[
    "IRRELEVANT_LOCATION",
    "SPAM",
    "TOO_SHORT",
    "TOO_LONG",
    "NO_MEANING",
    "OLD_MESSAGE",
    "AI_ERROR"
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


# ===== 2.1.3. ObjectData (Параметры объекта недвижимости) =====

class ObjectData(BaseModel):
    """
    Параметры объекта недвижимости, извлечённые AI из текста сообщения.
    Игнорирует лишние галлюцинации OpenAI, сохраняя стабильность.
    """
    model_config = ConfigDict(extra="ignore")
    
    price: Optional[int] = Field(
        default=None,
        ge=0,
        le=1_000_000_000,
        description="Цена в рублях (0 — 1 млрд)"
    )
    address: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Адрес объекта"
    )
    rooms: Optional[int] = Field(
        default=None,
        ge=1,
        le=10,
        description="Количество комнат (1-10)"
    )
    area: Optional[float] = Field(
        default=None,
        ge=1.0,
        le=1000.0,
        description="Площадь в м² (1.0 — 1000.0)"
    )
    floor: Optional[str] = Field(
        default=None,
        max_length=50,
        description="Этаж (например, '5/12')"
    )
    developer: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Застройщик"
    )
    completion_date: Optional[str] = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}$",
        description="Дата сдачи в формате ГГГГ-ММ (например, '2026-12')"
    )

    @field_validator("completion_date")
    @classmethod
    def validate_completion_date(cls, v: Optional[str]) -> Optional[str]:
        """Проверка: месяц должен быть 01-12."""
        if v is None:
            return v
        try:
            year, month = v.split("-")
            month_int = int(month)
            if not (1 <= month_int <= 12):
                raise ValueError(f"Месяц должен быть 01-12, получено: {month}")
        except (ValueError, AttributeError):
            raise ValueError(f"Неверный формат даты: {v}. Ожидается ГГГГ-ММ")
        return v
    

# ===== 2.1.4. RawTelegramMessage (Сырое сообщение из Telegram) =====

class RawTelegramMessage(BaseModel):
    """Сырое сообщение, полученное от Telegram API."""
    model_config = ConfigDict(extra="forbid")
    
    message_id: int = Field(ge=1, description="Уникальный ID сообщения в канале")
    channel_id: str = Field(min_length=1, max_length=100, description="ID канала")
    channel_name: str = Field(min_length=1, max_length=200, description="Название канала")
    content: str = Field(max_length=10000, description="Текст сообщения")
    date: datetime = Field(description="Дата и время публикации (UTC)")
    from_id: Optional[int] = Field(default=None, description="ID отправителя")
    views: Optional[int] = Field(default=None, ge=0, description="Количество просмотров")


# ===== 2.1.5. RawMessageWithTrace (Сообщение с trace_id) =====

class RawMessageWithTrace(RawTelegramMessage):
    """RawTelegramMessage + идентификатор цикла сбора (trace_id)."""
    trace_id: str = Field(
        pattern=r"^KRAKEN_\d{8}_\d{6}_[a-f0-9]{8}$",
        description="Уникальный ID цикла (KRAKEN_YYYYMMDD_HHMMSS_xxxxxxxx)"
    )
    collected_at: datetime = Field(
        default_factory=datetime.now,
        description="Когда сообщение было собрано (UTC)"
    )


# ===== 2.1.6. SanitizedMessage (Очищенное сообщение) =====

class SanitizedMessage(RawMessageWithTrace):
    """Сообщение после Harvester-обработки."""
    content_hash: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="SHA-256 хеш очищенного контента"
    )
    cleaned_content: str = Field(
        max_length=4000,
        description="Очищенный текст"
    )
    is_rejected: bool = Field(
        default=False,
        description="Отклонено ли сообщение фильтром"
    )
    reject_reason: Optional[RejectReason] = Field(
        default=None,
        description="Причина отклонения"
    )


# ===== 2.1.7. ClassificationResult + BatchAIResponse =====

class ClassificationResult(BaseModel):
    """
    Результат AI-классификации ОДНОГО сообщения.
    Игнорирует добавленные OpenAI левые теги и текстовые обоснования.
    """
    model_config = ConfigDict(extra="ignore")
    
    message_index: int = Field(
        ge=0,
        le=19,
        description="Индекс сообщения в батче (0..19)"
    )
    relevance_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Оценка релевантности (0.0 = нерелевантно, 1.0 = идеально)"
    )
    market_segment: Optional[MarketSegment] = Field(
        default=None,
        description="Сегмент рынка недвижимости"
    )
    geo_focus: Optional[GeoFocus] = Field(
        default=None,
        description="Географический фокус"
    )
    object_data: ObjectData = Field(
        default_factory=ObjectData,
        description="Извлечённые параметры объекта"
    )


class BatchAIResponse(BaseModel):
    """Ответ GPT на батч сообщений (до 20 штук)."""
    model_config = ConfigDict(extra="ignore")
    
    results: List[ClassificationResult] = Field(
        min_length=1,
        max_length=20,
        description="Результаты классификации"
    )

    @field_validator("results")
    @classmethod
    def validate_message_indices(cls, v: List[ClassificationResult]) -> List[ClassificationResult]:
        """Проверка: индексы должны идти по порядку 0, 1, 2..."""
        indices = [r.message_index for r in v]
        expected = list(range(len(v)))
        if indices != expected:
            raise ValueError(
                f"Индексы должны идти по порядку 0..{len(v)-1}, получено: {indices}"
            )
        return v


# ===== 2.1.8. ApprovedSignal (Одобренный сигнал) =====

class ApprovedSignal(SanitizedMessage):
    """Сообщение, прошедшее AI-фильтр."""
    relevance_score: float = Field(ge=0.0, le=1.0, description="Итоговая оценка релевантности от AI")
    is_approved: bool = Field(default=True, description="Флаг одобрения")
    approved_at: datetime = Field(default_factory=datetime.now, description="Когда сигнал был одобрен")
    signal_id: str = Field(default="", description="Уникальный ID сигнала")
    market_segment: Optional[MarketSegment] = Field(default=None, description="Определённый AI сегмент рынка")
    geo_focus: Optional[GeoFocus] = Field(default=None, description="Определённый AI гео-фокус")
    object_data: ObjectData = Field(default_factory=ObjectData, description="Извлечённые параметры объекта")

    @field_validator("signal_id")
    @classmethod
    def validate_signal_id_format(cls, v: str) -> str:
        if v and not re.match(r"^SIG_[a-f0-9]{8}_\d+$", v):
            raise ValueError(f"Неверный формат signal_id: {v}. Ожидается SIG_xxxxxxxx_N")
        return v
    
    def model_post_init(self, __context):
        if not self.signal_id:
            short_trace = self.trace_id.split("_")[-1][:8] if "_" in self.trace_id else self.trace_id[:8]
            object.__setattr__(self, "signal_id", f"SIG_{short_trace}_{self.message_id}")


# ===== 2.1.9. GoogleSheetsRow (Строка для записи в таблицу) =====

class GoogleSheetsRow(BaseModel):
    """Плоская модель для записи в Google Sheets."""
    model_config = ConfigDict(extra="forbid")
    
    signal_id: str = Field(description="Уникальный ID сигнала")
    trace_id: str = Field(description="ID цикла сбора")
    channel_name: str = Field(description="Название канала")
    message_id: int = Field(description="ID сообщения")
    content: Optional[str] = Field(default=None, description="Очищенный текст")
    relevance_score: float = Field(description="Оценка релевантности")
    market_segment: Optional[str] = Field(default=None, description="Сегмент рынка")
    geo_focus: Optional[str] = Field(default=None, description="Гео-фокус")
    price: Optional[int] = Field(default=None, description="Цена в рублях")
    rooms: Optional[int] = Field(default=None, description="Количество комнат")
    area: Optional[float] = Field(default=None, description="Площадь в м²")
    floor: Optional[str] = Field(default=None, description="Этаж")
    address: Optional[str] = Field(default=None, description="Адрес")
    developer: Optional[str] = Field(default=None, description="Застройщик")
    completion_date: Optional[str] = Field(default=None, description="Дата сдачи (ГГГГ-ММ)")
    collected_at: datetime = Field(description="Когда собрано")
    wf06_used_at: Optional[datetime] = Field(default=None, description="Когда прочитано заводом")
    
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


# ===== 2.1.10. ChannelRegistryEntry (Запись в реестре каналов) =====

class ChannelRegistryEntry(BaseModel):
    """Один канал в реестре tg_channels."""
    model_config = ConfigDict(extra="forbid")
    
    channel_id: str = Field(min_length=1, max_length=100, description="ID канала")
    title: str = Field(min_length=1, max_length=200, description="Название канала")
    source_type: ChannelSourceType = Field(description="Тип источника")
    tier: ChannelTier = Field(description="Уровень канала (1-5)")
    geo_focus: GeoFocus = Field(description="Географический фокус")
    status: ChannelStatus = Field(default="ACTIVE", description="Статус канала")
    last_scan: Optional[datetime] = Field(default=None, description="Дата последнего сканирования")
    subscribers: Optional[int] = Field(default=None, ge=0, description="Количество подписчиков")
    avg_reach: Optional[int] = Field(default=None, ge=0, description="Средний охват")
    engagement: Optional[float] = Field(default=None, ge=0.0, le=100.0, description="Вовлечённость (%)")
    citation_index: Optional[float] = Field(default=None, ge=0.0, description="Индекс цитирования")
    content_quality: Optional[float] = Field(default=None, ge=0.0, le=10.0, description="Качество контента (0-10)")
    fraud_signs: Optional[List[str]] = Field(default=None, description="Признаки накрутки")


# ===== 2.1.11. MiningCycleLog (Лог цикла сбора) =====

class MiningCycleLog(BaseModel):
    """Запись в логе цикла сбора (tg_mining_log)."""
    model_config = ConfigDict(extra="forbid")
    
    trace_id: str = Field(description="Уникальный ID цикла")
    started_at: datetime = Field(description="Время начала цикла")
    finished_at: Optional[datetime] = Field(default=None, description="Время завершения цикла")
    duration_seconds: Optional[float] = Field(default=None, ge=0, description="Длительность цикла в секундах")
    messages_collected: int = Field(ge=0, description="Всего собрано сообщений")
    messages_after_harvester: int = Field(ge=0, description="Сообщений после фильтра Harvester")
    signals_approved: int = Field(ge=0, description="Сигналов одобрено AI")
    errors: Optional            