"""
KRAKEN OpenAI Client — AI-классификатор сигналов.

Фаза 3: ИНТЕГРАЦИИ
Модуль: 3.2 openai_client.py
Версия: v5.2.5 (SRE 5.0 SHIELD REINFORCED)
Дата/Время стабилизации: 2026-05-29 21:58:00 UTC
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
        self.temperature = float(os.getenv("AI_TEMPERATURE", "0.2"))
        self.max_tokens = int(os.getenv("AI_MAX_TOKENS", "2000"))
        self.timeout = int(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "90"))
        self.retry_attempts = int(os.getenv("AI_RETRY_ATTEMPTS", "2"))
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

    async def classify_batch(self, messages: List[str]) -> Optional[Dict]:
        """Обрабатывает ВСЕ сообщения, деля их на безопасные чанки с защитой от JSONDecodeError."""
        if not self.api_key:
            log_sre("⚠️ OPENAI_API_KEY not set")
            return None
        
        if not messages:
            return {"signals": []}

        # Канон SRE: Бьем большой массив на чанки по 25 сообщений
        chunk_size = 25
        chunks = [messages[i:i + chunk_size] for i in range(0, len(messages), chunk_size)]
        log_sre(f"🧠 ИИ-Файрволл: Запуск тотальной обработки {len(messages)} сообщений (нарезано на {len(chunks)} батчей)")
        
        combined_signals = []
        client = self.client

        for chunk_idx, chunk in enumerate(chunks):
            # Санитизация: экранируем ломающие JSON-структуру кавычки и переносы
            batch_request = []
            for idx, msg in enumerate(chunk):
                safe_msg = msg.replace('"', '\\"').replace('\n', ' ').replace('\r', '')
                batch_request.append({"message_index": idx, "content": safe_msg[:4000]})
                
            estimated_tokens = sum(len(msg) for msg in chunk) // 4 + 1500
            
            if not self.token_budget.can_consume(estimated_tokens):
                log_sre(f"⚠️ Чанк #{chunk_idx + 1}: Лимит токенов превышен, пропуск.")
                continue

            max_retries = self.retry_attempts
            for attempt in range(max_retries + 1):
                try:
                    response = await asyncio.wait_for(
                        client.chat.completions.create(
                            model=self.model,
                            messages=[
                                {
                                    "role": "system", 
                                    "content": self.platinum_prompt + "\n\nCRITICAL SYSTEM REQUIREMENT:\nYou must strictly escape all double quotes inside text fields of the output JSON! Ensure the payload is a perfectly valid and complete JSON object without any syntax breaks."
                                },
                                {"role": "user", "content": json.dumps(batch_request, ensure_ascii=False)}
                            ],
                            temperature=self.temperature,
                            max_tokens=self.max_tokens,
                            response_format={"type": "json_object"}
                        ),
                        timeout=self.timeout
                    )
                    
                    self.token_budget.consume(estimated_tokens)
                    content = response.choices[0].message.content
                    data = json.loads(content)
                    
                    # Собираем сигналы из чанка
                    if "signals" in data and isinstance(data["signals"], list):
                        combined_signals.extend(data["signals"])
                        log_sre(f"✅ Чанк #{chunk_idx + 1} успешно обработан. Извлечено {len(data['signals'])} сигналов.")
                    break
                    
                except json.JSONDecodeError as jde:
                    log_sre(f"🔴 Чанк #{chunk_idx + 1} Сбой парсинга JSON (Попытка {attempt + 1}/{max_retries + 1}): {jde}")
                    if attempt < max_retries:
                        await asyncio.sleep((attempt + 1) * 3)
                    else:
                        log_sre(f"💀 Чанк #{chunk_idx + 1} окончательно потерян из-за JSONDecodeError.")
                except Exception as e:
                    log_sre(f"🔴 OpenAI Сбой на чанке #{chunk_idx + 1} (Попытка {attempt + 1}/{max_retries + 1}): {type(e).__name__}: {e}")
                    if attempt < max_retries:
                        await asyncio.sleep((attempt + 1) * 3)
                    else:
                        log_sre(f"💀 Чанк #{chunk_idx + 1} окончательно потерян после ретраев.")
        
        return {"signals": combined_signals}