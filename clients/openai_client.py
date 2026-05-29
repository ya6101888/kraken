"""
KRAKEN OpenAI Client — AI-классификатор сигналов.

Фаза 3: ИНТЕГРАЦИИ
Модуль: 3.2 openai_client.py
Версия: v5.2.7 (SRE 5.0 ATOMIC RESILIENCE — GOLDEN MASTER)
Дата/Время стабилизации: 2026-05-29 22:20:00 UTC

Принципы SRE 5.0 Canon:
- ГЕНИАЛЬНО = ПРОСТО = СИСТЕМА.
- Атомарный батчинг (Размер чанка = 1): абсолютная защита от обрыва JSON-строк.
- Изоляция отказов: падение парсинга одного сообщения не останавливает общий конвейер.
- Снижение температуры (0.1) и жесткое ограничение токенов вывода (800) для скорости.
"""

import os
import json
import asyncio
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict
from dotenv import load_dotenv

# ===== ПУТИ И ОКРУЖЕНИЕ (ОНБОРДИНГ ДЖУНОВ) =====
# Проверяем, где лежит файл конфигурации (.env). Кракен может запускаться
# как локально на машине разработчика (VS Code), так и внутри Docker-контейнера на Debian.
env_path = Path("/opt/kraken/secrets/.env")
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv(Path(__file__).parent.parent.parent / "secrets" / ".env")

# Системный проброс корня проекта в runtime-пути Python (sys.path)
sys.path.insert(0, str(Path(__file__).parent.parent))


def log_sre(message: str):
    """Каноничный потокобезопасный логгер. Не буферизирует вывод, пишет сразу."""
    sys.stdout.write(f"[{datetime.now().isoformat()}] {message}\n")
    sys.stdout.flush()


class TokenBudget:
    """
    SRE-Предохранитель (Rate-Limiter).
    Защищает корпоративный кошелек и API-аккаунт от внезапного выгорания баланса.
    """
    def __init__(self, daily_limit: int = 500_000):
        self.daily_limit = daily_limit
        self.consumed_today = 0
        self.reset_date = datetime.now().date()
    
    def _check_reset(self):
        """Сбрасывает счетчик потребления при наступлении новых суток."""
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
    """Компонент семантического скоринга и обогащения сырых Telegram-постов."""
    
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("AI_MODEL_NAME", "gpt-4o-mini")
        
        # Экстремально низкая температура (0.1) гарантирует жесткое следование 
        # JSON-схеме и убивает "творческие галлюцинации" модели
        self.temperature = 0.1
        
        # Короткий лимит токенов (800) ускоряет генерацию ответа OpenAI до пары секунд
        self.max_tokens = 800
        
        # Индивидуальный таймаут на одну транзакцию — 20 секунд (вместо 90 секунд Шурика)
        self.timeout = 20
        self.retry_attempts = int(os.getenv("AI_RETRY_ATTEMPTS", "2"))
        self.token_budget = TokenBudget()
        self.platinum_prompt = self._load_prompt()
        self._async_client = None
    
    @property
    def client(self):
        """Ленивая инициализация асинхронного клиента OpenAI через прокси-шлюз Squid."""
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
                log_sre(f"🛡️ OpenAI Client: Корпоративный шлюз Squid активирован -> {proxy_ip}:{proxy_port}")
                self._http_client = httpx.AsyncClient(proxy=proxy_url, timeout=self.timeout)
                self._async_client = AsyncOpenAI(api_key=self.api_key, http_client=self._http_client)
            else:
                log_sre("⚠️ OpenAI Client: Прямое подключение без прокси (Контур уязвим к блокировкам!)")
                self._async_client = AsyncOpenAI(api_key=self.api_key)
                
        return self._async_client
    
    def _load_prompt(self) -> str:
        """Многоуровневый поиск файла промпта на диске хоста и внутри Docker."""
        paths = [
            Path("/opt/kraken/prompts/platinum_prompt.txt"),
            Path("/app/prompts/platinum_prompt.txt"),
            Path(__file__).parent.parent / "prompts" / "platinum_prompt.txt",
        ]
        for prompt_path in paths:
            if prompt_path.exists():
                log_sre(f"📖 Система Обсервации: Системный промпт успешно загружен из: {prompt_path}")
                return prompt_path.read_text(encoding="utf-8")
        raise FileNotFoundError("❌ Критическая ошибка: Файл prompts/platinum_prompt.txt отсутствует на диске!")

    async def classify_batch(self, messages: List[str]) -> Optional[Dict]:
        """
        ФИЛЬТР ВЫСШЕГО ПОРЯДКА (АТОМАРНЫЙ ТАНК)
        
        Пункт ТЗ v1.2: Обработать ВСЕ входящие посты без потерь пачек данных.
        
        Реализация: Метод принимает массив строк и обрабатывает КАЖДОЕ сообщение
        индивидуально (chunk_size = 1). Это исключает синтаксические клинчи JSON.
        """
        if not self.api_key:
            log_sre("⚠️ Критическая ошибка: OPENAI_API_KEY не задан в конфигурации .env")
            return None
        
        if not messages:
            return {"signals": []}

        log_sre(f"🧠 ИИ-Файрволл: Запуск АТОМАРНОЙ изоляции для {len(messages)} сообщений.")
        
        combined_signals = []
        client = self.client

        # ===== ЦИКЛ ПОШТУЧНОЙ ИЗОЛЯЦИИ ТРАНЗАКЦИЙ =====
        for idx, msg in enumerate(messages):
            # Входящая санитизация: принудительно выпрямляем опасные символы риелторов,
            # меняем двойные кавычки на одинарные, убираем ломающие JSON переносы строк.
            clean_in = msg.replace('"', "'").replace('\n', ' ').replace('\r', ' ').strip()
            
            # Собираем DTO-запрос для ИИ
            batch_request = [{"message_index": 0, "content": clean_in[:4000]}]
            estimated_tokens = len(clean_in) // 4 + 1000
            
            if not self.token_budget.can_consume(estimated_tokens):
                log_sre(f"⚠️ Пост #{idx + 1}: Пропуск транзакции — исчерпан суточный лимит токенов.")
                continue

            # Индивидуальный цикл ретраев для конкретного сообщения
            max_retries = self.retry_attempts
            for attempt in range(max_retries + 1):
                try:
                    response = await asyncio.wait_for(
                        client.chat.completions.create(
                            model=self.model,
                            messages=[
                                {
                                    "role": "system", 
                                    "content": self.platinum_prompt + "\n\nSTRICT JSON RULE: Return a strict JSON object according to schema. Inside text fields, NEVER use double quotes, use single quotes instead."
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
                    raw_content = response.choices[0].message.content
                    
                    # Парсинг ответа. Так как чанк короткий (1 пост), JSON будет идеальным
                    data = json.loads(raw_content)
                    
                    # Накопление результата
                    if "signals" in data and isinstance(data["signals"], list) and data["signals"]:
                        # Маппинг индекса: возвращаем оригинальный индекс сообщения в пачке Harvester
                        for sig in data["signals"]:
                            sig["message_index"] = idx
                        combined_signals.extend(data["signals"])
                    
                    # Визуальный маркер успешного пролета в терминале для контроля SRE
                    sys.stdout.write(".")
                    sys.stdout.flush()
                    break # Транзакция успешна, выходим из цикла ретраев сообщения
                    
                except json.JSONDecodeError:
                    # Если модель всё же умудрилась выдать битый JSON — делаем быстрый ретрай
                    if attempt < max_retries:
                        await asyncio.sleep(2)
                    else:
                        sys.stdout.write("x")
                        sys.stdout.flush()
                except Exception as e:
                    # Амортизатор непредвиденных сетевых сбоев прокси-сервера
                    if attempt < max_retries:
                        await asyncio.sleep((attempt + 1) * 3)
                    else:
                        sys.stdout.write("x")
                        sys.stdout.flush()
                        
        # Закрываем строчку статус-маркеров в логе
        sys.stdout.write("\n")
        sys.stdout.flush()
        
        log_sre(f"✅ Атомарный раунд завершен. Извлечено {len(combined_signals)} валидных сигналов из {len(messages)} сообщений.")
        return {"signals": combined_signals}