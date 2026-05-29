"""
KRAKEN OpenAI Client — AI-классификатор сигналов.

Фаза 3: ИНТЕГРАЦИИ
Модуль: 3.2 openai_client.py
Версия: v5.2.10 (SRE 5.0 RUNTIME ADAPTER — FINAL MASTER)
Дата/Время стабилизации: 2026-05-29 22:55:00 UTC
"""

import os
import json
import asyncio
import sys
import re
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict
from dotenv import load_dotenv

env_path = Path("/opt/kraken/secrets/.env")
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv(Path(__file__).parent.parent.parent / "secrets" / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent))


def log_sre(message: str):
    sys.stdout.write(f"[{datetime.now().isoformat()}] {message}\n")
    sys.stdout.flush()


class TokenBudget:
    def __init__(self, daily_limit: int = 500_000):
        self.daily_limit = daily_limit
        self.consumed_today = 0
        self.reset_date = datetime.now().date()
    
    def _check_reset(self):
        today = datetime.now().date()
        if today != self.reset_date:
            self.consumed_today = 0
            self.reset_date = today
    
    def can_consume(self, tokens: int) -> bool:
        self._check_reset()
        return self.consumed_today + tokens <= self.daily_limit
    
    def consume(self, tokens: int):
        self._check_reset()
        self.consumed_today += tokens


class OpenAIClient:
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("AI_MODEL_NAME", "gpt-4o-mini")
        self.temperature = 0.1
        self.max_tokens = 800
        self.timeout = 25
        self.retry_attempts = 1
        self.token_budget = TokenBudget()
        self.platinum_prompt = self._load_prompt()
        self._async_client = None
    
    @property
    def client(self):
        if self._async_client is None:
            from openai import AsyncOpenAI
            import httpx
            
            proxy_ip = os.getenv("TG_PROXY_IP", "")
            proxy_port = os.getenv("TG_PROXY_PORT", "")
            proxy_user = os.getenv("TG_PROXY_USER", "")
            proxy_pass = os.getenv("TG_PROXY_PASS", "")
            
            if proxy_ip and proxy_port:
                proxy_auth = f"{proxy_user}:{proxy_pass}@" if proxy_user and proxy_pass else ""
                proxy_url = f"http://{proxy_auth}{proxy_ip}:{proxy_port}"
                log_sre(f"🛡️ OpenAI Client: Подключение параллельного пула Squid -> {proxy_ip}:{proxy_port}")
                limits = httpx.Limits(max_keepalive_connections=50, max_connections=100)
                self._http_client = httpx.AsyncClient(proxy=proxy_url, timeout=self.timeout, limits=limits)
                self._async_client = AsyncOpenAI(api_key=self.api_key, http_client=self._http_client)
            else:
                log_sre("⚠️ OpenAI Client: Прямое подключение без прокси")
                self._async_client = AsyncOpenAI(api_key=self.api_key)
                
        return self._async_client
    
    def _load_prompt(self) -> str:
        paths = [
            Path("/opt/kraken/prompts/platinum_prompt.txt"),
            Path("/app/prompts/platinum_prompt.txt"),
            Path(__file__).parent.parent / "prompts" / "platinum_prompt.txt",
        ]
        for prompt_path in paths:
            if prompt_path.exists():
                log_sre(f"📖 Loaded system prompt: {prompt_path}")
                return prompt_path.read_text(encoding="utf-8")
        raise FileNotFoundError("❌ Critical error: prompts/platinum_prompt.txt not found!")

    async def _process_single_message_async(self, idx: int, msg: str) -> List[Dict]:
        """Изолированная атомарная транзакция с нативной runtime-адаптацией типов."""
        clean_in = msg.replace('"', "'").replace('\r', '').strip()
        batch_request = [{"message_index": 0, "content": clean_in[:4000]}]
        
        client = self.client
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.platinum_prompt},
                        {"role": "user", "content": json.dumps(batch_request, ensure_ascii=False)}
                    ],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    response_format={"type": "json_object"}
                ),
                timeout=self.timeout
            )
            
            raw_content = response.choices[0].message.content
            data = json.loads(raw_content)
            
            signals = data.get("results", [])
            if isinstance(signals, list):
                valid_signals = []
                for sig in signals:
                    sig["message_index"] = idx
                    
                    # ===== SRE RUNTIME ADAPTER: ВЫПРЯМЛЯЕМ ТИПЫ ДАННЫХ ДЛЯ PYDANTIC =====
                    source = sig.get("source", {})
                    if isinstance(source, dict):
                        # 1. Защита source_type Enum
                        st = str(source.get("source_type", "")).upper()
                        if st not in ['ANALYTIC', 'DEVELOPER', 'NEWS', 'AGENCY', 'PRIVATE']:
                            source["source_type"] = "AGENCY" # Безопасный дефолт по канону ТЗ
                        
                        # 2. Защита source_tier Int (Парсим 'TIER_1' -> 1 или оставляем int)
                        tier_raw = source.get("source_tier", 3)
                        if isinstance(tier_raw, str):
                            digits = re.findall(r'\d+', tier_raw)
                            source["source_tier"] = int(digits[0]) if digits else 3
                        else:
                            source["source_tier"] = int(tier_raw) if tier_raw else 3
                            
                    valid_signals.append(sig)
                return valid_signals
        except Exception:
            pass
        return []

    async def classify_batch(self, messages: List[str]) -> Optional[Dict]:
        """Параллельный неблокирующий обстрел OpenAI с адаптацией типов."""
        if not self.api_key:
            log_sre("⚠️ OPENAI_API_KEY not set")
            return None
        
        if not messages:
            return {"signals": []}

        log_sre(f"🧠 ИИ-Файрволл: Запуск СИНХРОНИЗИРОВАННОГО пула с Runtime-Адаптером Pydantic для {len(messages)} сообщений.")
        
        tasks = [self._process_single_message_async(idx, msg) for idx, msg in enumerate(messages)]
        results = await asyncio.gather(*tasks)
        
        combined_signals = []
        for sublist in results:
            if sublist:
                combined_signals.extend(sublist)
                
        log_sre(f"✅ Параллельный раунд завершен. Извлечено {len(combined_signals)} аппаратно-совместимых сигналов из {len(messages)} сообщений.")
        return {"signals": combined_signals}