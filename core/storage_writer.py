"""
KRAKEN Storage Writer — Запись сигналов в Google Sheets.

Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.6 storage_writer.py
Версия: v5.3.5 (РОТОРНЫЙ ВЫПРЯМИТЕЛЬ — ТОТАЛЬНАЯ СЕПАРАЦИЯ КОЛОНОК)
Дата/Время стабилизации: 2026-05-30 17:25:00 UTC
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
    """
    
    def __init__(self, gsheets_client=None):
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
        Строгая защита от склеивания — замена None на пустые строки.
        """
        if not signals:
            return True
        
        try:
            for signal in signals:
                row = self.from_approved_signal(signal)
                
                # Сборка плоского списка под физические столбцы таблицы (Закон Матрицы v1.2 — 25 колонок)
                cells_array = [
                    str(row.signal_id) if row.signal_id else "",
                    str(row.trace_id) if row.trace_id else "",
                    str(row.channel_id) if row.channel_id else "",
                    str(row.channel_name) if row.channel_name else "",
                    int(row.message_id) if row.message_id else 0,
                    row.market_segment.value if hasattr(row.market_segment, 'value') else str(row.market_segment),
                    float(row.segment_confidence) if row.segment_confidence else 0.0,
                    row.source_type.value if hasattr(row.source_type, 'value') else str(row.source_type),
                    int(row.source_tier) if row.source_tier else 3,
                    row.geo_focus.value if hasattr(row.geo_focus, 'value') else str(row.geo_focus),
                    float(row.priority_score) if row.priority_score else 0.0,
                    int(row.price) if row.price else "",
                    str(row.address) if row.address else "",
                    int(row.rooms) if row.rooms is not None else "",
                    float(row.area) if row.area else "",
                    str(row.floor) if row.floor else "",
                    str(row.developer) if row.developer else "",
                    str(row.completion_date) if row.completion_date else "",
                    str(row.phone_number) if row.phone_number else "",
                    str(row.original_content) if row.original_content else "",
                    str(row.cleaned_content) if row.cleaned_content else "",
                    float(row.relevance_score) if row.relevance_score else 0.0,
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