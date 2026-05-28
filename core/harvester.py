"""
KRAKEN Harvester — Очистка, дедупликация и фильтрация сообщений.

Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.4 harvester.py
Версия: v5.2.3 (GOLDEN ASSEMBLY)

Принципы:
- Постоянный дисковый FIFO-кэш хешей (сохраняется при перезапусках Docker)
- SHA-256 дедупликация (FIFO-ограничение на 10 000 хешей)
- Regex-очистка: HTML, ссылки, лишние пробелы
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
    """Очищает текст сообщения от мусора."""
    
    RE_HTML = re.compile(r'<[^>]+>')
    RE_LINKS = re.compile(r'https?://\S+')
    RE_EMOJI = re.compile(
        r'[\U00010000-\U0010FFFF]'
        r'|[\u2600-\u27BF]'
        r'|[\uE000-\uF8FF]'
        r'|[\u2011-\u26FF]'
        r'|[\uFE0E-\uFE0F]'
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
        if not text:
            return ""
        
        if remove_html:
            text = cls.RE_HTML.sub('', text)
        if remove_links:
            text = cls.RE_LINKS.sub('', text)
        if remove_emoji:
            text = cls.RE_EMOJI.sub('', text)
            
        text = cls.RE_SPACES.sub(' ', text).strip()
        return text


class Harvester:
    """
    Очищает и фильтрует сообщения перед отправкой в AI.
    Хранит базу хэшей на диске хоста для сквозной дедупликации.
    """
    
    def __init__(
        self,
        max_cache_size: int = 10000,
        min_length: int = 20,
        max_length: int = 4000,
        max_age_hours: int = 24
    ):
        self.max_cache_size = max_cache_size
        self.min_length = min_length
        self.max_length = max_length
        self.max_age_hours = max_age_hours
        
        # SRE Канон: Файловый кэш в примонтированном Docker-томе логов
        self.cache_file = Path("/app/logs/processed_hashes.uid")
        
        # Загружаем существующие хэши с диска
        self.processed_hashes, temp_queue = self._load_cache_from_disk()
        self.hash_queue: deque = deque(temp_queue, maxlen=max_cache_size)
        
        self.sanitizer = RegexSanitizer()
        print(f"🧹 Harvester перманентная память загружена: {len(self.processed_hashes)} хэшей.")
    
    # ===== 4.4.6. ДИСКОВЫЙ СЛОЙ AMORTIZATION =====
    
    def _load_cache_from_disk(self) -> Tuple[set, list]:
        """Загружает хэши из файла при старте контейнера."""
        loaded_set = set()
        loaded_list = []
        try:
            if not self.cache_file.exists():
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                self.cache_file.touch()
                return loaded_set, loaded_list
            
            with open(self.cache_file, "r", encoding="utf-8") as f:
                for line in f:
                    h_val = line.strip()
                    if h_val and h_val not in loaded_set:
                        loaded_set.add(h_val)
                        loaded_list.append(h_val)
                        
            # Если лог на диске разросся больше лимита, берем последние max_cache_size
            if len(loaded_list) > self.max_cache_size:
                loaded_list = loaded_list[-self.max_cache_size:]
                loaded_set = set(loaded_list)
                
        except Exception as e:
            print(f"⚠️ Ошибка загрузки кэша дедупликатора: {e}")
            
        return loaded_set, loaded_list

    def _sync_cache_to_disk(self):
        """Полностью перезаписывает кэш-файл по принципу FIFO."""
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                for h_val in self.hash_queue:
                    f.write(f"{h_val}\n")
        except Exception as e:
            print(f"💥 Критическая ошибка синхронизации кэша Harvester: {e}")

    # ===== 4.4.1. ДЕДУПЛИКАЦИЯ =====
    
    def _compute_hash(
        self,
        channel_id: str,
        message_id: int,
        content: str
    ) -> str:
        """Вычисляет сквозной SHA-256 хеш сообщения."""
        raw = f"{channel_id}:{message_id}:{content}"
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()
    
    def is_duplicate(
        self,
        channel_id: str,
        message_id: int,
        content: str
    ) -> bool:
        """Проверяет дубликаты с автоматическим сбросом на диск."""
        h = self._compute_hash(channel_id, message_id, content)
        
        if h in self.processed_hashes:
            return True
        
        # FIFO вытеснение
        if len(self.processed_hashes) >= self.max_cache_size:
            oldest = self.hash_queue.popleft()
            self.processed_hashes.discard(oldest)
        
        self.processed_hashes.add(h)
        self.hash_queue.append(h)
        
        # Потоковая запись нового хэша в лог
        try:
            with open(self.cache_file, "a", encoding="utf-8") as f:
                f.write(f"{h}\n")
        except Exception as e:
            print(f"⚠️ Ошибка атомарной дозаписи хэша: {e}")
            
        return False
    
    # ===== 4.4.3. ФИЛЬТРЫ =====
    
    def filter_by_length(self, text: str) -> Tuple[bool, Optional[str]]:
        if len(text) < self.min_length:
            return (False, "TOO_SHORT")
        if len(text) > self.max_length:
            return (False, "TOO_LONG")
        return (True, None)
    
    def filter_by_date(self, msg_date: datetime) -> Tuple[bool, Optional[str]]:
        # Канон: Избавляемся от привязки к локальной таймзоне, сравниваем наивно
        now = datetime.now()
        if msg_date.tzinfo:
            msg_date = msg_date.replace(tzinfo=None)
        if msg_date < now - timedelta(hours=self.max_age_hours):
            return (False, "OLD_MESSAGE")
        return (True, None)
    
    # ===== 4.4.5. ПОЛНЫЙ ЦИКЛ ОБРАБОТКИ =====
    
    async def process(
        self,
        messages: List[RawMessageWithTrace]
    ) -> List[SanitizedMessage]:
        result: List[SanitizedMessage] = []
        initial_cache_size = len(self.processed_hashes)
        
        for msg in messages:
            # 1. Дедупликация (теперь сквозная и перманентная!)
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
            
            # Валидация пройдена
            sanitized = SanitizedMessage(
                message_id=msg.message_id,
                channel_id=msg.channel_id,
                channel_name=msg.channel_name,
                content=msg.content,
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
        
        # Если база хэшей приросла — раз в цикл делаем полную FIFO-синхронизацию файла
        if len(self.processed_hashes) > initial_cache_size:
            self._sync_cache_to_disk()
            
        print(f"🧹 Harvester: {len(messages)} in → {len(result)} out "
              f"(dedup={len(messages) - len(result)})")
        
        return result