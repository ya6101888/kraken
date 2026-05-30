"""
KRAKEN Storage Writer — Запись сигналов в Google Sheets.

Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.6 storage_writer.py
Версия: v5.3.1 (КАНАНИЧЕСКИЙ ПЛОСКИЙ ВЫПРЯМИТЕЛЬ — МАТРИЦА v1.2)
Дата/Время стабилизации: 2026-05-30 16:10:00 UTC
"""

import sys
from pathlib import Path
from typing import List
from datetime import datetime

# Настройка путей рантайма
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.signal import ApprovedSignal, GoogleSheetsRow


class StorageWriter:
    """
    Записывает одобренные сигналы в Google Sheets по плоскому контракту ТЗ v1.2.
    
    Использование:
        writer = StorageWriter(gsheets_client)
        await writer.write_signals(approved_signals)
    """
    
    def __init__(self, gsheets_client=None):
        self._gsheets_client = gsheets_client
        self._buffer = None
        self.total_written: int = 0
        self.total_failed: int = 0
    
    @property
    def gsheets(self):
        """Ленивая初始化 GoogleSheetsClient."""
        if self._gsheets_client is None:
            from clients.gsheets_client import GoogleSheetsClient
            self._gsheets_client = GoogleSheetsClient()
        return self._gsheets_client
    
    @property
    def buffer(self):
        """Ленивая инициализация накопительного буфера с ограничением max_size=10."""
        if self._buffer is None:
            from clients.gsheets_client import BufferedWriter
            self._buffer = BufferedWriter(max_size=10) 
        return self._buffer
    
    @staticmethod
    def convert(signal: ApprovedSignal) -> GoogleSheetsRow:
        """Каноническая конвертация: сопряжение типов через валидацию v2.1 Golden Master."""
        return GoogleSheetsRow(**signal.model_dump())
        
    @staticmethod
    def from_approved_signal(signal: ApprovedSignal) -> GoogleSheetsRow:
        """Алиас совместимости для сохранения внешних вызовов ядра."""
        return StorageWriter.convert(signal)
    
    # ===== 4.6.3. ЗАПИСЬ В ТАБЛИЦУ СТРОГО ПО МАТРИЦЕ ТЗ (25 КОЛОНОК) =====
    
    async def write_signals(self, signals: List[ApprovedSignal]) -> bool:
        """
        Парсит сигналы в плоские массивы ячеек и отправляет в буфер.
        Строгое позиционирование от столбца A до Y (25 колонок).
        """
        if not signals:
            return True
        
        try:
            for signal in signals:
                row = self.from_approved_signal(signal)
                
                # Сборка плоского списка под физические столбцы таблицы (Закон Матрицы v1.2 — 25 колонок)
                cells_array = [
                    row.signal_id,
                    row.trace_id,
                    row.channel_id,
                    row.channel_name,
                    row.message_id,
                    row.market_segment.value if hasattr(row.market_segment, 'value') else str(row.market_segment),
                    row.segment_confidence,
                    row.source_type.value if hasattr(row.source_type, 'value') else str(row.source_type),
                    row.source_tier,
                    row.geo_focus.value if hasattr(row.geo_focus, 'value') else str(row.geo_focus),
                    row.priority_score,
                    row.price,
                    row.address,
                    row.rooms,
                    row.area,
                    row.floor,
                    row.developer,
                    row.completion_date,
                    row.phone_number,
                    row.original_content,
                    row.cleaned_content,
                    row.relevance_score,
                    row.collected_at.isoformat() if isinstance(row.collected_at, datetime) else str(row.collected_at),
                    "TRUE",  # is_approved всегда TRUE на этом листе
                    row.wf06_used_at.isoformat() if isinstance(row.wf06_used_at, datetime) else (str(row.wf06_used_at) if row.wf06_used_at else "")
                ]
                
                # Передаем готовый плоский массив строк в BufferedWriter
                await self.buffer.add(cells_array, self.gsheets)
            
            self.total_written += len(signals)
            return True
            
        except Exception as e:
            sys.stdout.write(f"[{datetime.now().isoformat()}] ❌ StorageWriter v1.2 runtime error: {e}\n")
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