"""
KRAKEN Google Sheets Client — Запись сигналов в таблицы.

Фаза 3: ИНТЕГРАЦИИ
Модуль: 3.3 gsheets_client.py
Версия: v5.4.6 (SRE 5.0 PROXY-AUTH FIX — CREDS.SESSION INJECTION)
Дата изменения: 2026-06-24 06:45:00 UTC

Принципы:
- Авторизация через service_account.json
- Явная сессия requests с прокси из core.config.settings
- Тотальная маршрутизация через SRE Proxy
- Динамический размер буфера из core.config.settings
- Retry: 3 попытки с exponential backoff
- Прямой транзит канонического DTO v2.1 (25 колонок)
- DLQ: сохранение неудавшихся записей в JSON
- Тотальная наблюдаемость: вывод критических SRE-логов напрямую в stdout хоста
- DIAGNOSTIC: пошаговый вывод для отладки загрузки каналов
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

# Импорт моделей и настроек
sys.path.insert(0, str(Path(__file__).parent.parent))
from models.signal import MiningCycleLog

# Импорт настроек для прокси
try:
    from core.config import settings
except ImportError:
    # fallback для тестов
    settings = None


class GoogleSheetsClient:
    """
    Клиент для работы с Google Sheets API.
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
    
    # ===== 3.3.1. АВТОРИЗАЦИЯ С ЯВНОЙ СЕССИЕЙ ПРОКСИ (ИСПРАВЛЕНО v5.4.6) =====
    
    def _init_client(self):
        """
        Авторизуется через сервисный аккаунт Google.
        Внедряет прокси-слой через явную requests.Session.
        ИСПРАВЛЕНИЕ: вызываем creds.authorize(session) перед передачей в gspread.
        """
        try:
            import gspread
            from oauth2client.service_account import ServiceAccountCredentials
            import requests

            # 1. Получаем настройки прокси
            if settings and settings.PROXY_ENABLED:
                proxy_url = settings.HTTP_PROXY or settings.HTTPS_PROXY
                if proxy_url:
                    session = requests.Session()
                    session.proxies = {
                        "http": proxy_url,
                        "https": proxy_url,
                    }
                    sys.stdout.write(f"🛡️ [SRE-NETWORK] Google Sheets using explicit proxy: {proxy_url}\n")
                    sys.stdout.flush()
                else:
                    session = requests.Session()
                    sys.stdout.write("⚠️ [SRE-NETWORK] Proxy enabled but URL missing, using direct connection\n")
                    sys.stdout.flush()
            else:
                session = requests.Session()
                sys.stdout.write("ℹ️ [SRE-NETWORK] Google Sheets using direct connection (no proxy)\n")
                sys.stdout.flush()

            # 2. Авторизация с явной инъекцией сессии в creds
            scope = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"
            ]
            
            creds = ServiceAccountCredentials.from_json_keyfile_name(
                self.service_account_path,
                scope
            )
            
            # 🔥 ГЛАВНЫЙ ФИКС: привязываем сессию к credentials
            # Без этого oauth2client использует дефолтный http и не видит прокси,
            # а gspread получает 403 "Method doesn't allow unregistered callers"
            creds.authorize(session)
            
            # Теперь передаём уже авторизованные creds и сессию
            self.client = gspread.authorize(creds, session=session)
            print(f"✅ Google Sheets authorized with explicit proxy session")
            
        except FileNotFoundError:
            print(f"⚠️ Service account file not found: {self.service_account_path}")
            print("   Google Sheets will not be available")
        except Exception as e:
            print(f"❌ Google Sheets auth error: {e}")
    
    @property
    def is_available(self) -> bool:
        """Доступен ли Google Sheets API."""
        return self.client is not None
    
    # ===== 3.3.2. ЗАГРУЗКА КАНАЛОВ (FINAL DIAGNOSTIC) =====
    
    def load_channels(self) -> List[Dict]:
        """Загружает реестр каналов из листа tg_channels."""
        print("🔍 [DIAGNOSTIC] load_channels() START")
        
        if not self.is_available:
            print("⚠️ Google Sheets client not available")
            return []
        
        try:
            print(f"🔍 [DIAGNOSTIC] Opening spreadsheet: {self.spreadsheet_id}")
            sheet = self.client.open_by_key(self.spreadsheet_id)
            print("✅ [DIAGNOSTIC] Spreadsheet opened successfully")
            
            print("🔍 [DIAGNOSTIC] Getting worksheet: tg_channels")
            worksheet = sheet.worksheet("tg_channels")
            print("✅ [DIAGNOSTIC] Worksheet obtained")
            
            print("🔍 [DIAGNOSTIC] Fetching all records...")
            rows = worksheet.get_all_records()
            print(f"✅ [DIAGNOSTIC] Got {len(rows)} rows from sheet")
            
            print(f"🔍 [DIAGNOSTIC] Filtering status='ACTIVE'...")
            active = [
                row for row in rows 
                if str(row.get("status", "")).strip().upper() == "ACTIVE"
            ]
            
            print(f"📡 [DIAGNOSTIC] Loaded {len(active)} active channels (from {len(rows)} total)")
            
            if active:
                print(f"📡 [DIAGNOSTIC] First channel: {active[0].get('channel_id')}")
            
            return active
            
        except Exception as e:
            import traceback
            print(f"❌ [DIAGNOSTIC] Failed to load channels: {e}")
            traceback.print_exc()
            return []
    
    # ===== 3.3.3. BATCH ЗАПИСЬ СИГНАЛОВ (СТРОГО 25 КОЛОНОК) =====
    
    def write_signals(self, rows: List[List]) -> bool:
        """
        Записывает подготовленные плоские списки строк в лист tg_signals_approved.
        Транзит "выпрямленных" данных напрямую без повторной сборки.
        """
        if not self.is_available or not rows:
            return False
        
        try:
            sheet = self.client.open_by_key(self.spreadsheet_id)
            worksheet = sheet.worksheet("tg_signals_approved")
            
            worksheet.append_rows(rows, value_input_option="USER_ENTERED")
            print(f"📊 Written {len(rows)} signals to Google Sheets (25 columns configuration)")
            return True
            
        except Exception as e:
            print(f"❌ Write failed: {e}")
            return False
    
    # ===== 3.3.4. ЗАПИСЬ ЛОГА =====
    
    def write_mining_log(self, log: MiningCycleLog) -> bool:
        """Записывает лог цикла сбора в лист tg_mining_log."""
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
    
    async def write_with_retry(self, rows: List[List]) -> bool:
        """Retry: 3 попытки с exponential backoff: 1с, 2с, 4с + jitter."""
        if not rows:
            return True
        
        max_retries = int(os.getenv("GSHEETS_RETRY_ATTEMPTS", "3"))
        
        for attempt in range(max_retries):
            try:
                result = self.write_signals(rows)
                if result:
                    return True
            except Exception:
                pass
            
            if attempt < max_retries - 1:
                delay = (2 ** attempt) + random.uniform(0, 1)
                print(f"⏳ Retry {attempt + 2}/{max_retries} in {delay:.1f}s")
                await asyncio.sleep(delay)
        
        print(f"❌ All {max_retries} retries failed")
        return False
    
    # ===== 3.3.8. HEALTH CHECK =====
    
    def health_check(self) -> bool:
        """Проверяет доступность Google Sheets тестовой записью."""
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
    Буфер для накопления плоских сигналов перед пакетной записью.
    Принимает уже готовые сформированные списки ячеек (Матрица ТЗ v2.1).
    """
    
    def __init__(self, max_size: Optional[int] = None):
        from core.config import settings
        self.max_size = max_size or getattr(settings, "GSHEETS_BUFFER_SIZE", 100)
        
        self.buffer: List[List] = []  # ОЗУ-буфер плоских списков
        self.dlq_path = Path(os.getenv(
            "GSHEETS_DLQ_PATH",
            "/opt/kraken/dlq/failed_writes.json"
        ))
        
        if str(self.dlq_path).startswith("/app/") and not Path("/app").exists():
            self.dlq_path = Path("/opt/kraken") / self.dlq_path.relative_to("/app")
            
        print(f"📦 BufferedWriter initialized with max_size={self.max_size}")
    
    async def add(self, flat_row: List, writer: GoogleSheetsClient):
        """Добавляет готовую выпрямленную строку в буфер."""
        self.buffer.append(flat_row)
        if len(self.buffer) >= self.max_size:
            await self.flush(writer)
    
    async def flush(self, writer: GoogleSheetsClient):
        """Отправляет накопленные плоские строки напрямую в Google Sheets."""
        if not self.buffer:
            return
        
        sys.stdout.write(f"📤 [SRE BUFFER] Педаль нажата! Выталкиваем {len(self.buffer)} сигналов строго по Матрице v2.1...\n")
        sys.stdout.flush()
        
        success = await writer.write_with_retry(self.buffer)
        
        if success:
            self.buffer.clear()
            sys.stdout.write("✅ [SRE BUFFER] Пакет успешно доставлен в Google Sheets (25 columns configuration)!\n")
            sys.stdout.flush()
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
                "classification": flat_row[5] if len(flat_row) > 5 else "UNKNOWN",
                "priority_score": flat_row[10] if len(flat_row) > 10 else 0.0,
                "error": "Google Sheets write failed after retries"
            })
        
        self.dlq_path.parent.mkdir(parents=True, exist_ok=True)
        self.dlq_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
        sys.stdout.write(f"💀 [SRE DLQ] {len(self.buffer)} сигналов аварийно сохранены в DLQ: {self.dlq_path}\n")
        sys.stdout.flush()
        
    async def shutdown(self, writer: GoogleSheetsClient):
        sys.stdout.write(f"🛑 [SRE SHUTDOWN] Перехват сигнала: принудительный слив {len(self.buffer)} buffered сигналов...\n")
        sys.stdout.flush()
        await self.flush(writer)