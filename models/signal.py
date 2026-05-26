"""
KRAKEN Data Contracts — Модели данных для всех этапов обработки сигналов.

Фаза 2: DATA CONTRACTS
Версия: v5.2.3
"""

# ===== 2.1.1. ИМПОРТЫ =====
from datetime import datetime
from typing import Optional, Literal, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator
import re


# ===== 2.1.2. LITERAL-ТИПЫ (Классификаторы) =====

# Статусы сигнала (5 значений)
SignalStatus = Literal[
    "PENDING",    # Ожидает обработки
    "APPROVED",   # Прошёл AI-фильтр
    "REJECTED",   # Отклонён AI
    "DUPLICATE",  # Дубль (хеш уже есть)
    "ERROR"       # Ошибка обработки
]

# Причины отклонения (7 значений)
RejectReason = Literal[
    "IRRELEVANT_LOCATION",  # Не Ростов и не Мариуполь
    "SPAM",                 # Реклама/спам
    "TOO_SHORT",            # Меньше 20 символов
    "TOO_LONG",             # Больше 4000 символов
    "NO_MEANING",           # Нет смысловой нагрузки
    "OLD_MESSAGE",          # Старше 24 часов
    "AI_ERROR"              # GPT не ответил или таймаут
]

# Сегменты рынка (5 значений)
MarketSegment = Literal[
    "PRIMARY",    # Первичный рынок (новостройки)
    "SECONDARY",  # Вторичный рынок
    "RENT",       # Аренда
    "INVEST",     # Инвестиции
    "PRO"         # Профессиональный B2B
]

# Географический фокус (4 значения)
GeoFocus = Literal[
    "ROSTOV_CITY",         # Ростов-на-Дону
    "ROSTOV_REGION",       # Ростовская область
    "SOUTHERN_DISTRICT",   # Южный федеральный округ
    "FEDERAL"              # Федеральный
]

# Тип источника канала (5 значений)
ChannelSourceType = Literal[
    "ANALYTIC",   # Аналитика
    "DEVELOPER",  # Застройщик
    "NEWS",       # Новости/Агрегатор
    "AGENCY",     # Агентство недвижимости
    "PRIVATE"     # Частные объявления
]

# Статус канала (3 значения)
ChannelStatus = Literal[
    "ACTIVE",    # Активен
    "TESTING",   # Тестируется
    "BANNED"     # Забанен
]

# Уровень канала (5 значений, 1-5)
ChannelTier = Literal[1, 2, 3, 4, 5]
# TIER_1 = Экспертный (сканируется каждые 15 мин)
# TIER_2 = Профессиональный (каждые 30 мин)
# TIER_3 = Новостной (каждый час)
# TIER_4 = Частные объявления (каждые 2 часа)
# TIER_5 = Спам (исключён из сбора)

# Серьёзность алерта (5 значений)
AlertSeverity = Literal[
    "FATAL",      # 💀 Требуется ручное вмешательство
    "CRITICAL",   # 🔴 Система не работает
    "WARNING",    # 🟡 Нужно внимание
    "INFO",       # 🔵 Информационное сообщение
    "RECOVERED"   # 🟢 Всё восстановлено
]

# Решение AI (4 значения)
AIDecision = Literal[
    "RELEVANT",    # Сообщение релевантно
    "IRRELEVANT",  # Не релевантно
    "UNCERTAIN",   # Низкая уверенность
    "ERROR"        # Ошибка классификации
]

# Статус чтения заводом (3 значения)
WF06ReadStatus = Literal[
    "NOT_USED",   # Ещё не прочитан
    "USED",       # Прочитан WF06
    "ARCHIVED"    # В холодном хранилище
]

# Среда выполнения (4 значения)
Environment = Literal[
    "DEV",         # Разработка
    "STAGING",     # Тестирование
    "PRODUCTION",  # Боевая среда
    "DISASTER"     # Аварийное восстановление
]

# Статус поля объекта (2 значения)
ObjectFieldStatus = Literal[
    "PRESENT",   # Поле присутствует
    "MISSING"    # Поле отсутствует (null)
]

# Источник сигнала (4 значения)
SignalSource = Literal[
    "TELEGRAM_CHANNEL",     # Публичный канал
    "TELEGRAM_SUPERGROUP",  # Супергруппа
    "TELEGRAM_CHAT",        # Обычный чат
    "TELEGRAM_BOT"          # Бот (forward)
]

# ===== 2.1.3. ObjectData (Параметры объекта недвижимости) =====

class ObjectData(BaseModel):
    """
    Параметры объекта недвижимости, извлечённые AI из текста сообщения.
    
    ВСЕ ПОЛЯ ОПЦИОНАЛЬНЫЕ — если AI не нашёл параметр, он остаётся None.
    Это лучше, чем галлюцинация (выдуманные данные).
    """
    model_config = ConfigDict(extra="forbid")
    
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
    """
    Сырое сообщение, полученное от Telegram API.
    Это ВХОДНАЯ точка данных — всё, что приходит из канала.
    """
    model_config = ConfigDict(extra="forbid")
    
    message_id: int = Field(
        ge=1,
        description="Уникальный ID сообщения в канале"
    )
    channel_id: str = Field(
        min_length=1,
        max_length=100,
        description="ID канала (например, '@forumrostov')"
    )
    channel_name: str = Field(
        min_length=1,
        max_length=200,
        description="Название канала"
    )
    content: str = Field(
        max_length=10000,
        description="Текст сообщения (макс 10 000 символов)"
    )
    date: datetime = Field(
        description="Дата и время публикации (UTC)"
    )
    from_id: Optional[int] = Field(
        default=None,
        description="ID отправителя (если доступен)"
    )
    views: Optional[int] = Field(
        default=None,
        ge=0,
        description="Количество просмотров"
    )

    # ===== 2.1.5. RawMessageWithTrace (Сообщение с trace_id) =====

class RawMessageWithTrace(RawTelegramMessage):
    """
    RawTelegramMessage + идентификатор цикла сбора (trace_id).
    
    Наследование означает: все поля RawTelegramMessage + эти два новых.
    """
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
    """
    Сообщение после Harvester-обработки: 
    - удалены ссылки и HTML
    - посчитан SHA-256 хеш
    - проставлен статус отклонения (если не прошло фильтр)
    """
    content_hash: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="SHA-256 хеш очищенного контента (64 hex-символа)"
    )
    cleaned_content: str = Field(
        max_length=4000,
        description="Очищенный текст (без ссылок, HTML, эмодзи)"
    )
    is_rejected: bool = Field(
        default=False,
        description="Отклонено ли сообщение фильтром"
    )
    reject_reason: Optional[RejectReason] = Field(
        default=None,
        description="Причина отклонения (если is_rejected=True)"
    )


# ===== 2.1.7. ClassificationResult + BatchAIResponse =====

class ClassificationResult(BaseModel):
    """
    Результат AI-классификации ОДНОГО сообщения.
    
    GPT получает на вход до 20 сообщений и возвращает массив таких объектов.
    """
    model_config = ConfigDict(extra="forbid")
    
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
    """
    Ответ GPT на батч сообщений (до 20 штук).
    
    Содержит список ClassificationResult — по одному на каждое сообщение.
    """
    model_config = ConfigDict(extra="forbid")
    
    results: List[ClassificationResult] = Field(
        min_length=1,
        max_length=20,
        description="Результаты классификации (1-20 сообщений)"
    )

    @field_validator("results")
    @classmethod
    def validate_message_indices(cls, v: List[ClassificationResult]) -> List[ClassificationResult]:
        """Проверка: индексы должны идти по порядку 0, 1, 2..."""
        indices = [r.message_index for r in v]
        expected = list(range(len(v)))
        if indices != expected:
            raise ValueError(
                f"Индексы должны идти по порядку 0..{len(v)-1}, "
                f"получено: {indices}"
            )
        return v

# ===== 2.1.8. ApprovedSignal (Одобренный сигнал) =====

class ApprovedSignal(SanitizedMessage):
    """
    Сообщение, прошедшее AI-фильтр.
    
    Наследует ВСЕ поля SanitizedMessage (включая RawMessageWithTrace и RawTelegramMessage)
    и добавляет поля, специфичные для одобренного сигнала.
    """
    relevance_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Итоговая оценка релевантности от AI"
    )
    is_approved: bool = Field(
        default=True,
        description="Флаг одобрения (всегда True для этого класса)"
    )
    approved_at: datetime = Field(
        default_factory=datetime.now,
        description="Когда сигнал был одобрен"
    )
    signal_id: str = Field(
        default="",
        description="Уникальный ID сигнала (автогенерация, если пусто)"
    )
    market_segment: Optional[MarketSegment] = Field(
        default=None,
        description="Определённый AI сегмент рынка"
    )
    geo_focus: Optional[GeoFocus] = Field(
        default=None,
        description="Определённый AI гео-фокус"
    )
    object_data: ObjectData = Field(
        default_factory=ObjectData,
        description="Извлечённые параметры объекта недвижимости"
    )

    @field_validator("signal_id")
    @classmethod
    def validate_signal_id_format(cls, v: str) -> str:
        """Проверяет формат signal_id, если он уже задан."""
        if v and not re.match(r"^SIG_[a-f0-9]{8}_\d+$", v):
            raise ValueError(f"Неверный формат signal_id: {v}. Ожидается SIG_xxxxxxxx_N")
        return v
    
    def model_post_init(self, __context):
        """Автоматически генерирует signal_id после инициализации, если он пуст."""
        if not self.signal_id:
            short_trace = self.trace_id.split("_")[-1][:8] if "_" in self.trace_id else self.trace_id[:8]
            object.__setattr__(self, "signal_id", f"SIG_{short_trace}_{self.message_id}")

# ===== 2.1.9. GoogleSheetsRow (Строка для записи в таблицу) =====

class GoogleSheetsRow(BaseModel):
    """
    Плоская модель для записи в Google Sheets.
    
    В отличие от ApprovedSignal, здесь нет вложенных объектов —
    все данные развёрнуты в одну строку (как колонки в таблице).
    """
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
        """
        Фабричный метод: создаёт GoogleSheetsRow из ApprovedSignal.
        
        Разворачивает вложенный ObjectData в плоские поля.
        """
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
    """
    Один канал в реестре tg_channels.
    """
    model_config = ConfigDict(extra="forbid")
    
    channel_id: str = Field(
        min_length=1,
        max_length=100,
        description="ID канала (например, '@forumrostov')"
    )
    title: str = Field(
        min_length=1,
        max_length=200,
        description="Название канала"
    )
    source_type: ChannelSourceType = Field(
        description="Тип источника"
    )
    tier: ChannelTier = Field(
        description="Уровень канала (1-5)"
    )
    geo_focus: GeoFocus = Field(
        description="Географический фокус"
    )
    status: ChannelStatus = Field(
        default="ACTIVE",
        description="Статус канала"
    )
    last_scan: Optional[datetime] = Field(
        default=None,
        description="Дата последнего сканирования"
    )
    subscribers: Optional[int] = Field(
        default=None,
        ge=0,
        description="Количество подписчиков"
    )
    avg_reach: Optional[int] = Field(
        default=None,
        ge=0,
        description="Средний охват"
    )
    engagement: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Вовлечённость (%)"
    )
    citation_index: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Индекс цитирования"
    )
    content_quality: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Качество контента (0-10)"
    )
    fraud_signs: Optional[List[str]] = Field(
        default=None,
        description="Признаки накрутки"
    )

# ===== 2.1.11. MiningCycleLog (Лог цикла сбора) =====

class MiningCycleLog(BaseModel):
    """
    Запись в логе цикла сбора (tg_mining_log).
    
    Создаётся после каждого завершённого цикла и содержит статистику.
    """
    model_config = ConfigDict(extra="forbid")
    
    trace_id: str = Field(description="Уникальный ID цикла")
    started_at: datetime = Field(description="Время начала цикла")
    finished_at: Optional[datetime] = Field(
        default=None,
        description="Время завершения цикла"
    )
    duration_seconds: Optional[float] = Field(
        default=None,
        ge=0,
        description="Длительность цикла в секундах"
    )
    messages_collected: int = Field(
        ge=0,
        description="Всего собрано сообщений"
    )
    messages_after_harvester: int = Field(
        ge=0,
        description="Сообщений после фильтра Harvester"
    )
    signals_approved: int = Field(
        ge=0,
        description="Сигналов одобрено AI"
    )
    errors: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Список ошибок (тип, описание, trace_id)"
    )
    floodwait_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description="Секунд ожидания FloodWait (если был)"
    )

# ===== 2.1.12. DLQEntry (Запись в Dead Letter Queue) =====

class DLQEntry(BaseModel):
    """
    Запись в Dead Letter Queue — сигнал, который не удалось записать в Google Sheets.
    
    Хранится в /app/dlq/failed_writes.json для повторных попыток.
    """
    model_config = ConfigDict(extra="forbid")
    
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="Когда произошёл сбой"
    )
    trace_id: str = Field(description="ID цикла, в котором был сбой")
    signal: ApprovedSignal = Field(description="Сигнал, который не удалось записать")
    error: str = Field(description="Текст ошибки")
    retry_count: int = Field(
        default=0,
        ge=0,
        le=3,
        description="Количество попыток перезаписи (макс 3)"
    )

# ===== 2.1.13. BeaconAlert (Алерт мониторинга) =====

class BeaconAlert(BaseModel):
    """
    Алерт, отправляемый через Beacon-бота.
    
    Используется для мониторинга состояния KRAKEN:
    - ошибки подключения
    - предупреждения о FloodWait
    - статус циклов сбора
    """
    model_config = ConfigDict(extra="forbid")
    
    severity: AlertSeverity = Field(
        description="Серьёзность алерта (FATAL, CRITICAL, WARNING, INFO, RECOVERED)"
    )
    error_type: Optional[str] = Field(
        default=None,
        description="Тип ошибки (AUTH_REVOKED, SOCKET_CLOSED, FLOOD_WAIT...)"
    )
    trace_id: Optional[str] = Field(
        default=None,
        description="ID цикла, в котором произошло событие"
    )
    message: str = Field(
        min_length=1,
        max_length=1000,
        description="Текст алерта"
    )
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="Время создания алерта"
    )
    
# ===== 2.1.16. УТИЛИТЫ НОРМАЛИЗАЦИИ =====

def normalize_price(raw_price: str) -> Optional[int]:
    """
    Извлекает цену в рублях из «грязной» строки.
    
    Примеры:
    - '5,2 млн' -> 5_200_000
    - '500 тыс' -> 500_000
    - '3 500 000' -> 3_500_000
    """
    if not raw_price:
        return None
    
    cleaned = re.sub(r'\s', '', raw_price.lower())
    
    # Обработка миллионов
    if 'млн' in cleaned:
        num = re.search(r'(\d+(?:[.,]\d+)?)', cleaned)
        if num:
            value = float(num.group(1).replace(',', '.'))
            return int(value * 1_000_000)
    
    # Обработка тысяч
    if 'тыс' in cleaned or 'к' in cleaned:
        num = re.search(r'(\d+(?:[.,]\d+)?)', cleaned)
        if num:
            value = float(num.group(1).replace(',', '.'))
            return int(value * 1_000)
    
    # Просто число
    num = re.search(r'(\d+(?:[.,]\d+)?)', cleaned)
    if num:
        return int(float(num.group(1).replace(',', '.')))
    
    return None


def normalize_rooms(raw_rooms: str) -> Optional[int]:
    """
    Извлекает количество комнат из «грязной» строки.
    
    Примеры:
    - 'студия' -> 1
    - 'двушка' -> 2
    - '3-к' -> 3
    - 'четырехкомнатная' -> 4
    """
    if not raw_rooms:
        return None
    
    low = raw_rooms.lower()
    
    # Словарь нестандартных обозначений
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
    
    # Если нет в словаре — ищем просто число
    num = re.search(r'(\d+)', raw_rooms)
    if num:
        return int(num.group(1))
    
    return None                    