FROM ${PYTHON_IMAGE:-python:3.11-slim}

LABEL maintainer="ya6101888@gmail.com"
LABEL version="5.3.2"

# Прокси для apt и pip (во время сборки)
ENV HTTP_PROXY=http://192.168.0.1:3128
ENV HTTPS_PROXY=http://192.168.0.1:3128
ENV http_proxy=http://192.168.0.1:3128
ENV https_proxy=http://192.168.0.1:3128

# Устанавливаем ca-certificates через прокси
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Создаём пользователя
RUN useradd --create-home --shell /bin/bash kraken

WORKDIR /app

# Копируем зависимости и устанавливаем их (с прокси)
COPY requirements.txt .
RUN pip install --no-cache-dir --proxy http://192.168.0.1:3128 -r requirements.txt

# Копируем код
COPY . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Создаём папки
RUN mkdir -p /app/sessions /app/secrets /app/logs /app/dlq /app/prompts \
    && chown -R kraken:kraken /app

USER kraken

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]