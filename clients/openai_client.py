"""
KRAKEN OpenAI Client — AI-классификатор сигналов.

Фаза 3: ИНТЕГРАЦИИ
Модуль: 3.2 openai_client.py
Версия: v5.2.3 (GOLDEN SRE 5.0 Edition v1.2)
Дата/Время стабилизации: 2026-05-29 18:47:41 UTC
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


def log_sre(message: str):
    """Потокобезопасное логирование SRE 5.0 с принудительным сбросом буфера Docker."""
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
    
    @property
    def remaining(self) -> int:
        self._check_reset()
        return max(0, self.daily_limit - self.consumed_today)


class OpenAIClient:
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("AI_MODEL_NAME", "gpt-4o-mini")
        self.temperature = float(os.getenv("AI_TEMPERATURE", "0.2"))
        self.max_tokens = int(os.getenv("AI_MAX_TOKENS", "1000"))
        self.timeout = int(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "30"))
        self.retry_attempts = int(os.getenv("AI_RETRY_ATTEMPTS", "2"))
        self.token_budget = TokenBudget()
        self.platinum_prompt = self._load_prompt()
        self._async_client = None
    
    @property
    def client(self):
        if self._async_client is None:
            from openai import AsyncOpenAI
            import httpx
            
            # Извлекаем проверенные SRE-переменные из .env
            proxy_ip = os.getenv("TG_PROXY_IP", "")
            proxy_port = os.getenv("TG_PROXY_PORT", "")
            proxy_user = os.getenv("TG_PROXY_USER", "")
            proxy_pass = os.getenv("TG_PROXY_PASS", "")
            
            # Динамическая сборка строки прокси по закону системы
            if proxy_ip and proxy_port:
                if proxy_user and proxy_pass:
                    proxy_auth = f"{proxy_user}:{proxy_pass}@"
                else:
                    proxy_auth = ""
                
                proxy_url = f"http://{proxy_auth}{proxy_ip}:{proxy_port}"
                log_sre(f"🛡️ OpenAI Client: routing through corporate proxy {proxy_ip}:{proxy_port}")
                
                self._http_client = httpx.AsyncClient(proxy=proxy_url, timeout=self.timeout)
                self._async_client = AsyncOpenAI(api_key=self.api_key, http_client=self._http_client)
            else:
                log_sre("⚠️ OpenAI Client: running without proxy (direct connection)")
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
                log_sre(f"📖 Loaded system prompt from file: {prompt_path}")
                return prompt_path.read_text(encoding="utf-8")
        raise FileNotFoundError("❌ Critical error: prompts/platinum_prompt.txt not found on disk!")

    def build_batch_request(self, messages: List[str]) -> List[Dict]:
        return [{"message_index": idx, "content": msg[:4000]} for idx, msg in enumerate(messages[:5])]
    
    async def classify_batch(self, messages: List[str]) -> Optional[Dict]:
        if not self.api_key:
            log_sre("⚠️ OPENAI_API_KEY not set")
            return None
        
        client = self.client
        batch = self.build_batch_request(messages)
        estimated_tokens = sum(len(msg) for msg in messages) // 4 + 1000
        
        if not self.token_budget.can_consume(estimated_tokens):
            log_sre("⚠️ Token budget exceeded")
            return None
        
        max_retries = self.retry_attempts
        for attempt in range(max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": self.platinum_prompt},
                            {"role": "user", "content": json.dumps(batch, ensure_ascii=False)}
                        ],
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                        response_format={"type": "json_object"}
                    ),
                    timeout=self.timeout
                )
                
                self.token_budget.consume(estimated_tokens)
                content = response.choices[0].message.content
                return json.loads(content)
                
            except Exception as e:
                err_type = type(e).__name__
                err_msg = str(e)
                log_sre(f"🔴 OpenAI Клиент поймал сбой (Попытка {attempt + 1}/{max_retries + 1}):")
                log_sre(f"   [ТИП ОШИБКИ]: {err_type}")
                log_sre(f"   [ТЕКСТ ОШИБКИ]: {err_msg}")
                
                if attempt < max_retries:
                    delay = ((attempt + 1) * 2) + random.uniform(0, 1)
                    log_sre(f"⏳ Ожидание {delay:.2f}с перед следующим ретраем...")
                    await asyncio.sleep(delay)
                else:
                    log_sre(f"💀 Все {max_retries + 1} попыток запроса к OpenAI исчерпаны. Пропуск батча.")
        return None