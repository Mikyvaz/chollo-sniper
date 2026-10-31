FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir curl_cffi pillow discord.py requests

COPY core_engine.py antiban.py sniper.py vinted_sniper.py multi_sniper.py milanuncios_sniper.py cashconverters_sniper.py ebay_sniper.py todocoleccion_sniper.py bot.py /app/
