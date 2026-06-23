"""
KRAKEN Harvester — Очистка, дедупликация и фильтрация сообщений.

Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.4 harvester.py
Версия: v5.3.0 (SRE 5.0 CANON COMPATIBLE — МАТРИЦА v1.2)
Дата/Время стабилизации: 2026-05-30 16:00:00 UTC
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
        max_age_hours: int = 72  # Окно сбора до 3 суток
    ):
        self.max_cache_size = max_cache_size
        self.min_length = min_length
        self.max_length = max_length
        self.max_age_hours = max_age_hours
        
        # СИНХРОНИЗАЦИЯ ПУТИ: Строго в примонтированный том /opt/kraken/logs
        self.cache_file = Path("/opt/kraken/logs/processed_hashes.uid")
        
        # Загружаем существующие хэши с диска
        self.processed_hashes, temp_queue = self._load_cache_from_disk()
        self.hash_queue: deque = deque(temp_queue, maxlen=max_cache_size)
        
        self.sanitizer = RegexSanitizer()
        sys.stdout.write(f"[{datetime.now().isoformat()}] 🧹 Harvester перманентная память загружена: {len(self.processed_hashes)} хэшей.\n")
        sys.stdout.flush()
    
    # ===== 4.4.6. ДИСКОВЫЙ СЛОЙ AMORTIZATION =====
    
    def _load_cache_from_disk(self) -> Tuple[set, list]:
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
                        
            if len(loaded_list) > self.max_cache_size:
                loaded_list = loaded_list[-self.max_cache_size:]
                loaded_set = set(loaded_list)
                
        except Exception as e:
            sys.stdout.write(f"[{datetime.now().isoformat()}] ⚠️ Ошибка загрузки кэша дедупликатора: {e}\n")
            sys.stdout.flush()
            
        return loaded_set, loaded_list

    def _sync_cache_to_disk(self):
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                for h_val in self.hash_queue:
                    f.write(f"{h_val}\n")
        except Exception as e:
            sys.stdout.write(f"[{datetime.now().isoformat()}] 💥 Критическая ошибка синхронизации кэша Harvester: {e}\n")
            sys.stdout.flush()

    # ===== 4.4.1. ДЕДУПЛИКАЦИЯ =====
    
    def _compute_hash(self, channel_id: str, message_id: int, content: str) -> str:
        raw = f"{channel_id}:{message_id}:{content}"
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()
    
    def mark_as_processed(self, h: str):
        """Фиксирует хэш в кэше только ПОСЛЕ успешного прохождения всех фильтров."""
        if h in self.processed_hashes:
            return
            
        if len(self.processed_hashes) >= self.max_cache_size:
            oldest = self.hash_queue.popleft()
            self.processed_hashes.discard(oldest)
        
        self.processed_hashes.add(h)
        self.hash_queue.append(h)
        
        try:
            with open(self.cache_file, "a", encoding="utf-8") as f:
                f.write(f"{h}\n")
        except Exception as e:
            sys.stdout.write(f"[{datetime.now().isoformat()}] ⚠️ Ошибка атомарной дозаписи хэша: {e}\n")
            sys.stdout.flush()
    
    # ===== 4.4.3. СТРОГИЕ СЛУЖЕБНЫЕ ФИЛЬТРЫ =====
    
    def filter_by_length(self, text: str) -> Tuple[bool, Optional[RejectReason]]:
        if len(text) < self.min_length:
            return (False, RejectReason.SHORT_TEXT)
        if len(text) > self.max_length:
            return (False, RejectReason.SHORT_TEXT)  # Маппинг на базовый лимит размера
        return (True, None)
    
    def filter_by_date(self, msg_date: datetime) -> Tuple[bool, Optional[RejectReason]]:
        now = datetime.now()
        if msg_date.tzinfo:
            msg_date = msg_date.replace(tzinfo=None)
        if msg_date < now - timedelta(hours=self.max_age_hours):
            return (False, RejectReason.NO_DATA)  # Дроп по сроку давности (Устарело)
        return (True, None)
    
    # ===== 4.4.5. ПОЛНЫЙ ЦИКЛ ОБРАБОТКИ =====
    
    async def process(self, messages: List[RawMessageWithTrace]) -> List[SanitizedMessage]:
        result: List[SanitizedMessage] = []
        initial_cache_size = len(self.processed_hashes)
        
        for msg in messages:
            h = self._compute_hash(msg.channel_id, msg.message_id, msg.content)
            
            # 1. Быстрая проверка на дубликат (без записи в базу!)
            if h in self.processed_hashes:
                continue
            
            # 2. Regex-очистка
            cleaned = self.sanitizer.clean(msg.content)
            
            # 3. Фильтр длины
            ok, reason = self.filter_by_length(cleaned)
            if not ok:
                continue
            
            # 4. Фильтр даты (пропускает сообщения моложе 72 часов)
            ok, reason = self.filter_by_date(msg.date)
            if not ok:
                continue
            
            # Валидация пройдена! Только теперь фиксируем хэш перманентно
            self.mark_as_processed(h)
            
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
                content_hash=h,
                cleaned_content=cleaned,
                is_rejected=False,
                reject_reason=None
            )
            result.append(sanitized)
        
        if len(self.processed_hashes) > initial_cache_size:
            self._sync_cache_to_disk()
            
        # СИНТАКСИЧЕСКИЙ ФИКС ЛОГГЕРА: Вырезали Шуриков опечаточный артефакт
        sys.stdout.write(f"[{datetime.now().isoformat()}] 🧹 Harvester: {len(messages)} in → {len(result)} out (dedup={len(messages) - len(result)})\n")
        sys.stdout.flush()
        
        return result