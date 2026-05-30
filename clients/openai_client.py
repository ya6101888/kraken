"""
KRAKEN OpenAI Client — AI-классификатор сигналов.

Фаза 3: ИНТЕГРАЦИИ
Модуль: 3.2 openai_client.py
Версия: v5.4.0 (SRE 5.0 RUNTIME ADAPTER — CONCURRENCY SEMAPHORE FIXED)
Дата/Время стабилизации: 2026-05-30 20:53:00 UTC
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

    async def _process_single_message_async(self, semaphore: asyncio.Semaphore, idx: int, msg: str) -> Optional[Dict]:
        """Изолированная атомарная транзакция под защитой семафора по плоскому контракту v1.4.0."""
        async with semaphore:
            clean_in = msg.strip()
            client = self.client
            try:
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": self.platinum_prompt},
                            {"role": "user", "content": f"Сырое сообщение для анализа:\n{clean_in}"}
                        ],
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                        response_format={"type": "json_object"}
                    ),
                    timeout=self.timeout
                )

                raw_content = response.choices[0].message.content
                sig = json.loads(raw_content)

                if isinstance(sig, dict):
                    sig["message_index"] = idx
                    return sig
            except Exception as e:
                sys.stdout.write(f"⚠️ [AI Client Connection Error]: {e}\n")
                sys.stdout.flush()
            return None

    async def classify_batch(self, messages: List[str]) -> Optional[Dict]:
        """Параллельный обстрел OpenAI с жестким ограничением конкурентности."""
        if not self.api_key:
            log_sre("⚠️ OPENAI_API_KEY not set")
            return None

        if not messages:
            return {"signals": []}

        log_sre(f"🧠 ИИ-Файрволл: Запуск СИНХРОНИЗИРОВАННОГО пула для {len(messages)} сообщений.")

        # Ограничиваем поток до 5 параллельных слотов, чтобы не вешать TCP-таблицу Squid прокси
        sem = asyncio.Semaphore(5)

        tasks = [self._process_single_message_async(sem, idx, msg) for idx, msg in enumerate(messages)]
        results = await asyncio.gather(*tasks)

        combined_signals = []
        for single_signal in results:
            if single_signal and isinstance(single_signal, dict):
                combined_signals.append(single_signal)

        log_sre(f"✅ Параллельный раунд завершен. Извлечено {len(combined_signals)} плоских сигналов из {len(messages)} сообщений.")
        return {"signals": combined_signals}