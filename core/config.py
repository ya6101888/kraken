"""
KRAKEN Configuration — Все параметры из .env через Pydantic Settings.
Фаза 5: ОРКЕСТРАЦИЯ
Модуль: config.py
Версия: v5.3.4 (SRE 5.0 GOLDEN CONFIG — UNIFIED PROXY LAYER)
"""

import sys
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """
    Централизованные настройки KRAKEN.
    Внедрен единый сетевой слой для всех библиотек.
    """
    
    # ===== 🖥️ NETWORK PROXY (Squid Tunneling) =====
    PROXY_ENABLED: bool = True
    PROXY_HTTP_URL: str = "http://192.168.0.1:3128"
    PROXY_TELETHON_TYPE: str = "http"
    # Единый транспортный уровень для всех HTTP-клиентов
    HTTP_PROXY: str = "http://192.168.0.1:3128"
    HTTPS_PROXY: str = "http://192.168.0.1:3128"
    
    # ===== 🦑 KRAKEN CORE ORCHESTRATION =====
    KRAKEN_CRON_INTERVAL_MINUTES: int = 15
    KRAKEN_BULK_FETCH_LIMIT: int = 50
    KRAKEN_SESSION_PATH: str = "/app/sessions/kraken.session"
    KRAKEN_SESSION_BACKUP_PATH: str = "/app/sessions/kraken.session.bak"
    KRAKEN_HEARTBEAT_INTERVAL_SECONDS: int = 300
    KRAKEN_GRACEFUL_SHUTDOWN_TIMEOUT: int = 30
    
    # ===== 🧹 HARVESTER TECHNICAL FILTER =====
    HARVESTER_DEDUP_ENABLED: bool = True
    HARVESTER_DISK_CACHE_LIMIT: int = 10000
    HARVESTER_CACHE_FILE_PATH: str = "/app/logs/processed_hashes.uid"
    HARVESTER_MIN_MESSAGE_LENGTH: int = 20
    HARVESTER_MAX_MESSAGE_LENGTH: int = 4000
    HARVESTER_FILTER_OLDER_THAN_HOURS: int = 24
    HARVESTER_REGEX_REMOVE_LINKS: bool = True
    HARVESTER_REGEX_REMOVE_HTML: bool = True
    HARVESTER_REGEX_REMOVE_EMOJI: bool = False
    
    # ===== 🧠 AI FIREWALL EXPERT SYSTEM =====
    OPENAI_API_KEY: Optional[str] = None
    AI_MODEL_NAME: str = "gpt-4o-mini"
    AI_TEMPERATURE: float = 0.0
    AI_MAX_TOKENS: int = 1000
    AI_REQUEST_TIMEOUT_SECONDS: int = 30
    AI_RELEVANCE_THRESHOLD: float = 0.7
    AI_PROMPTS_DIR: str = "/app/prompts"
    
    # ===== 💾 STORAGE GOOGLE SHEETS API =====
    GSHEETS_SPREADSHEET_ID: str = ""
    GSHEETS_WORKSHEET_APPROVED: str = "tg_signals_approved"
    GSHEETS_WORKSHEET_LOG: str = "tg_mining_log"
    GSHEETS_WORKSHEET_CHANNELS: str = "tg_channels"
    GSHEETS_SERVICE_ACCOUNT_PATH: str = "/app/secrets/service_account.json"
    GSHEETS_BUFFER_SIZE: int = 100
    GSHEETS_DLQ_PATH: str = "/app/dlq/failed_writes.json"
    
    # ===== 📡 TELEGRAM MTPROTO APP INTERFACES =====
    TG_API_ID: int = 0
    TG_API_HASH: str = ""
    TG_PHONE_NUMBER: str = ""
    TG_2FA_PASSWORD: Optional[str] = None
    TG_FLOODWAIT_MAX_WAIT_SECONDS: int = 30
    TG_MESSAGE_LIMIT: int = 10
    TG_CHANNEL_DELAY_MIN: float = 2.0
    TG_CHANNEL_DELAY_MAX: float = 5.0

    # ===== 🚨 MONITORING SRE BEACON MONITOR =====
    BEACON_ENABLED: bool = True
    BEACON_BOT_TOKEN: str = ""
    BEACON_CHAT_ID: str = ""
    BEACON_RATE_LIMIT_PER_ERROR_MINUTES: int = 5
    BEACON_HEALTH_CHECK_INTERVAL_SECONDS: int = 30
    BEACON