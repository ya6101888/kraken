"""
KRAKEN Harvester — Очистка, дедупликация и фильтрация сообщений.

Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.4 harvester.py
Версия: v5.2.3

Принципы:
- SHA-256 дедупликация (FIFO-кэш на 10 000 хешей)
- Regex-очистка: HTML, ссылки, эмодзи (опционально)
- Фильтр длины: 20-4000 символов
- Фильтр даты: старше 24 часов → OLD_MESSAGE
- На выходе: List[SanitizedMessage]
"""

import hashlib
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from collections import deque

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.signal import (
    RawMessageWithTrace,
    SanitizedMessage,
    RejectReason
)


class RegexSanitizer:
    """
    Очищает текст сообщения от мусора.
    
    Удаляет:
    - HTML-теги: <div>, <br>, <a href="..."> и т.д.
    - Ссылки: https://..., http://...
    - Эмодзи (опционально)
    - Лишние пробелы и переносы строк
    """
    
    # Скомпилированные регулярки (для скорости)
    RE_HTML = re.compile(r'<[^>]+>')
    RE_LINKS = re.compile(r'https?://\S+')
    RE_EMOJI = re.compile(
        r'[\U00010000-\U0010FFFF]'  # Supplementary Multilingual Plane
        r'|[\u2600-\u27BF]'          # Разные символы
        r'|[\uE000-\uF8FF]'          # Private Use Area
        r'|[\u2011-\u26FF]'          # Разные символы
        r'|[\uFE0E-\uFE0F]'         # Variation Selectors
    , re.UNICODE)
    RE_SPACES = re.compile(r'\s+')
    
    @classmethod
    def clean(
        cls,
        text: str,
        remove_links: bool = True,
        remove_html: bool = True,
        remove_emoji: bool = False
    ) -> str:
        """
        Очищает текст сообщения.
        
        Args:
            text: Исходный текст
            remove_links: Удалять ли ссылки
            remove_html: Удалять ли HTML-теги
            remove_emoji: Удалять ли эмодзи
        
        Returns:
            Очищенный текст.
        """
        if not text:
            return ""
        
        # Удаление HTML-тегов
        if remove_html:
            text = cls.RE_HTML.sub('', text)
        
        # Удаление ссылок
        if remove_links:
            text = cls.RE_LINKS.sub('', text)
        
        # Удаление эмодзи (опционально)
        if remove_emoji:
            text = cls.RE_EMOJI.sub('', text)
        
        # Схлопывание пробелов и обрезка краёв
        text = cls.RE_SPACES.sub(' ', text).strip()
        
        return text


class Harvester:
    """
    Очищает и фильтрует сообщения перед отправкой в AI.
    
    Этапы обработки:
    1. Дедупликация (SHA-256)
    2. Regex-очистка (HTML, ссылки, эмодзи)
    3. Фильтр длины (20-4000 символов)
    4. Фильтр даты (не старше 24 часов)
    """
    
    def __init__(
        self,
        max_cache_size: int = 10000,
        min_length: int = 20,
        max_length: int = 4000,
        max_age_hours: int = 24
    ):
        """
        Args:
            max_cache_size: Размер кэша хешей для дедупликации
            min_length: Минимальная длина сообщения
            max_length: Максимальная длина сообщения
            max_age_hours: Максимальный возраст сообщения в часах
        """
        self.max_cache_size = max_cache_size
        self.min_length = min_length
        self.max_length = max_length
        self.max_age_hours = max_age_hours
        
        # Дедупликация: set для быстрого поиска + deque для FIFO
        self.processed_hashes: set = set()
        self.hash_queue: deque = deque(maxlen=max_cache_size)
        
        self.sanitizer = RegexSanitizer()
    
    # ===== 4.4.1. ДЕДУПЛИКАЦИЯ =====
    
    def _compute_hash(
        self,
        channel_id: str,
        message_id: int,
        content: str
    ) -> str:
        """
        Вычисляет SHA-256 хеш сообщения.
        
        Хеш строится из: channel_id + message_id + content
        Это гарантирует уникальность даже при одинаковом контенте
        из разных каналов.
        """
        raw = f"{channel_id}:{message_id}:{content}"
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()
    
    def is_duplicate(
        self,
        channel_id: str,
        message_id: int,
        content: str
    ) -> bool:
        """
        Проверяет, было ли такое сообщение уже обработано.
        
        Использует FIFO-кэш: при переполнении старейший хеш удаляется.
        """
        h = self._compute_hash(channel_id, message_id, content)
        
        if h in self.processed_hashes:
            return True
        
        # FIFO: удаляем старейший при переполнении
        if len(self.processed_hashes) >= self.max_cache_size:
            oldest = self.hash_queue.popleft()
            self.processed_hashes.discard(oldest)
        
        self.processed_hashes.add(h)
        self.hash_queue.append(h)
        return False
    
    # ===== 4.4.3. ФИЛЬТР ДЛИНЫ =====
    
    def filter_by_length(self, text: str) -> Tuple[bool, Optional[str]]:
        """
        Проверяет длину сообщения.
        
        Returns:
            (True, None) если прошло проверку
            (False, "TOO_SHORT") если меньше 20 символов
            (False, "TOO_LONG") если больше 4000 символов
        """
        if len(text) < self.min_length:
            return (False, "TOO_SHORT")
        if len(text) > self.max_length:
            return (False, "TOO_LONG")
        return (True, None)
    
    # ===== 4.4.4. ФИЛЬТР ДАТЫ =====
    
    def filter_by_date(self, msg_date: datetime) -> Tuple[bool, Optional[str]]:
        """
        Проверяет, не старше ли сообщение 24 часов.
        
        Returns:
            (True, None) если свежее
            (False, "OLD_MESSAGE") если старше 24 часов
        """
        if msg_date < datetime.now() - timedelta(hours=self.max_age_hours):
            return (False, "OLD_MESSAGE")
        return (True, None)
    
    # ===== 4.4.5. ПОЛНЫЙ ЦИКЛ ОБРАБОТКИ =====
    
    async def process(
        self,
        messages: List[RawMessageWithTrace]
    ) -> List[SanitizedMessage]:
        """
        Пропускает сообщения через все фильтры.
        
        Порядок обработки:
        1. Дедупликация (SHA-256)
        2. Regex-очистка
        3. Фильтр длины
        4. Фильтр даты
        
        Args:
            messages: Сырые сообщения с trace_id
        
        Returns:
            Очищенные сообщения, прошедшие все фильтры.
        """
        result: List[SanitizedMessage] = []
        
        for msg in messages:
            # 1. Дедупликация
            if self.is_duplicate(msg.channel_id, msg.message_id, msg.content):
                continue
            
            # 2. Regex-очистка
            cleaned = self.sanitizer.clean(msg.content)
            
            # 3. Фильтр длины
            ok, reason = self.filter_by_length(cleaned)
            if not ok:
                continue
            
            # 4. Фильтр даты
            ok, reason = self.filter_by_date(msg.date)
            if not ok:
                continue
            
            # Все проверки пройдены
            sanitized = SanitizedMessage(
                message_id=msg.message_id,
                channel_id=msg.channel_id,
                channel_name=msg.channel_name,
                content=msg.content,  # Оригинал сохраняем
                date=msg.date,
                from_id=msg.from_id,
                views=msg.views,
                trace_id=msg.trace_id,
                collected_at=msg.collected_at,
                content_hash=self._compute_hash(
                    msg.channel_id, msg.message_id, msg.content
                ),
                cleaned_content=cleaned,
                is_rejected=False,
                reject_reason=None
            )
            result.append(sanitized)
        
        print(f"🧹 Harvester: {len(messages)} in → {len(result)} out "
              f"(dedup={len(messages) - len(result)})")
        
        return result