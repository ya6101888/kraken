"""
Squid
🦑 KRAKEN v5.2.3 — Главный входной файл.

Фаза 5: ОРКЕСТРАЦИЯ
Модуль: main.py
Версия: v5.2.3 (GOLDEN MASTER SRE 5.0)
"""

import sys
import asyncio
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

# Выпрямляем пути рантайма
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, "/app")

from core.config import settings

# Глобальный стейт (Immutable DNA)
start_time: datetime = datetime.now()
channel_manager = None
engine = None
trigger = None
beacon_obj = None
storage_writer = None
heartbeat_task = None
beacon_health_task = None
beacon_watcher_task = None


def log_sre(message: str):
    """Потокобезопасное логирование SRE 5.0 с принудительным сбросом буфера Docker."""
    sys.stdout.write(f"[{datetime.now().isoformat()}] {message}\n")
    sys.stdout.flush()


# ===== 5.1.1. LIFESPAN CONTEXT =====

@asynccontextmanager
async def lifespan(app: FastAPI):
    global channel_manager, engine, trigger, beacon_obj
    global storage_writer, heartbeat_task, beacon_health_task, beacon_watcher_task
    
    # ========== STARTUP ==========
    log_sre("============================================================")
    log_sre("🦑 KRAKEN v5.2.3 GOLDEN ASSEMBLY STARTING...")
    log_sre(f"   Environment: {settings.INFRA_ENVIRONMENT}")
    log_sre(f"   Interval: {settings.KRAKEN_CRON_INTERVAL_MINUTES} min")
    log_sre("============================================================")
    
    # 1. Инициализация Telegram клиента
    from clients.telegram_client import TelegramClientManager
    log_sre("📡 Initializing Telegram client...")
    await TelegramClientManager.init_client()
    log_sre("✅ Telegram client ready")
    
    # 2. Инициализация Channel Manager
    from core.channel_manager import ChannelManager
    log_sre("📋 Loading channel registry from Google Sheets...")
    channel_manager = ChannelManager()
    await channel_manager.ensure_fresh_cache()
    active_count = len(channel_manager.get_active_channels())
    log_sre(f"✅ Channel manager ready: {active_count} active channels loaded")
    
    # 3. Инициализация Storage Writer
    from core.storage_writer import StorageWriter
    log_sre("💾 Initializing storage writer & buffer layer...")
    storage_writer = StorageWriter()
    log_sre("✅ Storage writer ready")
    
    # 4. Инициализация Engine
    from core.engine import Engine
    log_sre("🎯 Initializing main processing engine...")
    engine = Engine(channel_manager)
    log_sre("✅ Engine ready")

    # 5. Инициализация Набатного Бикона (С каноничным подключением узлов)
    from core.beacon import Beacon
    log_sre("🚨 Initializing SRE Beacon system...")
    beacon_obj = Beacon(None, None)
    beacon_obj._engine = engine
    beacon_obj._channel_manager = channel_manager
    log_sre("✅ Beacon configuration injected successfully")
    
    # 6. Запуск фоновых контуров наблюдения
    log_sre("🔄 Activating background observability loops...")
    heartbeat_task = asyncio.create_task(TelegramClientManager.heartbeat_loop())
    beacon_health_task = asyncio.create_task(beacon_obj.health_check_loop())
    beacon_watcher_task = asyncio.create_task(beacon_obj.watcher_loop())
    
    # 7. Запуск CRON-планировщика
    from core.trigger import Trigger
    log_sre("⏰ Starting CRON scheduler loop...")
    trigger = Trigger(engine.run_cycle)
    trigger.start()
    log_sre(f"✅ CRON scheduler active (interval={settings.KRAKEN_CRON_INTERVAL_MINUTES}min)")
    
    # Гарантированный сквозной набат. Если прокси лежит — мы увидим ошибку при старте!
    try:
        log_sre("📤 Sending synchronous startup contract alert to Telegram...")
        await beacon_obj.send_alert(
            severity="INFO",
            message="🚨 KRAKEN v5.2.3 RELEASE LIVE\n\n• Статус: СЕТЬ СТАБИЛЬНА\n• Контракт: Вложенный DTO v1.2 активирован\n• Контур: Точка входа main.py стабилизирована\n\n⚙️ Деталь пошла по конвейеру, Архитектор!"
        )
        log_sre("🟢 [SRE] Стартовый набат успешно доставлен через шлюз Squid!")
    except Exception as beacon_err:
        log_sre(f"⚠️ [WARNING] Стартовый набат заблокирован шлюзом: {beacon_err}")

    log_sre("============================================================")
    log_sre("🏆 KRAKEN v5.2.3 GOLDEN MASTER ИГРАЕТ ВДЛИННУЮ! КОНТУР ГОТОВ!")
    log_sre("============================================================")
    
    yield  # Рантайм сервиса
    
    # ========== SHUTDOWN ==========
    log_sre("============================================================")
    log_sre("🛑 KRAKEN SHUTTING DOWN (GRACEFUL SHUTDOWN TRIGGERED)...")
    log_sre("============================================================")
    
    if trigger:
        trigger.stop()
        log_sre("✅ CRON scheduler stopped")
    
    for task in [heartbeat_task, beacon_health_task, beacon_watcher_task]:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    log_sre("✅ Background loops terminated")
    
    if storage_writer:
        await storage_writer.flush()
        log_sre("✅ Local memory buffer flushed to Google Sheets")
    
    from clients.telegram_client import TelegramClientManager
    try:
        await TelegramClientManager.disconnect()
        log_sre("✅ Disconnected from Telegram core network")
    except Exception as e:
        log_sre(f"⚠️ Disconnect error: {e}")
    
    log_sre("✅ Graceful shutdown completed. Deck is clear.")


# ===== FastAPI Инициализация =====

app = FastAPI(
    title=" Squid KRAKEN ETL Service",
    version="5.2.3",
    description="Сборщик сигналов недвижимости из Telegram с AI-фильтрацией стандарта SRE 5.0",
    lifespan=lifespan
)


@app.get("/")
async def root_ping():
    return {
        "status": "ok",
        "service": "KRAKEN",
        "version": "5.2.3",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "version": "5.2.3",
        "timestamp": datetime.now().isoformat(),
        "uptime_seconds": (datetime.now() - start_time).total_seconds()
    }


@app.get("/ready")
async def readiness_check():
    from clients.telegram_client import TelegramClientManager
    client = await TelegramClientManager.get_instance()
    
    checks = {
        "telegram_connected": client.is_connected() if client else False,
        "session_valid": await client.is_authorized() if client and client.is_connected() else False
    }
    
    if all(checks.values()):
        return {"status": "ready", "checks": checks}
    else:
        raise HTTPException(status_code=503, detail={"status": "not ready", "checks": checks})


@app.get("/metrics")
async def metrics():
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
async def system_status():
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


@app.post("/flush")
async def manual_buffer_flush():
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level=settings.INFRA_LOG_LEVEL.lower()
    )