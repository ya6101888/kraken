"""
KRAKEN AI Firewall — Классификация сигналов через GPT.

Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.5 ai_firewall.py
Версия: v5.2.3 (GOLDEN SRE 5.0 v1.2)
"""

import os
import sys
from pathlib import Path
from typing import List, Dict
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

env_path = Path("/opt/kraken/secrets/.env")
if env_path.exists():
    load_dotenv(env_path)

from models.signal import ApprovedSignal, MarketSegment, GeoFocus, SourcePassport, ObjectDetails

class AIFirewall:
    def __init__(self):
        self.threshold = float(os.getenv("AI_RELEVANCE_THRESHOLD", "0.7"))
        self._client = None
    
    @property
    def client(self):
        if self._client is None:
            from clients.openai_client import OpenAIClient
            self._client = OpenAIClient()
        return self._client
    
    def is_approved(self, score: float) -> bool:
        return score >= self.threshold
    
    async def classify_batch(self, messages: List) -> List[ApprovedSignal]:
        if not messages:
            return []

        all_approved: List[ApprovedSignal] = []
        batch_size = 3  # Консервативный батч по канону SRE

        for i in range(0, len(messages), batch_size):
            batch = messages[i:i+batch_size]
            contents = [msg.cleaned_content for msg in batch]
            
            raw_response = await self.client.classify_batch(contents)
            if not raw_response or "results" not in raw_response:
                print("⚠️ AI Firewall: Empty response or invalid JSON structure from OpenAI client")
                continue

            for res in raw_response["results"]:
                idx = res.get("message_index")
                if idx is None or idx >= len(batch):
                    continue
                    
                msg = batch[idx]
                score = res.get("relevance_score", 0.0)

                if self.is_approved(score):
                    try:
                        # Собираем вложенные DTO v1.2 строго по контракту
                        source_passport = SourcePassport(
                            source_type=res.get("source", {}).get("source_type", "AGENCY"),
                            source_tier=res.get("source", {}).get("source_tier", 3)
                        )
                        
                        obj_raw = res.get("object_data", {})
                        object_details = ObjectDetails(
                            price=obj_raw.get("price"),
                            address=obj_raw.get("address") or msg.address if hasattr(msg, 'address') else obj_raw.get("address"),
                            rooms=obj_raw.get("rooms"),
                            area=obj_raw.get("area"),
                            floor=str(obj_raw.get("floor")) if obj_raw.get("floor") else None,
                            developer=obj_raw.get("developer"),
                            completion_date=obj_raw.get("completion_date")
                        )

                        # Корневой DTO
                        approved = ApprovedSignal(
                            signal_id=f"SIG_{msg.content_hash[:8]}_{msg.message_id}" if hasattr(msg, 'content_hash') else f"SIG_ffffffff_{msg.message_id}",
                            trace_id=msg.trace_id,
                            channel_name=msg.channel_name,
                            message_id=msg.message_id,
                            classification=MarketSegment(res.get("classification", "PRIMARY")),
                            segment_confidence=res.get("segment_confidence", 1.0),
                            source=source_passport,
                            geo=GeoFocus(res.get("geo", "ROSTOV_CITY")),
                            object_data=object_details,
                            original_content=msg.content if hasattr(msg, 'content') else msg.cleaned_content,
                            cleaned_content=msg.cleaned_content,
                            relevance_score=score,
                            collected_at=msg.collected_at if hasattr(msg, 'collected_at') else datetime.now()
                        )
                        all_approved.append(approved)
                    except Exception as e:
                        print(f"⚠️ Failed to assemble ApprovedSignal v1.2 for msg_{msg.message_id}: {e}")

        print(f"🧠 AI Firewall v1.2 End-to-End: {len(messages)} in → {len(all_approved)} approved")
        return all_approved