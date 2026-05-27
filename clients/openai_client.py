"""
KRAKEN OpenAI Client — AI-классификатор сигналов.

Фаза 3: ИНТЕГРАЦИИ
Модуль: 3.2 openai_client.py
Версия: v5.2.3 (GOLDEN СБОРКА)
"""

import os
import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict
import sys
from dotenv import load_dotenv

# Загрузка .env
env_path = Path("/app/secrets/.env")
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv(Path(__file__).parent.parent.parent / "secrets" / ".env")

# Импорт моделей данных
sys.path.insert(0, str(Path(__file__).parent.parent))
from models.signal import (
    ClassificationResult,
    BatchAIResponse,
    ObjectData
)


class TokenBudget:
    """
    Бюджет токенов на день.
    
    Защита от перерасхода: если сегодня уже потратили 100k токенов,
    новые запросы к OpenAI не отправляются до сброса в полночь.
    """
    
    def __init__(self, daily_limit: int = 100_000):
        self.daily_limit = daily_limit
        self.consumed_today = 0
        self.reset_date = datetime.now().date()
    
    def _check_reset(self):
        """Сбрасывает счётчик, если наступил новый день."""
        today = datetime.now().date()
        if today != self.reset_date:
            self.consumed_today = 0
            self.reset_date = today
    
    def can_consume(self, tokens: int) -> bool:
        """Можно ли потратить ещё tokens штук?"""
        self._check_reset()
        return self.consumed_today + tokens <= self.daily_limit
    
    def consume(self, tokens: int):
        """Списывает tokens из дневного бюджета."""
        self._check_reset()
        self.consumed_today += tokens
    
    @property
    def remaining(self) -> int:
        """Сколько ещё токенов можно потратить сегодня."""
        self._check_reset()
        return max(0, self.daily_limit - self.consumed_today)


class OpenAIClient:
    """
    Клиент для AI-классификации сообщений через OpenAI API.
    
    Использование:
        ai = OpenAIClient()
        response = await ai.classify_batch(messages)
    """
    
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("AI_MODEL_NAME", "gpt-4o-mini")
        self.temperature = float(os.getenv("AI_TEMPERATURE", "0.3"))
        self.max_tokens = int(os.getenv("AI_MAX_TOKENS", "500"))
        self.timeout = int(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "30"))
        self.retry_attempts = int(os.getenv("AI_RETRY_ATTEMPTS", "2"))
        self.threshold = float(os.getenv("AI_RELEVANCE_THRESHOLD", "0.7"))
        self.fallback_action = os.getenv("AI_FALLBACK_ON_ERROR", "skip")
        
        self.platinum_prompt = self._load_prompt()
        self.token_budget = TokenBudget()
    
    # ===== 3.2.1. ЗАГРУЗКА PLATINUM PROMPT =====
    
    def _load_prompt(self) -> str:
        """Загружает Platinum Prompt из файла."""
        paths = [
            Path("/app/src/prompts/platinum_prompt.txt"),
            Path(__file__).parent.parent / "prompts" / "platinum_prompt.txt",
        ]
        
        for prompt_path in paths:
            if prompt_path.exists():
                return prompt_path.read_text(encoding="utf-8")
        
        print("⚠️ Platinum prompt file not found, using built-in")
        return self._get_default_prompt()
    
    def _get_default_prompt(self) -> str:
        """Встроенный промпт, если файл не найден."""
        return """Ты — AI-классификатор сигналов недвижимости KRAKEN.
Проанализируй сообщения и верни JSON с полями:
- message_index: номер сообщения (0-19)
- relevance_score: оценка релевантности (0.0-1.0)
- market_segment: PRIMARY/SECONDARY/RENT/INVEST/PRO или null
- geo_focus: ROSTOV_CITY/ROSTOV_REGION/SOUTHERN_DISTRICT/FEDERAL или null
- object_data: {price, address, rooms, area, floor, developer, completion_date} — все поля null если нет данных"""
    
    # ===== 3.2.2. ФОРМИРОВАНИЕ BATCH-ЗАПРОСА =====
    
    def build_batch_request(self, messages: List[str]) -> List[Dict]:
        """Формирует запрос для OpenAI из массива сообщений."""
        batch = []
        for idx, msg in enumerate(messages[:20]):
            batch.append({
                "message_index": idx,
                "content": msg[:4000]
            })
        return batch
    
    # ===== 3.2.3. ВЫЗОВ OPENAI API С RETRY =====
    
    async def classify(self, messages: List[str]) -> Optional[Dict]:
        """
        Отправляет batch-запрос к OpenAI и возвращает ответ.
        Использует синтаксис openai>=1.0.0 (AsyncOpenAI).
        """
        from openai import AsyncOpenAI
        
        if not self.api_key:
            print("⚠️ OPENAI_API_KEY not set, using fallback")
            return None
        
        client = AsyncOpenAI(api_key=self.api_key)
        batch = self.build_batch_request(messages)
        
        # Оценка токенов (грубо: ~4 символа = 1 токен)
        estimated_tokens = sum(len(msg) for msg in messages) // 4 + 500
        
        if not self.token_budget.can_consume(estimated_tokens):
            print(f"⚠️ Token budget exceeded: {self.token_budget.consumed_today} used today")
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
                
            except asyncio.TimeoutError:
                print(f"⏱️ OpenAI timeout (attempt {attempt + 1}/{max_retries + 1})")
            except Exception as e:
                print(f"🔴 OpenAI error (attempt {attempt + 1}/{max_retries + 1}): {e}")
            
            if attempt < max_retries:
                delay = (attempt + 1) * 2
                print(f"⏳ Waiting {delay}s before next attempt...")
                await asyncio.sleep(delay)
        
        print(f"❌ All {max_retries + 1} attempts failed")
        return None
    
    # ===== 3.2.4. ВАЛИДАЦИЯ ОТВЕТА =====
    
    def validate_response(self, response: Dict) -> Optional[BatchAIResponse]:
        """Проверяет ответ OpenAI через Pydantic-модель BatchAIResponse."""
        try:
            validated = BatchAIResponse.model_validate(response)
            return validated
        except Exception as e:
            print(f"❌ Invalid AI response format: {e}")
            return None
    
    # ===== 3.2.5. FALLBACK ПРИ ОШИБКЕ =====
    
    def create_fallback_response(self, messages: List[str]) -> BatchAIResponse:
        """Создаёт «пустой» ответ согласно экшену из .env, если OpenAI лежит."""
        results = []
        action = self.fallback_action
        
        for i in range(min(len(messages), 20)):
            score = 0.0
            if action == "pass_all":
                score = self.threshold
            
            results.append(ClassificationResult(
                message_index=i,
                relevance_score=score,
                market_segment=None,
                geo_focus=None,
                object_data=ObjectData()
            ))
        
        return BatchAIResponse(results=results)
    
    # ===== ОСНОВНОЙ МЕТОД =====
    
    async def classify_batch(self, messages: List[str]) -> BatchAIResponse:
        """Главный метод: классифицирует batch сообщений с каскадом защиты."""
        response = await self.classify(messages)
        
        if response is None:
            print("🔄 Using fallback: AI unavailable")
            return self.create_fallback_response(messages)
        
        validated = self.validate_response(response)
        if validated is None:
            print("🔄 Using fallback: invalid response format")
            return self.create_fallback_response(messages)
        
        return validated