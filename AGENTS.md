# 🚨 Entorno Remoto (Samba + SSH) — LECTURA OBLIGATORIA

Este directorio es un montaje en red (Samba SMB) correspondiente al servidor Linux remoto `miky-p6657es` (`100.114.8.117`).

## Reglas Obligatorias de Operación

1. **PROHIBIDO ejecutar herramientas de ejecución/build en Windows local:**
   - No ejecutes `npm`, `pnpm`, `pip`, `python`, `pytest`, `cargo`, `docker`, etc. directamente en el PowerShell/CMD local de Windows. Las dependencias y el runtime residen en el servidor Linux.

2. **Ejecución remota vía SSH:**
   - Cualquier comando de consola (instalar paquetes, correr tests, compilar, arrancar scripts o contenedores) debe ejecutarse a través de SSH con el alias `servidor`:
     `ssh servidor "cd /opt/wallapop-sniper && <comando>"`

3. **Traducción de Rutas (Path Translation):**
   - Windows `\\100.114.8.117\Descargas_Servidor\opt_servidor` (o `Z:\opt_servidor`) --> Linux `/opt/wallapop-sniper` (también montado como bind en `/home/miky/opt_servidor`).
   - La ruta canónica para Docker y ejecución es: `/opt/wallapop-sniper`.
   - *Ejemplo:* Para gestionar el stack de contenedores:
     `ssh servidor "cd /opt/wallapop-sniper && docker compose ps"`

4. **Edición de archivos:**
   - La lectura y edición de archivos (`read_file`, `edit_file`, `write_file`) sí se realiza directamente sobre las rutas Samba de Windows.

---

# AGENTS.md — Guía Operativa del Proyecto para Agentes de IA

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
| `core_engine.py` | **Cerebro del sistema.** Inicialización SQLite (`init_db`), caché CeX (`fetch_cex_live`), Puerta 1 IA (`prescreen_item_text`), Puerta 2 IA multimodal (`analyze_and_generate_kit` con compresión RAM 640px JPEG Q=78 y 3 Niveles de Chollo), alertas Flash (`ntfy.sh` / Discord) y deduplicación semántica. | Al tocar prompts de IA, lógica de 3 niveles (CeX vs P2P), base de datos, embeddings o alertas 1-Tap. |
| `fraud_signals.py` | **Motor antifraude determinista (Coste 0) + Enriquecimiento de Vendedor.** Analiza el perfil real del vendedor (`enrich_seller_profile`), reputación histórica (`seller_reputation`), coherencia de precio, texto y dHash de imágenes. | Al ajustar pesos de riesgo, umbrales de bloqueo (`should_block`), normalización de ratings o tokens de defectos. |
| `multi_sniper.py` | **Orquestador concurrente de escaneo.** Ejecuta Milanuncios, Cash Converters, eBay España y TodoColección por radar, aplica la cola de prioridad *Fast-Lane*, enriquece el vendedor y despacha los candidatos al motor IA. | Al cambiar tiempos de ciclo, orden de prioridad Fast-Lane o concurrencia entre tiendas. |
| `sniper.py` | Escáner principal de Wallapop (ES e IT) con baja latencia y alertas Flash (<80ms). | Si cambia la API v3 o cabeceras de Wallapop. |
| `vinted_sniper.py` | Escáner europeo de Vinted con gestión automática de cookies de sesión (`refresh_cookies`) y catálogo. | Si cambia la API de catálogo o cookies de Vinted. |
| `antiban.py` | Evasión TLS (`curl_cffi` thread-safe), rotación JA3, rate-limiter adaptativo con cerrojos y monitor de salud (`AntiBanHealthMonitor`, `ParserHealthTracker`). | Si hay bloqueos 403/429 o alertas de selectores rotos. |
| `bot.py` | Centro de mando interactivo en Discord (asistente de compraventa, tasador CeX en vivo, cartera financiera y comandos slash) y bucle de alertas con botones de 1 clic. | Al añadir comandos interactivos o cambiar el formato visual de las alertas. |
| `fraud_signals.py` | Heurísticas deterministas antifraude, detección de teléfonos camuflados y pHash/dHash de fotos. | Al actualizar heurísticas antifraude o tokens de despiece. |

---

## 3. Flujo del Pipeline de Evaluación (De la Captura a la Alerta 1-Tap)

1. **Captura Concurrente (`multi_sniper.py`, `sniper.py`, `vinted_sniper.py`):** Lanza los scrapers en paralelo por cada objetivo de `config.json`.
2. **Filtro Rápido Local (`passes_filters` + `check_item_seen_or_dropped`):** Descarta al instante anuncios ya vistos (`seen_items`), precios fuera de rango o palabras prohibidas (`global_blacklist`).
3. **Cola de Prioridad (`Fast-Lane Queue`):** Ordena los candidatos nuevos por mayor descuento relativo (`price / max_price`) para procesar primero el chollo más extremo.
4. **Enriquecimiento de Vendedor + Puerta 0 Antifraude (`fraud_signals.py`):**
   * `enrich_seller_profile` obtiene valoraciones, ventas y antigüedad real del vendedor (normalizando la escala `0-100` de Wallapop a `0.0-5.0`).
   * Cruza con `seller_reputation`, detecta estafas de cuentas nuevas (`0 ventas`), caja vacía y reutilización de fotos (`seen_image_hashes`). Si `should_block == True` o `risk >= 4`, se descarta en seco sin gastar tokens de IA.
5. **Flash Alert + 1-Tap (`ntfy.sh` / Discord):** Si `price <= direct_buy_max_price` y el vendedor es fiable (`reviews >= 2` y `rating >= 4.0`, o `CashConverters`), dispara alerta inmediata con cabecera `Click` antes de la IA pesada.
6. **Cruce con CeX (`fetch_cex_live` / `cex_cache`):** Busca el modelo en Algolia/espejo local para obtener `cash_price`, `voucher_price` y precio de venta.
7. **Puerta 1 IA — Pre-filtrado de Texto (`prescreen_item_text`):** Llamada ultraligera a `gemini-3.5-flash-lite` (con fallback en OpenRouter a `google/gemini-2.5-flash-lite` y `openai/gpt-4o-mini`) cacheada en `ai_prescreen_cache`.
8. **Deduplicación Semántica (`find_semantic_duplicate`):** Comprueba mediante embeddings (`gemini-embedding-2-preview`) si el mismo producto ya fue subido por otro usuario/plataforma.
9. **Puerta 2 IA — Auditoría Multimodal y Clasificación en 3 Niveles (`analyze_and_generate_kit`):**
   * Descarga en paralelo hasta 3 fotos, las comprime en RAM a `640x640 JPEG Q=78` y audita estado, originalidad, extras incluidos y rentabilidad en **3 Niveles (`NIVEL 1: Arbitraje CeX`, `NIVEL 2: Chollo P2P + Colchón CeX`, `NIVEL 3: Reventa P2P`)**.
   * Smart Model Routing: Si el ítem vale `<150€` y no es lote complejo, usa `gemini-3.5-flash-lite`; para ticket $\ge 150\text{€}$ o lotes con juegos/mandos extra, usa `gemini-3.5-flash`.
   * Si el anuncio es válido (`is_valid_deal == True`), registra la observación de precio en `price_history` y despacha la alerta enriquecida.

---

## 4. Esquema de Base de Datos SQLite (`sniper.db`)

Todas las tablas residen en `/opt/wallapop-sniper/sniper.db` bajo modo `WAL`:

* `seen_items`: Registro histórico de IDs/URLs procesadas y su último precio visto para detectar bajadas.
* `cex_cache` y `cex_catalog_mirror`: Caché puntual (TTL 3d) y espejo local del catálogo de CeX España.
* `ai_prescreen_cache`: Caché (TTL 7d) de decisiones de Puerta 1 por hash MD5 de `(radar_id | title | price)`.
* `ai_prompt_cache`: Caché de respuestas LLM por hash del prompt para evitar llamadas redundantes a las APIs.
* `deals`: Historial financiero y de alertas (`status`: `'ALERTED'`, `'BOUGHT'`, `'SOLD'`, `'DISCARDED'`).
* `item_embeddings`: Vectores semánticos de anuncios analizados para deduplicación por similitud coseno.
* `seen_image_hashes`: Huellas perceptuales (dHash 8x8 de 64 bits) para cazar fotos robadas entre diferentes cuentas.
* `price_history`: Observaciones reales de mercado validadas por IA para medianas y percentiles locales.
* `seller_reputation`: Historial de reputación por vendedor y plataforma (ventas, flags de fraude, score promedio).
* `ai_cost_log`: Auditoría de tokens y coste en euros por llamada a Gemini / OpenRouter.

---

## 5. Invariantes y Reglas de Oro (NO ROMPER)

1. **Prohibido `thinkingConfig` en Gemini API v1beta:** Nunca añadir `"thinkingConfig"` dentro de `"generationConfig"` en llamadas REST a `generativelanguage.googleapis.com`.
2. **Modelos vigentes:** Usar `gemini-3.5-flash-lite` para Puerta 1 y anuncios simples ($<150\text{ €}$), y `gemini-3.5-flash` para lotes complejos o ticket alto ($\ge 150\text{ €}$), manteniendo siempre la cadena de fallback a OpenRouter.
3. **Fail-Open sin envenenar caché:** Si las APIs de IA fallan por red o cuota, `prescreen_item_text` debe devolver `"proceed"` **sin guardar** ese resultado de emergencia en `ai_prescreen_cache`.
4. **Concurrencia SQLite:** Usar siempre `get_db()` (que configura `PRAGMA journal_mode=WAL;` y `busy_timeout=30000`). No compartir conexiones SQLite entre distintos hilos sin lock.
5. **Normalización de Ratings:** Wallapop devuelve valoraciones en escala `0-100` en endpoints internos; normalizar siempre a `0.0-5.0` antes de evaluar `SELLER_LOW_RATING` y no confundir `0 valoraciones` con `mala puntuación`.
6. **Thread-Safety en Clases Compartidas:** `AdaptiveRateLimiter`, `ParserHealthTracker` y administradores de sesión deben usar locks (`threading.Lock`) para evitar `RuntimeError: Set changed size during iteration`.

---

## 6. Comandos Rápidos de Validación

Antes de dar por terminada cualquier modificación, verifica:
1. **Sintaxis limpia en todo el proyecto:**
   `python3 -m py_compile core_engine.py bot.py multi_sniper.py fraud_signals.py antiban.py sniper.py vinted_sniper.py`
2. **Integridad de `config.json`:**
   `python3 -c "import json; cfg=json.load(open('config.json', encoding='utf-8')); print('Targets OK:', len(cfg.get('targets', cfg.get('searches', []))))"`
3. **Reinicio de contenedores en producción (si aplica):**
   `docker compose restart` (o `docker compose up -d --build`)
