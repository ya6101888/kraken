"""
🦑 KRAKEN v5.2.3 — Главный входной файл.

Фаза 5: ОРКЕСТРАЦИЯ
Модуль: main.py

Принципы:
- FastAPI с lifespan (STARTUP / SHUTDOWN)
- Все компоненты инициализируются ОДИН раз при старте
- Graceful shutdown: остановка CRON, отключение Telegram, flush буфера
- Health check endpoints: /health, /ready, /metrics
"""

import sys
import asyncio
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

# Добавляем /app в путь (для Docker) или корень проекта (для Windows)
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, "/app")  # Docker-контейнер

from core.config import settings

# Глобальные переменные (инициализируются в lifespan)
start_time: datetime = datetime.now()
channel_manager = None
engine = None
trigger = None
beacon_obj = None
storage_writer = None
heartbeat_task = None
beacon_health_task = None
beacon_watcher_task = None


# ===== 5.1.1. LIFESPAN =====

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Жизненный цикл приложения.
    
    STARTUP: инициализация всех компонентов.
    SHUTDOWN: корректное завершение всех процессов.
    """
    global channel_manager, engine, trigger, beacon_obj
    global storage_writer, heartbeat_task, beacon_health_task, beacon_watcher_task
    
    # ========== STARTUP ==========
    print("=" * 60)
    print(f"🦑 KRAKEN v5.2.3 starting...")
    print(f"   Environment: {settings.INFRA_ENVIRONMENT}")
    print(f"   Interval: {settings.KRAKEN_CRON_INTERVAL_MINUTES} min")
    print("=" * 60)
    
    # 1. Инициализация Telegram клиента
    from clients.telegram_client import TelegramClientManager
    print("📡 Initializing Telegram client...")
    await TelegramClientManager.init_client()
    print("✅ Telegram client ready")
    
    # 2. Инициализация Channel Manager
    from core.channel_manager import ChannelManager
    print("📋 Loading channel registry...")
    channel_manager = ChannelManager()
    await channel_manager.ensure_fresh_cache()
    active_count = len(channel_manager.get_active_channels())
    print(f"✅ Channel manager ready: {active_count} active channels")
    
    # 3. Инициализация Storage Writer
    from core.storage_writer import StorageWriter
    print("💾 Initializing storage writer...")
    storage_writer = StorageWriter()
    print("✅ Storage writer ready")
    
    # 4. Инициализация Engine
    from core.engine import Engine
    print("🎯 Initializing engine...")
    engine = Engine(channel_manager)
    print("✅ Engine ready")

    # 5. Инициализация Beacon (ДО тяжёлых компонентов!)
    from core.beacon import Beacon
    print("🚨 Initializing beacon...")
    beacon_obj = Beacon(None, None)  # Ленивая инициализация — engine и channel_manager будут добавлены позже
    beacon_obj._engine = engine  # Подключаем engine после его создания
    beacon_obj._channel_manager = channel_manager  # Подключаем channel_manager
    
    # Стреляем стартовым алертом в первую секунду жизни контейнера!
    asyncio.create_task(beacon_obj.alert("INFO", "🦑 KRAKEN v5.2.3 запущен и вышел в дозор!"))
    print("✅ Beacon ready")
    
    # 6. Запуск фоновых задач
    print("🔄 Starting background tasks...")
    
    heartbeat_task = asyncio.create_task(
        TelegramClientManager.heartbeat_loop()
    )
    
    beacon_health_task = asyncio.create_task(
        beacon_obj.health_check_loop()
    )
    
    beacon_watcher_task = asyncio.create_task(
        beacon_obj.watcher_loop()
    )
    
    # 7. Запуск CRON-планировщика
    from core.trigger import Trigger
    print("⏰ Starting CRON scheduler...")
    trigger = Trigger(engine.run_cycle)
    trigger.start()
    print(f"✅ CRON scheduler started (interval={settings.KRAKEN_CRON_INTERVAL_MINUTES}min)")
    
    print("=" * 60)
    print("✅ KRAKEN is ready!")
    print("=" * 60)
    
    # Отправляем алерт о старте
    # await beacon_obj.alert("INFO", "KRAKEN started")  # TODO: fix Beacon Bot API
    # Безопасный асинхронный алерт без блокировки lifespan
    asyncio.create_task(beacon_obj.alert("INFO", "🦑 KRAKEN v5.2.3 запущен и вышел в дозор!"))    
    
    yield  # Приложение работает здесь
    
    # ========== SHUTDOWN ==========
    print("=" * 60)
    print("🛑 KRAKEN shutting down...")
    print("=" * 60)
    
    # 1. Останавливаем CRON
    if trigger:
        trigger.stop()
        print("✅ CRON scheduler stopped")
    
    # 2. Останавливаем фоновые задачи
    for task in [heartbeat_task, beacon_health_task, beacon_watcher_task]:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    print("✅ Background tasks stopped")
    
    # 3. Flush буфера
    if storage_writer:
        await storage_writer.flush()
        print("✅ Buffer flushed")
    
    # 4. Отключаем Telegram
    from clients.telegram_client import TelegramClientManager
    try:
        await TelegramClientManager.disconnect()
        print("✅ Disconnected from Telegram")
    except Exception as e:
        print(f"⚠️ Disconnect error: {e}")
    
    # 5. Финальный алерт
    # if beacon_obj:
    #     await beacon_obj.alert("INFO", "KRAKEN shutdown complete")
    
    print("✅ Graceful shutdown complete")


# ===== FASTAPI APP =====

app = FastAPI(
    title="🦑 KRAKEN ETL Service",
    version="5.2.3",
    description="Сборщик сигналов недвижимости из Telegram с AI-фильтрацией",
    lifespan=lifespan
)


# ===== 5.2.1. HEALTH CHECK =====

# ===== КОРНЕВОЙ ЭНДПОИНТ ДЛЯ МОНИТОРИНГА =====
@app.get("/")
async def root_ping():
    """Ответ на опрос админского мониторинга трафика"""
    return {
        "status": "ok",
        "service": "KRAKEN",
        "version": "5.2.3",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
async def health_check():
    """Базовый health check для балансировщика."""
    return {
        "status": "ok",
        "version": "5.2.3",
        "timestamp": datetime.now().isoformat(),
        "uptime_seconds": (datetime.now() - start_time).total_seconds()
    }


# ===== 5.2.3. READINESS CHECK =====

@app.get("/ready")
async def readiness_check():
    """Проверка готовности (для K8s readiness probe)."""
    from clients.telegram_client import TelegramClientManager
    
    client = await TelegramClientManager.get_instance()
    
    checks = {
        "telegram_connected": client.is_connected(),
        "session_valid": await client.is_authorized() if client.is_connected() else False
    }
    
    all_ok = all(checks.values())
    
    if all_ok:
        return {"status": "ready", "checks": checks}
    else:
        raise HTTPException(status_code=503, detail={"status": "not ready", "checks": checks})


# ===== 5.2.2. METRICS =====

@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


# ===== 5.3. STATUS =====

@app.get("/status")
async def system_status():
    """Детальный статус системы."""
    return {
        "version": "5.2.3",
        "environment": settings.INFRA_ENVIRONMENT,
        "start_time": start_time.isoformat(),
        "uptime_seconds": (datetime.now() - start_time).total_seconds(),
        "cycle_counter": trigger.cycle_counter if trigger else 0,
        "next_run": str(trigger.next_run_time) if trigger else None,
        "active_channels": len(channel_manager.get_active_channels()) if channel_manager else 0,
        "telegram": beacon_obj.health_status.get("telegram", "UNKNOWN") if beacon_obj else "UNKNOWN",
        "sheets": beacon_obj.health_status.get("sheets", "UNKNOWN") if beacon_obj else "UNKNOWN",
        "session": beacon_obj.health_status.get("session", "UNKNOWN") if beacon_obj else "UNKNOWN",
    }

# ===== 5.4. РУЧНОЙ СБРОС БУФЕРА (КРАСНАЯ КНОПКА SRE 5.0) =====

@app.post("/flush")
async def manual_buffer_flush():
    """Принудительно сбрасывает накопительный буфер сигналов в Google Sheets."""
    if engine and hasattr(engine, '_writer') and engine._writer:
        try:
            await engine._writer.flush()
            return {
                "status": "success",
                "message": "Буфер успешно сброшен в Google Sheets на ходу!",
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ошибка сброса буфера: {e}")
    else:
        return {
            "status": "ignored",
            "message": "Буфер пуст или StorageWriter еще не инициализирован движком."
        }

# ===== ТОЧКА ВХОДА =====

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level=settings.INFRA_LOG_LEVEL.lower()
    )