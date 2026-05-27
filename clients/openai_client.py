"""
KRAKEN OpenAI Client — AI-классификатор сигналов.

Фаза 3: ИНТЕГРАЦИИ
Модуль: 3.2 openai_client.py
Версия: v5.2.3 (GOLDEN СБОРКА)
"""

import os
import json
import asyncio
import re
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
        
        # HTTPX-клиент и OpenAI-клиент (ленивая инициализация)
        self._http_client = None
        self._async_client = None
    
    # ===== ЛЕНИВАЯ ИНИЦИАЛИЗАЦИЯ OPENAI КЛИЕНТА С ПРОКСИ =====
    
    @property
    def client(self):
        """Ленивая синглтон-инициализация OpenAI клиента с поддержкой HTTPX-прокси."""
        if self._async_client is None:
            from openai import AsyncOpenAI
            import httpx
            
            proxy_ip = os.getenv("TG_PROXY_IP", "")
            proxy_port = os.getenv("TG_PROXY_PORT", "")
            proxy_user = os.getenv("TG_PROXY_USER", "")
            proxy_pass = os.getenv("TG_PROXY_PASS", "")
            
            if proxy_ip and proxy_port:
                if proxy_user and proxy_pass:
                    proxy_auth = f"{proxy_user}:{proxy_pass}@"
                else:
                    proxy_auth = ""
                proxy_url = f"http://{proxy_auth}{proxy_ip}:{proxy_port}"
                print(f"🛡️ OpenAI Client: routing through proxy {proxy_ip}:{proxy_port}")
                self._http_client = httpx.AsyncClient(proxy=proxy_url, timeout=self.timeout)
                self._async_client = AsyncOpenAI(api_key=self.api_key, http_client=self._http_client)
            else:
                self._async_client = AsyncOpenAI(api_key=self.api_key)
        return self._async_client
    
    # ===== 3.2.1. ЗАГРУЗКА PLATINUM PROMPT =====
    
    def _load_prompt(self) -> str:
        """Загружает Platinum Prompt из файла."""
        paths = [
            Path("/app/prompts/platinum_prompt.txt"),
            Path(__file__).parent.parent / "prompts" / "platinum_prompt.txt",
        ]
        
        for prompt_path in paths:
            if prompt_path.exists():
                return prompt_path.read_text(encoding="utf-8")
        
        print("⚠️ Platinum prompt file not found, using built-in")
        return self._get_default_prompt()
    
    def _get_default_prompt(self) -> str:
        """Встроенный промпт с жесткой фиксацией корневого ключа results"""
        return """Ты — AI-классификатор сигналов недвижимости KRAKEN.
Ты ОБЯЗАН ответить строго JSON-объектом, у которого есть единственный корневой ключ "results", содержащий массив объектов.

Структура ответа:
{
  "results": [
    {
      "message_index": 0,
      "relevance_score": 0.95,
      "market_segment": "PRIMARY",
      "geo_focus": "ROSTOV_CITY",
      "object_data": {
        "price": 5200000,
        "address": "ул. Ленина, 45",
        "rooms": 2,
        "area": 54.5,
        "floor": "5/17",
        "developer": "ГК СМУ-1",
        "completion_date": "2026-12"
      }
    }
  ]
}

Правила классификации:
- market_segment: PRIMARY/SECONDARY/RENT/INVEST/PRO или null
- geo_focus: ROSTOV_CITY/ROSTOV_REGION/SOUTHERN_DISTRICT/FEDERAL или null
- Все поля внутри object_data строго optional и ставятся в null, если точных данных нет."""
    
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
        Использует синтаксис openai>=1.0.0 (AsyncOpenAI) + HTTPX-прокси.
        """
        if not self.api_key:
            print("⚠️ OPENAI_API_KEY not set, using fallback")
            return None
        
        client = self.client
        batch = self.build_batch_request(messages)
        
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
    
    # ===== 3.2.4. ВАЛИДАЦИЯ ОТВЕТА С АДАПТЕРОМ НОРМАЛИЗАЦИИ =====

    def validate_response(self, response: Dict) -> Optional[BatchAIResponse]:
        """
        Проверяет ответ OpenAI через Pydantic-модель BatchAIResponse.
        Защищает пайплайн от капризов модели.
        """
        try:
            if not isinstance(response, dict):
                print(f"❌ Критической сбой: OpenAI вернул не словарь, а {type(response)}")
                return None

            # КЕЙС 1: Модель выдала поля одиночного ClassificationResult прямо в корень
            if "results" not in response and "message_index" in response:
                print("⚠️ Адаптер KRAKEN: OpenAI вынес поля в корень. Заворачиваем...")
                response = {"results": [response]}

            # КЕЙС 2: Массив внутри другого ключа
            elif "results" not in response:
                for key, value in response.items():
                    if isinstance(value, list):
                        print(f"⚠️ Адаптер KRAKEN: Массив найден в ключе '{key}'. Перемапливаем...")
                        response = {"results": value}
                        break

            # === АДАПТЕР KRAKEN: Лечим капризы OpenAI перед валидацией ===
            if "results" in response and isinstance(response["results"], list):
                for item in response["results"]:
                    if isinstance(item, dict) and item.get("object_data"):
                        od = item["object_data"]
                        if not isinstance(od, dict):
                            continue
                        
                        # 1. Принудительный каст этажа в строку
                        if "floor" in od and od["floor"] is not None:
                            od["floor"] = str(od["floor"])
                        
                        # 2. Нормализация дат вида "июнь 2026 г" -> "2026-06"
                        if "completion_date" in od and od["completion_date"]:
                            cd_str = str(od["completion_date"]).strip()
                            if not re.match(r"^\d{4}-\d{2}$", cd_str):
                                year_match = re.search(r"\d{4}", cd_str)
                                months = {
                                    "янв":1,"фев":2,"мар":3,"апр":4,"май":5,"июн":6,
                                    "июл":7,"авг":8,"сен":9,"окт":10,"ноя":11,"дек":12
                                }
                                found_month = 1
                                for m_name, m_num in months.items():
                                    if m_name in cd_str.lower():
                                        found_month = m_num
                                        break
                                if year_match:
                                    od["completion_date"] = f"{year_match.group(0)}-{found_month:02d}"
                                else:
                                    od["completion_date"] = None

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