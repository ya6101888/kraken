"""
KRAKEN Trigger — Планировщик циклов сбора сигналов.

Фаза 4: ЯДЕРНЫЕ КОМПОНЕНТЫ
Модуль: 4.1 trigger.py
Версия: v5.2.3

Принципы:
- APScheduler запускает цикл каждые 15 минут
- Lock-файл защищает от параллельных запусков
- FloodWait → пропуск следующего цикла
- Каждый цикл имеет уникальный trace_id
"""

import os
import sys
try:
    import fcntl  # Только для Linux/Unix
except ModuleNotFoundError:
    fcntl = None  # На Windows не работает
import asyncio
import random
import string
from pathlib import Path
from datetime import datetime
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
env_path = Path("/opt/kraken/secrets/.env")
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv(Path(__file__).parent.parent.parent / "secrets" / ".env")


class Trigger:
    """
    Планировщик циклов сбора KRAKEN.
    
    Использование:
        trigger = Trigger(engine_func)
        trigger.start()
        # Теперь каждые 15 минут будет вызываться engine_func()
    """
    
    def __init__(self, engine_func):
        """
        Args:
            engine_func: async функция, которая принимает trace_id
                         и выполняет полный цикл сбора.
        """
        self.engine_func = engine_func
        self.scheduler = AsyncIOScheduler()
        self.cycle_counter = 0
        self.skip_next_cycle_flag = False
        self.lock_fd: Optional[int] = None
        
        # Интервал из .env (по умолчанию 15 минут)
        self.interval_minutes = int(
            os.getenv("KRAKEN_CRON_INTERVAL_MINUTES", "15")
        )
    
    # ===== 4.1.1. ЗАПУСК ПЛАНИРОВЩИКА =====
    
    def start(self):
        """
        Запускает планировщик.
        
        Добавляет задачу run_cycle, которая будет вызываться
        каждые self.interval_minutes минут.
        """
        self.scheduler.add_job(
            self.run_cycle,
            trigger=IntervalTrigger(minutes=self.interval_minutes),
            id="mining_cycle",
            replace_existing=True,
            max_instances=1  # Не запускать новый цикл, пока старый не завершён
        )
        self.scheduler.start()
        print(f"✅ CRON scheduler started (interval={self.interval_minutes}min)")
    
    def stop(self):
        """Останавливает планировщик."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            print("🛑 Scheduler stopped")
    
    # ===== 4.1.2. LOCK-ФАЙЛ =====
    
    LOCK_PATH = "/tmp/kraken.lock"
    
    async def acquire_lock(self) -> bool:
        """
        Пытается захватить lock-файл.
        
        На Linux: использует fcntl.flock для эксклюзивной блокировки.
        На Windows: создаёт файл и проверяет его существование (менее надёжно).
        
        Returns:
            True если lock захвачен, False если занят.
        """
        try:
            if fcntl is not None:
                # Linux/Unix: используем fcntl.flock
                self.lock_fd = open(self.LOCK_PATH, "w")
                fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                # Windows: простая проверка через создание файла
                if os.path.exists(self.LOCK_PATH):
                    print("⚠️ Lock file exists — skipping")
                    return False
                self.lock_fd = open(self.LOCK_PATH, "w")
            return True
        except (IOError, OSError):
            print("⚠️ Lock file exists — previous cycle still running, skipping")
            if self.lock_fd:
                self.lock_fd.close()
                self.lock_fd = None
            return False
    
    async def release_lock(self):
        """Освобождает lock-файл."""
        if self.lock_fd:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                self.lock_fd.close()
            except Exception:
                pass
            finally:
                self.lock_fd = None
            
            # Удаляем файл, если существует
            try:
                os.unlink(self.LOCK_PATH)
            except FileNotFoundError:
                pass
    
    # ===== 4.1.3. SKIP FLAG =====
    
    async def run_cycle(self):
        """
        Основной метод, вызываемый планировщиком.
        
        1. Проверяет skip_next_cycle_flag (FloodWait)
        2. Захватывает lock-файл
        3. Выполняет цикл сбора
        4. Освобождает lock
        """
        # Проверка флага пропуска (после FloodWait)
        if self.skip_next_cycle_flag:
            print("⏭️ Skipping cycle due to previous FloodWait")
            self.skip_next_cycle_flag = False
            return
        
        # Захват lock-файла
        if not await self.acquire_lock():
            return
        
        try:
            await self._execute_cycle()
        finally:
            await self.release_lock()
    
    # ===== 4.1.4. ГЕНЕРАЦИЯ trace_id =====
    
    def generate_trace_id(self) -> str:
        """
        Генерирует уникальный идентификатор цикла.
        
        Формат: KRAKEN_YYYYMMDD_HHMMSS_xxxxxxxx
        Пример: KRAKEN_20260526_143000_a1b2c3d4
        
        Returns:
            Строка trace_id.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 8 случайных символов: буквы a-f + цифры 0-9
        random_suffix = ''.join(
            random.choices(string.hexdigits.lower()[:16], k=8)
        )
        return f"KRAKEN_{timestamp}_{random_suffix}"
    
    # ===== 4.1.6. ВЫПОЛНЕНИЕ ЦИКЛА =====
    
    async def _execute_cycle(self):
        """
        Выполняет один полный цикл сбора сигналов.
        
        Порядок:
        1. Генерирует trace_id
        2. Вызывает engine_func(trace_id)
        3. При FloodWait — поднимает skip_next_cycle_flag
        4. При ошибке — логирует
        """
        self.cycle_counter += 1
        trace_id = self.generate_trace_id()
        
        print()
        print("=" * 60)
        print(f"🔄 Cycle #{self.cycle_counter} started: {trace_id}")
        print("=" * 60)
        
        try:
            # Вызов основного цикла (engine.run_cycle)
            await self.engine_func(trace_id)
            print(f"✅ Cycle #{self.cycle_counter} completed: {trace_id}")
            
        except Exception as e:
            error_name = type(e).__name__
            print(f"❌ Cycle #{self.cycle_counter} failed: {error_name}: {e}")
            
            # Если FloodWait — пропускаем следующий цикл
            if "FloodWait" in error_name or "flood" in str(e).lower():
                self.skip_next_cycle_flag = True
                print(f"🌊 Next cycle will be skipped due to FloodWait")
            
            # Пробрасываем дальше для логирования
            raise
    
    # ===== ДОПОЛНИТЕЛЬНО: СТАТУС =====
    
    @property
    def is_running(self) -> bool:
        """Запущен ли планировщик."""
        return self.scheduler.running if hasattr(self.scheduler, 'running') else False
    
    @property
    def next_run_time(self):
        """Время следующего запуска."""
        job = self.scheduler.get_job("mining_cycle")
        return job.next_run_time if job else None