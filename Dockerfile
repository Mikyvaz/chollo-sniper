FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends procps && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY core_engine.py antiban.py http_client.py sniper.py vinted_sniper.py multi_sniper.py bot.py healthcheck.py fraud_signals.py text_rules.py store_breaker.py logger.py outcome_tracker.py /app/
COPY tools /app/tools

