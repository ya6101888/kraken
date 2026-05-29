"""
KRAKEN Storage Writer — Запись сигналов в Google Sheets.

Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.6 storage_writer.py
Версия: v5.2.3 (GOLDEN MASTER SRE 5.0 Canon)
Дата/Время стабилизации: 2026-05-29 19:30:00 UTC

Принципы:
- Конвертация ApprovedSignal (вложенный v1.2) → GoogleSheetsRow (плоский контракт)
- Ленивая инициализация буфера и клиента
- Потокобезопасный слив буфера при Graceful Shutdown
- Полная обратная совместимость вызовов фабрики
"""

import sys
from pathlib import Path
from typing import List, Optional
from datetime import datetime

# Настройка путей рантайма
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.signal import ApprovedSignal, GoogleSheetsRow


class StorageWriter:
    """
    Записывает одобренные сигналы в Google Sheets.
    
    Использование:
        writer = StorageWriter(gsheets_client)
        await writer.write_signals(approved_signals)
    """
    
    def __init__(self, gsheets_client=None):
        """
        Args:
            gsheets_client: GoogleSheetsClient (будет создан при первом использовании)
        """
        self._gsheets_client = gsheets_client
        self._buffer = None
        self.total_written: int = 0
        self.total_failed: int = 0
    
    @property
    def gsheets(self):
        """Ленивая инициализация GoogleSheetsClient."""
        if self._gsheets_client is None:
            from clients.gsheets_client import GoogleSheetsClient
            self._gsheets_client = GoogleSheetsClient()
        return self._gsheets_client
    
    @property
    def buffer(self):
        """Ленивая инициализация накопительного буфера."""
        if self._buffer is None:
            from clients.gsheets_client import BufferedWriter
            self._buffer = BufferedWriter(max_size=100)
        return self._buffer
    
    # ===== 4.6.6. КАНОНИЧЕСКАЯ КОНВЕРТАЦИЯ (ВЫПРЯМЛЕНИЕ DTO v1.2) =====
    
    @staticmethod
    def convert(signal: ApprovedSignal) -> GoogleSheetsRow:
        """
        Преобразует сложный вложенный ApprovedSignal v1.2 в плоский GoogleSheetsRow
        для корректной вставки в 23 колонки таблицы Google Sheets.
        """
        return GoogleSheetsRow(
            signal_id=signal.signal_id,
            trace_id=signal.trace_id,
            channel_name=signal.channel_name,
            message_id=signal.message_id,
            classification=signal.classification.value if hasattr(signal.classification, 'value') else str(signal.classification),
            segment_confidence=signal.segment_confidence,
            source_type=signal.source.source_type.value if hasattr(signal.source.source_type, 'value') else str(signal.source.source_type),
            source_tier=signal.source.source_tier,
            geo_focus=signal.geo.value if hasattr(signal.geo, 'value') else str(signal.geo),
            priority_score=signal.priority_score,
            object_price=signal.object_data.price,
            object_address=signal.object_data.address,
            object_rooms=signal.object_data.rooms,
            object_area=signal.object_data.area,
            object_floor=signal.object_data.floor,
            object_developer=signal.object_data.developer,
            object_completion_date=signal.object_data.completion_date,
            original_content=signal.original_content,
            cleaned_content=signal.cleaned_content,
            relevance_score=signal.relevance_score,
            collected_at=signal.collected_at,
            is_approved=signal.is_approved,
            wf06_used_at=signal.wf06_used_at,
            
            # Поля совместимости со старыми плоскими схемами
            content=signal.cleaned_content,
            market_segment=signal.classification.value if hasattr(signal.classification, 'value') else str(signal.classification),
            price=signal.object_data.price,
            rooms=signal.object_data.rooms,
            area=signal.object_data.area,
            floor=signal.object_data.floor,
            address=signal.object_data.address,
            developer=signal.object_data.developer,
            completion_date=signal.object_data.completion_date
        )

    @staticmethod
    def from_approved_signal(signal: ApprovedSignal) -> GoogleSheetsRow:
        """Алиас совместимости: пробрасывает вызов старого метода Шурика на каноничный convert."""
        return StorageWriter.convert(signal)
    
    # ===== 4.6.3. ЗАПИСЬ С БУФЕРОМ =====
    
    async def write_signals(self, signals: List[ApprovedSignal]) -> bool:
        """
        Записывает сигналы в Google Sheets через буфер.
        Когда буфер заполняется (100 записей) — автоматический flush.
        """
        if not signals:
            return True
        
        try:
            for signal in signals:
                # Используем алиас обратной совместимости
                row = self.from_approved_signal(signal)
                await self.buffer.add(row, self.gsheets)
            
            self.total_written += len(signals)
            return True
            
        except Exception as e:
            sys.stdout.write(f"[{datetime.now().isoformat()}] ❌ StorageWriter runtime error: {e}\n")
            sys.stdout.flush()
            self.total_failed += len(signals)
            return False
    
    # ===== ПРИНУДИТЕЛЬНЫЙ FLUSH ДЛЯ GRACEFUL SHUTDOWN =====
    
    async def flush(self) -> bool:
        """Принудительно отправляет остатки буфера в Google Sheets при остановке."""
        if self._buffer:
            await self._buffer.flush(self.gsheets)
            return True
        return False
    
    # ===== СТАТИСТИКА =====
    
    def get_stats(self) -> dict:
        """Возвращает текущую метрику заполнения буферного слоя."""
        return {
            "total_written": self.total_written,
            "total_failed": self.total_failed,
            "buffer_size": len(self._buffer.buffer) if self._buffer else 0
        }