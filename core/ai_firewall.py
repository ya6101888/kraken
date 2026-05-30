"""
KRAKEN AI Firewall — Фильтрация сигналов и отсечка шума.

Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.5 ai_firewall.py
Версия: v5.3.0 (SRE 5.0 CLEAN SHIELD — МАТРИЦА v1.2)
Дата/Время стабилизации: 2026-05-30 16:15:00 UTC
"""

import os
import sys
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

# Настройка путей рантайма
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

env_path = Path("/opt/kraken/secrets/.env")
if env_path.exists():
    load_dotenv(env_path)

from models.signal import ApprovedSignal, MarketSegment, GeoFocus


class AIFirewall:
    """Служебный щит гигиены данных. Проверяет пороговые метрики релевантности ИИ."""
    
    def __init__(self):
        # Порог отсечки шума. По умолчанию 0.70 по канону ТЗ
        self.threshold = float(os.getenv("AI_RELEVANCE_THRESHOLD", "0.70"))
    
    def is_approved(self, score: float) -> bool:
        """Бинарный фильтр прохождения порога ценности лида."""
        return score >= self.threshold

    def filter_signals(self, signals: List[ApprovedSignal]) -> List[ApprovedSignal]:
        """
        Проводит финальную SRE-фильтрацию готовых плоских сигналов.
        Выжигает объекты, не прошедшие валидацию по relevance_score.
        """
        approved_pool: List[ApprovedSignal] = []
        
        for signal in signals:
            if self.is_approved(signal.relevance_score):
                approved_pool.append(signal)
            else:
                sys.stdout.write(
                    f"[{datetime.now().isoformat()}] 🛡️ AI Firewall DROP: "
                    f"{signal.signal_id} из-за низкого score ({signal.relevance_score:.2f} < {self.threshold})\n"
                )
                sys.stdout.flush()
                
        return approved_pool