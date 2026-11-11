# Sondas de la auditoría del 2026-11-11

Scripts de evidencia del [plan maestro](../2026-11-11-plan-maestro.md). Se ejecutan **siempre** así:

- en un contenedor efímero **sin red** (`--network none`);
- con el código montado en **solo lectura** en `/src`;
- sobre una **copia** de la BD en `/work/test.db`;
- **sin `.env`**: no hay claves de IA ni webhooks.

Ninguna sonda envía notificaciones, llama a APIs externas o escribe en la BD de producción.

## Ejecución

Cuando exista el arnés de T1.0:

```bash
cd /opt/wallapop-sniper
tools/run_isolated.sh docs/audits/2026-11-11-sondas/probe_filters.py
```

Mientras tanto, con el comando equivalente:

```bash
cd /opt/wallapop-sniper
WORK=$(mktemp -d); mkdir -p "$WORK/kits" "$WORK/logs"
python3 -I -c "import sqlite3,sys; s=sqlite3.connect('file:data/sniper.db?mode=ro',uri=True); d=sqlite3.connect(sys.argv[1]); s.backup(d)" "$WORK/test.db"
docker run --rm --network none --user "$(id -u):$(id -g)" -v "$PWD":/src:ro -v "$WORK":/work -w /work \
  -e DB_PATH=/work/test.db -e CONFIG_PATH=/src/config.json -e KITS_DIR=/work/kits -e LOG_DIR=/work/logs \
  -e HOME=/work -e TZ=Europe/Madrid -e PYTHONPATH=/src -e PYTHONDONTWRITEBYTECODE=1 \
  --entrypoint python boti-sniper:v4.1 /src/docs/audits/2026-11-11-sondas/probe_filters.py
rm -rf "$WORK"
```

## Sondas

| Script | Resultado obtenido el 2026-11-11 (commit `56221b4`) |
|---|---|
| `probe_filters.py` | **Filtros:** pasan 3 de 3 juegos/mandos y se descartan 9 de 9 anuncios legítimos. **Similitud del embedding:** 0,962 entre dos Switch OLED distintas y 0,041 entre dos textos casi iguales. **Tiempos:** `passes_filters` 0,36 ms; embedding 11,9 ms; `find_semantic_duplicate` 7,3 ms |
| `probe_runtime.py` | **D)** ntfy: `UnicodeEncodeError` y 0 peticiones. **E)** PS5 con lector recibe la ficha Digital (372 € frente a 437 €). **F)** vendedor no consultado → 0 valoraciones → aviso silenciado. **G)** eBay: 1.234,56 → 234,56. **H)** un 429 lanza 2 peticiones. **I)** un 400 de Discord: 2 POST y ningún log |
| `probe_headers.py` | La búsqueda XHR envía `Sec-Fetch-Mode: navigate` / `Dest: document`; las estadísticas de vendedor envían `User-Agent: Mozilla/5.0` |
| `probe_logger.py` | `metrics.jsonl`: 0 líneas perdidas con 1 proceso; con 5, entre el 11,6 % y el 20,1 % de 2.000 (varía en cada ejecución) |
| `console_classifier_eval.py` | Prototipo de T1.2: atrapa el 57,2 % de 1.755 negativos (61,6 % con la regla de Switch 2) y rechaza el 2,1 % de 336 positivos, que son exactamente las 7 alertas basura conocidas: 0 consolas reales perdidas |

`console_classifier_eval.py` y `probe_runtime.py` leen `data/logs/` y la BD copiada. Los números cambiarán a medida que crezcan los datos.
