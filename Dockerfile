FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends procps && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir curl_cffi pillow discord.py requests

COPY core_engine.py antiban.py sniper.py vinted_sniper.py multi_sniper.py bot.py healthcheck.py fraud_signals.py logger.py /app/
COPY tools /app/tools

