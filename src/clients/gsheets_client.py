"""
KRAKEN Google Sheets Client — Запись сигналов в таблицы.

Фаза 3: ИНТЕГРАЦИИ
Модуль: 3.3 gsheets_client.py
Версия: v5.2.3

Принципы:
- Авторизация через service_account.json
- Batch-запись до 100 строк за раз
- Retry: 3 попытки с exponential backoff
- Буфер: до 100 записей в памяти при недоступности
- DLQ: сохранение неудавшихся записей в JSON
- Health-check: тестовая запись при старте
"""

import os
import json
import asyncio
import random
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict
from dotenv import load_dotenv

# Загрузка .env
env_path = Path("/opt/kraken/secrets/.env")
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv(Path(__file__).parent.parent.parent / "secrets" / ".env")

# Импорт моделей
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from models.signal import GoogleSheetsRow, MiningCycleLog, ApprovedSignal


class GoogleSheetsClient:
    """
    Клиент для работы с Google Sheets API.
    
    Использование:
        gs = GoogleSheetsClient()
        channels = gs.load_channels()
        gs.write_signals(signals)
    """
    
    def __init__(self):
        self.spreadsheet_id = os.getenv("GSHEETS_SPREADSHEET_ID", "")
        self.service_account_path = os.getenv(
            "GSHEETS_SERVICE_ACCOUNT_PATH",
            "/app/secrets/service_account.json"
        )
        
        # Корректировка пути для запуска вне Docker
        if self.service_account_path.startswith("/app/") and not Path("/app").exists():
            self.service_account_path = "/opt/kraken" + self.service_account_path[4:]
        
        self.client = None
        self._init_client()
    
    # ===== 3.3.1. АВТОРИЗАЦИЯ =====
    
    def _init_client(self):
        """Авторизуется через сервисный аккаунт Google."""
        try:
            import gspread
            from oauth2client.service_account import ServiceAccountCredentials
            
            scope = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"
            ]
            
            creds = ServiceAccountCredentials.from_json_keyfile_name(
                self.service_account_path,
                scope
            )
            self.client = gspread.authorize(creds)
            print(f"✅ Google Sheets authorized")
            
        except FileNotFoundError:
            print(f"⚠️ Service account file not found: {self.service_account_path}")
            print("   Google Sheets will not be available")
        except Exception as e:
            print(f"❌ Google Sheets auth error: {e}")
    
    @property
    def is_available(self) -> bool:
        """Доступен ли Google Sheets API."""
        return self.client is not None
    
    # ===== 3.3.2. ЗАГРУЗКА КАНАЛОВ =====
    
    def load_channels(self) -> List[Dict]:
        """
        Загружает реестр каналов из листа tg_channels.
        
        Returns:
            Список каналов со статусом ACTIVE.
        """
        if not self.is_available:
            return []
        
        try:
            sheet = self.client.open_by_key(self.spreadsheet_id)
            worksheet = sheet.worksheet("tg_channels")
            rows = worksheet.get_all_records()
            
            active = [row for row in rows if row.get("status") == "ACTIVE"]
            print(f"📡 Loaded {len(active)} active channels (from {len(rows)} total)")
            return active
            
        except Exception as e:
            print(f"❌ Failed to load channels: {e}")
            return []
    
    # ===== 3.3.3. BATCH ЗАПИСЬ СИГНАЛОВ =====
    
    def write_signals(self, signals: List[GoogleSheetsRow]) -> bool:
        """
        Записывает список сигналов в лист tg_signals_approved.
        
        Args:
            signals: Список GoogleSheetsRow для записи.
        
        Returns:
            True если запись успешна.
        """
        if not self.is_available or not signals:
            return False
        
        try:
            sheet = self.client.open_by_key(self.spreadsheet_id)
            worksheet = sheet.worksheet("tg_signals_approved")
            
            rows = []
            for signal in signals:
                row = [
                    signal.signal_id,
                    signal.trace_id,
                    signal.channel_name,
                    signal.message_id,
                    signal.content or "",
                    signal.relevance_score,
                    signal.market_segment or "",
                    signal.geo_focus or "",
                    signal.price or "",
                    signal.rooms or "",
                    signal.area or "",
                    signal.floor or "",
                    signal.address or "",
                    signal.developer or "",
                    signal.completion_date or "",
                    signal.collected_at.isoformat() if signal.collected_at else "",
                    signal.wf06_used_at.isoformat() if signal.wf06_used_at else ""
                ]
                rows.append(row)
            
            worksheet.append_rows(rows, value_input_option="USER_ENTERED")
            print(f"📊 Written {len(signals)} signals to Google Sheets")
            return True
            
        except Exception as e:
            print(f"❌ Write failed: {e}")
            return False
    
    # ===== 3.3.4. ЗАПИСЬ ЛОГА =====
    
    def write_mining_log(self, log: MiningCycleLog) -> bool:
        """
        Записывает лог цикла сбора в лист tg_mining_log.
        """
        if not self.is_available:
            return False
        
        try:
            sheet = self.client.open_by_key(self.spreadsheet_id)
            worksheet = sheet.worksheet("tg_mining_log")
            
            errors_json = json.dumps(log.errors, ensure_ascii=False) if log.errors else ""
            
            row = [
                log.trace_id,
                log.started_at.isoformat(),
                log.finished_at.isoformat() if log.finished_at else "",
                str(log.duration_seconds) if log.duration_seconds else "",
                log.messages_collected,
                log.messages_after_harvester,
                log.signals_approved,
                errors_json,
                str(log.floodwait_seconds) if log.floodwait_seconds else ""
            ]
            
            worksheet.append_row(row, value_input_option="USER_ENTERED")
            print(f"📝 Mining log written: {log.trace_id}")
            return True
            
        except Exception as e:
            print(f"❌ Mining log write failed: {e}")
            return False
    
    # ===== 3.3.5. RETRY С EXPONENTIAL BACKOFF =====
    
    async def write_with_retry(self, signals: List[GoogleSheetsRow]) -> bool:
        """
        Пытается записать сигналы с повторными попытками.
        
        Retry: 3 попытки с exponential backoff: 1с, 2с, 4с + jitter.
        """
        if not signals:
            return True
        
        max_retries = int(os.getenv("GSHEETS_RETRY_ATTEMPTS", "3"))
        
        for attempt in range(max_retries):
            try:
                result = self.write_signals(signals)
                if result:
                    return True
            except Exception as e:
                pass
            
            if attempt < max_retries - 1:
                delay = (2 ** attempt) + random.uniform(0, 1)
                print(f"⏳ Retry {attempt + 2}/{max_retries} in {delay:.1f}s")
                await asyncio.sleep(delay)
        
        print(f"❌ All {max_retries} retries failed")
        return False
    
    # ===== 3.3.8. HEALTH CHECK =====
    
    def health_check(self) -> bool:
        """
        Проверяет доступность Google Sheets тестовой записью.
        """
        if not self.is_available:
            return False
        
        try:
            sheet = self.client.open_by_key(self.spreadsheet_id)
            worksheet = sheet.worksheet("tg_mining_log")
            
            test_row = [
                "HEALTH_CHECK",
                datetime.now().isoformat(),
                "", "", "", "", "", "", ""
            ]
            worksheet.append_row(test_row, value_input_option="USER_ENTERED")
            print("✅ Google Sheets health check OK")
            return True
            
        except Exception as e:
            print(f"❌ Health check failed: {e}")
            return False


# ===== 3.3.6-3.3.7. БУФЕР И DLQ =====

class BufferedWriter:
    """
    Буфер для накопления сигналов перед записью.
    
    - Хранит до 100 записей в памяти
    - При заполнении — flush в Google Sheets
    - При ошибке — сохраняет в DLQ (failed_writes.json)
    """
    
    def __init__(self, max_size: int = 100):
        self.buffer: List[GoogleSheetsRow] = []
        self.max_size = max_size
        self.dlq_path = Path(os.getenv(
            "GSHEETS_DLQ_PATH",
            "/opt/kraken/dlq/failed_writes.json"
        ))
        
        # Корректировка пути
        if str(self.dlq_path).startswith("/app/") and not Path("/app").exists():
            self.dlq_path = Path("/opt/kraken") / self.dlq_path.relative_to("/app")
    
    async def add(self, signal: GoogleSheetsRow, writer: GoogleSheetsClient):
        """
        Добавляет сигнал в буфер. Если буфер полон — flush.
        """
        self.buffer.append(signal)
        
        if len(self.buffer) >= self.max_size:
            await self.flush(writer)
    
    async def flush(self, writer: GoogleSheetsClient):
        """
        Отправляет накопленные сигналы в Google Sheets.
        При ошибке — сохраняет в DLQ.
        """
        if not self.buffer:
            return
        
        print(f"📤 Flushing {len(self.buffer)} signals...")
        success = await writer.write_with_retry(self.buffer)
        
        if success:
            self.buffer.clear()
            print("✅ Flush successful")
        else:
            await self._save_to_dlq()
            self.buffer.clear()
    
    async def _save_to_dlq(self):
        """
        Сохраняет неудавшиеся записи в Dead Letter Queue.
        """
        entries = []
        if self.dlq_path.exists():
            try:
                entries = json.loads(self.dlq_path.read_text())
            except json.JSONDecodeError:
                pass
        
        for signal in self.buffer:
            entries.append({
                "timestamp": datetime.now().isoformat(),
                "signal_id": signal.signal_id,
                "trace_id": signal.trace_id,
                "channel_name": signal.channel_name,
                "content": signal.content[:200] if signal.content else "",
                "error": "Google Sheets write failed after retries"
            })
        
        self.dlq_path.parent.mkdir(parents=True, exist_ok=True)
        self.dlq_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
        print(f"💀 {len(self.buffer)} signals saved to DLQ: {self.dlq_path}")
    
    async def shutdown(self, writer: GoogleSheetsClient):
        """Принудительный flush при остановке."""
        print(f"🛑 Shutdown: flushing {len(self.buffer)} buffered signals")
        await self.flush(writer)