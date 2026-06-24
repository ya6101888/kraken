"""
KRAKEN Google Sheets Client — Запись сигналов в таблицы.

Фаза 3: ИНТЕГРАЦИИ
Модуль: 3.3 gsheets_client.py
Версия: v5.4.8 (SRE 5.0 GOOGLE-AUTH MIGRATION — OAUTH2CLIENT REMOVED)
Дата изменения: 2026-06-24 07:15:00 UTC

Принципы:
- Авторизация через google-auth (service_account.json)
- Прокси через переменные окружения HTTP_PROXY/HTTPS_PROXY
- Полный отказ от устаревшего oauth2client
- Совместимость с gspread >= 6.1.0
"""

import os
import json
import asyncio
import random
import sys
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

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.signal import MiningCycleLog

try:
    from core.config import settings
except ImportError:
    settings = None


class GoogleSheetsClient:
    """Клиент для работы с Google Sheets API."""
    
    def __init__(self):
        self.spreadsheet_id = os.getenv("GSHEETS_SPREADSHEET_ID", "")
        self.service_account_path = os.getenv(
            "GSHEETS_SERVICE_ACCOUNT_PATH",
            "/app/secrets/service_account.json"
        )
        if self.service_account_path.startswith("/app/") and not Path("/app").exists():
            self.service_account_path = "/opt/kraken" + self.service_account_path[4:]
        
        self.client = None
        self._init_client()
    
    def _init_client(self):
        """Авторизация через google-auth (современный метод, без oauth2client)."""
        try:
            import gspread
            from google.oauth2.service_account import Credentials

            scopes = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"
            ]
            
            creds = Credentials.from_service_account_file(
                self.service_account_path,
                scopes=scopes
            )
            self.client = gspread.authorize(creds)
            print(f"✅ Google Sheets authorized via google-auth (proxy via environment)")
        except FileNotFoundError:
            print(f"⚠️ Service account file not found: {self.service_account_path}")
        except Exception as e:
            print(f"❌ Google Sheets auth error: {e}")
    
    @property
    def is_available(self) -> bool:
        return self.client is not None
    
    def load_channels(self) -> List[Dict]:
        """Загружает реестр каналов из листа tg_channels."""
        print("🔍 [DIAGNOSTIC] load_channels() START")
        if not self.is_available:
            print("⚠️ Google Sheets client not available")
            return []
        try:
            sheet = self.client.open_by_key(self.spreadsheet_id)
            worksheet = sheet.worksheet("tg_channels")
            rows = worksheet.get_all_records()
            active = [row for row in rows if str(row.get("status", "")).strip().upper() == "ACTIVE"]
            print(f"📡 [DIAGNOSTIC] Loaded {len(active)} active channels (from {len(rows)} total)")
            if active:
                print(f"📡 [DIAGNOSTIC] First channel: {active[0].get('channel_id')}")
            return active
        except Exception as e:
            import traceback
            print(f"❌ [DIAGNOSTIC] Failed to load channels: {e}")
            traceback.print_exc()
            return []
    
    def write_signals(self, rows: List[List]) -> bool:
        if not self.is_available or not rows:
            return False
        try:
            sheet = self.client.open_by_key(self.spreadsheet_id)
            worksheet = sheet.worksheet("tg_signals_approved")
            worksheet.append_rows(rows, value_input_option="USER_ENTERED")
            print(f"📊 Written {len(rows)} signals")
            return True
        except Exception as e:
            print(f"❌ Write failed: {e}")
            return False
    
    def write_mining_log(self, log: MiningCycleLog) -> bool:
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
            return True
        except Exception as e:
            print(f"❌ Mining log write failed: {e}")
            return False
    
    async def write_with_retry(self, rows: List[List]) -> bool:
        if not rows:
            return True
        max_retries = int(os.getenv("GSHEETS_RETRY_ATTEMPTS", "3"))
        for attempt in range(max_retries):
            try:
                if self.write_signals(rows):
                    return True
            except Exception:
                pass
            if attempt < max_retries - 1:
                delay = (2 ** attempt) + random.uniform(0, 1)
                await asyncio.sleep(delay)
        print(f"❌ All {max_retries} retries failed")
        return False
    
    def health_check(self) -> bool:
        if not self.is_available:
            return False
        try:
            sheet = self.client.open_by_key(self.spreadsheet_id)
            worksheet = sheet.worksheet("tg_mining_log")
            test_row = ["HEALTH_CHECK", datetime.now().isoformat(), "", "", "", "", "", "", ""]
            worksheet.append_row(test_row, value_input_option="USER_ENTERED")
            return True
        except Exception:
            return False


class BufferedWriter:
    def __init__(self, max_size: Optional[int] = None):
        from core.config import settings
        self.max_size = max_size or getattr(settings, "GSHEETS_BUFFER_SIZE", 100)
        self.buffer: List[List] = []
        self.dlq_path = Path(os.getenv("GSHEETS_DLQ_PATH", "/opt/kraken/dlq/failed_writes.json"))
        if str(self.dlq_path).startswith("/app/") and not Path("/app").exists():
            self.dlq_path = Path("/opt/kraken") / self.dlq_path.relative_to("/app")
    
    async def add(self, flat_row: List, writer: GoogleSheetsClient):
        self.buffer.append(flat_row)
        if len(self.buffer) >= self.max_size:
            await self.flush(writer)
    
    async def flush(self, writer: GoogleSheetsClient):
        if not self.buffer:
            return
        success = await writer.write_with_retry(self.buffer)
        if success:
            self.buffer.clear()
        else:
            await self._save_to_dlq()
            self.buffer.clear()
    
    async def _save_to_dlq(self):
        entries = []
        if self.dlq_path.exists():
            try:
                entries = json.loads(self.dlq_path.read_text())
            except json.JSONDecodeError:
                pass
        for flat_row in self.buffer:
            entries.append({
                "timestamp": datetime.now().isoformat(),
                "signal_id": flat_row[0] if len(flat_row) > 0 else "UNKNOWN",
                "trace_id": flat_row[1] if len(flat_row) > 1 else "UNKNOWN",
                "error": "Google Sheets write failed after retries"
            })
        self.dlq_path.parent.mkdir(parents=True, exist_ok=True)
        self.dlq_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
    
    async def shutdown(self, writer: GoogleSheetsClient):
        await self.flush(writer)