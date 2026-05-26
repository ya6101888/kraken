"""
KRAKEN Storage Writer — Запись сигналов в Google Sheets.

Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.6 storage_writer.py
Версия: v5.2.3

Принципы:
- Конвертация ApprovedSignal → GoogleSheetsRow
- Буферизация (до 100 записей)
- Retry: 3 попытки с exponential backoff
- DLQ при недоступности Google Sheets
"""

import sys
from pathlib import Path
from typing import List, Optional

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
        """Ленивая инициализация буфера."""
        if self._buffer is None:
            from clients.gsheets_client import BufferedWriter
            self._buffer = BufferedWriter(max_size=100)
        return self._buffer
    
    # ===== 4.6.6. КОНВЕРТАЦИЯ ApprovedSignal → GoogleSheetsRow =====
    
    @staticmethod
    def convert(signal: ApprovedSignal) -> GoogleSheetsRow:
        """
        Преобразует ApprovedSignal в плоскую строку для Google Sheets.
        
        Использует фабричный метод from_approved_signal(),
        определённый в моделях (Фаза 2.1.9).
        """
        return GoogleSheetsRow.from_approved_signal(signal)
    
    # ===== 4.6.3. ЗАПИСЬ С БУФЕРОМ =====
    
    async def write_signals(self, signals: List[ApprovedSignal]) -> bool:
        """
        Записывает сигналы в Google Sheets через буфер.
        
        Каждый сигнал добавляется в буфер. Когда буфер заполняется
        (100 записей) — автоматический flush в таблицу.
        
        Args:
            signals: Список одобренных сигналов
        
        Returns:
            True если все сигналы обработаны (записаны или в буфере).
        """
        if not signals:
            return True
        
        try:
            for signal in signals:
                row = self.convert(signal)
                await self.buffer.add(row, self.gsheets)
            
            self.total_written += len(signals)
            return True
            
        except Exception as e:
            print(f"❌ StorageWriter error: {e}")
            self.total_failed += len(signals)
            return False
    
    # ===== ПРИНУДИТЕЛЬНЫЙ FLUSH =====
    
    async def flush(self) -> bool:
        """
        Принудительно отправляет буфер в Google Sheets.
        
        Вызывается при остановке KRAKEN (Graceful Shutdown).
        """
        if self._buffer:
            await self.buffer.flush(self.gsheets)
            return True
        return False
    
    # ===== СТАТИСТИКА =====
    
    def get_stats(self) -> dict:
        """Возвращает статистику записи."""
        return {
            "total_written": self.total_written,
            "total_failed": self.total_failed,
            "buffer_size": len(self._buffer.buffer) if self._buffer else 0
        }