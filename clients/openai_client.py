"""
KRAKEN OpenAI Client — AI-классификатор сигналов.

Фаза 3: ИНТЕГРАЦИИ
Модуль: 3.2 openai_client.py
Версия: v5.2.8 (SRE 5.0 ASYNC GATHER POOL — GOLDEN MASTER)
Дата/Время стабилизации: 2026-05-29 22:30:00 UTC
"""

import os
import json
import asyncio
import sys
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
        self.timeout = 25  # Лимит на индивидуальный параллельный запрос
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
                log_sre(f"🛡️ OpenAI Client: Активирован параллельный шлюз Squid -> {proxy_ip}:{proxy_port}")
                # Увеличием пул соединений (limits) для параллельного обстрела API
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
        """Изолированная атомарная транзакция для одного сообщения."""
        clean_in = msg.replace('"', "'").replace('\n', ' ').replace('\r', ' ').strip()
        batch_request = [{"message_index": 0, "content": clean_in[:4000]}]
        
        client = self.client
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.platinum_prompt + "\nSTRICT JSON RULE: Return valid JSON object. Text fields must use single quotes instead of double quotes."},
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
            
            signals = data.get("signals", [])
            if isinstance(signals, list):
                for sig in signals:
                    sig["message_index"] = idx
                return signals
        except Exception:
            pass
        return []

    async def classify_batch(self, messages: List[str]) -> Optional[Dict]:
        """Параллельный неблокирующий обстрел OpenAI через asyncio.gather."""
        if not self.api_key:
            log_sre("⚠️ OPENAI_API_KEY not set")
            return None
        
        if not messages:
            return {"signals": []}

        log_sre(f"🧠 ИИ-Файрволл: Запуск ПАРАЛЛЕЛЬНОГО пула для {len(messages)} сообщений.")
        
        # Создаем массив корутин для одновременного выполнения
        tasks = [self._process_single_message_async(idx, msg) for idx, msg in enumerate(messages)]
        
        # Запускаем тотальный параллельный штурм
        results = await asyncio.gather(*tasks)
        
        # Схлопываем результаты из всех тасок в единый плоский список сигналов
        combined_signals = []
        for sublist in results:
            if sublist:
                combined_signals.extend(sublist)
                
        log_sre(f"✅ Параллельный раунд завершен. Извлечено {len(combined_signals)} валидных сигналов из {len(messages)} сообщений.")
        return {"signals": combined_signals}