# Plan Maestro de Ingeniería — Sniper multiplataforma

**Fecha:** 2026-11-11 · **Rama:** `auditoria-datos-cartera` · **Base:** commit `56221b4` (árbol limpio) · **Ejecutor:** agente de IA codificador

> Las referencias `archivo:Lnnn` corresponden a ese commit. Las líneas se desplazan al editar: localiza siempre por **nombre de función** y usa el número solo como pista.
> Evidencia reproducible en [`2026-11-11-sondas/`](2026-11-11-sondas/README.md).

---

## 0. Reglas de ejecución (obligatorias)

1. **Modo seguro.** Prohibido arrancar `sniper.py`, `vinted_sniper.py`, `multi_sniper.py`, `outcome_tracker.py` o `bot.py` fuera de los contenedores de producción; enviar mensajes a Discord o ntfy; y llamar a Gemini, OpenRouter, Algolia, Wallapop o Vinted desde tests. Toda verificación se hace con `tools/run_isolated.sh` (T1.0): contenedor efímero `--network none`, código en solo lectura, **copia** de la BD y sin `.env`.
2. **Nunca ejecutes tests dentro de los contenedores de producción** (`docker exec relister-bot …`): comparten la BD real y los webhooks. Ya ocurrió: `data/logs/relister-bot.log` contiene la salida de `test_verify_punto6` (2026-11-09 14:27, 17:26 y 17:43).
3. **Desplegar es decisión del usuario.** Prepara el cambio, verifícalo en aislado y pide aprobación antes de `docker compose restart|up`. Ejecuta compose **solo** desde `/opt/wallapop-sniper` (proyecto `wallapop-sniper`). Desde `/home/miky/opt_servidor` el nombre de proyecto cambia y choca con los `container_name`.
4. Siguen vigentes las **invariantes de `AGENTS.md` §5**: sin `thinkingConfig`; enrutado lite/flash con fallback a OpenRouter; fail-open sin cachear; `get_db()` por hilo; normalización de ratings; locks en las clases compartidas. Se añade la **invariante 7: «perfil no consultado» ≠ «0 valoraciones»** (T1.4).
5. **La medición del Nivel 1 está en curso** (ROADMAP, revisión hacia el 2026-11-24). Cada alerta debe guardar `pipeline_version` (T1.0) para poder separar el análisis antes y después de cada despliegue.
6. Cada tarea cerrada se registra en `docs/audits/registro.md` (IDs desde `A-007`; pon la `Tn.m` en la nota), y `ROADMAP.md` se actualiza al cerrar cada fase.
7. Entorno: Python 3.11.18, curl_cffi 0.17.0, Pillow 13.0.0, discord.py 2.7.1, requests 2.34.2 (imagen `boti-sniper:v4.1`). **No hay pytest en la imagen**: los tests nuevos van en `tests/unit/test_*.py` con `unittest`.
8. Tras cada tarea: `python3 -m py_compile core_engine.py bot.py multi_sniper.py fraud_signals.py antiban.py sniper.py vinted_sniper.py outcome_tracker.py logger.py` + la verificación propia de la tarea. Los tests antiguos (`tests/test_*.py`) son scripts: ejecútalos con `run_isolated.sh` **antes y después** del cambio y compara las salidas. No cuentan como regresión los fallos previos conocidos (ROADMAP: `test_verify_task06`, `test_bloque5`, `test_fase5`, `test_task32`, `test_task41`, `test_verify_all`).

---

## 1. Diagnóstico medido

Fuentes: BD copiada con la API de backup (31-oct → 11-nov), `metrics.jsonl` de las últimas 24 h, logs de `data/logs/` y sondas aisladas (Apéndice A).

### 1.1 Latencia

| Escáner | Ronda (mediana) | Espera tras ronda | Ciclo real | Causa dominante |
|---|---|---|---|---|
| Wallapop | 17,5 s (p90 19,3) | 9 s | **≈ 26,5 s** | 26 consultas en serie + `inter_query_sleep` de 0,3–0,6 s (`sniper.py:372`); `requests.get` sin sesión, con TLS nuevo en cada consulta (`sniper.py:196`). IA: 0,02 evaluaciones por ronda, así que **no es el cuello de botella** |
| Vinted | 58,8 s (p90 68,3) | 49 s | **≈ 108 s** | 26 consultas a la página HTML del catálogo + `inter_query_sleep(1.0, 2.0)` (`vinted_sniper.py:608`) + `sleep(1.2–2.3)` antes de cada ficha (`:665`) |
| Multi | 35,3 s | 41 s | ≈ 76 s | Tiendas de rendimiento casi nulo (§1.4) |

- La espera final **se suma** a la duración de la ronda en lugar de descontarse (`sniper.py:784-801`, `vinted_sniper.py:1012-1029`).
- Publicación→alerta en Wallapop (`alert_outcomes`, n = 21): mediana 3,5 min, mínimo 1,9 min (suelo de indexación de la plataforma) y máximo 9,0 min. La cola por encima de unos 2 min depende de nosotros.
- Vinted: 3 de 4 chollos directos desaparecen en menos de 60 min; las alertas de Wallapop acumulan una mediana de 8 conversaciones.

### 1.2 Embudo de IA y coste

- **Gasto:** 0,06–0,09 $ al día en los últimos 3 días. El coste no es el problema. **El cortafuegos de 0,10 $ al día sí lo es:** el 7 y el 8 de noviembre se gastaron 0,26 y 0,25 $, el embudo se pausó y quedaron 131 anuncios sin evaluar (A-001).
- **Puerta 1** rechaza el ~70 % (11-11: 375 *reject* frente a 129 *proceed*). De los 2.564 rechazos en caché, **el 56,6 % son juegos sueltos y el 17,4 % accesorios**: casos que debería cubrir una regla determinista.
- **Visión en Vinted** (1.201 llamadas): el 74 % termina en rechazo visual y, de esos rechazos, el **53 % son juegos o accesorios** que ya habían pasado la Puerta 1.
- **Ruta «pro» `gemini-3.5-flash`:** las 5 últimas llamadas de visión salen con 443–446 tokens de salida (tope 450, agotado por el *thinking*) y 1 s después se repiten con `gemini-3.5-flash-lite` con los mismos `tokens_in`. Se paga dos veces y la respuesta del modelo caro nunca se usa. `gemini-3.8-flash` hizo 83 llamadas por 0,33 $ (hoy ya está bloqueado por `"3.8" in model`, `core_engine.py:825`).
- **Imágenes:** con 1 imagen de 384 px, la visión lite consume ≈ 1.750 tokens de entrada, frente a ≈ 700–800 del texto, es decir, unos 900–1.000 tokens por imagen. Gemini 3.x no factura 258 tokens por tile como la serie 2.x, así que el downscale no ahorra pero sí pierde detalle. Está **pendiente de confirmar** con T1.13.

### 1.3 Precisión

**Sonda A** (`passes_filters` + `evaluate_fraud_risk` con el código y la `config.json` reales, sin red):

| Título (radar, precio) | Resultado | Esperado | Causa |
|---|---|---|---|
| «Mario Kart 8 Deluxe Nintendo Switch» (switch_std_lite, 28 €) | pasa | rechazar | `"nintendo switch"` ∈ `CONSOLE_HARDWARE_WORDS` (`core_engine.py:1786-1793`) |
| «Luigi's mansion 3 Nintendo switch» (35 €) | pasa | rechazar | ídem |
| «Joycon Nintendo switch» (41 €) | pasa | rechazar | El regex de mandos sueltos no cubre «joycon …» |
| «Nintendo Switch V2 completa · incluye joy-con, dock…» (60 €) | **descartada** | pasar | `"joy-con"` en la blacklist del radar |
| «Switch neón 32gb · Sin golpes ni rayadas» (60 €) | **descartada** | pasar | El token `"pes "` es subcadena de «gol**pes** » (`core_engine.py:1810`) |
| «New 3DS XL · Pantalla superior e inferior sin rayadas» (95 €) | **descartada** | pasar | `DEFECT_TOKENS` «pantalla superior» con peso 4, sin IA |
| «Nintendo 3DS XL · …no cargador…» (70 €) | **descartada** | pasar | «no carga» es subcadena de «no carga**dor**» |
| «iPad 9 · Sin bloqueo iCloud» (160 €) | **descartada** | pasar | `"icloud"` en las blacklists global y del radar; «bloqueo icloud» sin negación |
| «PS5 Slim · 2 mandos y 3 juegos ps5. No acepto bizum» (280 €) | **descartada** | pasar | `"bizum"`, `"juegos ps5"` |
| «PS5 slim lector · Regalo mando de ps4» (290 €) | **descartada** | pasar | `"ps4"` |
| «Switch OLED · Joy-con sin problemas con drift» (95 €) | **descartada** | pasar | «con drift» sin negación |
| «Steam Deck 512GB · luz azul de carga ok» (240 €) | **descartada** | pasar | «luz azul» |

- **CeX (sonda E):** tras evaluar un anuncio «digital sin lector», una PS5 Slim **con lector** recibe de la caché la ficha Digital (372 € en efectivo) en lugar de la suya (437 €). La clave `v43:{radar}:{query}:b:l` (`core_engine.py:2493`, `:2662`) ignora el contexto que decide qué ficha se elige.
- **Vendedor (sonda F):** si `/api/v3/users` falla, `reviews=None` se normaliza a 0 (`fraud_signals.py:619`, `:741-742`). Resultado: `SELLER_ZERO_REVIEWS` y aviso silenciado («vendedor sin valoraciones»).
- **Deduplicación semántica:** hay 339 rechazos heredados en los logs (149 entre títulos distintos), con motivos que dependen del precio o del vendedor. El embedding local da 0,962 entre dos Switch OLED distintas y 0,041 entre «Nintendo Switch» y «Nintendo switch / Usada, funciona».
- **Alertas basura:** 7 de 336 alertas de radares de consola (2,1 %) eran juegos o accesorios («Zelda Nintendo 3DS», «FC 25 Nintendo Switch EA Sports»…).
- **eBay (sonda G):** «1.234,56 EUR» se lee como 234,56 €.
- **Valoración:** `optimal_price` es la estimación de la IA siempre que valga más de 0 (`core_engine.py:4184-4189`). Su mediana es 0,67 × PVP de CeX (no infla frente a CeX), pero no tiene cota: 11 alertas superan 0,85 × PVP y 59 no tienen CeX (estimación libre, mediana 1,07 × precio). La oferta recomendada se topa en `direct_buy_max_price`, de ahí el beneficio medio de 89 € en las 390 alertas de regateo.

### 1.4 Red y anti-detección

- **Wallapop:** ≈ 1 petición/s y unos 2 bloqueos 403 al día (7 min). Ante un 429/403, `fetch_wallapop` lanza en el acto una segunda petición a `/general/search` (`sniper.py:198-202`, sonda H).
- **Cabeceras reales capturadas** (sonda de cabeceras):
  - La búsqueda XHR de Wallapop sale con `Sec-Fetch-Mode: navigate`, `Sec-Fetch-Dest: document`, `Sec-Fetch-User: ?1` y `Upgrade-Insecure-Requests: 1` junto a `Origin`. Es incoherente para una petición CORS.
  - `enrich_seller_profile` (stats) envía `User-Agent: Mozilla/5.0` a secas sobre una huella TLS de Chrome 124, con `Accept: text/html` y `Accept-Language: en-US` (`fraud_signals.py:519-520`).
- **Huellas:** `chrome124`, `chrome131` y `safari17_0` (`antiban.py:26-67`) son de 2024, cuando curl_cffi 0.17.0 ya soporta `chrome152`, `chrome152_macos` y `safari260`. El `Dockerfile` instala sin versiones fijadas.
- **Tiendas del multi-sniper:**
  - Milanuncios: entre 65 y 92 bloqueos de WAF al día, con un cooldown fijo de 15 min y un embed de salud en cada bloqueo (`multi_sniper.py:266-277`). 0 alertas en 12 días.
  - Cash Converters: 1.051 rondas al día para 41 anuncios nuevos en 48 h.
  - TodoColección: 2 nuevos en 48 h.
- **Una sola IP residencial** (sin `PROXY_URL`) para 5 contenedores más las comprobaciones de `outcome-tracker`.

### 1.5 Notificaciones

- **ntfy está 100 % roto** (sonda D: `'latin-1' codec can't encode '⚡'`, 0 peticiones enviadas). Además, `NTFY_TOPIC_URL` no está en `.env`.
- **Discord:** un 400 o un 429 se pierde sin dejar log (sonda I) y no se guarda el `message_id` de la alerta.
- Los enlaces `wallapop://` y `vinted://` en el Markdown del embed no son clicables: Discord solo enlaza http(s).
- La primera línea del push, que es lo que se ve en el móvil, no incluye ni modelo ni precio: «✅ ¡APROBADO POR IA (8/10)! SIGUE CON LA COMPRA (+87€ LIMPIOS) @… 👉 ABRIR ANUNCIO: https://…».

### 1.6 Observabilidad e infraestructura

- `metrics.jsonl` lo escriben 5 procesos con `RotatingFileHandler`. En la sonda, con 5 procesos se pierde **entre el 11 y el 20 %** de las líneas al rotar (dos ejecuciones: 11,6 % y 20,1 %; varía según la carrera entre procesos); con 1 proceso, el 0 %.
- `tests/` está montado en los 4 contenedores principales (`docker-compose.yml:26,56,86,118`).
- No hay indicios de fugas de memoria (38–115 MiB por contenedor) ni errores `database is locked`.
- La API key de Gemini viaja en la query string (`core_engine.py:835`) y las excepciones se imprimen con `{e}` (`:862`). Hoy hay 0 apariciones de `key=AIza` en los logs, pero la fuga es cuestión de tiempo.

---

## 2. Contraste con el estándar del sector

| Área | Estándar de un sniper de alto rendimiento | Estado actual | Brecha → tarea |
|---|---|---|---|
| Sesiones | Una sesión persistente por plataforma y perfil, con keep-alive y HTTP/2 | Wallapop: `requests.get` suelto por consulta. Vinted: sesión pegajosa (correcto) | T2.2 |
| Huella | Perfil actual (Chrome estable −1/−2); cabeceras coherentes con el tipo de petición (navegación o XHR) y con el idioma | Chrome 124/131; XHR con cabeceras de navegación; UA «Mozilla/5.0» | T2.2, T2.5 |
| Ritmo | Presupuesto de peticiones por plataforma (*token bucket*), cadencia por consulta según su valor, `Retry-After` y *circuit breaker* | Esperas fijas por consulta y por ronda; cooldown fijo; un 429 duplica la petición | T1.9, T1.10, T2.3 |
| Proxies | Pool residencial o móvil con sesión pegajosa, **solo** si la tasa de bloqueo lo exige | 1 IP; bloqueos bajos salvo Milanuncios | Pospuesto (§7) |
| Concurrencia | Captura desacoplada de la evaluación mediante una cola de prioridad (asyncio o hilos) | Bucle síncrono: captura → IA → alerta en serie | T2.3, T2.6 |
| Dedup de IDs | Set o LRU en memoria con TTL, más persistencia | Clave primaria en SQLite (µs por consulta): suficiente | LRU opcional en T2.6 |
| Embudo de IA | Reglas gratuitas → caché → modelo ligero solo para los casos dudosos | Reglas incompletas (juegos y accesorios) y a la vez demasiado agresivas (sin negaciones) | T1.1, T1.2, T1.13 |
| Valoración | Anclada en datos (tienda o precio de cierre observado), con la estimación del modelo acotada | La IA estima libremente y tiene prioridad sobre CeX | T3.2, T3.3 |
| Notificación | Un toque lleva al anuncio (app link https); lo esencial en la primera línea; sin pérdidas; estado vivo de la alerta | ntfy roto; pérdidas silenciosas; primera línea sin precio | T1.5, T1.12, T3.6, T3.7 |

---

## 3. Orden de ejecución

| ID | Estado | Modelo | Tarea | Impacto esperado | Esfuerzo | Depende de |
|---|---|---|---|---|---|---|
| T1.0 | [x] | — | Arnés aislado + `pipeline_version` | Habilita todas las verificaciones | S | — |
| T1.1 | [x] | — | Matcher con límites de palabra y negación + limpieza de blacklists | Recupera los 9 de 9 legítimos de §1.3 | M | T1.0 |
| T1.2 | [x] | — | Clasificador consola / juego / accesorio | Entre −25 y −35 % de llamadas a la Puerta 1, −20 % de visión en Vinted, −2 % de alertas basura | M | T1.1 |
| T1.3 | [x] | — | CeX: caché por consulta, frescura y caché negativa | Niveles 1 y 2 correctos (65 € de error en la sonda) | M | T1.0 |
| T1.4 | [x] | — | Perfil no consultado ≠ 0 valoraciones | Riesgo y aviso sonoro correctos | S | T1.0 |
| T1.5 | [x] | — | Discord sin pérdidas + `message_id` | 0 alertas perdidas en silencio | M | T1.0 |
| T1.6 | [x] | — | Gemini: detectar `MAX_TOKENS` y presupuesto de salida por modelo | Fin del pago doble | S | T1.0 |
| T1.7 | [x] | — | API key en cabecera + redacción en logs | Seguridad | S | — |
| T1.8 | [ ] | — | Tope mensual 0,10 USD decidido, implementado y probado; revisión de ampliación pendiente | Gasto mensual acorde a decisión del usuario | S | Decisión recibida 2026-11-14 |
| T1.9 | [x] | — | Wallapop: sin segunda petición en 403/429 + `Retry-After` | Menos bloqueos | S | T1.0 |
| T1.10 | [x] | — | *Circuit breaker* exponencial por tienda | −90 % de sondeo al WAF y de spam de salud | S | T1.0 |
| T1.11 | [x] | — | eBay: precios ≥ 1.000 € y precio tachado | Sin falsos candidatos | S | T1.0 |
| T1.12 | [x] | — | ntfy mediante JSON | Canal *1-Tap* operativo | S | T1.0 |
| T1.13 | [x] | — | Visión: salida mínima + tokens por modalidad | Entre −20 y −25 % de tokens de salida; aporta el dato que necesita T3.10 | S | T1.6 |
| T2.1 | [x] | Gemini 3.8 Max | Latencia por etapa + métricas por proceso | Medir; 0 % de métricas perdidas | S | T1.0 |
| T2.2 | [x] | Argon 4 | Capa HTTP coherente (sesiones, huella, cabeceras) | Anti-detección y menos latencia por petición | M | T2.1 |
| T2.3 | [x] | Argon 4 | Cadencia por consulta + *token bucket* | Wallapop de 26 a 20 s; Vinted de 108 a 75 s o menos | M | T2.1, T2.2 |
| T2.4 | [x] | Gemini 3.8 Max | Vinted: ficha y vendedor robustos | No evaluar a ciegas | S | T2.3 |
| T2.5 | [x] | Argon 4 | Vendedor Wallapop: identidad, límite y caché persistente | Menos peticiones y ningún falso «0 valoraciones» | S | T2.2, T1.4 |
| T2.6 | [ ] | Argon 4 | Pipeline común + workers de evaluación | Fin del bloqueo de cabeza de cola | L | T2.1 (condicional) |
| T2.7 | [ ] | Argon 4 | IA: breaker y plazos implementados y probados; falta revisión independiente de la corrección SQLite | 10 s Puerta 1 + 20 s visión en el gateway | S | T1.6 |
| T2.8 | [x] | Argon 4 | Embeds de salud asíncronos + locks | Encolado <5 ms; envío fuera del bucle | S | T1.5 |
| T2.9 | [x] | Gemini 3.8 Max | Frecuencia por tienda en multi-sniper | −70 % de peticiones de bajo valor | S | T1.10 |
| T3.1 | [x] | Gemini 3.8 Max | Dedup sin herencia entre vendedores distintos | Recupera anuncios válidos | S | T1.0 |
| T3.2 | [ ] | Argon 4 | Pre-CeX implementado y probado; revisión independiente pendiente | Replay 372/517 familias (71,95 %) | M | T1.3 |
| T3.3 | [ ] | Argon 4 | Valoración anclada implementada y probada; revisión independiente pendiente | CeX × 0,85 / p75 × 1,15 y confianza explícita | M | T3.2 |
| T3.4 | [x] | Gemini 3.8 Max | Vinted: envío según país | Margen real | S | — |
| T3.5 | [x] | Gemini 3.8 Max | Re-chequeo de reservados | Recupera oportunidades | S | — |
| T3.6 | [x] | Gemini 3.8 Max | Payload: primera línea útil y sin deeplinks rotos | Acción en un toque | S | T1.5, T1.12 |
| T3.7 | [x] | Gemini 3.8 Max | Ciclo de vida de la alerta en Discord | No perseguir anuncios vendidos | S | T1.5 |
| T3.8 | [x] | Gemini 3.8 Max | Resumen diario de alertas silenciosas | Menos ruido | S | — |
| T3.9 | [x] | Gemini 3.8 Max | Muestreo de anuncios bloqueados | Medir los falsos positivos del antifraude | M | — |
| T3.10 | [ ] | Gemini 3.8 Max | A/B de resolución de imagen (código listo; A/B real pendiente aprobación) | Precisión visual a coste constante | S | T1.13 + aprobación |
| T3.11 | [x] | Gemini 3.8 Max | Coherencia de umbrales + `AGENTS.md` | Sin contradicciones entre código y documentación | S | — |

---


> **Continuación 2026-11-14:** T2.8 implementada y revisada con suite aislada (236/236). T2.6 sigue sin activar: en las últimas 48 h, rondas con IA Wallapop 78/2790 (2,80 %), Vinted 115/1273 (9,03 %); no hay evidencia de espera de cola que justifique la condición del plan. Sin despliegue. ROADMAP.md está eliminado en el árbol de trabajo y se conserva ese cambio del usuario.

## 4. Fase 1 — Bugs críticos y ahorro de costes/API

> En euros, el ahorro de esta fase es marginal (el gasto actual es de 0,06–0,09 $ al día). Lo que de verdad aporta es menos latencia por ítem, menos ruido, más margen bajo el cortafuegos y menos peticiones desde la IP compartida.

### T1.0 — Arnés de verificación aislado y marca de versión

**Archivos:** crear `tools/run_isolated.sh`, `tests/unit/_harness.py` y `tests/unit/test_harness.py`; modificar `core_engine.py` (constante `PIPELINE_VERSION` y `save_alert_context` L4865-4895) y `docker-compose.yml` (líneas `./tests:/app/tests:ro` en L26, L56, L86 y L118).

**Problema:** varios tests usan la BD real o llaman a funciones de IA y Discord, y se han ejecutado dentro de `relister-bot`. No existe una forma estándar de verificar sin riesgo. Además, en los datos del Nivel 1 no se puede saber con qué versión del código se generó cada alerta.

**Solución:**
1. `tools/run_isolated.sh`:
   ```bash
   #!/usr/bin/env bash
   # Python en un contenedor efímero SIN RED, con COPIA de la BD y SIN secretos.
   #   tools/run_isolated.sh -m unittest discover -s tests/unit -p 'test_*.py' -v
   set -euo pipefail
   REPO="${REPO:-/opt/wallapop-sniper}"; IMAGE="${IMAGE:-boti-sniper:v4.1}"
   WORK="$(mktemp -d "${TMPDIR:-/tmp}/sniper-iso.XXXXXX")"; trap 'rm -rf "$WORK"' EXIT
   mkdir -p "$WORK/kits" "$WORK/logs"
   if [ -f "$REPO/data/sniper.db" ]; then
     python3 -I - "$REPO/data/sniper.db" "$WORK/test.db" <<'PY'
   import sqlite3, sys
   s = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30); d = sqlite3.connect(sys.argv[2])
   s.backup(d); d.close(); s.close()
   PY
   fi
   docker run --rm --network none --user "$(id -u):$(id -g)" \
     -v "$REPO":/src:ro -v "$WORK":/work -w /src \
     -e DB_PATH=/work/test.db -e CONFIG_PATH=/src/config.json -e KITS_DIR=/work/kits -e LOG_DIR=/work/logs \
     -e HOME=/work -e TZ=Europe/Madrid -e PYTHONPATH=/src -e PYTHONDONTWRITEBYTECODE=1 -e SNIPER_ISOLATED=1 \
     --entrypoint python "$IMAGE" "$@"
   ```
   No se usa `exec`, para que el `trap` borre el directorio temporal. Los logs reales quedan legibles en `/src/data/logs`, en solo lectura.
2. `tests/unit/_harness.py`, que debe importarse primero en cada test nuevo:
   - Elimina del entorno `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `DISCORD_*`, `NTFY_TOPIC_URL` y `TELEGRAM_*`.
   - Hace `assert os.environ.get("SNIPER_ISOLATED") == "1"`.
   - Parchea `socket.socket.connect` para que solo admita loopback (cubre `requests` estándar; a curl_cffi lo cubre `--network none`).
3. `tests/unit/test_harness.py` debe comprobar:
   - `core_engine.DB_PATH.startswith("/work/")`;
   - `core_engine.GEMINI_API_KEY == ""`;
   - que `curl_cffi.requests.get("https://example.com", timeout=3)` lanza una excepción.
4. `core_engine.py`: añadir `PIPELINE_VERSION = "2026.11.11-f1"` junto a las constantes y `"pipeline_version": PIPELINE_VERSION` en el `ctx` de `save_alert_context`. Incrementa la versión en cada despliegue.
5. `docker-compose.yml`: quitar las 4 líneas `./tests:/app/tests:ro`. Exige recrear los contenedores, así que **es un despliegue y necesita aprobación**.

**Verificación:**
```bash
cd /opt/wallapop-sniper && chmod +x tools/run_isolated.sh
tools/run_isolated.sh -m unittest discover -s tests/unit -p 'test_harness.py' -v      # 3 OK
tools/run_isolated.sh -c "import core_engine as c; print(c.PIPELINE_VERSION)"
grep -c 'tests:/app/tests' docker-compose.yml                                        # 0
```

### T1.1 — Matcher con límites de palabra y negación (Puerta 0 y blacklists)

**Archivos:**
- Crear `text_rules.py`, `tests/unit/test_text_rules.py` y `tools/replay_filters.py`.
- Modificar `fraud_signals.py`: `DEFECT_TOKENS` (L74-104), defectos en `evaluate_fraud_risk` (L1024-1033), pagos (L995-1011) y urgencia (L1013-1022).
- Modificar `core_engine.py`: `_token_in_text` (L1831-1837) y `passes_filters` (L1840-1926).
- Modificar `config.json` (blacklists).

**Problema:** la coincidencia por subcadena no tiene en cuenta límites de palabra ni negaciones, y por eso se descartan los 9 de 9 anuncios legítimos de la tabla de §1.3:
- `_token_in_text` solo aplica `\b` a tokens de 4 caracteres o menos sin espacios (L1835-1836).
- Un `DEFECT_TOKENS` pesa 4, lo que bloquea directamente sin pasar por la IA (L1024-1033).
- La negación solo existe para los pagos (L995-1011).
- `passes_filters` llama a `evaluate_fraud_risk` sin vendedor (L1871), así que no hay bonus de confianza y un solo defecto falso basta para bloquear.

**Solución:**
1. `text_rules.py`, sin dependencias ni E/S:
   ```python
   import re, html, unicodedata
   from functools import lru_cache
   NEG_CUE = re.compile(r"\b(sin|no|ni|nunca|ningun[oa]?|cero|libre de|jamas|sans|pas de|aucune?|senza|nessun[oa]?|without|free of)\b")
   CLAUSE_BREAK = ".,;:!?\n"

   def norm(text: str) -> str:
       t = html.unescape(text or "").lower()
       t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
       t = re.sub(r"[’'`´]", "", t)
       return re.sub(r"[ \t\r]+", " ", t)

   @lru_cache(maxsize=8192)
   def _pattern(token: str) -> re.Pattern:
       words = norm(token).strip().split()
       body = r"[\s\-]+".join(re.escape(w) for w in words)
       plural = r"(?:s|es)?" if len(words) == 1 and len(words[0]) >= 4 and words[0][-1].isalpha() else ""
       return re.compile(rf"(?<!\w){body}{plural}(?!\w)")

   def _negated(text_n: str, start: int, window: int = 5) -> bool:
       pre = text_n[max(0, start - 80):start]
       cut = max(pre.rfind(ch) for ch in CLAUSE_BREAK)
       if cut >= 0:
           pre = pre[cut + 1:]
       return bool(NEG_CUE.search(" ".join(pre.split()[-window:])))

   def has_token(text_n: str, token: str, negatable: bool = True) -> bool:
       """True si hay al menos una aparición NO negada. text_n debe venir de norm()."""
       if negatable and NEG_CUE.match(norm(token).strip()):   # "sin joycon", "no enciende" ya son negativos
           negatable = False
       return any(not negatable or not _negated(text_n, m.start()) for m in _pattern(token).finditer(text_n))
   ```
   Criterio de diseño: una negación mal detectada solo envía el anuncio a la IA (cuesta una llamada); un bloqueo falso pierde el chollo. Por eso la ventana es de 5 palabras y se corta en los signos de puntuación.
2. Defectos:
   - Sustituir `if d_token in text` por `has_token(text_n, d_token)`.
   - Sacar de `DEFECT_TOKENS` «pantalla superior», «pantalla inferior» y «luz azul» (se mantiene «pantalla superior a reemplazar»).
   - Añadir reglas contextuales, no negables y con peso 4:
     - `(pantalla|ecran|schermo)( (superior|inferior|de arriba|de abajo))? (rota|partida|rajada|quebrada|danada|con (lineas|manchas))`
     - `luz azul (de la muerte|parpade\w*|intermitente)`
     - `icloud (bloqueado|activo|vinculado)|bloqueado por icloud|con cuenta icloud`
   - Usar el mismo matcher en `EXTERNAL_PAYMENT_TOKENS` (sustituye el `negation_re` ad hoc de L998-1003) y en `URGENCY_SCAM_TOKENS`.
   - A los regex de `CONTACT_EXTERNAL_PATTERNS` aplicarles `_negated` sobre `m.start()` («no tengo whatsapp»).
3. `passes_filters`:
   - Calcular `text_n = norm(text)` una sola vez.
   - Blacklists (L1907-1909): `has_token(text_n, bad, negatable=True)`.
   - `must_contain_any` (L1911-1913) y `submodel_price_caps` (L1916-1924): `negatable=False`.
   - En L1884, buscar los tokens de `VIDEO_GAME_TITLES_AND_TOKENS` con `has_token(token.strip(), negatable=False)`: con límites de palabra, «pes» ya no coincide con «golpes» ni «lego» con «llego». **No modifiques la lista**: también alimenta `CLEAN_GAME_TOKENS`, que usa el matcher de CeX (L1982, L2047-2051). El bloque entero desaparece en T1.2.
4. Cambios en `config.json` (muestra el diff al usuario):

   | Lista | Quitar | Motivo |
   |---|---|---|
   | `switch_std_lite.blacklist` | `"joy-con"`, `"mando switch"`, `"mando pro"` | Descartan consolas completas; los mandos sueltos los cubre T1.2 por título |
   | `ps5_consoles_gta6.blacklist` | `"ps4"`, `"mando ps5"`, `"juego ps5"`, `"juegos ps5"` | Los lotes tienen valor (el *Bundle Engine* los suma) |
   | `apple_high_liquidity.blacklist` y `global_blacklist` | `"icloud"` | Pasa a la regla contextual del punto 2 |

   Los tokens de pago y contacto se quedan, pero pasan a respetar la negación.
5. `tools/replay_filters.py` (se ejecuta con `run_isolated.sh`):
   - Recorre `deals` de los últimos 14 días (título, descripción, `radar_id` y precio, con el radar sacado de `load_config()`) y cuenta cuántos dejan de pasar `passes_filters`.
   - Imprime los casos de §1.3 con el veredicto nuevo.

**Verificación:**
```bash
tools/run_isolated.sh -m unittest discover -s tests/unit -p 'test_text_rules.py' -v
tools/run_isolated.sh tools/replay_filters.py
tools/run_isolated.sh docs/audits/2026-11-11-sondas/probe_filters.py
```
`test_text_rules.py` es una tabla de casos:
- Las filas 4–12 de §1.3 (los 9 legítimos) deben pasar. Las filas 1–3 (juegos y mandos que hoy pasan) se cierran en T1.2.
- Siguen bloqueando: «solo bizum», «pago por bizum», «pantalla superior rota», «joycon drift», «no enciende», «luz azul parpadeando», «icloud bloqueado», «cuenta icloud activa», «para piezas», «busco ps5».
- Pasan: «no acepto bizum ni transferencias» y «envío sin coste, pago dentro de la app».

**Criterios:**
- 9 de 9 legítimos de §1.3 pasan, y todos los casos de «siguen bloqueando» bloquean.
- El replay mantiene el 99 % o más de las `deals` pasando.
- `tests/test_fase3.py`, `tests/test_bloque2.py` y `tests/test_verify_task05.py` dan la misma salida que antes (con `run_isolated.sh`), salvo los casos corregidos.

### T1.2 — Clasificador determinista consola / juego suelto / accesorio suelto

**Archivos:**
- Añadir `classify_console_listing` a `text_rules.py`.
- Modificar `core_engine.py`: bloque `if radar_id in CONSOLE_RADAR_IDS` de `passes_filters` (L1865-1891) y `CONSOLE_HARDWARE_WORDS` (L1786-1793).
- Crear `tests/unit/data/console_neg.json` y `tests/unit/data/console_pos.json` (solo títulos, generados con `docs/audits/2026-11-11-sondas/console_classifier_eval.py <dir>`), `tests/unit/data/known_junk_alerts.txt` y `tests/unit/test_console_classifier.py`.

**Problema:** «nintendo switch», «ps5» y «3ds» forman parte de `CONSOLE_HARDWARE_WORDS`, así que cualquier juego que lleve la plataforma en el título «tiene hardware» (L1884-1887) y pasa el filtro. Coste medido:
- el 56,6 % de los rechazos de la Puerta 1 son juegos y el 17,4 % accesorios;
- en Vinted, el 53 % de los rechazos de visión son juegos o accesorios;
- 7 juegos o accesorios llegaron a alertarse.

Además, la comprobación de L1889-1891 mira «juego/juegos» en la **descripción**, lo que descartaría consolas que dicen «incluye 2 juegos» si la lista de hardware se corrigiera.

**Solución:** la función `classify_console_listing(title, radar_id) -> "console" | "game_only" | "accessory_only" | "out_of_scope" | "unknown"` mira **solo el título** normalizado (`norm` de T1.1). Las reglas son las del prototipo v2, validadas contra datos reales: atrapan el **57,2 %** de 1.755 títulos de juegos o accesorios que hoy rechaza la IA (61,6 % en los radares de Switch con la regla de Switch 2) y **no pierden ninguna** de las 329 consolas reales. Los 7 títulos de alertas que sí rechaza eran alertas basura.
```python
STRONG = re.compile(r"\b(consola|console|konsole|hac-?001|heg-?001|hdh-?001|ctr-?001|spr-?001|red-?001|ktr-?001|jan-?001|pch-?[12]0\d\d|cfi-?[12]\d{3}|ags-?[01]01|oled|lite|v1|v2|slim|digital(e|a)?|lector|xl|ll|new ?3ds|new ?2ds|2ds ?xl|steam ?deck|rog ally|legion go|gba ?sp|advance ?sp|game ?boy micro|dsi ?xl|vita (1000|2000|slim|oled)|(1ere|primera|first|1a) (edicion|edition)|(edicion|edition|edizione) (digital(e)?|standard|estandar|disco|lector|slim|fat))\b")
WEAK = re.compile(r"\b(completa|completo|con caja|caja original|con dock|\d{2,4} ?(gb|go|tb|to)|portatil)\b")
GAME = re.compile(r"\b(juegos?|videojuegos?|jeux?|giochi|gioco|jogos?|games?|cartuchos?|cartouches?|cartucce|cartuccia|cartridge|deluxe|remaster(ed)?|goty|definitive|collection|trilogy|coleccionista|collector|zelda|mario|luigis?|pokemon|kirby|metroid|animal crossing|splatoon|smash|xenoblade|fire emblem|octopath|minecraft|sonic|hogwarts|fifa|fc ?2[4-9]|just dance|ring fit|tomodachi|yo-?kai|monster hunter|disgaea|god eater|jump force|tales of|persona \d|bayonetta|donkey kong|pikmin|layton|inazuma|dragon (ball|quest)|naruto|one piece|call of duty|gta|grand theft auto|red dead|spider-?man|god of war|uncharted|horizon|gran turismo|ghost of|assassins? creed|advance wars)\b")
ACC_FIRST = re.compile(r"^\W*(\d+\s*)?(accesorios?|accessoires?|accessori|joy-?cons?|mandos?|controllers?|manettes?|dualsense|dualshock|pro controller|fundas?|housse|custodia|dock|base de carga|cargador|chargeur|caricatore|volante|protector|carcasa|skin|bolsa|maletin|estuche|soporte|grip|auriculares|casque|cuffie|cable|adaptador|tarjeta|micro ?sd|lote (de )?(juegos|jogos|jeux|giochi)|pack (de )?juegos|lot de jeux)\b")
ACC_ANY = re.compile(r"\b(joy-?cons?|mandos?|controllers?|manettes?|dualsense|dualshock)\b")
BUNDLE = re.compile(r"(\+|&|\b(con|avec|with|pack|lote|bundle|et|y|e|and|plus|mas)\b)")
SWITCH2 = re.compile(r"\bswitch ?2\b")

def classify_console_listing(title, radar_id=""):
    t = norm(title)
    if radar_id in ("switch_std_lite", "switch_oled") and SWITCH2.search(t):
        return "out_of_scope"                      # Switch 2 (consola o juego) no entra en radares de Switch 1
    strong, weak, bundle = bool(STRONG.search(t)), bool(WEAK.search(t)), bool(BUNDLE.search(t))
    if ACC_FIRST.search(t) and not re.search(r"\b(consola|console)\b", t):
        return "accessory_only"
    if GAME.search(t) and not strong and not bundle:
        return "game_only"
    if ACC_ANY.search(t) and not (strong or weak or bundle):
        return "accessory_only"
    return "console" if (strong or weak) else "unknown"
```
Integración en `passes_filters`:
- En los radares de `CONSOLE_RADAR_IDS`, rechazar si el resultado es `game_only`, `accessory_only` u `out_of_scope`.
- Eliminar las comprobaciones de L1884-1887 y L1889-1891, que esta función sustituye.
- Mantener `SPARE_PARTS_AND_COMPONENTS`, el regex de accesorios sueltos (L1865-1868) y `GAME_OR_ACC_STRONG_MARKERS`.
- El resultado `unknown` sigue yendo a la IA, como ahora.

**Verificación:**
```bash
# 1) Congelar los datasets (/src es de solo lectura dentro del contenedor): usa el comando manual de
#    docs/audits/2026-11-11-sondas/README.md SIN el "rm -rf $WORK" final, pasando /work como argumento:
#      ... boti-sniper:v4.1 /src/docs/audits/2026-11-11-sondas/console_classifier_eval.py /work
#    y después: cp "$WORK"/console_neg.json "$WORK"/console_pos.json tests/unit/data/ && rm -rf "$WORK"
# 2) Test y medición
tools/run_isolated.sh -m unittest discover -s tests/unit -p 'test_console_classifier.py' -v
tools/run_isolated.sh docs/audits/2026-11-11-sondas/console_classifier_eval.py
```
**Criterios:**
- Con T1.1 y T1.2 aplicadas, 12 de 12 casos de §1.3 correctos (`probe_filters.py`).
- El 55 % o más de `console_neg.json` sale como `game_only`, `accessory_only` u `out_of_scope`.
- En `console_pos.json`, cualquier título rechazado tiene que estar en `known_junk_alerts.txt`, que contiene exactamente estos 7: «9 juegos de Mario para Nintendo 3DS», «Dragon Ball Sparking Zero Collector PS5», «FC 25 Nintendo Switch EA Sports», «Lotto 6 giochi Nintendo 3DS», «The Legend of Zelda: Tri Force Heroes – Nintendo 3DS», «Volante Logitech G923 con Pedales y Shifter» y «Zelda Nintendo 3DS».
- Casos fijos: «Mario Kart 8 Deluxe Nintendo Switch» → `game_only`; «Joycon Nintendo switch» → `accessory_only`; «Nintendo switch et 3 jeux» → no rechazado; «Ps5 plus 2 manette» → no rechazado; «Ps5 édition digitale» → `console`.
- KPI tras el despliegue (§8): entre −25 y −35 % de llamadas `prescreen` al día.

### T1.3 — CeX: caché por consulta (no por contexto), espejo con frescura y caché negativa

**Archivos:**
- `core_engine.py`: `fetch_cex_live` (L2478-2634), `get_cex_Cached_or_live` (L2637-2724), `lookup_cex_catalog_mirror` (L2379-2475), `sync_cex_catalog_mirror` (L2290-2376), `init_db` (tabla nueva) y `run_db_maintenance` (L196-224).
- `bot.py:2550`, que solo cuenta las filas de `cex_cache`.
- Crear `tests/unit/test_cex_lookup.py`.

**Problema:**
- La caché guarda la **ficha ya elegida** bajo una clave que no incluye el contexto del anuncio. Sin embargo, `_box_matches_query_and_radar` decide la ficha en función de ese contexto (digital/sin lector, slim/1tb, oled, lite, new, xl, 512/256…). Resultado: la ficha del primer anuncio se reutiliza durante 3 días para anuncios distintos (sonda E).
- El espejo se lee sin TTL: 79 de 576 filas tienen más de un día y 9 más de tres (la más antigua es del 2026-11-06).
- No hay caché negativa: cada consulta sin resultados vuelve a Algolia en vivo (timeout de 10 s).
- La misma lógica está duplicada en dos funciones.

**Solución:**
1. Un solo camino, `fetch_cex_live`, que conserve `get_cex_Cached_or_live` como alias compatible (acepta `radar_id` como kwarg):
   1. `candidates = _mirror_candidates(conn, clean_q)`: el AND de tokens de L2405-2432 con `AND last_synced >= datetime('now','-36 hours')`.
   2. Si no hay candidatos, `candidates = _algolia_hits_cached(conn, clean_q)`. Usa una tabla nueva:
      ```sql
      CREATE TABLE IF NOT EXISTS cex_query_hits (query TEXT PRIMARY KEY, hits_json TEXT NOT NULL, fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
      ```
      La entrada vale 24 h si hay hits y 6 h si `hits_json='[]'` (caché negativa). Si no está fresca, se consulta Algolia (L2552-2564) y se guardan los **hits crudos normalizados** (`box_name`, `category`, `cash`, `voucher`, `sell`, `box_id`, `in_stock_online`).
   3. En **cada** llamada se filtra y puntúa con el contexto real (`_box_matches_query_and_radar` + `_score_cex_candidate`), en memoria y en menos de 1 ms para 50 candidatos o menos.
   4. Este flujo deja de leer y escribir `cex_cache`.
2. `sync_cex_catalog_mirror`: al terminar, `DELETE FROM cex_catalog_mirror WHERE last_synced < datetime('now','-7 days')`.
3. `run_db_maintenance`: `DELETE FROM cex_query_hits WHERE fetched_at < datetime('now','-2 days')`.
4. `bot.py:2550`: cuenta `cex_query_hits` en lugar de `cex_cache`.

**Verificación:** `tests/unit/test_cex_lookup.py`, con BD temporal y `init_db(conn)` bajo `mock.patch("core_engine.threading.Thread")` para no lanzar la sincronización:
1. Espejo con «Playstation 5 Slim Digital, 825GB, …, Sin Caja» (efectivo 372, categoría «PS5 Consolas») y «Playstation 5 Slim, 1TB, Blanca, Sin Caja» (437), ambas frescas. Llamada A con contexto «digital sin lector» → ficha Digital. Llamada B con «con lector» → ficha 1TB (regresión de la sonda E).
2. Filas del espejo con 3 días → se ignoran y se recurre a Algolia (mock).
3. Algolia simulado devuelve `[]` → una segunda llamada dentro de las 6 h no vuelve a llamar a Algolia (el mock se invoca 1 vez).
```bash
tools/run_isolated.sh -m unittest discover -s tests/unit -p 'test_cex_lookup.py' -v
tools/run_isolated.sh docs/audits/2026-11-11-sondas/probe_runtime.py   # bloque E: B -> "Playstation 5 Slim, 1TB…"
```

### T1.4 — «Perfil no consultado» ≠ «0 valoraciones»

**Archivos:**
- `fraud_signals.py`: `enrich_seller_profile` (L617-671) y `evaluate_seller_risk` (L733-742, L801-847, L884-885).
- `core_engine.py`: `_seller_review_count` (L4740-4749), `decide_alert_push` (L4752-4777) y `format_seller_line` (L4791-4822).
- `AGENTS.md` §5 (invariante 7).
- Crear `tests/unit/test_seller_unknown.py`.

**Problema:**
- Sonda F: cuando la API no responde, L619 y L741-742 convierten `None` en 0 → +2 por `SELLER_ZERO_REVIEWS`, sin flash y con el aviso sonoro silenciado.
- Milanuncios no expone reputación, así que suma +2 siempre.
- Un 403 en `/users`, más probable con las cabeceras de §1.4, degrada la evaluación de todos los vendedores.

**Solución:**
1. `enrich_seller_profile`:
   - Dejar `None` cuando no se obtuvo el dato.
   - Añadir `profile["seller_profile_status"]` con estos valores: `"ok"`, `"unavailable"` (error HTTP o red) o `"not_applicable"` (MILANUNCIOS, TODOCOLECCION, o EBAY sin feedback parseado). CASHCONVERTERS da `"ok"`.
   - `item_data["seller_reviews_count"]` puede ser `None`.
   - El rating vale `None` cuando las valoraciones son 0 o `None`.
2. `evaluate_seller_risk`:
   - Valoraciones `None` y estado `unavailable` → hit `SELLER_PROFILE_UNKNOWN` (peso 1, `block_flash=True`, etiqueta «Perfil del vendedor no verificado: la plataforma no respondió»).
   - Estado `not_applicable` → ningún hit por reputación.
   - `SELLER_ZERO_REVIEWS`, `SELLER_NEW_ACCOUNT_ZERO_REVIEWS`, `HIGH_TICKET_ZERO_REP_SCAM_RISK` y `SELLER_GHOST_PROFILE` solo se aplican con un `reviews_count == 0` **confirmado**.
3. `decide_alert_push`: un `reviews is None` no silencia. `format_seller_line` muestra «valoraciones no disponibles».
4. Revisar los `int(x or 0)` que leen `seller_reviews_count` en los tres escáneres (`sniper.py:489`, `vinted_sniper.py:703`, `multi_sniper.py:356`): con `None` el resultado es 0 y no hay flash, que es lo correcto. Se documenta sin cambiar código.
5. Añadir a `AGENTS.md` §5 la invariante 7.

**Verificación:** con `mock.patch("fraud_signals._get_thread_local_session")`:
- Si `get` lanza una excepción: `seller_reviews_count is None`, el estado es `unavailable`, los hits incluyen `SELLER_PROFILE_UNKNOWN` y no `SELLER_ZERO_REVIEWS`, y `decide_alert_push(conn,"WALLAPOP","direct_chollo",90,seller=…)[0] is True`.
- Si responde 200 con `{"stats":{"reviews":{"total":0}}}` → aparece `SELLER_ZERO_REVIEWS`.

```bash
tools/run_isolated.sh -m unittest discover -s tests/unit -p 'test_seller_unknown.py' -v
tools/run_isolated.sh docs/audits/2026-11-11-sondas/probe_runtime.py   # bloque F: reviews=None, push=True
```

### T1.5 — Discord: no perder alertas, validar el payload y guardar `message_id`

**Archivos:** `core_engine.py`: nueva función `_discord_send`, `send_discord_alert` (L5564-5599), `send_flash_discord_alert` (L5145-5178) y `resolve_flash_discord_alert` (L5220-5256). Crear `tests/unit/test_discord_send.py`.

**Problema:**
- Sonda I: un 400 genera 2 POST y ninguna línea de log.
- Un 429 no se reintenta con `retry_after`.
- No se guarda el `message_id` de la alerta, lo que bloquea T3.7.
- Si `clean_item_url` está vacío, el `embed.url` o un botón quedan sin URL válida y Discord responde 400.

**Solución:**
```python
def _discord_send(method, url, *, headers=None, json_payload=None, multipart=None, timeout=10, attempts=3):
    """Devuelve (ok: bool, status: int|None, message_id: str|None).
    2xx -> ok. 429 -> dormir min(retry_after, 5) y reintentar. 5xx/red -> backoff 0.5 s, 1.5 s.
    4xx != 429 -> sin reintento: print(f"[DISCORD ERROR] {status} {_redact(resp.text[:300])}") + log_metric("discord_error", ...)."""
```
- `retry_after` sale del JSON (`retry_after`) o de la cabecera `Retry-After`.
- **Fallback mínimo:** si el envío con embed falla con 400, reintenta una vez con `{"content": push_notification_content[:1900]}`, sin embed ni componentes.
- `_valid_http(u)` exige `u.startswith(("http://","https://"))`. `embed["url"]` solo se rellena si la URL es válida y los botones de estilo 5 sin URL válida se eliminan.
- El webhook se usa como respaldo solo si el bot falla de forma definitiva. Ante un timeout ambiguo, se prefiere un posible duplicado a perder la alerta, y se registra.
- Tras un envío correcto, `UPDATE deals SET alert_context_json = json_set(COALESCE(NULLIF(alert_context_json,''),'{}'), '$.discord_message_id', ?, '$.discord_channel_id', ?) WHERE deal_id = ?`.

**Verificación:** con `mock.patch.object(core_engine.HTTP_SESSION, "post")`:
1. Respuestas [400, 200] → el segundo envío es solo `content`, el resultado es OK y la salida contiene `[DISCORD ERROR] 400`.
2. [429 con `{"retry_after":0.01}`, 200] → OK con 2 llamadas.
3. 200 con `{"id":"123"}` → `deals.alert_context_json` contiene `"discord_message_id":"123"` (BD temporal).
4. `item_url=""` → el embed enviado no lleva `url` y no hay botones con URL vacía.

```bash
tools/run_isolated.sh -m unittest discover -s tests/unit -p 'test_discord_send.py' -v
```

### T1.6 — Gemini: detectar el truncado (`MAX_TOKENS`) y presupuesto de salida por modelo

**Archivos:** `core_engine.py`: `ai_gateway_call` (topes en L789-799 y parseo en L838-856), `analyze_and_generate_kit` (L3894-3899 y L3928), `log_ai_cost` (L568-582) e `init_db` (columnas nuevas). Crear `tests/unit/test_ai_gateway.py`.

**Problema:** según §1.2, la ruta flash se trunca siempre y obliga a llamar a lite un segundo después. El `maxOutputTokens` ≤ 450/500 incluye el *thinking* de los modelos no lite; además, `finishReason` no se lee y el fallo pasa inadvertido.

**Solución** (respeta las invariantes 1 y 2):
1. Leer `finish = candidates[0].get("finishReason")` y guardarlo en `ai_cost_log`:
   - Columnas nuevas `finish_reason TEXT` y `tokens_thoughts INTEGER`, añadidas con `ALTER TABLE` en `init_db`, tolerante a columna duplicada.
   - `log_ai_cost(..., finish_reason=None)`.
2. Con `finish == "MAX_TOKENS"`:
   - `print(f"[AI_GATEWAY] MAX_TOKENS {model} out={c_tokens} thoughts={thoughts_tokens}")`;
   - no parsear la respuesta parcial y pasar al siguiente modelo.
3. Presupuesto de salida por modelo Gemini:
   - modelos **no lite**: `maxOutputTokens = 2048`, para que quepan el *thinking* y el JSON;
   - modelos lite: los topes actuales (prescreen 200, resto 500);
   - OpenRouter, sin cambios.
   - **Nunca** añadir `thinkingConfig`.
4. Se mantiene el enrutado de `AGENTS.md` (flash para lotes complejos de 150 € o más). Se añade el interruptor `config.json → "ai": {"pro_model_enabled": true}`; ponerlo a `false` es decisión del usuario, porque toca la invariante 2.

**Verificación:** con `mock.patch("core_engine.HTTP_SESSION.post")`, flash devuelve `{"candidates":[{"finishReason":"MAX_TOKENS","content":{"parts":[{"text":"{\"is_valid_deal\": tr"}]}}],"usageMetadata":{"promptTokenCount":1800,"candidatesTokenCount":40,"thoughtsTokenCount":410}}` y lite devuelve JSON válido. Comprobar:
- el resultado es el de lite;
- `ai_cost_log` registra `finish_reason='MAX_TOKENS'` para flash;
- el payload de flash lleva `maxOutputTokens == 2048` y el de lite 500 o menos;
- `"thinkingConfig" not in payload["generationConfig"]` en todas las llamadas.

```bash
tools/run_isolated.sh -m unittest discover -s tests/unit -p 'test_ai_gateway.py' -v
```
Tras el despliegue: §8, KPI «visión flash con `MAX_TOKENS`».

### T1.7 — API key de Gemini fuera de la URL y redacción en logs

**Archivos:** `core_engine.py`: L835-837 (URL con `?key=`), L860-863 (prints) y helper `_redact`. Aplicar `_redact` también en los prints de error de red de `antiban.py` y `fraud_signals.py`.

**Problema:** la key viaja en la query string. Una excepción de red de `requests` (por ejemplo, `ConnectionError` o `MaxRetryError`) incluye la URL completa y se imprime con `{e}` en `data/logs/*.log`.

**Solución:**
- `url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"`, con `headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}`.
- `_redact(s) = re.sub(r"(key=|Bearer\s+|Bot\s+|/webhooks/\d+/)[A-Za-z0-9._\-]+", r"\1***", str(s))`, aplicado en todos los `print` de errores de IA, Discord y ntfy.

**Verificación:**
- Test: la URL de `call_args` del mock no contiene `key=` y la cabecera `x-goog-api-key` sí está presente.
- `_redact("https://x/y?key=AIzaXYZ") == "https://x/y?key=***"`.
- `grep -n 'key={GEMINI_API_KEY}' core_engine.py` no devuelve nada.

### T1.8 — Cortafuegos de presupuesto de IA: tope mensual y día local

**Archivos:** `.env` y `.env.example` (`AI_MONTHLY_BUDGET_USD`, `AI_DAILY_BUDGET_USD`), y en `core_engine.py`, contabilidad y comprobaciones de presupuesto del gateway; `tests/unit/test_monthly_budget.py`.

**Problema:**
- Con 0,10 $ al día el embudo ya se pausó una vez (A-001). El gasto actual roza el tope y las Fases 1 y 2 aumentarán el volumen evaluado.
- El día se corta a medianoche UTC (`date('now','start of day')`), una hora antes que en Madrid.

**Solución:**
- **Decisión del usuario (2026-11-14): 0,10 USD al mes.** Sustituye la propuesta anterior de 0,50 USD/día. `AI_MONTHLY_BUDGET_USD=0.10`; conservar el límite diario adicional sin aumentarlo.
- Contar el mes civil en `Europe/Madrid`, con límites UTC y cambio de mes sin esperar al TTL. El gateway relee el gasto mensual antes de cada proveedor/fallback y después del semáforo; si falta contabilidad, pausar.
- Contar el día local: `WHERE created_at >= datetime('now','localtime','start of day','utc')`.
- Memorizar el gasto del día durante 60 s, para no ejecutar un `SUM` en cada llamada.
- Mantener el aviso al 80 %.

**Verificación:** test con BD temporal y `TZ=Europe/Madrid`. Un coste registrado a las 23:30 UTC del día anterior (00:30 en Madrid) cuenta como «hoy», y `check_ai_budget_status` devuelve `PAUSED` al superar el tope.

Ampliación mensual: 9/9 tests nuevos; suite final conjunta 263/263 OK. `.env` guarda solo la nueva asignación mensual, sin mostrar secretos ni reiniciar. Límite basado en la contabilidad estimada del proyecto, comprobado antes de llamar; las llamadas ya en curso pueden terminar y el coste final facturado depende del proveedor. La copia de BD registra aproximadamente 1,13 USD este mes: al desplegar quedará pausada la IA hasta el próximo mes. Falta revisión independiente de esta ampliación (cuota de subagentes agotada).

### T1.9 — Wallapop: no duplicar la petición ante 403/429 y respetar `Retry-After`

**Archivos:** `sniper.py` (`fetch_wallapop`, L196-215) y `antiban.py` (`handle_http_response`, L361-412).

**Problema:** sonda H: un 429 provoca 2 peticiones, porque cualquier estado distinto de 200 activa el fallback a `/general/search` (L198-202).

**Solución:**
- El fallback solo se usa con `status in (400, 404, 410)`.
- Con 401, 403 o 429, ir directamente a `health_monitor.handle_http_response(status, ..., retry_after=…)` y devolver `[]`.
- Ampliar `handle_http_response(status_code, url="", query="", retry_after=None)` para que fije `cooldown_until = max(cooldown_until, now + retry_after)` cuando la cabecera venga en segundos.

**Verificación:** test con `mock.patch.object(sniper.requests, "get")` y con `sniper.health_monitor.send_health_embed` parcheado:
- 429 → 1 llamada;
- 404 → 2 llamadas;
- 429 con `Retry-After: 900` → `get_remaining_cooldown() >= 890`.

### T1.10 — *Circuit breaker* exponencial por tienda (Milanuncios)

**Archivos:** `multi_sniper.py` (L266-277 y `STORE_PIPELINE` en L55-104), `config.json` (bloque nuevo `stores`). Crear `store_breaker.py` y `tests/unit/test_store_breaker.py`.

**Problema:**
- Entre 65 y 92 bloqueos de WAF al día: el cooldown fijo de 900 s (L267) hace que DataDome se vuelva a sondear cada 15 min durante las 24 h.
- Se envía un embed de salud en cada bloqueo (L270-276).
- Milanuncios no ha dado ni una alerta en 12 días y desgasta la reputación de la IP compartida.

**Solución:**
```python
class StoreBreaker:
    BASE, CAP = 900, 12 * 3600
    def __init__(self, path): ...                 # JSON en os.path.dirname(DB_PATH)/store_breakers.json, escritura tmp + os.replace
    def allow(self, store) -> bool: ...           # time.time() >= open_until
    def record_block(self, store) -> tuple[int, bool]:   # (cooldown_s, notify): failures += 1; cd = min(BASE * 2 ** (failures - 1), CAP)
    def record_success(self, store) -> bool: ...  # True si se recupera de un estado abierto (notificar)
```
- Solo se notifica a Discord en tres momentos: el primer bloqueo, al alcanzar el `CAP` y al recuperarse.
- `config.json` admite `"stores": {"milanuncios": {"enabled": true}}`. Recomendar al usuario `false`, pero es decisión suya.

**Verificación:**
- Test con reloj falso: cooldowns de 900, 1800, 3600… hasta un máximo de 43200; un éxito lo reinicia; el estado sobrevive a una nueva instancia; 6 bloqueos seguidos y una recuperación generan como mucho 3 notificaciones.
- Tras el despliegue, el recuento diario de `COOLDOWN PREVENTIVO` en `data/logs/multi-sniper.log` debe quedar en 8 o menos.

### T1.11 — eBay: precios de 1.000 € o más y precio tachado

**Archivos:** `core_engine.py`: `fetch_ebay_es` (precio en L3164-3170, envío en L3179-3184) y helper `_parse_eur_amount`.

**Problema:** sonda G: «1.234,56 EUR» se lee como 234,56 € y pasa los filtros de los radares baratos.

**Solución:**
```python
_EUR_RE = re.compile(r"(\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d+\.\d{2})\s*(?:EUR|€)")
def _parse_eur_amount(s):
    s = s.strip()
    return float(s.replace(".", "").replace(",", ".")) if "," in s else float(s)
```
Usar la primera coincidencia dentro de un elemento cuyo `class` contenga `price` y no contenga `strikethrough`, `previous` ni `was`. Si no la hay, la primera coincidencia de `_EUR_RE` en la tarjeta. Aplicar lo mismo al envío.

**Verificación:** test con `_ebay_mgr.get_session` simulado (el HTML de la sonda G):
- «1.234,56 EUR» con rango 50–400 → 0 resultados; con rango 1000–2000 → 1234.56;
- «123,45 EUR» → 123.45;
- «99.99 €» → 99.99;
- un precio tachado de 150,00 antes de 120,00 → 120.

### T1.12 — ntfy: publicación en JSON (UTF-8) con *click* al anuncio

**Archivos:** `core_engine.py`: `send_one_tap_ntfy_alert` (L1501-1564) y `.env.example` (`NTFY_TOPIC_URL`, `NTFY_TOKEN` opcional).

**Problema:** sonda D: `http.client` codifica las cabeceras en latin-1, así que «⚡» (o cualquier emoji del título) provoca un `UnicodeEncodeError` y no se envía nada. `AGENTS.md` anuncia este canal, pero no funciona.

**Solución:**
```python
base, topic = NTFY_TOPIC_URL.rstrip("/").rsplit("/", 1)
payload = {"topic": topic, "title": title_line[:250], "message": body[:4000], "click": clean_url,
           "priority": 5 if deal_tier == 1 else 4, "tags": ["moneybag"],
           "actions": [{"action": "view", "label": "Abrir anuncio", "url": clean_url, "clear": True}]}
headers = {"Authorization": f"Bearer {NTFY_TOKEN}"} if NTFY_TOKEN else {}
HTTP_SESSION.post(base, json=payload, headers=headers, timeout=3)
```
- `title_line` sigue el formato de T3.6.
- Se mantiene el filtrado actual (`flash_mode` y `push`).
- Activar el canal (rellenar `NTFY_TOPIC_URL` y suscribir el móvil) es decisión del usuario.

**Verificación:** test con un servidor HTTP local en 127.0.0.1, como en la sonda D:
- llega 1 petición a `/` con JSON que incluye `topic`, un `title` con «🔥» y `click` con la URL https;
- con `is_flash=True` y `flash_mode` en `off` → 0 peticiones.

### T1.13 — Puerta 2: salida mínima y telemetría de tokens por modalidad

**Archivos:** `core_engine.py`:
- `analyze_and_generate_kit`: prompt en L3864-3882, schema en L3906-3919 y clave de caché en L3803;
- `ai_gateway_call` (`usageMetadata`, L840-845);
- `log_ai_cost` (columnas `tokens_img` y `tokens_text`);
- `send_discord_alert` (L5370-5375) y `register_deal_in_db` (L5058), que consumen `fast_buy_message` y `value_add_tip`.

**Problema:**
- Cada llamada de visión genera texto libre (`visual_notes`, `value_add_tip`, `fast_buy_message`) también para el 60–74 % de anuncios que se rechazan. `fast_buy_message` ya tiene plantilla de respaldo (L5373-5375).
- No se registra qué parte de los tokens de entrada corresponde a imágenes, así que no se puede decidir la resolución (T3.10).

**Solución:**
1. Añadir al prompt: «Si is_valid_deal=false, rellena solo is_valid_deal, has_defect_or_missing_parts, visual_confidence, identified_model y reject_reason (15 palabras como máximo) y deja vacío el resto. visual_notes: 25 palabras como máximo».
2. Quitar `fast_buy_message` del JSON que se pide a la IA; se genera siempre con la plantilla. `value_add_tip` solo para anuncios válidos.
3. Leer `usageMetadata.promptTokensDetails` (`[{modality, tokenCount}]`) y guardar `tokens_img` y `tokens_text` en `ai_cost_log`.
4. Prefijo de la clave de caché: de `v45` a `v46`, porque cambia el prompt.

**Verificación:**
- Test: el prompt contiene la instrucción nueva, `fast_buy_message` no aparece en el esquema pedido y el parseo de `promptTokensDetails` simulado da `tokens_img` correctos.
- Tras el despliegue, la media de `tokens_out` de visión lite en 24 h baja de unos 255 a 210 o menos, y aparece `avg(tokens_img)` por imagen (dato para T3.10).

---

## 5. Fase 2 — Latencia y anti-detección

### T2.1 — Latencia por etapa y métricas por proceso

**Archivos:** `logger.py` (L17-18, L24-59 y L143-151), los puntos de captura de los tres escáneres y `core_engine.save_alert_context`. Actualizar `tools/metrics_report.py` (`get_metrics_path`, L20-25), que es el único que lee `metrics.jsonl`, para que lea `metrics*.jsonl`. Crear `tools/latency_report.py`.

**Problema:**
- Sin tiempos por etapa no se puede separar la captura, la IA y la alerta (ROADMAP, «Velocidad de reacción»).
- `metrics.jsonl` es compartido y pierde entre el 11 y el 20 % de las líneas al rotar (sonda del logger).

**Solución:**
1. `logger.py`:
   - Un fichero por servicio, `metrics.{service}.jsonl`, donde el servicio es `SERVICE_NAME` del entorno o el primer `setup_logger(name)`.
   - Proteger la escritura y la rotación con `handler.acquire()` / `handler.release()`.
2. Cada ítem lleva un diccionario `t` con marcas `perf_counter` en: captura, filtros, enriquecimiento, Puerta 1, imagen, visión, CeX y envío.
   - Se emite `log_metric("item_pipeline", service, platform, radar_id=…, outcome=…, ms_fetch_to_p1=…, ms_p1=…, ms_vision=…, ms_cex=…, ms_alert=…)` para **todo** ítem que llega a la Puerta 1, no solo para los alertados.
   - En las alertas, se guarda `pipeline_ms` en `alert_context_json`.
3. `tools/latency_report.py --since 24h`: p50 y p90 por etapa y plataforma a partir de `metrics.*.jsonl`, más el tiempo publicación→alerta desde `alert_outcomes` (con la BD de solo lectura).

**Verificación:**
- Test de 5 procesos que escriben 400 líneas cada uno con servicios distintos: se conservan las 2.000 (adaptación de `probe_logger.py`).
- `tools/run_isolated.sh tools/latency_report.py --since 24h` se ejecuta sin errores sobre `/src/data/logs`.

### T2.2 — Capa HTTP coherente: sesiones persistentes y huella actual

**Archivos:**
- Crear `http_client.py` y `requirements.txt`.
- Modificar `antiban.py` (`BROWSER_PROFILES` en L26-67, L82-89), `sniper.py` (`fetch_wallapop`, L177-235), `fraud_signals.py` (sesión en L374-382; llamadas a Wallapop en L476-557 y a Vinted en L565-611), `core_engine.py` (Algolia en L2331-2338 y L2553-2560; `_fetch_single_image` en L3248-3257), `outcome_tracker.py` (sesiones en L185-208) y el `Dockerfile`.

**Problema** (§1.4):
- `requests.get` suelto en cada consulta, con TLS nuevo cada vez.
- La petición XHR lleva cabeceras de navegación; las estadísticas de vendedor usan el UA «Mozilla/5.0» y `Accept-Language: en-US`.
- Perfiles de 2024 sobrescriben a mano el UA y `sec-ch-ua`.
- Las versiones de las librerías no están fijadas.

**Solución:**
1. `requirements.txt` con lo que hay hoy en la imagen (`pip freeze` dentro de `boti-sniper:v4.1`): `curl_cffi==0.17.0`, `pillow==13.0.0`, `discord.py==2.7.1`, `requests==2.34.2` y `pillow-heif` en su versión instalada. En el `Dockerfile`: `COPY requirements.txt` y `pip install --no-cache-dir -r requirements.txt`.
2. Perfiles: `chrome152` (Windows), `chrome152_macos` y `safari260`. **No sobrescribir** `User-Agent` ni `sec-ch-ua*`, porque curl_cffi ya envía los que corresponden a la versión impersonada. Hoy la sobrescritura desalinea versiones en dos sitios:
   - `outcome_tracker.py` (L37-38, L189-193 y L201-202): impersona `chrome` (es decir, 152) pero envía un UA de Chrome 131;
   - las estadísticas de vendedor: UA «Mozilla/5.0».

   Sobrescribir solo `Accept`, `Accept-Language` (`es-ES,es;q=0.9`), `Origin`, `Referer` y `Sec-Fetch-*`.
3. `http_client.py`:
   ```python
   XHR_HEADERS = {"Accept": "application/json, text/plain, */*", "Accept-Language": "es-ES,es;q=0.9",
                  "Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}
   class PlatformClient:
       """Una sesión curl_cffi por plataforma y por hilo (threading.local: Session no es thread-safe)."""
       def __init__(self, platform, *, impersonate, origin=None, referer=None, extra_headers=None,
                    bucket=None, health_monitor=None, proxies=None): ...
       def get(self, url, *, kind="xhr", **kw):   # kind: "xhr" | "document"; aplica bucket.acquire(),
           ...                                     # default_rate_limiter.record_request, Retry-After y 403/429 -> health_monitor
       def rotate(self): ...                       # sesión nueva con otro perfil (tras un cooldown o cada 6 h)
   ```
   - Cliente de Wallapop: `origin="https://es.wallapop.com"`, `referer="https://es.wallapop.com/"`, `extra_headers={"X-DeviceOS": "0"}`. Lo comparten la búsqueda y `/users`/`/stats` (T2.5), para que sea siempre la misma identidad.
   - Vinted: catálogo y fichas son documentos HTML, así que van con `kind="document"`, cabeceras de navegación, `Accept-Language` en español y `Referer` igual a la página anterior.
   - Algolia de CeX: `origin="https://es.webuy.com"`.
4. **Quitar `Sec-Fetch-User` y `Upgrade-Insecure-Requests` en XHR.** Compruébalo con la sonda de cabeceras. Si curl_cffi no permite eliminarlas, usa `default_headers=False` y construye el juego XHR completo de Chrome 152 copiando el `sec-ch-ua` exacto que la propia sonda captura con `default_headers=True`.
5. Opcional, dentro de este cambio de imagen: montar el código como directorio (`./:/app:ro` con exclusiones), en lugar de ficheros sueltos (deuda técnica del ROADMAP).

**Verificación:**
- `tools/run_isolated.sh docs/audits/2026-11-11-sondas/probe_headers.py`, adaptada para usar `PlatformClient`. La búsqueda de Wallapop debe llevar `Sec-Fetch-Mode: cors`, `Sec-Fetch-Dest: empty`, `Sec-Fetch-Site: same-site`, sin `Sec-Fetch-User` ni `Upgrade-Insecure-Requests`, con `Chrome/152` en el UA, `v="152"` en `sec-ch-ua` y `Accept-Language: es-ES…`. Las estadísticas de vendedor deben llevar el mismo UA que la búsqueda.
- Test: 3 `get` seguidos reutilizan una sola `Session` (mock con contador de instancias).
- Tras el despliegue: Wallapop con 2 o menos 403 al día y menor latencia media de `record_request`.

### T2.3 — Cadencia por consulta con *token bucket* (fin de las esperas fijas)

**Archivos:** `antiban.py` (clases nuevas `TokenBucket` y `QueryScheduler`; `inter_query_sleep` en L159-161 e `item_processing_sleep` en L163-165 quedan sin uso), `sniper.py` (`main`, L316-801), `vinted_sniper.py` (`main`, L555-1029) y `config.json` (bloque nuevo `scheduling`; `tier` de `hdd_server_storage`). Crear `tests/unit/test_scheduler.py`.

**Problema** (§1.1):
- Wallapop tiene un ciclo de 26,5 s y Vinted de 108 s.
- Hay esperas fijas por consulta (`sniper.py:372`, `vinted_sniper.py:608` y `:665`), tras cada alerta (`sniper.py:768`, `vinted_sniper.py:990`) y por ronda, y esta última no descuenta la duración de la ronda.
- Todos los radares *hot* se revisitan al mismo ritmo.

**Solución:**
```python
class TokenBucket:                                   # thread-safe (threading.Lock)
    def __init__(self, rate_per_s: float, burst: int = 2): ...
    def acquire(self) -> float: ...                  # bloquea hasta tener 1 token; devuelve los segundos esperados
    def penalize(self, factor=0.5): ...              # tras un 403/429: rate = max(rate * factor, base * 0.25)
    def recover(self): ...                           # cada 50 éxitos: rate = min(base, rate * 1.1)

@dataclass(order=True)
class QueryTask:
    next_due: float
    radar_id: str = field(compare=False); query: str = field(compare=False)
    min_price: float = field(compare=False); max_price: float = field(compare=False); interval_s: float = field(compare=False)

class QueryScheduler:                                # heapq por next_due
    def next(self) -> QueryTask: ...                 # espera a next_due (touch_heartbeat cada <= 15 s) y luego bucket.acquire()
    def done(self, task): ...                        # next_due = now + interval_s * random.uniform(0.85, 1.15)
    def reload_if_changed(self, config_mtime): ...   # reconstruye las tareas si cambia config.json
```
- Bloque `config.json → scheduling` con estos valores de partida:

  | Plataforma | `hot_interval_s` | `standard_interval_s` | `rate_per_s` | Comparación |
  |---|---|---|---|---|
  | Wallapop | 20 | 80 | 1.0 | Hoy ≈ 0,98 |
  | Vinted | 60 | 240 | 0.30 | Hoy ≈ 0,27 |

  Con `night_multiplier: 3` entre las 02:00 y las 07:00 (sustituye al «Modo Guardia Nocturna»).
- `hdd_server_storage`: `tier: "standard"`. Está en modo personal y acapara 5 consultas en Wallapop y 4 en Vinted. Es decisión del usuario y se recomienda.
- Las peticiones de fichas de Vinted (T2.4) y de vendedor (T2.5) también pasan por el mismo *bucket*.
- La evaluación sigue en línea: los ítems nuevos de una consulta se procesan al momento, como hoy. Los workers llegan con T2.6.
- **Rampa:** subir `rate_per_s` un 10 % por semana mientras los 403+429 se mantengan por debajo de 3 al día en esa plataforma.
- Cuentas: con 1,0 req/s, Wallapop atiende 16 consultas *hot* cada 20 s más 15 *standard* cada 80 s, lo que equivale a la tasa actual. En Vinted, a 0,30 req/s, las 18 consultas *hot* se revisitan cada ~75 s; para bajar a 60 s hacen falta ~0,37 req/s.

**Verificación:** test con reloj falso, simulando una hora con 16 tareas *hot* y 15 *standard* a 1,0 req/s:
- revisita *hot* con p95 de 25 s o menos, *standard* de 100 s o menos y 60 o menos peticiones por minuto;
- `penalize` y `recover` se comportan como se espera;
- se aplica el multiplicador nocturno.

Tras el despliegue, nueva métrica `query_completed` y KPIs de §8.

### T2.4 — Vinted: ficha y vendedor robustos

**Archivos:** `vinted_sniper.py` (`fetch_item_details` en L341-471 y bucle en L665-687) y `fraud_signals.enrich_seller_profile` (rama de Vinted, L559-615).

**Problema:**
- Si `fetch_item_details` falla (403, 429 o timeout), devuelve `("", [], "", None)` y el ítem sigue adelante: filtros con la descripción vacía, IA sin descripción y `mark_item_seen`, así que nunca se reevalúa con datos completos.
- Durante un cooldown se siguen pidiendo fichas: la comprobación solo está en `fetch_items` (L94-95).

**Solución:**
- `fetch_item_details` devuelve `None` cuando falla.
- En el bucle, con `None`: no se marca como visto y se incrementa `details_fail[item_id]`; se reintenta en la siguiente pasada, hasta 2 veces. Después se marca como visto y se registra `log_metric("item_skipped", reason="details_unavailable")`.
- Antes de cada ficha se comprueba `health_monitor.is_in_cooldown()`, y se sale del bucle de ítems en cuanto entra en cooldown.
- Si la ficha ya trae `feedback_count` y `feedback_reputation`, no se llama a `/api/v2/users/{id}`, salvo que falte la antigüedad y haya menos de 10 valoraciones (regla actual).
- Opcional, solo con aprobación porque necesita red real: medir en un script aparte el JSON de `/api/v2/catalog/items` con la sesión ya calentada, frente a la página HTML del catálogo.

**Verificación:** test con `VintedClient` simulado:
- ficha con 403 → el ítem no se marca visto y no se llama a `analyze_and_generate_kit`;
- en la segunda pasada, con 200 → el ítem se evalúa;
- con cooldown activo → no se piden más fichas.

### T2.5 — Vendedor de Wallapop: identidad coherente, límite y caché persistente

**Archivos:** `fraud_signals.py` (rama de Wallapop de `enrich_seller_profile`, L476-557; `_SELLER_PROFILE_CACHE`, L369-396) y `core_engine.init_db` (tabla `seller_profile_cache`).

**Problema:**
- Se hacen 2 peticiones por vendedor nuevo (`/users/{id}` y `/users/{id}/stats`, cada una con timeout de 2,5 s), fuera del rate limiter y del monitor de 403/429.
- Las cabeceras son incoherentes (§1.4).
- La caché solo vive en memoria (30 min) y se pierde en cada reinicio.

**Solución:**
- Usar el `PlatformClient` de Wallapop (T2.2) y pasar el `health_monitor` del escáner como parámetro opcional. Un 403/429 lleva a `seller_profile_status="unavailable"` (T1.4).
- Tabla `seller_profile_cache(platform TEXT, seller_id TEXT, profile_json TEXT, fetched_at TIMESTAMP, PRIMARY KEY(platform, seller_id))`, con TTL según las valoraciones:

  | Valoraciones | TTL |
  |---|---|
  | 20 o más | 24 h |
  | 1–19 | 6 h |
  | 0 | 1 h |

- Orden de consulta: memoria, luego SQLite y por último red.

**Verificación:**
- Dos llamadas para el mismo vendedor → 1 sola petición de red (mock).
- Tras vaciar `_SELLER_PROFILE_CACHE` → 0 peticiones, porque el perfil sale de SQLite.
- Un 429 → estado `unavailable` y llamada a `handle_http_response`.

### T2.6 — Pipeline común y workers de evaluación con cola de prioridad global (condicional)

**Archivos:** crear `pipeline.py`; convertir en adaptadores los bloques `sniper.py:389-768`, `vinted_sniper.py:625-990` y `multi_sniper.py:301-618`; `antiban.AntiBanHealthMonitor` (lock).

**Problema:**
- Los tres escáneres duplican unas 350 líneas del mismo embudo, con divergencias:
  - el multi-sniper no envía ntfy;
  - `cex_query … or q` reutiliza la variable del bucle (`sniper.py:683`, `vinted_sniper.py:897`, `multi_sniper.py:549`).
- La evaluación corre en el hilo de captura: un ítem que entra en IA (Puerta 1 1–2 s + visión 2–5 s, o hasta 15 s por modelo si hay fallback) bloquea las consultas siguientes.
- **Hazlo solo si T2.1 lo justifica:** espera en cola con p90 por encima de 10 s, o más del 20 % de las rondas con IA. Hoy Wallapop da 0,02 llamadas de IA por ronda y Vinted 0,13.

**Solución:**
1. `pipeline.py`:
   - `@dataclass ItemCtx(platform, item_id, title, desc, price, landed_cost, fees, target, item_url, image_urls, seller, country_tag, is_local, published_ts, extra_meta, price_drop_info, adapter, timings)`;
   - `evaluate_item(ctx, conn, deps) -> Outcome`, con las etapas en el **mismo orden y la misma lógica** que hoy.
2. **Test *golden*, antes de refactorizar:** grabar con mocks de IA, CeX y Discord las decisiones sobre unos 30 ítems sintéticos por escáner y exigir resultados idénticos después.
3. Workers:
   - `queue.PriorityQueue` con clave `(0 if price <= direct_max else 1, price / max_price, seq)`;
   - `EVAL_WORKERS=2`;
   - un `inflight: set` protegido por `Lock`, para no evaluar dos veces el mismo `PLAT:id`;
   - `get_db()` independiente en cada worker;
   - `budget_deferred` e `item_eval_fails` en una estructura con lock;
   - `AntiBanHealthMonitor` con `threading.Lock` (invariante 6);
   - las fichas de Vinted, con una sesión por hilo.
4. `decide_alert_push` (L4768-4776): contar y registrar dentro de una transacción `BEGIN IMMEDIATE`, para no superar `push_max_per_hour` con 2 workers.
5. Opcional: un LRU en memoria con los IDs vistos (`OrderedDict`, 50.000 entradas, TTL de 24 h) delante de `check_item_seen_or_dropped`.

**Verificación:**
- El test *golden* da resultados idénticos.
- Concurrencia: 2 workers, 100 ítems sintéticos con 20 duplicados → 80 evaluaciones y ningún doble.
- Tras el despliegue: p90 de captura→alerta de 15 s o menos (`latency_report`).

### T2.7 — Gateway de IA: *breaker* por modelo y plazo máximo por ítem

**Archivos:** `core_engine.py`: `ai_gateway_call` (L706-931); `prescreen_item_text` (timeout 12 en L3473) y `analyze_and_generate_kit` (timeout 15 en L3934).

**Problema:**
- El gateway no recuerda los fallos. Si Gemini se degrada, cada ítem puede esperar 15 s por cada modelo (2 de Gemini y 3–4 de OpenRouter): unos 150 s entre Puerta 1 y visión, dentro del hilo de captura.
- Cuando OpenRouter devuelve un error con estado 200, falta `choices` y se produce un `KeyError` silencioso (`relister-bot.log`, 11-09).

**Solución:**
- `ModelBreaker`: por modelo, tras 3 fallos seguidos (timeout, 5xx, 429 o `MAX_TOKENS`), abrir durante 300 s (o el `Retry-After` de un 429). Los modelos abiertos se saltan.
- Plazo máximo por llamada (`deadline_s`): 10 s para la Puerta 1 y 20 s para visión. No se empieza un intento si quedan menos de 3 s.
- Timeouts por intento: 6 s en la Puerta 1 y 12 s en visión.
- OpenRouter: si `"error" in resp_json`, registrar `_redact(error.message[:200])` y contar el fallo.

**Verificación:**
- Mock con timeout en Gemini: a partir del 4.º ítem no se llama a Gemini y la llamada dura menos de 1 s.
- Modelos lentos simulados: la llamada total no pasa de `deadline + 0,5 s`.
- `{"error":{"message":"x"}}` → se registra y se pasa al siguiente modelo.

### T2.8 — Efectos secundarios fuera del camino crítico

**Archivos:** `antiban.py` (`AntiBanHealthMonitor`: `send_health_embed` en L344-359, `handle_http_response` en L361-412 y los `notify_*`).

**Problema:**
- Cada embed de salud es un POST síncrono (timeout de 10 s) dentro del bucle de captura. Con Milanuncios eran entre 65 y 92 al día.
- `AntiBanHealthMonitor` no tiene lock (invariante 6 cuando haya workers).

**Solución:**
- Cola `queue.Queue(maxsize=100)` y un hilo daemon `HealthNotifier` que envía con `_discord_send` (T1.5). `send_health_embed` solo encola y vuelve.
- Deduplicar por `title` en ventanas de 10 min.
- `threading.Lock` en `handle_http_response`, `is_in_cooldown` y `reset_cooldown`.

**Verificación:**
- Con el POST simulado a 2 s, `send_health_embed` vuelve en menos de 5 ms.
- 5 embeds iguales en un minuto → 1 envío.

### T2.9 — multi-sniper: frecuencia por tienda según su rendimiento

**Archivos:** `multi_sniper.py` (`STORE_PIPELINE` en L55-104 y `next_fetch_at` en L643-657) y `config.json → stores`.

**Problema:** las tiendas de bajo rendimiento gastan peticiones desde la misma IP que Wallapop y Vinted:

| Tienda | Rondas al día | Anuncios nuevos | Alertas en 12 días |
|---|---|---|---|
| Cash Converters | 1.051 | 41 en 48 h | 0 |
| TodoColección | 592 | 2 en 48 h | 0 |

**Solución:**
- Propuesta (decisión del usuario): `"stores": {"cashconverters": {"interval_s": 600}, "todocoleccion": {"interval_s": 1800}, "ebay": {"interval_s": 300}}`.
- `next_fetch_at = now + interval_s × uniform(0.85, 1.15)`.
- Revisarlo cada mes con `market_seen_items` (anuncios nuevos al día por tienda).

**Verificación:**
- Test de planificación con reloj falso.
- Tras el despliegue: las peticiones diarias a Cash Converters y TodoColección (`record_request` en las métricas) bajan un 70 % o más.

---

## 6. Fase 3 — Precisión de arbitraje y UX de notificaciones

### T3.1 — Deduplicación semántica sin herencia entre vendedores distintos

**Archivos:** `sniper.py` (L550-589), `vinted_sniper.py` (L762-805) y `multi_sniper.py` (L409-453), o `pipeline.py` si se ha hecho T2.6; `core_engine.find_semantic_duplicate` (L1210-1288).

**Problema:**
- Con similitud de 0,96 o más se hereda el rechazo de **otro** anuncio, sin mirar el vendedor ni el motivo.
- En los logs hay 339 casos; 149 entre títulos distintos y con motivos que dependen del precio («El precio de 35€ es irrealmente bajo…»).
- El embedding local (64 dimensiones, hashing) no discrimina (§1.3).

**Solución:**
- Solo se hereda el rechazo en dos casos de republicación:
  - mismo `seller_id`, no vacío;
  - foto idéntica: el dHash coincide con el del anuncio previo en `seen_image_hashes`.
- Nunca se hereda un rechazo cuyo motivo cuadre con `precio|margen|caro|barato|irreal|estafa|anzuelo`, porque depende del precio actual.
- El log debe incluir el vendedor actual y el previo.
- Los aprobados se siguen reutilizando como ahora (ya exige el mismo vendedor).

**Verificación:** test con BD temporal, con un embedding previo rechazado del vendedor A:
- anuncio nuevo del vendedor B con similitud de 0,96 o más → se llama a `analyze_and_generate_kit` (mock);
- anuncio del vendedor A → no se llama.

Tras el despliegue, ninguna línea `DEDUP SEMÁNTICA RECHAZO` entre vendedores distintos.

### T3.2 — CeX por título antes de la IA (pre-CeX determinista)

**Archivos:** `core_engine.py` (nueva `estimate_cex_from_title(conn, title, desc, target)`; `pre_gate_cex_offer_margin` en L3509-3569; prompt de visión en L3864-3892), `fraud_signals.evaluate_fraud_risk` (parámetro `cex_cash`, ya existe) y el punto previo a la Puerta 1 en los escáneres o en `pipeline.py`.

**Problema:** CeX solo se consulta después de la visión, con la `cex_search_query` que devuelve la IA. Consecuencias:
- La heurística de anzuelo `ANOMALOUS_BELOW_CEX_CASH_NEW_SELLER` / `check_impossible_price` (`fraud_signals.py:1035-1069`) nunca se dispara, porque en la Puerta 0 `cex_cash` vale 0.
- El pre-gate solo cubre la franja de oferta (L3528) y usa el título crudo: palabras como «como» o «nueva» rompen el AND de L2412-2420.
- El 17,5 % de los rechazos visuales de Vinted son «precio irreal o estafa», detectables gratis con una referencia de CeX.
- La IA estima la reventa sin ninguna referencia.

**Solución:**
- **Normalizar el título** a tokens de modelo:
  - quitar palabras comerciales (`como nueva`, `perfecto`, `estado`, `vendo`, `urge`, `envio`, `precintad*`…);
  - aplicar alias (`ps5→playstation 5`, `switch→nintendo switch`, `deck→steam deck`…);
  - quedarse con la intersección con el vocabulario de `cex_catalog_mirror.box_name`, cargado en memoria cada 12 h.
- **Buscar** con el contexto (T1.3). La confianza es media si hay 2 tokens de modelo o más y todos los candidatos son de la misma familia.
- **Con confianza media o alta:**
  - pasar `cex_cash` a `evaluate_fraud_risk`, lo que activa la heurística de anzuelo con vendedor nuevo;
  - ampliar el pre-gate a la zona directa: descartar sin IA si `max_cash_variantes − landed < −20 €` **y** `sell × 0,7 − landed < min_profit × 0,5`;
  - añadir al prompt de visión «Referencia CeX: efectivo X €, PVP Y € (ficha Z)».

**Verificación:**
- Replay sobre `deals`: la familia de la ficha estimada por título coincide con `cex_box_name` en el 70 % de los casos o más.
- Tests de normalización y alias.
- Test: una PS5 a 90 € de un vendedor con 0 valoraciones genera `ANOMALOUS_BELOW_CEX_CASH_NEW_SELLER` antes de la IA.
- KPI: los rechazos visuales por «precio irreal» en Vinted bajan un 50 % o más.

Implementación 2026-11-14: referencia solo del espejo fresco (36 h), vocabulario por BD en RAM 12 h con lock; alias y cantidades de extras normalizados. Integrado tras enriquecimiento y antes del antifraude en los tres escáneres; pre-gate directo usa el coste puesto en casa real y ambas condiciones, conservando lotes y compra personal. Prompt con referencia y clave de caché dependiente de ella. 6 tests específicos, suite 251/251 y compilación OK. `tools/replay_pre_cex.py`: 372/517 = 71,95 % sobre **todos** los deals con CeX, sin excluir los que no producen estimación; 372/372 coinciden entre los cubiertos. Falta revisión independiente (cuota agotada) y KPI posterior al despliegue. Sin desplegar.

### T3.3 — Valoración P2P anclada y con confianza

**Archivos:** `core_engine.evaluate_deal_math_and_score` (L4165-4261, en especial L4184-4189), `save_alert_context`, `decide_alert_push` y `send_discord_alert` (mostrar la confianza).

**Problema:** `optimal_price` es `estimated_resale_price` en cuanto vale más de 0, y CeX solo hace de respaldo (§1.3). De ese número dependen los niveles 2 y 3, el ~80 % de alertas de regateo y la decisión de que la alerta suene.

**Solución:**
- Ancla de `optimal`:

  | Datos disponibles | `optimal` | Confianza |
  |---|---|---|
  | `cex_sell > 0` | `min(ai_est or inf, cex_sell × 0.85)` | — |
  | Sin CeX y n ≥ 5 en `price_history` de `"{radar}:{identified_model}"` (14 días) | `min(ai_est, p75_precio_pedido × 1.15)` | — |
  | Sin CeX ni histórico | `ai_est` | `valuation_confidence="baja"` |

- Guardar `alert_context_json.valuation = {ai_est, cex_sell, cex_cash, anchor, optimal, confidence}` para calibrar en el Nivel 2.
- `decide_alert_push`: con confianza `baja`, exigir `push_min_profit_eur × 1.5`.

**Verificación:**
- Tests de `evaluate_deal_math_and_score`:
  - `ai_est` 400 y `cex_sell` 300 → `optimal` 255;
  - sin CeX ni histórico → confianza `baja`;
  - el caso ID-03 de `AUDIT_CONCLUSION.md` (Switch OLED a 110 €) sigue devolviendo `OPORTUNIDAD_OFERTA` con beneficio de 35 € o más.
- Replay sobre `deals`: informe de qué alertas cambiarían de tipo o de beneficio (es informativo, no un criterio de aceptación).

Implementación 2026-11-14: 8 tests específicos y suite 259/259 OK; compilación aislada OK. CeX 300 €/IA 400 € → 255 €, p75 interpolado solo con ≥5 observaciones del submodelo en 14 días; confianza alta/media/baja y contexto `valuation` persistido. Discord muestra el precio anclado y confianza. Confianza baja multiplica el mínimo sonoro por 1,5 sin reducir el umbral especial de ofertas; compra personal conservada. `tools/replay_valuation.py`: reconstrucción informativa de 624 reventas, 22 precios óptimos cambian; 360 beneficios y 61 rechazos bajo la configuración actual. No hay estimación IA original de todas las alertas: se usa el óptimo guardado como proxy; estos cambios no son atribuibles solo al ancla. Falta revisión independiente (cuota agotada). Sin desplegar.

### T3.4 — Vinted: coste puesto en casa según el país

**Archivos:** `vinted_sniper.py` (`parse_vinted_price` en L509-514 y su llamada en L633) y `config.json → vinted_shipping_eur`.

**Problema:** el envío se toma como fijo (3,29 €) aunque el vendedor esté en FR, IT, PT, BE o NL. El país ya se conoce (`country_tag`, L939-943), así que el margen se sobreestima en las compras internacionales.

**Solución:**
- `parse_vinted_price(item, country)` con una tabla configurable. Valores iniciales conservadores, **a calibrar** con compras reales:

  | País | ES | PT | FR | IT | BE | NL | Por defecto |
  |---|---|---|---|---|---|---|---|
  | Envío (€) | 3,29 | 5,5 | 7,5 | 7,5 | 8,5 | 8,5 | 8,0 |

- Calcular el coste puesto en casa después de `fetch_item_details`, cuando el país ya se conoce.

**Verificación:** para un precio de 100 €:

| País | Coste puesto en casa |
|---|---|
| FR | 100 + 5,70 + 7,5 |
| ES | 100 + 5,70 + 3,29 |
| Desconocido | Valor por defecto |

### T3.5 — Reservados: volver a comprobarlos en lugar de darlos por vistos

**Archivos:** `sniper.py` (L416-418), `core_engine.mark_item_seen` (L1322-1331), `check_item_seen_or_dropped` (L1027-1050) y `market_seen_items` (columna `state`).

**Problema:** un anuncio reservado se marca como visto y no se reevalúa aunque la reserva se caiga; solo lo reabre una rebaja (≥ 15 % y ≥ 10 €).

**Solución:**
- Columna `market_seen_items.state TEXT DEFAULT 'seen'`. Los reservados se guardan con `state='reserved'`.
- `check_item_seen_or_dropped(..., is_reserved_now)` devuelve `(False, None)` para un `reserved` que hoy aparece sin reserva (el flag viene en la propia búsqueda).

**Verificación:** test. Un anuncio marcado como reservado reaparece con `reserved.flag=False`: entra de nuevo en el embudo y se actualiza a `state='seen'`.

### T3.6 — Payload de la alerta: lo esencial en la primera línea y un toque hasta el anuncio

**Archivos:** `core_engine.py`: `send_discord_alert` (contenido en L5383-5463, deeplink en L5465-5470, embed en L5492-5507), `send_flash_discord_alert` (L5126-5134) y `build_native_deeplink` (L1623-1659); `send_one_tap_ntfy_alert` (T1.12).

**Problema** (§1.5):
- La primera línea, que es lo que se ve con el móvil bloqueado, no trae ni modelo ni precio.
- Los enlaces de esquema propio no son clicables.
- El embed tiene 7 campos (unos 4.000 caracteres).
- Las alertas silenciosas empiezan por «🔕 Sin aviso sonoro…».

**Solución:**
- **Línea 1** (110 caracteres como máximo):
  - general: `{emoji} {modelo} · {precio} € → +{beneficio} € · {PLATAFORMA} {ciudad|país}`;
  - regateo: `🤝 {modelo} · ofertar {X} € (pide {P} €) → +{B} €`.
- **Línea 2:** la URL https limpia. Discord la hace clicable y en el móvil abre la app mediante *app links*.
- **Línea 3:** la mención, si la alerta debe sonar. En las silenciosas, el motivo 🔕 va al final y no al principio.
- Quitar `deeplink_header` y `app_link_str` (esquema propio). Se mantienen los botones https.
- **Embed:**
  - título igual a la línea 1;
  - 4 campos como máximo: «Veredicto IA» (nota, confianza y una frase), «CeX» (ficha, efectivo y neto), «Vendedor» y «Acción» (mensaje de oferta);
  - descripción de 200 caracteres como máximo.
- **ntfy:** el mismo título, con `click` apuntando a la URL https.

**Verificación:** test de *snapshot* que captura el JSON enviado (mock) para los casos directo, oferta, alto riesgo y silenciosa. Comprobar en cada uno:
- la línea 1 contiene modelo, precio y beneficio, y no pasa de 110 caracteres;
- no aparece `wallapop://` ni `vinted://`;
- el embed suma 2.500 caracteres o menos;
- todas las `url` empiezan por `https://`.

### T3.7 — Ciclo de vida de la alerta: marcar en Discord las reservadas y vendidas

**Archivos:** `outcome_tracker.py` (`record_check`, L366-403) y `core_engine` (helper `_discord_edit_message`). Depende de T1.5 (`discord_message_id`).

**Problema:** la alerta sigue pareciendo viva cuando el anuncio ya está reservado o vendido (3 de 4 chollos directos de Vinted desaparecen en menos de 60 min). Se pierde tiempo y se lanzan regateos inútiles.

**Solución:**
- Cuando el estado pasa a `reserved`, `sold` o `gone`: `PATCH /channels/{ch}/messages/{id}` con el `content` prefijado («❌ VENDIDO» o «⏸️ RESERVADO (hace N min)») y los `components` sin el botón de compra.
- Una sola edición por estado (`alert_outcomes.discord_state`).
- Usar `_discord_send`; una edición no genera notificación.

**Verificación:** test con mock. `record_check` con `sold` → 1 PATCH con el prefijo y sin el botón `deal:buy`; repetirlo → 0 PATCH.

### T3.8 — Resumen diario de alertas silenciosas

**Archivos:** `bot.py` (`tasks.loop` diario a las 21:00, hora de Madrid) y `core_engine` (consulta).

**Problema:** las silenciosas (25–45 al día, la mayoría de regateo) se publican sueltas. El ROADMAP (Nivel 2) prevé un resumen diario.

**Solución:**
- A las 21:00, un mensaje con las 10 mejores silenciosas de las últimas 24 h que sigan disponibles (`alert_outcomes.last_status='available'`), ordenadas por beneficio y con enlace.
- Opción `config.notifications.silent_mode="digest"` para dejar de publicarlas sueltas.

**Verificación:** test de la consulta con BD temporal: 12 silenciosas, de las que 3 están vendidas → las 9 disponibles, ordenadas por beneficio.

### T3.9 — Medir los falsos positivos del antifraude con una muestra de bloqueados

**Archivos:** el bloque de bloqueo de cada escáner (`sniper.py:481-485`, `vinted_sniper.py:696-700`, `multi_sniper.py:349-353`), `outcome_tracker.py` (tabla nueva y su comprobación) y `bot.py` (`/resultados`).

**Problema:** los bloqueos de la Puerta 0 no se siguen, así que se desconoce cuántos eran vendedores legítimos (ROADMAP, «Antifraude»). Son unos 150 al día (223 en 36 h), sobre todo cuentas creadas hace menos de 48 h, con 0 valoraciones y ticket alto.

**Solución:**
- Muestreo determinista del 10 % (`int(sha1(f"{plat}:{id}").hexdigest(), 16) % 10 == 0`).
- Tabla `fraud_samples(platform, item_id, seller_id, risk, codes, created_at, item_status, seller_status, checked_at)`.
- `outcome_tracker` comprueba el anuncio y el vendedor a las 24 h, con la misma cadencia y *backoff*.
- `/resultados` compara el % de cuentas suspendidas entre bloqueados y alertados.

**Verificación:** test del muestreo (10 % ± 2 % sobre 1.000 IDs) y del registro. KPI a 2 semanas: tasa de suspensión de bloqueados frente a alertados.

### T3.10 — Resolución de imagen para la visión: A/B controlado

**Archivos:** `core_engine.analyze_and_generate_kit` (`target_img_dim` en L3828-3836), `compress_image_to_jpeg_640` (L3292-3316) y `config.json → ai.image_max_dim`. Crear el script `tools/ab_image_resolution.py`.

**Problema:** si Gemini 3.x factura cada imagen a precio fijo (unos 900–1.000 tokens a 384 px; se confirma con T1.13), reducir a 384 px pierde detalle (arañazos, número de modelo, drift) sin ahorrar nada.

**Solución:**
- Con los datos de `tokens_img` (T1.13), comparar 384 px frente a 768 px.
- `tools/ab_image_resolution.py`: 20 fotos de `kits/` con el mismo prompt, registrando `promptTokensDetails` y el veredicto. **Solo con aprobación del usuario**, porque hace llamadas reales (coste inferior a 0,05 $).
- Si los tokens a 768 px no superan 1,1 veces los de 384 px, subir `image_max_dim` a 768 en lite.
- En caso contrario, probar `generationConfig.mediaResolution = "MEDIA_RESOLUTION_LOW"` para abaratar (no es `thinkingConfig`, así que la invariante 1 se mantiene).

**Verificación:**
- Informe del A/B con los tokens por imagen y la concordancia de veredictos.
- Test unitario: `target_img_dim` se lee de la configuración.

### T3.11 — Coherencia de umbrales y documentación

**Archivos:** `core_engine.py` (`pre_gate_cex_offer_margin` L3562, `passes_filters` L1844-1846, `select_multimodal_model` L3761-3772) y `AGENTS.md`.

**Problema:**
- El pre-gate usa `target.get("min_profit", 15)`, que ningún radar define, mientras que el motor matemático usa `min_profit_eur` / `min_net_profit_eur` (35).
- `passes_filters` fija 1,30 aunque `load_config` lea `offer_scan_multiplier` (L1434).
- `AGENTS.md` describe un sistema distinto del código:

  | Aspecto | `AGENTS.md` | Código |
  |---|---|---|
  | Imágenes | 640 px, JPEG Q=78 | 384 px, Q=76 |
  | Embeddings | Gemini | Hashing local de 64 dimensiones |
  | Ubicación de la BD | Raíz del repo | `data/sniper.db` |
  | Enrutado a flash | ≥ 150 € **o** lote | Lote **y** ≥ 150 € |

**Solución:**
- `resolve_min_profit(target, config)` única, compartida por el pre-gate y el motor matemático.
- `passes_filters` usa el `max_price` ya ampliado por `load_config`, sin recalcularlo.
- Actualizar `AGENTS.md` (§2–§5) con el estado real y la invariante 7.

**Verificación:**
- Test: un radar sin `min_profit_eur` y la configuración global a 35 → el pre-gate y el motor usan 35.
- `grep -n "1.30" core_engine.py` solo aparece en `load_config`.
- Revisión manual de `AGENTS.md`.

---

## 7. Descartado o pospuesto (no reabrir sin datos nuevos)

- **Reescritura completa a asyncio:** el cuello de botella medido es la cadencia de las esperas y la serialización captura→IA, no el GIL ni el número de sockets. Con hilos más *token bucket* (T2.3 y T2.6) se obtiene la misma mejora con mucho menos riesgo (curl_cffi síncrono, SQLite, tres escáneres). Reconsiderarlo por encima de unas 5 peticiones/s por proceso.
- **Bloom filter:** con unos 65.000 IDs, la clave primaria de SQLite responde en microsegundos. Un LRU opcional va en T2.6.
- **Proxies residenciales o VPS:** hoy Wallapop da unos 2 bloqueos al día y Vinted ninguno en 8 días. Activarlo solo si, tras la rampa de T2.3, los 403+429 superan 5 al día en alguna de las dos. En ese caso, empezar por un proxy residencial con sesión pegajosa por plataforma, a través de `PROXY_URL` (ya soportado).
- **Embeddings de Gemini para la deduplicación:** tras T3.1 solo se hereda por vendedor o foto, así que la calidad del embedding deja de ser crítica.
- **Resolver el DataDome de Milanuncios con un navegador headless:** el coste no compensa (0 alertas en 12 días); basta con el *breaker* y, si el usuario quiere, desactivarlo (T1.10).
- **Subir la tasa de peticiones sin instrumentación:** primero T2.1.

---

## 8. KPIs de aceptación

| KPI | Hoy | Objetivo | Cómo medirlo |
|---|---|---|---|
| Revisita de consultas *hot* en Wallapop | 26,5 s | ≤ 20 s | `tools/latency_report.py` |
| Revisita de consultas *hot* en Vinted | 108 s | ≤ 75 s a 0,30 req/s | ídem |
| Publicación→alerta en Wallapop (mediana) | 3,5 min | ≤ 2,5 min | `tools/latency_report.py` (la consulta (A) da la media) |
| Llamadas a la Puerta 1 al día | ~500–600 | Entre −25 y −35 % | (B) |
| Visión en Vinted al día | ~130 | −20 % | (B) |
| Visión flash con `MAX_TOKENS` | 100 % | 0 % | (C) |
| Errores de Discord sin registrar | Desconocido | 0 | `grep -c '\[DISCORD ERROR\]' data/logs/*.log` (con registro, no silencioso) |
| Bloqueos de WAF en Milanuncios al día | 65–92 | ≤ 8, o 0 si se desactiva | `grep 'COOLDOWN PREVENTIVO' data/logs/multi-sniper.log \| cut -c1-10 \| uniq -c` |
| 403+429 de Wallapop al día | ~2 | ≤ 2 | `grep 'COOLDOWN ANTIBAN\] Wallapop' data/logs/wallapop-sniper.log* \| cut -c1-10 \| uniq -c` |
| Casos de §1.3 resueltos | 0/12 | 12/12 | `probe_filters.py` tras T1.1 + T1.2 |
| Juegos o accesorios en alertas de consola | 2,1 % | ≤ 0,5 % | Replay de T1.2 sobre `deals` de 7 días |
| Métricas perdidas al rotar | 11–20 % | 0 % | Test de T2.1 |

Consultas de solo lectura sobre la BD viva (en el host no está instalado `sqlite3`):
```bash
q(){ python3 -I -c "import sqlite3,sys; c=sqlite3.connect('file:/opt/wallapop-sniper/data/sniper.db?mode=ro',uri=True); [print(r) for r in c.execute(sys.argv[1])]" "$1"; }
# (A)
q "SELECT platform, count(*), round(avg(alert_delay_min),1) FROM alert_outcomes WHERE alert_at>=datetime('now','-7 days') AND alert_delay_min IS NOT NULL GROUP BY 1"
# (B)
q "SELECT date(created_at), platform, call_type, count(*) FROM ai_cost_log WHERE created_at>=datetime('now','-7 days') AND call_type IN ('prescreen','vision') GROUP BY 1,2,3 ORDER BY 1"
# (C) requiere T1.6
q "SELECT model, finish_reason, count(*), round(avg(tokens_out)) FROM ai_cost_log WHERE created_at>=datetime('now','-2 days') AND call_type LIKE 'vision%' GROUP BY 1,2"
```

---

## Apéndice A — Evidencia reproducible

Todas las sondas se ejecutan sin red, sobre una copia de la BD. Instrucciones en [`2026-11-11-sondas/README.md`](2026-11-11-sondas/README.md).

| Sonda | Qué demuestra | Sección |
|---|---|---|
| `probe_filters.py` | Los 12 casos de filtros y fraude, la similitud del embedding local y los microbenchmarks (`passes_filters` 0,36 ms; embedding 11,9 ms; dedup 7,3 ms) | §1.3, T1.1, T3.1 |
| `probe_runtime.py` | D) ntfy latin-1 · E) contaminación de la caché de CeX · F) vendedor desconocido → 0 · G) precio de eBay · H) petición duplicada en un 429 · I) un 400 de Discord sin log | §1.3–§1.5 |
| `probe_headers.py` | Cabeceras reales que envía curl_cffi con la configuración actual | §1.4, T2.2 |
| `probe_logger.py` | Pérdida de métricas con 1 frente a 5 procesos | §1.6, T2.1 |
| `console_classifier_eval.py` | Construye el dataset (logs + `deals`) y mide el prototipo de T1.2 | T1.2 |
