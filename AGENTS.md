# 🚨 Entorno Remoto (Samba + SSH) — LECTURA OBLIGATORIA

El proyecto reside en el servidor Linux `miky-p6657es` (`100.114.8.117`). Desde el portátil se accede mediante un montaje en red (Samba SMB); dentro del propio servidor también está disponible como bind local en `/home/miky/opt_servidor`. Comprueba primero en qué entorno estás.

## Reglas Obligatorias de Operación

0. **Comprobar el entorno antes de ejecutar:**
   - Antes de recurrir a SSH, comprueba si estás en el portátil o dentro del servidor. No lo deduzcas solo por la ruta de trabajo: puede ser un montaje Samba.
   - Comprueba el sistema operativo, el hostname y la disponibilidad de la ruta canónica `/opt/wallapop-sniper`. Si el entorno es Linux y el hostname es `miky-p6657es`, ejecuta directamente en `/opt/wallapop-sniper`.
   - Si estás en el portátil (Windows o cualquier otro sistema), ejecuta en el servidor por SSH con el alias `servidor`. Si no puedes determinar el entorno, no ejecutes runtimes ni builds localmente hasta aclararlo.

1. **PROHIBIDO ejecutar herramientas de ejecución/build en Windows local:**
   - No ejecutes `npm`, `pnpm`, `pip`, `python`, `pytest`, `cargo`, `docker`, etc. directamente en el PowerShell/CMD local de Windows. Las dependencias y el runtime residen en el servidor Linux.

2. **Ejecución en el servidor, directa o vía SSH según el entorno:**
   - Desde el portátil, cualquier comando de consola (instalar paquetes, correr tests, compilar, arrancar scripts o contenedores) debe ejecutarse a través de SSH con el alias `servidor`:
     `ssh servidor "cd /opt/wallapop-sniper && <comando>"`
   - Si ya estás dentro de `miky-p6657es` en Linux, ejecuta directamente desde `/opt/wallapop-sniper`; no es necesario conectar por SSH al mismo servidor.

3. **Traducción de Rutas (Path Translation):**
   - Windows `\\100.114.8.117\Descargas_Servidor\opt_servidor` (o `Z:\opt_servidor`) --> Linux `/opt/wallapop-sniper` (también montado como bind en `/home/miky/opt_servidor`).
   - La ruta canónica para Docker y ejecución es: `/opt/wallapop-sniper`.
   - *Ejemplo:* Para gestionar el stack de contenedores:
     `ssh servidor "cd /opt/wallapop-sniper && docker compose ps"`

4. **Edición de archivos:**
   - La lectura y edición de archivos (`read_file`, `edit_file`, `write_file`) sí se realiza directamente sobre las rutas Samba de Windows.

---

# AGENTS.md — Guía Operativa del Proyecto para Agentes de IA

> **Hoja de ruta y estado del trabajo en curso:** lee también [`ROADMAP.md`](ROADMAP.md) (Niveles 1–3, pendientes y limitaciones conocidas) y actualízalo al terminar cada bloque.

> **Instrucción para el Agente:** Lee este archivo SIEMPRE al inicio de cada sesión antes de explorar el repositorio. Contiene el mapa completo de la arquitectura, esquema de base de datos, flujos de datos e invariantes críticas para que no tengas que gastar tokens ni tiempo redescubriendo cómo funciona el sistema.

---

## 1. Propósito del Sistema
Plataforma automatizada de **arbitraje y detección de chollos en tiempo real (24/7)** en mercados secundarios (**Wallapop, Vinted, eBay, Cash Converters, Milanuncios y TodoColección**), especializada en consolas, videojuegos coleccionables y tecnología de alta liquidez.
Cruza los anuncios capturados con los precios oficiales de compra/efectivo de **CeX (WeBuy)** y el **valor real de reventa en el mercado particular (P2P)**, filtra fraudes de forma determinista enriqueciendo el perfil real del vendedor y evalúa visual/textualmente cada oportunidad con IA multimodal clasificándola en **3 Niveles de Chollo** antes de notificar en 1 toque por **ntfy.sh, Telegram y Discord**.

---

## 2. Mapa de Archivos Clave (Dónde tocar según la tarea)

| Archivo | Rol Principal | Cuándo modificarlo |
| :--- | :--- | :--- |
| `config.json` | Configuración maestra de radares (`targets`/`searches`), precios tope, modelos de IA y exclusiones globales. | Al ajustar precios máximos (`max_price`, `direct_buy_max_price`), añadir radares o cambiar modelos (`ai_model`, `prescreen_model`). |
| `core_engine.py` | **Cerebro del sistema.** Inicialización SQLite (`init_db`), caché CeX (`fetch_cex_live`), fuente única de umbrales (`resolve_min_profit`), Puerta 1 IA (`prescreen_item_text`), Puerta 2 IA multimodal (`analyze_and_generate_kit` con compresión RAM `ai.image_max_dim`, `384 px` por defecto, `JPEG Q=76` y 3 Niveles de Chollo), alertas (`ntfy.sh` / Discord con ciclo de vida) y deduplicación por hashing local de 64 dimensiones. | Al tocar prompts de IA, lógica de 3 niveles (CeX vs P2P), base de datos, embeddings o alertas 1-Tap. |
| `fraud_signals.py` | **Motor antifraude determinista (Coste 0) + Enriquecimiento de Vendedor.** Analiza el perfil real del vendedor (`enrich_seller_profile`), reputación histórica (`seller_reputation`), coherencia de precio, texto, detección de teléfonos camuflados y pHash/dHash de imágenes. | Al ajustar pesos de riesgo, umbrales de bloqueo (`should_block`), normalización de ratings o tokens de defectos/despiece. |
| `text_rules.py` | **Reglas de texto y clasificador determinista** (`classify_console_listing`, normalización de texto y detección de juegos/accesorios sueltos). | Al ajustar exclusiones por título o reglas léxicas por consola. |
| `http_client.py` | **Cliente HTTP unificado** sobre `curl_cffi` con timeout duro y reintentos homogéneos para scrapers y APIs. | Al cambiar timeouts, sesiones TLS o manejo de errores HTTP. |
| `store_breaker.py` | **Cortacircuitos por tienda (`StoreCircuitBreaker`).** Aísla temporalmente tiendas secundarias tras fallos consecutivos. | Al ajustar umbrales de apertura o enfriamiento por tienda. |
| `outcome_tracker.py` | **Seguimiento automático de alertas (`outcome-tracker`) y muestreo antifraude (`fraud_samples`).** Verifica estado en plataforma, actualiza `discord_state` en Discord (reservado/vendido) y audita falsos positivos a 24 h. | Al tocar intervalos de seguimiento, ciclo de vida en Discord o muestreo de bloqueados. |
| `multi_sniper.py` | **Orquestador concurrente de escaneo.** Ejecuta Milanuncios, Cash Converters, eBay España y TodoColección por radar, aplica la cola de prioridad *Fast-Lane*, enriquece el vendedor y despacha los candidatos al motor IA. | Al cambiar tiempos de ciclo, orden de prioridad Fast-Lane o concurrencia entre tiendas. |
| `sniper.py` | Escáner principal de Wallapop (ES e IT) con baja latencia, re-chequeo de reservados (`market_seen_items.state`) y alertas. | Si cambia la API v3 o cabeceras de Wallapop. |
| `vinted_sniper.py` | Escáner europeo de Vinted con gestión automática de cookies de sesión (`refresh_cookies`), cálculo puesto en casa por país (`vinted_shipping_eur`) y catálogo. Valoraciones del vendedor desde el bloque de datos de la ficha (`parse_vinted_seller_feedback`, comillas escapadas; incluye `business` = vendedor profesional) y el id del vendedor del catálogo (`merge_catalog_seller`). | Si cambia la API de catálogo, envío, cookies o el HTML/RSC de la ficha de Vinted. |
| `antiban.py` | Evasión TLS (`curl_cffi` thread-safe), rotación JA3, rate-limiter adaptativo con cerrojos, monitor de salud (`AntiBanHealthMonitor`, `ParserHealthTracker`) y planificación de consultas: `TokenBucket` (con tokens prioritarios a crédito, `acquire_item_token`) y `QueryScheduler` (ajusta las cadencias a la cuota, elige la consulta más atrasada en proporción a su cadencia y, con `scheduling.adaptive`, reparte la misma cuota según candidatos/alertas por consulta; estado en `data/scheduler_state.<plataforma>.json`). | Si hay bloqueos 403/429, alertas de selectores rotos o al cambiar cadencias (`config.json → scheduling`). Informe: `tools/cadence_report.py`. |
| `bot.py` | Centro de mando interactivo en Discord (asistente de compraventa, tasador CeX en vivo, cartera financiera, resumen diario a las 21:00 y comandos slash) y bucle de alertas con botones de 1 clic. | Al añadir comandos interactivos, tareas programadas o cambiar el formato visual de las alertas. |

---

## 3. Flujo del Pipeline de Evaluación (De la Captura a la Alerta 1-Tap)

1. **Captura Concurrente (`multi_sniper.py`, `sniper.py`, `vinted_sniper.py`):** Lanza los scrapers en paralelo por cada objetivo de `config.json` (con `max_price` expandido por `offer_scan_multiplier` en `load_config`). Wallapop y Vinted sondean cada consulta según `QueryScheduler`: si `scheduling` pide más peticiones de las que caben en `rate_per_s`, todas las cadencias se alargan en la misma proporción (ningún tier se queda sin sondear) y el reparto adaptativo adelanta las consultas que traen candidatos. La 2.ª página de Wallapop deja de pedirse en consultas cuya 2.ª página llega vacía (`SecondPagePolicy`, se recomprueba cada 40 sondeos).
2. **Filtro Rápido Local (`passes_filters` + `text_rules.py` + `check_item_seen_or_dropped`):** Descarta al instante juegos/accesorios sueltos (`classify_console_listing`), anuncios ya vistos (`seen_items` / `market_seen_items`, re-chequeando los `'reserved'`), precios fuera de rango o palabras prohibidas (`global_blacklist`).
3. **Cola de Prioridad (`Fast-Lane Queue`):** Ordena los candidatos nuevos por mayor descuento relativo (`price / max_price`) para procesar primero el chollo más extremo.
4. **Enriquecimiento de Vendedor + Puerta 0 Antifraude (`fraud_signals.py`):**
   * `enrich_seller_profile` obtiene valoraciones, ventas y antigüedad real del vendedor (normalizando la escala `0-100` de Wallapop a `0.0-5.0`).
   * Cruza con `seller_reputation`, detecta estafas de cuentas nuevas (`0 ventas`), caja vacía y reutilización de fotos (`seen_image_hashes`). Si `should_block == True` o `risk >= 4`, descarta en seco sin gastar tokens de IA (muestreando el 10% en `fraud_samples` para medir falsos positivos a 24 h).
5. **Flash Alert + 1-Tap (`ntfy.sh` / Discord):** Gobernada por `notifications.flash_mode` (`off` por defecto). Si está activa, `price <= direct_buy_max_price` y el vendedor es fiable (`reviews >= 2` y `rating >= 4.0`, o `CashConverters`), dispara alerta inmediata antes de la IA pesada.
6. **Pre-Gating y Cruce con CeX (`pre_gate_cex_offer_margin` / `fetch_cex_live`):** Consulta primero el espejo local (`cex_catalog_mirror`) usando el umbral unificado `resolve_min_profit` y, si pasa, obtiene `cash_price`, `voucher_price` y precio de venta de CeX.
7. **Puerta 1 IA — Pre-filtrado de Texto (`prescreen_item_text`):** Llamada ultraligera a `gemini-3.5-flash-lite` (con fallback en OpenRouter a `google/gemini-2.5-flash-lite` y `openai/gpt-4o-mini`) cacheada en `ai_prescreen_cache`.
8. **Deduplicación Semántica (`find_semantic_duplicate`):** Comprueba mediante embeddings de **hashing local de 64 dimensiones** (coste 0, sin llamadas a API externa) si el mismo producto ya fue subido por el mismo vendedor o en otra plataforma.
9. **Puerta 2 IA — Auditoría Multimodal y Clasificación en 3 Niveles (`analyze_and_generate_kit`):**
   * Descarga en paralelo hasta 3 fotos, las comprime en RAM a `ai.image_max_dim` (`384 px` por defecto, `JPEG Q=76`) y audita estado, originalidad, extras incluidos y rentabilidad en **3 Niveles (`NIVEL 1: Arbitraje CeX`, `NIVEL 2: Chollo P2P + Colchón CeX`, `NIVEL 3: Reventa P2P`)**.
   * Smart Model Routing (`select_multimodal_model`): Usa `gemini-3.5-flash` (si `pro_model_enabled=True`) únicamente cuando es **lote complejo Y ticket $\ge 150\text{ €}$**; para el resto usa `gemini-3.5-flash-lite`.
   * Si el anuncio es válido (`is_valid_deal == True`), registra la observación de precio en `price_history` y despacha la alerta enriquecida (con enlace HTTP directo en 1 toque).

---

## 4. Esquema de Base de Datos SQLite (`data/sniper.db`)

Todas las tablas residen en `/opt/wallapop-sniper/data/sniper.db` bajo modo `WAL`:

* `seen_items` y `market_seen_items`: Registro histórico de IDs/URLs procesadas, su último precio visto y estado (`'seen'` / `'reserved'` para volver a comprobar anuncios reservados).
* `cex_cache` y `cex_catalog_mirror`: Caché puntual (TTL 3d) y espejo local del catálogo de CeX España.
* `ai_prescreen_cache`: Caché (TTL 7d) de decisiones de Puerta 1 por hash MD5 de `(radar_id | title | price)`.
* `ai_prompt_cache`: Caché de respuestas LLM por hash del prompt para evitar llamadas redundantes a las APIs.
* `deals`: Historial financiero y de alertas (`status`: `'ALERTED'`, `'BOUGHT'`, `'SOLD'`, `'DISCARDED'`).
* `item_embeddings`: Vectores semánticos locales de 64 dimensiones de anuncios analizados para deduplicación por similitud coseno.
* `seen_image_hashes`: Huellas perceptuales (dHash 8x8 de 64 bits) para cazar fotos robadas entre diferentes cuentas.
* `price_history`: Observaciones reales de mercado validadas por IA para medianas y percentiles locales.
* `seller_reputation`: Historial de reputación por vendedor y plataforma (ventas, flags de fraude, score promedio).
* `ai_cost_log`: Auditoría de tokens y coste en euros por llamada a Gemini / OpenRouter.
* `alert_feedback`: Calificaciones manuales de alertas (botón 📝 Calificar, `/calificar`): veredicto, motivos, nota y foto de lo que estimó el sistema.
* `alert_outcomes` / `alert_outcome_checks`: Seguimiento automático (`outcome_tracker.py`, contenedor `outcome-tracker`) de cada alerta a 15 min/1 h/3 h/6 h/24 h/72 h: disponible/reservado/vendido/eliminado, visitas/favoritos/conversaciones, retraso publicación→alerta, `discord_state` (edición de alertas reservadas/vendidas) y estado de la cuenta del vendedor 24 h después (etiqueta de verdad antifraude). Informe: `/resultados`.
* `fraud_samples`: Muestra aleatoria (10%) de anuncios bloqueados en Puerta 0 antifraude y su comprobación a las 24 h para estimar tasa de falsos positivos.
* `deals.alert_context_json`: Perfil del vendedor (valoraciones, ventas, compras, antigüedad y su fuente, denuncias), hora de publicación y decisión de aviso sonoro (`push`, `quiet_reason`) de cada alerta.

### Política de notificaciones (`config.json` → `notifications`, `decide_alert_push`)
* `flash_mode` (`off`/`silent`/`on`): alerta previa a la IA. Desactivada por defecto (>90% las rechazaba luego la IA).
* `silent_mode` (`individual`/`digest`): en `"individual"` publica al momento; en `"digest"` agrupa las alertas silenciosas del día en un resumen diario a las 21:00 (`Europe/Madrid`).
* Solo suenan (mención + ntfy) las alertas finales con margen suficiente, sin vendedor de 0 valoraciones ni "alto riesgo", hasta `push_max_per_hour`. El resto se publica con el flag de Discord `SUPPRESS_NOTIFICATIONS` (4096) y una línea 🔕 con el motivo.
* **Compra asistida** (`push_top_deals_always`, `push_top_deal_min_profit_eur`; `is_top_deal`): la compra directa con nota 10 (arbitraje CeX nivel 1) suena aunque se haya alcanzado `push_max_per_hour` y con su propio mínimo de margen (25 €); en ntfy va con prioridad 5 y el botón «⚡ Comprar ya». Las reglas de seguridad (0 valoraciones, alto riesgo) no cambian. No existe compra automática: `tools/top_deals_report.py` mide si alguna clase de nota 10 llegaría a la precisión exigida.

---

## 5. Invariantes y Reglas de Oro (NO ROMPER)

1. **Prohibido `thinkingConfig` en Gemini API v1beta:** Nunca añadir `"thinkingConfig"` dentro de `"generationConfig"` en llamadas REST a `generativelanguage.googleapis.com`.
2. **Modelos vigentes:** Usar `gemini-3.5-flash` únicamente para **lote complejo Y ticket alto ($\ge 150\text{ €}$)** (cuando `pro_model_enabled=True`), y `gemini-3.5-flash-lite` para Puerta 1 y el resto de anuncios, manteniendo siempre la cadena de fallback a OpenRouter.
3. **Fail-Open sin envenenar caché:** Si las APIs de IA fallan por red o cuota, `prescreen_item_text` debe devolver `"proceed"` **sin guardar** ese resultado de emergencia en `ai_prescreen_cache`.
4. **Concurrencia SQLite:** Usar siempre `get_db()` (que configura `PRAGMA journal_mode=WAL;` y `busy_timeout=30000`). No compartir conexiones SQLite entre distintos hilos sin lock.
5. **Normalización de Ratings:** Wallapop devuelve valoraciones en escala `0-100` en endpoints internos; normalizar siempre a `0.0-5.0` antes de evaluar `SELLER_LOW_RATING` y no confundir `0 valoraciones` con `mala puntuación`.
6. **Thread-Safety en Clases Compartidas:** `AdaptiveRateLimiter`, `ParserHealthTracker` y administradores de sesión deben usar locks (`threading.Lock`) para evitar `RuntimeError: Set changed size during iteration`.
7. **«Perfil no consultado» ≠ «0 valoraciones»:** `enrich_seller_profile` deja `seller_reviews_count = None` cuando la plataforma no responde y marca `seller_profile_status` (`ok` / `unavailable` / `not_applicable`). Solo un `0` confirmado dispara `SELLER_ZERO_REVIEWS`, `SELLER_GHOST_PROFILE`, `SELLER_NEW_ACCOUNT_ZERO_REVIEWS`, `HIGH_TICKET_ZERO_REP_SCAM_RISK` o silencia el aviso sonoro; `None` con `unavailable` suma solo `SELLER_PROFILE_UNKNOWN` (peso 1). Los escáneres leen `int(x or 0)`: con `None` no hay flash, que es lo correcto.
8. **Tokens prioritarios solo para un anuncio ya detectado:** `acquire_item_token` / `TokenBucket.acquire(priority=True)` se reserva a la ficha (Vinted) y al perfil del vendedor de un candidato concreto; nunca a búsquedas, páginas extra ni reintentos. La deuda está acotada por `priority_burst` (3) y la pagan las búsquedas siguientes, así que la tasa media hacia la plataforma no cambia.
9. **Push ntfy fuera del camino de Discord:** `send_discord_alert` lanza el push final en un hilo (`_dispatch_ntfy_push`) antes de enviar a Discord y marca `ai_data["_ntfy_dispatched"]`; la llamada posterior de los escáneres a `send_one_tap_ntfy_alert` lo detecta (`already_sent`) y no lo duplica. Solo se envía si `decide_alert_push` aprobó el aviso sonoro.

---

## 6. Comandos Rápidos de Validación

Antes de dar por terminada cualquier modificación, verifica:
1. **Sintaxis limpia en todo el proyecto:**
   `python3 -m py_compile core_engine.py bot.py multi_sniper.py fraud_signals.py antiban.py sniper.py vinted_sniper.py`
2. **Integridad de `config.json`:**
   `python3 -c "import json; cfg=json.load(open('config.json', encoding='utf-8')); print('Targets OK:', len(cfg.get('targets', cfg.get('searches', []))))"`
3. **Reinicio de contenedores en producción (si aplica):**
   `docker compose restart` (o `docker compose up -d --build`)
