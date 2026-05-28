FROM python:3.13-slim

LABEL maintainer="ya6101888@gmail.com"
LABEL version="5.2.3"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash kraken

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app

RUN mkdir -p /app/sessions /app/secrets /app/logs /app/dlq /app/prompts \
    && chown -R kraken:kraken /app

USER kraken

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]