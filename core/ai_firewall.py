"""
KRAKEN AI Firewall — Классификация сигналов через GPT.

Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.5 ai_firewall.py
Версия: v5.2.3

Принципы:
- Batch-запросы к OpenAI (до 20 сообщений)
- Порог релевантности ≥ 0.7 → APPROVED
- Fallback при ошибке GPT: skip / pass_all / block_all
- На выходе: List[ApprovedSignal]
"""

import os
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

env_path = Path("/app/secrets/.env")
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv(Path(__file__).parent.parent.parent / "secrets" / ".env")

from models.signal import (
    SanitizedMessage,
    ApprovedSignal,
    ClassificationResult,
    BatchAIResponse,
    ObjectData
)


class AIFirewall:
    """
    Фильтрует сообщения через OpenAI GPT-4o-mini.
    
    Использование:
        firewall = AIFirewall()
        approved = await firewall.classify_batch(sanitized_messages)
    """
    
    def __init__(self):
        # Порог релевантности из .env (по умолчанию 0.7)
        self.threshold = float(os.getenv("AI_RELEVANCE_THRESHOLD", "0.7"))
        
        # Действие при ошибке GPT
        self.fallback_action = os.getenv("AI_FALLBACK_ON_ERROR", "skip")
        
        # OpenAI клиент (создаётся лениво)
        self._client = None
    
    @property
    def client(self):
        """Ленивая инициализация OpenAI клиента."""
        if self._client is None:
            from clients.openai_client import OpenAIClient
            self._client = OpenAIClient()
        return self._client
    
    # ===== 4.5.7. ПОРОГ РЕЛЕВАНТНОСТИ =====
    
    def is_approved(self, score: float) -> bool:
        """
        Проверяет, проходит ли сигнал по порогу релевантности.
        
        Args:
            score: Оценка от GPT (0.0 - 1.0)
        
        Returns:
            True если score >= threshold.
        """
        return score >= self.threshold
    
    # ===== 4.5.2. BATCH-КЛАССИФИКАЦИЯ =====
    
    async def classify_batch(
        self,
        messages: List[SanitizedMessage]
    ) -> List[ApprovedSignal]:
        """
        Классифицирует batch сообщений через GPT.
        Версия: v5.2.3 (GOLDEN СБОРКА)
        """
        if not messages:
            return []

        all_approved: List[ApprovedSignal] = []
        # Снижаем до 5 штук, чтобы OpenAI не обрезал JSON по лимиту max_tokens!
        batch_size = 5

        for i in range(0, len(messages), batch_size):
            batch = messages[i:i+batch_size]
            
            # Извлекаем только очищенный текст
            contents = [msg.cleaned_content for msg in batch]

            # Отправляем в OpenAI
            response = await self.client.classify_batch(contents)

            # Обрабатываем результаты
            for result in response.results:
                # Защита от выхода за границы массива, если AI вернул кривой индекс
                if result.message_index >= len(batch):
                    continue
                    
                msg = batch[result.message_index]

                if self.is_approved(result.relevance_score):
                    try:
                        approved = ApprovedSignal(
                            message_id=msg.message_id,
                            channel_id=msg.channel_id,
                            channel_name=msg.channel_name,
                            content=msg.content,
                            date=msg.date,
                            from_id=msg.from_id,
                            views=msg.views,
                            trace_id=msg.trace_id,
                            collected_at=msg.collected_at,
                            content_hash=msg.content_hash,
                            cleaned_content=msg.cleaned_content,
                            is_rejected=False,
                            relevance_score=result.relevance_score,
                            market_segment=result.market_segment,
                            geo_focus=result.geo_focus,
                            object_data=result.object_data
                        )
                        all_approved.append(approved)
                    except Exception as e:
                        print(f"⚠️ Failed to create ApprovedSignal for msg_{msg.message_id}: {e}")

        print(f"🧠 AI Firewall: {len(messages)} in → {len(all_approved)} approved "
              f"(threshold={self.threshold})")

        return all_approved
    
    # ===== СТАТИСТИКА =====
    
    def get_stats(self) -> dict:
        """Возвращает статистику Firewall."""
        return {
            "threshold": self.threshold,
            "fallback_action": self.fallback_action,
            "token_budget_remaining": self.client.token_budget.remaining if self._client else 0
        }