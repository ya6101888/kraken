"""
KRAKEN Configuration — Все параметры из .env через Pydantic Settings.

Фаза 5: ОРКЕСТРАЦИЯ
Модуль: config.py
Версия: v5.2.3
"""

import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Централизованные настройки KRAKEN.
    
    Все параметры читаются из .env файла.
    Если параметр не задан — используется значение по умолчанию.
    
    Использование:
        from src.core.config import settings
        print(settings.TG_API_ID)
    """
    
    # ===== KRAKEN CORE =====
    KRAKEN_CRON_INTERVAL_MINUTES: int = 15
    KRAKEN_BULK_FETCH_LIMIT: int = 50
    KRAKEN_SESSION_PATH: str = "/app/sessions/kraken.session"
    KRAKEN_SESSION_BACKUP_PATH: str = "/app/sessions/kraken.session.bak"
    KRAKEN_HEARTBEAT_INTERVAL_SECONDS: int = 300
    KRAKEN_GRACEFUL_SHUTDOWN_TIMEOUT: int = 30
    
    # ===== HARVESTER =====
    HARVESTER_DEDUP_ENABLED: bool = True
    HARVESTER_DEDUP_MEMORY_SIZE: int = 10000
    HARVESTER_MIN_MESSAGE_LENGTH: int = 20
    HARVESTER_MAX_MESSAGE_LENGTH: int = 4000
    HARVESTER_FILTER_OLDER_THAN_HOURS: int = 24
    HARVESTER_REGEX_REMOVE_LINKS: bool = True
    HARVESTER_REGEX_REMOVE_HTML: bool = True
    HARVESTER_REGEX_REMOVE_EMOJI: bool = False
    
    # ===== AI FIREWALL =====
    AI_MODEL_NAME: str = "gpt-4o-mini"
    AI_TEMPERATURE: float = 0.3
    AI_MAX_TOKENS: int = 500
    AI_REQUEST_TIMEOUT_SECONDS: int = 30
    AI_RETRY_ATTEMPTS: int = 2
    AI_FALLBACK_ON_ERROR: str = "skip"
    AI_RELEVANCE_THRESHOLD: float = 0.7
    
    # ===== TELEGRAM MTProto =====
    TG_API_ID: int = 0
    TG_API_HASH: str = ""
    TG_PHONE_NUMBER: str = ""
    TG_2FA_PASSWORD: Optional[str] = None
    TG_FLOODWAIT_MAX_WAIT_SECONDS: int = 30
    TG_FLOODWAIT_RETRY_ATTEMPTS: int = 5
    TG_RECONNECT_ATTEMPTS: int = 3
    TG_RECONNECT_DELAY_SECONDS: int = 5
    
    # ===== TELEGRAM PROXY =====
    TG_PROXY_TYPE: Optional[str] = None
    TG_PROXY_IP: Optional[str] = None
    TG_PROXY_PORT: Optional[str] = None
    TG_PROXY_USER: Optional[str] = None
    TG_PROXY_PASS: Optional[str] = None
    
    # ===== STORAGE GSHEETS =====
    GSHEETS_SPREADSHEET_ID: str = ""
    GSHEETS_SERVICE_ACCOUNT_PATH: str = "/app/secrets/service_account.json"
    GSHEETS_BUFFER_SIZE: int = 100
    GSHEETS_RETRY_ATTEMPTS: int = 3
    GSHEETS_DLQ_PATH: str = "/app/dlq/failed_writes.json"
    
    # ===== BEACON SRE =====
    BEACON_ENABLED: bool = True
    BEACON_BOT_TOKEN: str = ""
    BEACON_CHAT_ID: str = ""
    BEACON_RATE_LIMIT_PER_ERROR_MINUTES: int = 5
    BEACON_HEALTH_CHECK_INTERVAL_SECONDS: int = 30
    BEACON_ALERT_ON_SESSION_AGE_DAYS: int = 30
    BEACON_ALERT_ON_ZERO_SIGNALS_HOURS: int = 1
    
    # ===== INFRASTRUCTURE =====
    INFRA_LOG_LEVEL: str = "INFO"
    INFRA_ENVIRONMENT: str = "production"
    INFRA_TRACE_ID_ENABLED: bool = True
    
    # ===== OPENAI =====
    OPENAI_API_KEY: Optional[str] = None
    
    model_config = {
        "env_file": "/app/secrets/.env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore"
    }


# Создаём глобальный экземпляр
# На Windows файл .env может быть в другом месте — ищем
_env_path = Path("/app/secrets/.env")
if not _env_path.exists():
    _env_path = Path(__file__).parent.parent.parent / "secrets" / ".env"

if _env_path.exists():
    settings = Settings(_env_file=str(_env_path))
    print(f"✅ Settings loaded: environment={settings.INFRA_ENVIRONMENT}")
else:
    print(f"⚠️ .env not found at {_env_path}, using defaults")
    settings = Settings()