# ===== KRAKEN v5.2.3 — Docker Image =====
# Базовый образ: Python 3.13 на Debian Slim (лёгкий)
FROM python:3.13-slim

# Метаданные
LABEL maintainer="ya6101888@gmail.com"
LABEL version="5.2.3"
LABEL description="KRAKEN ETL — сборщик сигналов недвижимости из Telegram"

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Для fcntl (lock-файл)
    build-essential \
    # Для корректной работы с SSL
    ca-certificates \
    # Очистка кэша apt для уменьшения размера образа
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Создание непривилегированного пользователя (безопасность)
RUN useradd --create-home --shell /bin/bash kraken

# Рабочая директория
WORKDIR /app

# Копирование зависимостей (отдельно от кода — для кэширования слоёв)
COPY requirements.txt .

# Установка Python-зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY src/ ./src/

# Создание директорий для монтирования томов
RUN mkdir -p /app/sessions /app/secrets /app/logs /app/dlq /app/prompts \
    && chown -R kraken:kraken /app

# Переключение на непривилегированного пользователя
USER kraken

# Порт, который слушает приложение
EXPOSE 8000

# Health check (каждые 30 секунд)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Команда запуска
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]