# 🚀 PLAN DEFINITIVO V6 — Auditoría Profunda, Reparación de Fallos Críticos y Evolución Next-Level (Especial Gemini 3.8)

> **Documento Maestro de Ejecución Secuencial (Multi-Agente: Orquestador / Ejecutor / Verificador)**  
> **Fecha de Auditoría:** 2026-11-07  
> **Servidor de Producción:** `servidor` (`/opt/wallapop-sniper` ↔ `z:\opt_servidor`)  
> **Estado Inicial del Stack:** 4 contenedores activos (`wallapop-sniper`, `vinted-sniper`, `multi-sniper`, `relister-bot`) + `flaresolverr` (`172.24.0.1:8191` / `172.18.0.8:8191`) activo en el mismo host Docker.

---

## 📋 Reglas de Oro e Invariantes para Gemini 3.8 (LECTURA OBLIGATORIA)

1. **Entorno Remoto (Samba + SSH):**
   - **PROHIBIDO** ejecutar `python`, `pytest`, `pip` o `docker` en el PowerShell local de Windows.
   - Toda prueba, compilación o verificación debe ejecutarse por SSH en el servidor Linux:
     `ssh servidor "cd /opt/wallapop-sniper && <comando>"`
   - La edición de archivos (`read_file`, `edit_file`, `write_file`) se realiza directamente sobre `z:\opt_servidor\`.
2. **Invariantes Intocables del Código (`AGENTS.md`):**
   - **Prohibido `thinkingConfig` en Gemini API v1beta:** Jamás incluir `"thinkingConfig"` dentro de `"generationConfig"` en llamadas REST a `generativelanguage.googleapis.com`.
   - **Modelos vigentes en el servidor (verificados contra `/v1beta/models`):**
     - Pre-filtrado (Puerta 1) y Visión Ligera ($<70\text{ €}$): `gemini-3.5-flash-lite` (con fallback a `gemini-flash-lite-latest`, `gemini-2.5-flash-lite` y OpenRouter `google/gemini-2.5-flash-lite` / `openai/gpt-4o-mini`).
     - Visión Compleja / Ticket Alto ($\ge 70\text{ €}$ o lotes): `gemini-3.8-flash` (con fallback a `gemini-3.5-flash`, `gemini-flash-latest`, `gemini-2.5-flash` y OpenRouter `google/gemini-2.5-flash`).
     - Embeddings: `models/gemini-embedding-2` (con fallback a `models/gemini-embedding-2-preview`).
   - **Fail-Open sin envenenar caché:** Si las APIs de IA fallan por red o cuota, `prescreen_item_text` debe devolver `"proceed"` **sin guardar** ese resultado de emergencia en `ai_prescreen_cache`.
   - **Concurrencia SQLite:** Usar siempre `get_db()` (`PRAGMA journal_mode=WAL; busy_timeout=30000`).
   - **Retrocompatibilidad de Firmas:** No romper las firmas públicas de funciones consumidas por `sniper.py`, `vinted_sniper.py`, `multi_sniper.py`, `bot.py` o scripts de test.
3. **Flujo de Estados por Tarea:**
   - `[ ] PENDIENTE` ➔ `[~] EN PROGRESO` ➔ `[?] LISTO PARA VERIFICAR` ➔ `[x] VERIFICADO Y BLOQUEADO`
   - **Trabaja de una sola tarea a la vez.** No pases a la siguiente tarea hasta que el comando de verificación empírica por SSH devuelva `exit code 0`.

---

## 🔬 Resumen Ejecutivo de Hallazgos Empíricos (Auditoría en Vivo en el Servidor)

Durante la inspección profunda del código fuente, la base de datos en vivo (`/opt/wallapop-sniper/data/sniper.db`) y las trazas de red desde dentro de los contenedores Docker, se han descubierto **7 fallos críticos/altos en el producto actual** que estaban mermando silenciosamente la detección de chollos y la velocidad de escaneo:

| # | Subsistema Afectado | Diagnóstico Empírico Demostrado en Producción | Impacto Actual |
| :- | :--- | :--- | :--- |
| **F1** | `fraud_signals.py` (`enrich_seller_profile` en **Wallapop**) | Consulta solo `GET /api/v3/users/{id}`, que devuelve `micro_name` y `register_date`, pero **NO contiene `stats`, `scoring` ni `reviews_count`**. Esos datos están en `GET /api/v3/users/{id}/stats` (`counters: sells/reviews`, `rating_average`). | **El 100% de vendedores de Wallapop reciben `0 valoraciones` (`SELLER_ZERO_REVIEWS`, `risk +2`, `block_flash=True`), bloqueando el 100% de las Flash Alerts en Wallapop.** |
| **F2** | `vinted_sniper.py` + `fraud_signals.py` (`enrich_seller_profile` en **Vinted**) | `vinted_sniper.py` (L732) llama a `enrich_seller_profile(item)` **sin pasar `http_session=scraper.session`**. Sin las cookies de sesión de Vinted, `GET /api/v2/users/{id}` devuelve **`HTTP 401` el 100% de las veces**. Con `scraper.session`, devuelve `HTTP 200` con `feedback_count` y `feedback_reputation`. | **El 100% de vendedores de Vinted reciben `0 valoraciones` (`risk=2`)** y si Puerta 1 da `"unsure"` o `"reject"`, se descartan y registran erróneamente en `fraud_log`. |
| **F3** | `vinted_sniper.py` (`VintedClient.fetch_items`) | Intenta primero `GET /api/v2/catalog/items`, endpoint que en Vinted devuelve **`HTTP 404` el 100% de las veces**, registrando el `404` en `default_rate_limiter` antes de usar el fallback funcional `/catalog` (`HTTP 200`, 96 anuncios RSC). | **`AdaptiveRateLimiter` detecta 40-50% de errores no-200 (`>20%`) y penaliza el ciclo de Vinted con `+50%` continuo hasta >5.3 minutos por ronda.** |
| **F4** | `core_engine.py` (`pre_gate_cex_offer_margin` y `lookup_cex_catalog_mirror`) | 1) `bundle_markers` (L3149) incluye `"con "` (preposición española "con caja", "con lector", "con cargador"), haciendo que el **>80% de anuncios salten el filtro como `bundle_potential`**. 2) `lookup_cex_catalog_mirror` (L1998) toma los 2 tokens más largos (`"consola"`, `"tarjeta"`, `"grafica"`, `"volante"`), que no existen en `box_name` de CeX, fallando en **13 de 14 títulos reales (`no_mirror_match`)**. 3) `CEX_MASTER_QUERIES` solo sincroniza 12 queries, dejando fuera cámaras, portátiles gaming, retro y Kindles. 4) `direct_max` ignora `submodel_price_caps`. | **`pre_gate_cex_offer_margin` deja pasar casi todos los anuncios sin margen a Puerta 1 y Puerta 2.** |
| **F5** | `sniper.py`, `vinted_sniper.py`, `multi_sniper.py`, `core_engine.py` | 1) Ningún escáner pasa `cex_cash` a `evaluate_fraud_risk(...)` (usan `cex_cash=0.0`), por lo que `check_impossible_price` y `ANOMALOUS_BELOW_CEX_CASH_NEW_SELLER` **jamás se activan en producción**. 2) Ningún escáner inyecta `ai_data["fraud_risk_score"] = fraud_eval["risk"]`, por lo que `classify_deal_tier` siempre recibe `fraud_risk_score=0`. | **Reglas antifraude de precio vs CeX inactivas en producción y clasificación de 3 Niveles ciega al riesgo del vendedor.** |
| **F6** | `multi_sniper.py` + `core_engine.py` (**Milanuncios WAF**) | Milanuncios devuelve el challenge Imperva Incapsula (`reese84` / `Pardon Our Interruption`, `len=95989`) bloqueando a `multi-sniper` cada 15 min, mientras que el contenedor `flaresolverr` (`http://172.24.0.1:8191/v1`) ya corre en el mismo host y resuelve Milanuncios devolviendo `1.55 MB` con `window.__INITIAL_PROPS__` y la cookie `reese84`. | **Milanuncios está 0% operativo hoy; integrando el `flaresolverr` local como *Cookie Farm* pasa a 100% operativo.** |
| **F7** | `sniper.py`, `vinted_sniper.py`, `config.json` | 1) `sniper.py` y `vinted_sniper.py` no aplican la cola de prioridad `Fast-Lane` (`price / max_price`) ni disparan `send_one_tap_ntfy_alert`. 2) `sniper.py` descarta anuncios con `age_days > 1.0` aunque acaben de bajar de precio hoy. 3) `hdd_server_storage` incluye discos de `2TB` (`cap 35€`) y `3TB` (`cap 42€`) que con `min_savings_eur: 25€` son matemáticamente imposibles de aprobar, quemando IA en vano. | **Pérdida de velocidad en el chollo más extremo, ausencia de alertas push 1-Tap en Wallapop/Vinted y gasto inútil en discos de 2TB/3TB.** |

---

# 🛠️ PARTE I: BLOQUES DE ACTUALIZACIÓN Y ARREGLO DEL PRODUCTO ACTUAL

---

## BLOQUE A1 (`TASK-A1`): Reparación Integral de `enrich_seller_profile` en Wallapop (`/stats`) y Vinted (Sesión con Cookies)

- **Estado:** `[ ] PENDIENTE`
- **Archivos a modificar:**
  - `z:\opt_servidor\fraud_signals.py` (líneas 424–535)
  - `z:\opt_servidor\vinted_sniper.py` (líneas 730–780)
- **Problema Raíz Demostrado:**
  1. En **Wallapop**, `GET https://api.wallapop.com/api/v3/users/{seller_id}` devuelve `{"id": "...", "micro_name": "...", "register_date": 1729983135000, "is_top_profile": true, "seller_type": {"type": "Shop", "verified": true}}`, pero **NO incluye valoraciones ni ventas**. Las métricas reales están en `GET https://api.wallapop.com/api/v3/users/{seller_id}/stats`, que devuelve:
     ```json
     {
       "ratings": [{"type": "reviews", "value": 97}],
       "counters": [
         {"type": "publish", "value": 106},
         {"type": "buys", "value": 0},
         {"type": "sells", "value": 230},
         {"type": "reviews", "value": 75},
         {"type": "sold", "value": 230},
         {"type": "reports_received", "value": 2}
       ],
       "rating_average": 4.9,
       "deliveries_on_time_in_days": 0
     }
     ```
  2. En **Vinted**, `vinted_sniper.py` (L732) invoca `enrich_seller_profile(item)` sin pasar `http_session=scraper.session`. Al no llevar las cookies de Vinted (`access_token_web`, `_vinted_fr_session`), `GET https://www.vinted.es/api/v2/users/{seller_id}` responde `HTTP 401`. Pasando `http_session=scraper.session`, responde `HTTP 200` con `user.feedback_count`, `user.feedback_reputation` (`0.0–1.0`), `user.login` y `user.created_at`.
  3. Además, en `vinted_sniper.py` (L766), cuando `ps_decision == "reject"` (ej. un juego suelto o mando rechazado por Puerta 1) y el vendedor tiene `risk in (2, 3)`, el código entra en `if fraud_eval["risk"] in (2, 3) and ps_decision != "proceed":` y llama a `log_fraud_event(...)`, ensuciando `fraud_log` y `seller_reputation` con rechazos normales de catálogo.

- **Pasos Exactos de Implementación:**
  1. En `fraud_signals.py` dentro de `enrich_seller_profile(item_data: dict, http_session=None)` (bloque `if platform == "WALLAPOP":`, L424):
     - Mantener la petición a `https://api.wallapop.com/api/v3/users/{seller_id}` (con cabeceras `{"User-Agent": "Mozilla/5.0", "X-DeviceOS": "0"}` y `timeout=2.5`) para extraer:
       - `micro_name = u_data.get("micro_name") or u_data.get("name")` (actualizar `seller["name"]` si estaba vacío o era igual a `seller_id`).
       - `reg_date = u_data.get("register_date") or u_data.get("created_at")` ➔ calcular `joined_days`.
       - `verified`: `True` si `u_data.get("is_top_profile") is True` o `(u_data.get("seller_type") or {}).get("verified") is True` o cualquier valor de `u_data.get("verification") or {}` es verdadero.
       - Soportar también los campos legacy `u_data.get("stats")` y `u_data.get("scoring")` por si un mock/test unitario los inyecta en `/users/{seller_id}`.
     - Si tras consultar `/users/{seller_id}` `reviews_count is None` o `rating is None`, realizar inmediatamente una segunda llamada rápida a `https://api.wallapop.com/api/v3/users/{seller_id}/stats` (`timeout=2.5`, cabeceras `{"User-Agent": "Mozilla/5.0", "X-DeviceOS": "0"}`):
       - Si `r_stats.status_code == 200`:
         - Parsear `st_data = r_stats.json()`.
         - Buscar en `st_data.get("counters", [])` (lista de dicts `{"type": ..., "value": ...}`): extraer el valor de `"reviews"` y de `"sells"` / `"sold"`. Definir `reviews_count = max(rev_cnt, sells_cnt)` (o `rev_cnt` si existe, y guardar `seller["sales"] = sells_cnt`).
         - Si `st_data.get("rating_average") is not None` y `float(st_data["rating_average"]) > 0`: usar `rating = float(st_data["rating_average"])` (que ya viene en escala `0.0–5.0`, ej. `4.9`).
         - Como fallback si `rating_average` no está, buscar en `st_data.get("ratings", [])` el elemento con `"type": "reviews"` y tomar su `"value"` (escala `0–100`, que la regla v5.2 normaliza automáticamente dividiendo entre `20.0`).
  2. En `fraud_signals.py` dentro del bloque `elif platform == "VINTED":` (L461):
     - Si `u_data.get("login")` existe y `not seller.get("name")`: asignar `seller["name"] = u_data["login"]`.
     - Si `reviews_count` es `0` pero `u_data.get("given_item_count", 0) > 0`: guardar `seller["sales"] = int(u_data.get("given_item_count", 0))`.
     - **Importante (No cachear fallos 401/403):** Tanto en Wallapop como en Vinted, solo guardar en `_SELLER_PROFILE_CACHE` si la petición HTTP tuvo éxito (`status_code == 200`) o si ya venían datos válidos en `seller`, evitando cachear durante 30 minutos un perfil vacío `{reviews_count: 0}` cuando una petición falle por red o 401.
  3. En `vinted_sniper.py` (alrededor de L732):
     - Cambiar `enrich_seller_profile(item)` por `enrich_seller_profile(item, http_session=scraper.session)`.
     - En L764–775 de `vinted_sniper.py`, verificar primero `if ps_decision == "reject":` (para descartar limpiamente por Puerta 1 sin llamar a `log_fraud_event`), y solo si `ps_decision == "unsure"` y `fraud_eval["risk"] == 3` (riesgo moderado-alto real, no un simple `risk=2` por 0 valoraciones) descartar por precaución.

- **Verificación Empírica (SSH):**
  ```bash
  ssh servidor "cd /opt/wallapop-sniper && docker exec wallapop-sniper python3 -c '
  import fraud_signals, vinted_sniper
  # 1. Test real Wallapop seller enrichment
  it_w = {\"platform\": \"WALLAPOP\", \"seller\": {\"id\": \"vjrd122kvx6k\", \"name\": \"vjrd122kvx6k\"}}
  p_w = fraud_signals.enrich_seller_profile(it_w)
  print(\"Wallapop enriched:\", p_w)
  assert p_w[\"seller_reviews_count\"] >= 50, f\"Expected >=50 reviews, got {p_w}\"
  assert 4.0 <= (p_w[\"seller_rating\"] or 0) <= 5.0, f\"Expected rating 4.0..5.0, got {p_w}\"
  assert \"Fiable\" in p_w[\"seller_trust_badge\"] or \"Verificado\" in p_w[\"seller_trust_badge\"]

  # 2. Test real Vinted seller enrichment with VintedClient.session
  vc = vinted_sniper.VintedClient()
  it_v = {\"platform\": \"VINTED\", \"seller\": {\"id\": \"194476621\", \"name\": \"\"}}
  p_v = fraud_signals.enrich_seller_profile(it_v, http_session=vc.session)
  print(\"Vinted enriched:\", p_v)
  assert p_v[\"seller_reviews_count\"] >= 10, f\"Expected >=10 Vinted reviews, got {p_v}\"
  assert 3.5 <= (p_v[\"seller_rating\"] or 0) <= 5.0, f\"Expected Vinted rating 3.5..5.0, got {p_v}\"
  print(\"TASK-A1 OK\")
  '"
  ```

---

## BLOQUE A2 (`TASK-A2`): Eliminación del Cuello de Botella `HTTP 404` en Vinted (`VintedClient.fetch_items`), Cola Fast-Lane y Flash Alerts 1-Tap

- **Estado:** `[ ] PENDIENTE`
- **Archivos a modificar:**
  - `z:\opt_servidor\vinted_sniper.py` (líneas 86–250 y 690–860)
- **Problema Raíz Demostrado:**
  1. En `vinted_sniper.py` (L111–131), `VintedClient.fetch_items` llama primero a `https://www.vinted.es/api/v2/catalog/items?...`, endpoint que hoy devuelve **`HTTP 404` el 100% de las veces** (`len=9278`). Justo en L119 ejecuta `default_rate_limiter.record_request("VINTED", time.time() - t0, r.status_code)` registrando un `404`, y luego cae a `_fetch_items_catalog_fallback` (`https://www.vinted.es/catalog?...`), que devuelve `HTTP 200` con 96 productos en el JSON hidratado RSC.
  2. Como consecuencia, exactamente el **50% de las peticiones de búsqueda de Vinted registran código 404** en `default_rate_limiter`, provocando que `AdaptiveRateLimiter` dispare en cada ronda `Aumento +50% por: errores no-200 40% > 20%` y eleve la pausa de Vinted de 45s a **212s** (+108s de ronda = >5 minutos por vuelta).
  3. Además, `vinted_sniper.py` procesa los anuncios de una búsqueda en orden arbitrario (sin la cola `Fast-Lane` por descuento relativo `price / max_price`) y **no envía alertas Flash (`send_flash_discord_alert` ni `send_one_tap_ntfy_alert`)** cuando aparece un chollo directo de un vendedor fiable.

- **Pasos Exactos de Implementación:**
  1. En `VintedClient.fetch_items` (`vinted_sniper.py` L86–218):
     - Convertir `_fetch_items_catalog_fallback(query_text, p_min, p_max)` en el **método primario directo** de `fetch_items` (ya que `https://www.vinted.es/catalog?...` devuelve `HTTP 200` a la primera e incluye las 96 fichas completas con fotos HD, `totalItemPrice`, `user.id`, `user.login`, `brand` y `status` dentro del bloque RSC `\"items\":[...]`).
     - Añadir un flag de clase `self._api_v2_available = False`: solo intentar `/api/v2/catalog/items` si `_fetch_items_catalog_fallback` falla o devuelve vacío, y **nunca registrar un `404` de un endpoint experimental en `default_rate_limiter`**.
     - En `_fetch_items_catalog_fallback` (L291–321), si `user_data` dentro del JSON RSC incluye `feedback_reputation` o `feedback_count` (o si no vienen, dejarlos en `None` para que `enrich_seller_profile(item, http_session=scraper.session)` los complete), preservarlos en `seller`.
  2. En el bucle de evaluación de candidatos de `vinted_sniper.py` (alrededor de L690–760):
     - Ordenar la lista de `items` de cada radar mediante la clave **Fast-Lane** antes de iterar (igual que hace `multi_sniper.py`):
       `items.sort(key=lambda x: float(x.get("price") or 9999.0) / max(float(target.get("max_price") or 1.0), 1.0))`
     - Importar `can_send_flash_alert`, `send_flash_discord_alert` y `send_one_tap_ntfy_alert` desde `core_engine`.
     - Tras ejecutar `enrich_seller_profile(item, http_session=scraper.session)` y `fraud_eval = evaluate_fraud_risk(...)`, comprobar `if can_send_flash_alert(item, target, fraud_eval):` y disparar `send_flash_discord_alert(...)` + `send_one_tap_ntfy_alert(...)` antes de las llamadas pesadas a IA.

- **Verificación Empírica (SSH):**
  ```bash
  ssh servidor "cd /opt/wallapop-sniper && docker exec vinted-sniper python3 -c '
  import vinted_sniper
  vinted_sniper.default_rate_limiter._records.clear()
  vc = vinted_sniper.VintedClient()
  items = vc.fetch_items(\"Nintendo Switch OLED\", min_price=30, max_price=200)
  recs = vinted_sniper.default_rate_limiter._records.get(\"VINTED\", [])
  codes = [r[\"status_code\"] for r in recs]
  print(\"Fetched items:\", len(items), \"Recorded HTTP codes:\", codes)
  assert len(items) > 0, \"Expected Vinted items > 0\"
  assert all(c == 200 for c in codes), f\"Expected only 200 codes, got {codes}\"
  print(\"TASK-A2 OK\")
  '"
  ```

---

## BLOQUE A3 (`TASK-A3`): Reparación del Espejo Local CeX (`lookup_cex_catalog_mirror`, `CEX_MASTER_QUERIES`) y Pre-Gating Matemático (`pre_gate_cex_offer_margin`)

- **Estado:** `[ ] PENDIENTE`
- **Archivos a modificar:**
  - `z:\opt_servidor\core_engine.py` (líneas 1521–1560, 1771–2050 y 3128–3183)
- **Problema Raíz Demostrado:**
  En nuestra prueba empírica con 14 anuncios reales contra `lookup_cex_catalog_mirror` y `pre_gate_cex_offer_margin`, **13 de 14 fallaron** por 4 bugs combinados:
  1. **Bug 1 (`bundle_markers` en L3149 tiene `"con "`):**
     `bundle_markers = ("juego", "juegos", "mando", "mandos", "pack", "lote", "bundle", "+", "con ")`
     Cualquier anuncio en español que diga `"con lector"`, `"con caja"`, `"con cargador"`, `"con factura"` o `"con garantía"` contiene `"con "` y retorna inmediatamente `(True, "bundle_potential")`, desactivando `pre_gate_cex_offer_margin`.
  2. **Bug 2 (`lookup_cex_catalog_mirror` en L1981–2008 descarta tokens cortos de modelo y exige palabras genéricas inexistentes en `box_name`):**
     - Cuando busca `"Consola Nintendo Switch Lite Azul"`, `"Consola New Nintendo 3DS XL"`, `"Tarjeta gráfica GeForce RTX 3070 8GB"` o `"Apple Mac Mini M1"`, el primer `SELECT` exige `AND` de **todas** las palabras en `box_name` (falla porque CeX no pone `"Consola"`, `"Tarjeta"`, `"Gráfica"`, `"Volante"`, `"Cámara"` ni `"Apple"` en `box_name` de Mac Mini).
     - El segundo `SELECT` de respaldo hace `best_tokens = sorted(tokens, key=len, reverse=True)[:2]`, eligiendo las 2 palabras más largas (`"nintendo"` + `"consola"`, o `"tarjeta"` + `"grafica"`), ignorando los tokens de modelo de 2–4 caracteres (`"ps5"`, `"3ds"`, `"xl"`, `"g29"`, `"rtx"`, `"3070"`, `"m1"`, `"m2"`, `"lite"`, `"oled"`) y exigiendo `"consola"` o `"tarjeta"`, lo que devuelve `0 filas` (`None`).
     - Además, CeX nombra las consolas PS5 como `"Playstation 5..."` y nunca escribe `"PS5"` en `box_name`, por lo que buscar `"PS5 Slim"` con `box_name LIKE '%ps5%'` no encuentra las consolas si no se expande el sinónimo `ps5` ➔ `playstation 5`.
  3. **Bug 3 (`CEX_MASTER_QUERIES` en L1771 solo tiene 12 queries):**
     Faltan en `cex_catalog_mirror` las fichas de `ASUS ROG Ally`, `Lenovo Legion Go`, `Sony A6000`, `Sony A6400`, `Sony ZV-E10`, `Fujifilm X-T20`, `Fujifilm X-T30`, `Canon SX740`, `Gameboy Advance SP`, `Gameboy Micro`, `Nintendo DSi XL`, `Thrustmaster T300`, `Kindle Paperwhite`, `Kindle Oasis`, `Switch 2`, `Playstation 5 Pro`, `Meta Quest 3`, `MacBook Air`.
  4. **Bug 4 (`pre_gate_cex_offer_margin` L3138 ignora `submodel_price_caps` y `mode == "personal"`):**
     Calcula `direct_max = float(target.get("direct_buy_max_price") or target.get("max_price") or 0.0)` sin evaluar `submodel_price_caps`. Así, una `Switch Lite` a `95€` (cuyo cap de submodelo es `80€`, estando en zona de oferta) se compara contra `direct_max = 95€` de la Switch V2 y pasa como `"direct_zone"`. Y en `mode == "personal"` (`hdd_server_storage`), no verifica si el ahorro mínimo (`min_savings_eur`) es alcanzable frente al tope del submodelo.

- **Pasos Exactos de Implementación:**
  1. En `core_engine.py` (L1771), ampliar `CEX_MASTER_QUERIES` para cubrir el 100% de los radares actuales y nuevos:
     ```python
     CEX_MASTER_QUERIES = [
         "Nintendo Switch Consola",
         "Switch 2 Consola",
         "Nintendo 3DS Consola",
         "Nintendo 2DS Consola",
         "Nintendo DSi XL",
         "PS Vita Consola",
         "Gameboy Advance SP",
         "Gameboy Micro",
         "Steam Deck",
         "ASUS ROG Ally",
         "Lenovo Legion Go",
         "PlayStation 5 Consola",
         "Playstation 5 Pro",
         "Pokemon",
         "GeForce RTX",
         "Apple iPad",
         "Mac Mini",
         "MacBook Air M1",
         "MacBook Air M2",
         "Sony RX100",
         "Canon PowerShot G7X",
         "Canon PowerShot SX740",
         "Sony A6000",
         "Sony ZV-E10",
         "Fujifilm X-T20",
         "Fujifilm X-T30",
         "Logitech G29",
         "Thrustmaster T300",
         "Kindle Paperwhite",
         "Kindle Oasis",
         "Meta Quest 3",
         "DJI Osmo Pocket 3",
         "Retroid Pocket",
         "Anbernic"
     ]
     ```
  2. En `lookup_cex_catalog_mirror` (`core_engine.py` L1961–2015):
     - Definir un conjunto de palabras genéricas de anuncio que **no forman parte de `box_name` en CeX** (`CEX_GENERIC_NOISE_WORDS = {"consola", "console", "consolas", "videojuego", "juego", "juegos", "tarjeta", "grafica", "tarjetas", "graficas", "camara", "camaras", "cuerpo", "body", "volante", "pedales", "lector", "disco", "discos", "edicion", "version", "original", "oficial", "nuevo", "nueva", "usado", "impecable", "perfecto", "estado", "caja", "factura", "garantia", "envio", "regalo", "vendo", "urge", "bundle", "pack", "lote", "azul", "rojo", "negro", "blanco", "blanca", "gris", "plata", "rosa", "coral", "verde", "amarillo", "turquesa", "neon"}`).
     - Al extraer `q_tokens` de `q_norm`, filtrar `CEX_GENERIC_NOISE_WORDS` y mantener tokens de $\ge 2$ caracteres relevantes (`"ps5"`, `"3ds"`, `"2ds"`, `"xl"`, `"g29"`, `"rtx"`, `"m1"`, `"m2"`, `"sp"`, `"g7x"`, etc.).
     - Normalizar sinónimos clave de CeX antes de construir la consulta SQL:
       - Si `"ps5"` está en `q_tokens` y `"playstation"` no: reemplazar `"ps5"` por `"playstation"` + `"5"` (o buscar `(box_name LIKE '%playstation 5%' OR box_name LIKE '%ps5%')`).
       - Si `"game"` y `"boy"` están separados, buscar `(box_name LIKE '%gameboy%' OR box_name LIKE '%game boy%')`.
       - Si `"apple"` está en `q_tokens` junto con `"mac"` y `"mini"`, no exigir `"apple"` en el `AND` porque CeX titula `"Mac Mini 14,3/M2..."` sin `"Apple"`.
     - Si el `SELECT` con `AND` de todos los tokens filtrados no devuelve filas, hacer un fallback inteligente priorizando tokens de `STRICT_MODEL_TOKENS` / alfanuméricos de modelo (`rtx`, `3060`, `3070`, `a6000`, `zv`, `e10`, `xt20`, `xt30`, `g7x`, `sx740`, `rx100`, `g29`, `t300`, `paperwhite`, `oasis`, `switch`, `3ds`, `2ds`, `vita`, `deck`, `ally`, `legion`, `playstation`, `mac`, `ipad`, `micro`, `advance`, `dsi`), ejecutando el `SELECT` con los 1–2 tokens de modelo principales y dejando que `_box_matches_query_and_radar` y `_score_cex_candidate` filtren y puntúen con precisión en memoria.
  3. En `pre_gate_cex_offer_margin` (`core_engine.py` L3128–3183):
     - Calcular el tope de compra directa real (`effective_direct_max`) aplicando las reglas de `target.get("submodel_price_caps", [])` sobre `f"{title} {desc}".lower()`, exactamente igual que hace `evaluate_deal_math_and_score` (L3767–3775).
     - Si `target.get("mode") == "personal"`:
       - Estimar `optimal_price = effective_direct_max` y `min_savings = float(target.get("min_savings_eur", 25.0))`.
       - Si incluso con el descuento máximo de regateo (`min_achievable_landed`) el ahorro `optimal_price - min_achievable_landed < min_savings * 0.75`, devolver `(False, f"pre_gate_personal_savings_impossible (ask={price:.0f}€, cap={optimal_price:.0f}€, min_savings={min_savings:.0f}€)")`.
     - Reemplazar `"con "` en `bundle_markers` (L3149) por marcadores reales de lote:
       `bundle_markers = ("juego", "juegos", "mando extra", "2 mandos", "dos mandos", "mandos", "pack", "lote", "bundle", "+")`
       y comprobar también `bool(re.search(r"\bcon\s+(?:\d+|dos|tres|varios)\s+(?:juegos|mandos)\b", text))`.
     - Pasar `title_context=title, desc_context=desc` a `lookup_cex_catalog_mirror(conn, title, target=target, title_context=title, desc_context=desc)`.

- **Verificación Empírica (SSH):**
  ```bash
  ssh servidor "cd /opt/wallapop-sniper && docker exec multi-sniper python3 -c '
  import core_engine
  conn = core_engine.init_db()
  synced = core_engine.sync_cex_catalog_mirror(conn, force=True)
  print(\"Synced mirror rows:\", synced)
  cfg = core_engine.load_config()
  t_map = {t[\"id\"]: t for t in cfg[\"targets\"]}
  tests = [
      (\"ps5_consoles_gta6\", \"Consola PlayStation 5 Slim con lector\", 420.0),
      (\"new_3ds_family\", \"Consola New Nintendo 3DS XL\", 190.0),
      (\"switch_std_lite\", \"Consola Nintendo Switch Lite Azul\", 100.0),
      (\"steam_deck_handhelds\", \"Consola Valve Steam Deck 512GB\", 340.0),
      (\"steam_deck_handhelds\", \"Asus ROG Ally Z1 Extreme 512GB\", 350.0),
      (\"mirrorless_cameras\", \"Cámara Sony Alpha A6000 con 16-50mm\", 280.0),
      (\"digicams_premium\", \"Canon PowerShot G7X Mark II\", 350.0),
      (\"apple_high_liquidity\", \"Apple Mac Mini M1 8GB 256GB\", 320.0),
      (\"rtx_gpus\", \"Tarjeta gráfica GeForce RTX 3070 8GB\", 220.0),
      (\"simracing_wheels\", \"Volante Logitech G29 con pedales\", 140.0),
      (\"kindle_ereaders\", \"Amazon Kindle Paperwhite 16GB\", 80.0),
      (\"retro_handhelds\", \"Game Boy Advance SP AGS-001\", 75.0),
  ]
  hits = 0
  for rid, title, p in tests:
      m = core_engine.lookup_cex_catalog_mirror(conn, title, target=t_map[rid], title_context=title, radar_id=rid)
      print(f\"{rid:22s} -> {m.get(\\\"box_name\\\") if m else None} (cash={m.get(\\\"cash\\\") if m else None})\")
      if m and m.get(\"cash\", 0) > 0:
          hits += 1
  assert hits >= 10, f\"Expected >=10 mirror hits out of 12, got {hits}\"
  print(\"TASK-A3 OK:\", hits, \"/\", len(tests))
  '"
  ```

---

## BLOQUE A4 (`TASK-A4`): Conexión End-to-End de `cex_cash` en Puerta 0 (`evaluate_fraud_risk`) y `fraud_risk_score` en Puerta 2 (`evaluate_deal_math_and_score`)

- **Estado:** `[ ] PENDIENTE`
- **Archivos a modificar:**
  - `z:\opt_servidor\sniper.py` (líneas 405–540)
  - `z:\opt_servidor\vinted_sniper.py` (líneas 730–880)
  - `z:\opt_servidor\multi_sniper.py` (líneas 280–440)
  - `z:\opt_servidor\core_engine.py` (líneas 1450–1456 y 3850–3875)
- **Problema Raíz Demostrado:**
  1. En `sniper.py` (L418), `vinted_sniper.py` (L741) y `multi_sniper.py` (L292), `evaluate_fraud_risk(...)` se invoca **sin pasar `cex_cash`** (`cex_cash=0.0`). Por ello, las dos reglas críticas antifraude basadas en CeX:
     - `check_impossible_price` (`price < 0.35 * cex_cash` para `cex_cash >= 90€`)
     - `ANOMALOUS_BELOW_CEX_CASH_NEW_SELLER` (`TASK-05`: `cex_val >= 80.0 and price_val < cex_val * 0.82` con `reviews_count == 0` y `joined_days <= 30`)
     **jamás se han ejecutado en producción.**
  2. En `core_engine.py` (L3855), `evaluate_deal_math_and_score` lee `fraud_risk_num = int(ai_data.get("fraud_risk_score", 0) or 0)`, pero ningún escáner inyecta `ai_data["fraud_risk_score"] = fraud_eval["risk"]` ni `ai_data["seller_trust_badge"] = fraud_eval.get("seller_trust_badge", "")` antes de llamar a `evaluate_deal_math_and_score`.
  3. En `sniper.py` (L419) y `multi_sniper.py` (L293), el bloqueo comprueba `if fraud_eval["is_fraud"]:` en vez de `if fraud_eval["is_fraud"] or fraud_eval.get("should_block"):`.

- **Pasos Exactos de Implementación:**
  1. En `sniper.py`, `vinted_sniper.py` y `multi_sniper.py`, justo antes de llamar a `evaluate_fraud_risk(...)`:
     - Consultar en `<1ms` el espejo local CeX:
       ```python
       fast_mirror = lookup_cex_catalog_mirror(conn, title, target=target, title_context=title, desc_context=desc, radar_id=target.get("id", ""))
       fast_cex_cash = float(fast_mirror.get("cash", 0.0) or 0.0) if isinstance(fast_mirror, dict) else 0.0
       ```
     - Pasar `cex_cash=fast_cex_cash` a `evaluate_fraud_risk(...)`.
     - Comprobar `if fraud_eval["is_fraud"] or fraud_eval.get("should_block"):` para descartar y registrar en `log_fraud_event`.
  2. En `sniper.py`, `vinted_sniper.py` y `multi_sniper.py`, justo después de `ai_data = analyze_and_generate_kit(...)` y antes de `evaluate_deal_math_and_score(...)`:
     - Si `isinstance(ai_data, dict)`:
       ```python
       ai_data["fraud_risk_score"] = int(fraud_eval.get("risk", 0) or 0)
       ai_data["seller_trust_badge"] = fraud_eval.get("seller_trust_badge") or item.get("seller_trust_badge", "")
       ```
  3. En `sniper.py`:
     - Añadir el ordenamiento **Fast-Lane** de candidatos nuevos por radar (`candidates.sort(key=lambda x: float(x.get("price") or 9999.0) / max(float(target.get("max_price") or 1.0), 1.0))`) antes de evaluarlos.
     - Invocar `send_one_tap_ntfy_alert(...)` junto con `send_flash_discord_alert(...)` cuando `can_send_flash_alert(...)` sea `True`.
     - En L392 de `sniper.py`, permitir que anuncios con `age_days > 1.0` continúen si `modified_days <= 1.0` (calculado desde `item.get("modified_at")`) o si `check_item_seen_or_dropped` detectó una bajada de precio (`reason.startswith("price_drop")`).

- **Verificación Empírica (SSH):**
  ```bash
  ssh servidor "cd /opt/wallapop-sniper && docker exec wallapop-sniper python3 -c '
  import core_engine, fraud_signals
  conn = core_engine.init_db()
  cfg = core_engine.load_config()
  t_ps5 = [t for t in cfg[\"targets\"] if t[\"id\"] == \"ps5_consoles_gta6\"][0]
  title = \"PlayStation 5 Slim 1TB con lector\"
  m = core_engine.lookup_cex_catalog_mirror(conn, title, target=t_ps5, title_context=title, radar_id=\"ps5_consoles_gta6\")
  fast_cash = float(m.get(\"cash\", 0.0)) if m else 360.0
  scam_seller = {\"id\": \"scam99\", \"reviews_count\": 0, \"age_days\": 2}
  fe = fraud_signals.evaluate_fraud_risk(title, \"Como nueva\", 220.0, radar_id=\"ps5_consoles_gta6\", seller=scam_seller, cex_cash=fast_cash)
  print(\"Fraud eval with fast_cash:\", fe[\"risk\"], fe[\"should_block\"], [r[\"code\"] for r in fe[\"reasons\"]])
  assert fe[\"should_block\"] is True and fe[\"risk\"] >= 4
  assert any(r[\"code\"] == \"ANOMALOUS_BELOW_CEX_CASH_NEW_SELLER\" for r in fe[\"reasons\"])
  print(\"TASK-A4 OK\")
  '"
  ```

---

## BLOQUE A5 (`TASK-A5`): Desbloqueo de Milanuncios mediante Cookie Farming con `FlareSolverr` Local (`http://172.24.0.1:8191/v1`)

- **Estado:** `[ ] PENDIENTE`
- **Archivos a modificar:**
  - `z:\opt_servidor\docker-compose.yml`
  - `z:\opt_servidor\core_engine.py` (líneas 2353–2525, `MilanunciosSessionManager` y `fetch_milanuncios`)
- **Problema Raíz Demostrado:**
  - Milanuncios protege `/anuncios/` con el WAF Imperva Incapsula (`reese84`), devolviendo un HTML de bloqueo (`len=95989`, `"Pardon Our Interruption"`) ante peticiones directas sin token `reese84` resuelto por navegador real.
  - En el propio servidor ya está corriendo el contenedor `flaresolverr` (`v3.5.2`, Chrome 152), accesible desde la red de Docker en `http://172.24.0.1:8191/v1` (y vía `host.docker.internal:8191`).
  - Probado empíricamente desde dentro de `multi-sniper`: al solicitar una URL de Milanuncios a `FlareSolverr`, este resuelve el reto Imperva en ~4s y devuelve **1,557,842 bytes de HTML real con `window.__INITIAL_PROPS__`** y la cookie `reese84` válida durante ~30 minutos.

- **Pasos Exactos de Implementación:**
  1. En `docker-compose.yml`:
     - Añadir en el servicio `multi-sniper` (y opcionalmente en `wallapop-sniper` y `vinted-sniper`):
       ```yaml
       extra_hosts:
         - "host.docker.internal:host-gateway"
       environment:
         - FLARESOLVERR_URL=${FLARESOLVERR_URL:-http://172.24.0.1:8191/v1}
       ```
  2. En `core_engine.py`, mejorar `MilanunciosSessionManager` y `fetch_milanuncios` (L2353–2525):
     - Añadir en `MilanunciosSessionManager` soporte de **Cookie Farming + Fallback por FlareSolverr**:
       - Atributos: `self.flaresolverr_cookies = {}`, `self.flaresolverr_ua = ""`, `self.last_flaresolverr_ts = 0.0`, `self.flaresolverr_urls = [os.getenv("FLARESOLVERR_URL", "http://172.24.0.1:8191/v1"), "http://host.docker.internal:8191/v1", "http://172.18.0.8:8191/v1"]`.
       - Método `fetch_via_flaresolverr(self, url, timeout_ms=20000) -> str`:
         - Itera por `self.flaresolverr_urls`, hace `POST` con `{"cmd": "request.get", "url": url, "maxTimeout": timeout_ms}`.
         - Si `status == 200` y `"solution"` contiene `"response"` sin `"Pardon Our Interruption"`:
           - Extrae las cookies (`sol.get("cookies", [])`), actualiza `self.flaresolverr_cookies` (especialmente `reese84` y `didomi_token`) y `self.flaresolverr_ua = sol.get("userAgent", "")`.
           - Inyecta esas cookies y `User-Agent` en `self.session` para que las siguientes peticiones rápidas de `curl_cffi` reutilicen el pase Imperva sin pasar por FlareSolverr en cada query.
           - Devuelve el HTML `sol.get("response", "")`.
     - En `fetch_milanuncios(rotator, query_text, min_price, max_price)`:
       - Si la petición rápida con `self.session` devuelve bloqueo WAF (`r.status_code in (403, 429)` o `"Pardon Our Interruption"` en `r.text` o `len(r.text) < 130000` sin `__INITIAL_PROPS__`):
         - Antes de rendirse y pausar Milanuncios 15 minutos, invocar `html_text = _milanuncios_mgr.fetch_via_flaresolverr(url)`.
         - Si `html_text` contiene `__INITIAL_PROPS__` o anuncios válidos, registrar `200` en `default_rate_limiter` y continuar con el parseo normal de `__INITIAL_PROPS__` y tarjetas HTML.
         - Solo si `FlareSolverr` también falla o no está disponible, activar la pausa de 15 minutos.

- **Verificación Empírica (SSH):**
  ```bash
  ssh servidor "cd /opt/wallapop-sniper && docker exec multi-sniper python3 -c '
  import core_engine, antiban
  rot = antiban.BrowserProfileRotator()
  items = core_engine.fetch_milanuncios(rot, \"Nintendo Switch OLED\", 50, 220)
  print(\"Milanuncios items fetched:\", len(items))
  if items:
      print(\"Sample Milanuncios item:\", items[0][\"title\"], items[0][\"price\"], items[0][\"url\"])
  assert len(items) > 0, \"Expected Milanuncios items > 0 via FlareSolverr bypass\"
  print(\"TASK-A5 OK\")
  '"
  ```

---

## BLOQUE A6 (`TASK-A6`): Saneamiento de `hdd_server_storage` (2TB/3TB Inviables) y Desbloqueo de Submodelos de Alto Margen en `config.json`

- **Estado:** `[ ] PENDIENTE`
- **Archivos a modificar:**
  - `z:\opt_servidor\config.json`
- **Problema Raíz Demostrado:**
  1. En `hdd_server_storage` (`mode: "personal"`, `min_price: 18`, `min_savings_eur: 25`), los submodelos de `2tb` (`max_price: 35`) y `3tb` (`max_price: 42`) tienen un valor de mercado de segunda mano de `30–40€`. Sumando el envío (`5–12€`), es **matemáticamente imposible** ahorrar `25€` en un disco de 2TB o 3TB. Sin embargo, todos los anuncios de 2TB/3TB entre `18€` y `45€` pasan `passes_filters`, consumen llamadas a Puerta 1, Embeddings y Puerta 2 Visión, y acaban descartados al 100% con `[DESCARTADO - 5/10] -> Ahorro insuficiente (+-2€ < +25€)`.
  2. En `ps5_consoles_gta6`, el `max_price` global es `295€` (`direct_buy_max_price: 265€`). Sin embargo, CeX España paga hoy **`720€–740€ en EFECTIVO`** por la **PlayStation 5 Pro 2TB** (`Venta CeX: 900€–925€`). Cualquier PS5 Pro publicada en Wallapop/Vinted a `380€–520€` (que deja **+200€ a +340€ de beneficio neto inmediato en efectivo CeX**) es descartada en seco por `passes_filters` porque `price > 295 * 1.30 (383€)`.

- **Pasos Exactos de Implementación:**
  1. En `config.json`, dentro del radar `hdd_server_storage`:
     - Cambiar `keywords` de `"disco duro hdd 2tb 4tb 6tb 8tb nas"` a `"disco duro hdd 4tb 6tb 8tb 10tb 12tb 14tb 16tb nas"`.
     - Actualizar `min_price` a `30`.
     - En `submodel_price_caps`, cambiar el `max_price` de `2tb` / `2 tb` / `2000gb` y `3tb` / `3 tb` / `3000gb` a `1` (bloqueados al igual que `500gb` y `1tb`), para centrar el radar Homelab exclusivamente en capacidades de alta densidad ($\ge 4\text{ TB}$: `4TB`, `6TB`, `8TB`, `10TB`, `12TB`, `14TB`, `16TB`, `18TB`, `20TB`) donde el ahorro de $\ge 25\text{ €}$ sí es real y frecuente.
     - Añadir a `blacklist` de `hdd_server_storage`: `"2tb"`, `"2 tb"`, `"3tb"`, `"3 tb"`, `"wd20ezrx"`, `"st2000"`.
  2. En `config.json`, dentro del radar `ps5_consoles_gta6`:
     - Elevar `max_price` del radar a `480` y `direct_buy_max_price` a `460` (para permitir **PS5 Pro 2TB**, por la que CeX paga `720€–740€` cash), y fijar en `submodel_price_caps` los topes estrictos de los modelos estándar para que no suban de precio:
       - PS5 Digital (Fat / Slim Digital, `unless_any: ["ps5 pro", "playstation 5 pro", "2tb", "2 tb", "30 aniversario", "30th"]`): `max_price: 235`.
       - PS5 Estándar / Slim con Lector (`match_any: ["ps5", "playstation 5"]`, `unless_any: ["ps5 pro", "playstation 5 pro", "2tb", "2 tb", "30 aniversario", "30th"]`): `max_price: 285`.
       - PS5 Pro 2TB / 30th Anniversary (`match_any: ["ps5 pro", "playstation 5 pro", "30 aniversario", "30th anniversary"]`): `max_price: 480`.

- **Verificación Empírica (SSH):**
  ```bash
  ssh servidor "cd /opt/wallapop-sniper && docker exec wallapop-sniper python3 -c '
  import json, core_engine
  cfg = core_engine.load_config()
  bl = cfg[\"global_blacklist\"]
  t_map = {t[\"id\"]: t for t in cfg[\"targets\"]}
  # 1. Verify 2TB HDD is rejected immediately at cost 0
  assert not core_engine.passes_filters(\"Disco duro 2 TB Sata Western Digital WD20EZRX\", \"\", 25.0, t_map[\"hdd_server_storage\"], bl)
  # 2. Verify 8TB HDD at 70€ passes
  assert core_engine.passes_filters(\"Disco duro 8TB Seagate IronWolf NAS\", \"\", 70.0, t_map[\"hdd_server_storage\"], bl)
  # 3. Verify PS5 Slim standard at 420€ is rejected, while PS5 Pro 2TB at 450€ passes!
  assert not core_engine.passes_filters(\"Consola PlayStation 5 Slim 1TB\", \"\", 420.0, t_map[\"ps5_consoles_gta6\"], bl)
  assert core_engine.passes_filters(\"Consola PlayStation 5 Pro 2TB en caja\", \"\", 450.0, t_map[\"ps5_consoles_gta6\"], bl)
  print(\"TASK-A6 OK\")
  '"
  ```

---

# 🚀 PARTE II: BLOQUES DE MEJORA PARA LLEVAR EL PROYECTO AL SIGUIENTE NIVEL

---

## BLOQUE B1 (`TASK-B1`): Expansión Estratégica de `config.json` con los Nichos de Mayor Liquidez y Margen CeX/P2P de 2026

- **Estado:** `[ ] PENDIENTE`
- **Archivos a modificar:**
  - `z:\opt_servidor\config.json`
  - `z:\opt_servidor\core_engine.py` (`RADAR_ALLOWED_CEX_CATEGORIES`, `CONSOLE_RADAR_IDS`, `STRICT_MODEL_TOKENS`)
- **Justificación de Mercado y Cotizaciones Reales CeX España (Verificadas en Vivo en Algolia):**
  Cruzando el estudio de mercado de arbitraje 2026 con las cotizaciones reales en vivo de CeX España (`prod_cex_es`), existen **4 nichos de altísima rotación y margen neto superior a `+50€..+180€` por operación** que actualmente no están cubiertos o están infra-configurados:
  1. **Nuevo Radar `switch_2_consoles` (Nintendo Switch 2 256GB):**
     - **Cotización real CeX España hoy:** `Cash: 254€–272€ | Vale: 295€–316€ | Venta CeX: 410€–440€`.
     - **Oportunidad:** Particulares urgidos de liquidez o regalos duplicados publicándolas entre `180€ y 235€` (compra directa con beneficio garantizado en efectivo CeX + P2P rápido a `340€–370€`).
  2. **Nuevo Radar `vr_action_creators` (Meta Quest 3 / 3S + DJI Osmo Pocket 3 + DJI Mini 3/4 Pro):**
     - **Cotización real CeX España hoy:**
       - `Meta Quest 3 512GB`: `Cash: 306€ | Vale: 356€ | Venta: 495€` (128GB: `Cash: 229€ | Vale: 266€ | Venta: 370€`; Quest 3S 128GB: `Cash: 132€ | Venta: 240€`).
       - `DJI Osmo Pocket 3`: `Cash: 127€–176€ | Vale: 180€–214€ | Venta: 290€–320€` (y en mercado P2P Wallapop/Vinted vuela en `<2h` a `330€–430€` por la fiebre de creadores de contenido).
       - `DJI Mini 3 Pro / Mini 4 Pro (con mando RC)`: `Cash: 192€–319€ | Vale: 234€–388€ | Venta: 350€–580€`.
  3. **Ampliación del Radar `apple_high_liquidity` con `MacBook Air M1 / M2`:**
     - **Cotización real CeX España hoy:**
       - `MacBook Air M1 8GB/256GB 13"`: `Cash: 233€ | Vale: 330€ | Venta: 530€` (P2P real: `390€–430€`).
       - `MacBook Air M2 8GB/256GB 13"`: `Cash: 299€–312€ | Vale: 423€–442€ | Venta: 680€–710€` (P2P real: `520€–580€`).
     - Muy frecuente encontrar MacBook Air M1 entre `210€ y 280€` de usuarios que actualizan a M3/M4.
  4. **Ampliación del Radar `retro_handhelds` con `Retroid Pocket 4 Pro / 5` y `Anbernic RG556 / RG406V`:**
     - **Cotización real CeX España hoy:**
       - `Retroid Pocket 5 (8GB+128GB)`: `Cash: 112€–118€ | Vale: 137€–144€ | Venta: 205€–215€`.
       - `Retroid Pocket 4 Pro`: `Cash: 70€ | Vale: 99€ | Venta: 160€`.
       - `Anbernic RG556`: `Cash: 79€ | Vale: 97€ | Venta: 145€`.

- **Pasos Exactos de Implementación:**
  1. En `config.json`:
     - Añadir el radar `switch_2_consoles` (`enabled: true`, `min_price: 140`, `direct_buy_max_price: 235`, `max_price: 265`, `min_profit_eur: 25`, `offer_sniping: true`, con `must_contain_any: ["switch 2", "nintendo switch 2"]`, `blacklist` de juegos/fundas/cajas vacías y `vinted_queries: ["Consola Nintendo Switch 2", "Nintendo Switch 2 256GB"]`).
     - Añadir el radar `vr_action_creators` (`enabled: true`, `min_price: 95`, `direct_buy_max_price: 245`, `max_price: 275`, `min_profit_eur: 30`, `offer_sniping: true`, con `submodel_price_caps` específicos para `quest 3s` [`120€`], `quest 2` [`1€` bloqueado], `quest 3 128gb` [`195€`], `quest 3 512gb` [`255€`], `osmo pocket 3` [`245€`], `dji mini 3 pro` [`210€`], `dji mini 4 pro` [`275€`], y bloqueando accesorios sueltos como correas, filtros, hélices o baterías sueltas).
     - En `apple_high_liquidity`: añadir soporte para `macbook air m1` y `macbook air m2` (`max_price` global del radar a `340`, manteniendo `submodel_price_caps` de `ipad 9` en `115`, `ipad 10` en `155`, `ipad air` en `210`, `mac mini m1` en `220`, `mac mini m2` en `275`, `macbook air m1` en `265`, `macbook air m2` en `340`, y bloqueando cualquier MacBook Intel en `passes_filters`).
     - En `retro_handhelds`: añadir `"retroid pocket 5"`, `"retroid pocket 4 pro"`, `"anbernic rg556"` con sus `submodel_price_caps` (`105€`, `75€`, `75€`).
  2. En `core_engine.py`:
     - Añadir `"switch_2_consoles"` a `CONSOLE_RADAR_IDS`.
     - Actualizar `RADAR_ALLOWED_CEX_CATEGORIES` con:
       - `"switch_2_consoles": ("switch 2 consolas", "consolas")`
       - `"vr_action_creators": ("vr tech", "videocamaras", "robots", "camaras")`
       - En `"apple_high_liquidity"`: añadir `"portatiles - apple"`
       - En `"retro_handhelds"`: añadir `"pc gaming portatil"`
     - En `passes_filters` (`core_engine.py` L1476), aplicar el filtro anti-Intel tanto a `"mac mini"` como a `"macbook"` dentro de `apple_high_liquidity`.

- **Verificación Empírica (SSH):**
  ```bash
  ssh servidor "cd /opt/wallapop-sniper && docker exec wallapop-sniper python3 -c '
  import json, core_engine
  cfg = core_engine.load_config()
  bl = cfg[\"global_blacklist\"]
  t_map = {t[\"id\"]: t for t in cfg[\"targets\"]}
  assert \"switch_2_consoles\" in t_map and \"vr_action_creators\" in t_map
  assert core_engine.passes_filters(\"Consola Nintendo Switch 2 256GB completa\", \"\", 220.0, t_map[\"switch_2_consoles\"], bl)
  assert not core_engine.passes_filters(\"Juego Mario Kart Nintendo Switch 2\", \"\", 45.0, t_map[\"switch_2_consoles\"], bl)
  assert core_engine.passes_filters(\"Meta Quest 3 512GB con mandos y caja\", \"\", 240.0, t_map[\"vr_action_creators\"], bl)
  assert not core_engine.passes_filters(\"Meta Quest 2 128GB\", \"\", 120.0, t_map[\"vr_action_creators\"], bl)
  assert core_engine.passes_filters(\"MacBook Air M1 8GB 256GB impecable\", \"\", 250.0, t_map[\"apple_high_liquidity\"], bl)
  assert not core_engine.passes_filters(\"MacBook Air 2017 Intel Core i5 128GB\", \"\", 180.0, t_map[\"apple_high_liquidity\"], bl)
  print(\"TASK-B1 OK. Total targets:\", len(cfg[\"targets\"]))
  '"
  ```

---

## BLOQUE B2 (`TASK-B2`): Arbitraje Basado en Vale CeX (`Voucher Arbitrage Engine`) y Desglose Automático de Lotes (`Bundle Splitter`)

- **Estado:** `[ ] PENDIENTE`
- **Archivos a modificar:**
  - `z:\opt_servidor\core_engine.py` (`classify_deal_tier`, `evaluate_deal_math_and_score`, `send_discord_alert`)
- **Estándar SOTA de Bots de Arbitraje 2026:**
  1. Como demuestran las guías avanzadas de arbitraje CeX en España y Reino Unido, **el método profesional más rentable en CeX es el Arbitraje de Vale (`Voucher Loop`)**: CeX paga entre un **+18% y un +42% más en Vale (`voucher_price`) que en Efectivo (`cash_price`)** (ej. en una Canon G7X II, CeX paga `242€` en efectivo pero **`342€` en vale**; en un MacBook Air M2 paga `312€` en efectivo pero **`442€` en vale**). Los traders profesionales convierten esos vales al `82%–85%` de su valor nominal comprando productos líquidos en CeX o vendiendo el vale directamente.
  2. Además, cuando un vendedor sube un **Lote (`Consola + 3 Juegos + Mando Extra`)**, el bot ya calcula `evaluate_bundle_cex_bonus(conn, bundled_queries)` en los escáneres, pero en muchas ocasiones la estrategia óptima es el **Desglose Híbrido (`Hybrid Bundle Split`)**: entregar los juegos/mandos en CeX por efectivo/vale inmediato para recuperar el 40–60% del coste del lote el primer día, y vender la consola limpia en P2P (o viceversa).

- **Pasos Exactos de Implementación:**
  1. En `classify_deal_tier` (`core_engine.py` L778–895):
     - Calcular además de `cex_cash_net` y `cex_voucher_net`:
       - `voucher_cash_equivalent = round(cex_v * 0.82, 2)` (valor líquido conservador del vale CeX al 82%).
       - `voucher_arbitrage_net = round(voucher_cash_equivalent - cost, 2)`.
     - En el diccionario devuelto por `classify_deal_tier`, incluir `"voucher_cash_equivalent": voucher_cash_equivalent` y `"voucher_arbitrage_net": voucher_arbitrage_net`.
     - En **NIVEL 2 (`TIER_2_HYBRID_PLAY`)**, permitir también la entrada si `cex_voucher_net >= 35.0` y `coverage_pct >= 80.0` (cuando el vale de CeX cubre sobradamente toda la compra con `>= +35€` de superávit en vale y la cobertura en efectivo es `>= 80%`).
  2. En `evaluate_deal_math_and_score` (`core_engine.py` L3833–3901):
     - Si `ai_data.get("extra_cex_cash", 0) > 0` (lote detectado con extras tasados en CeX):
       - Calcular `net_console_cost_after_extras = round(effective_cost - float(ai_data.get("extra_cex_cash", 0)), 2)`.
       - Guardar en `ai_data["bundle_split_strategy"]`: texto conciso indicando `"💡 Estrategia Desglose: Vende los extras en CeX por {extra_cash:.0f}€ efectivo ({extra_voucher:.0f}€ vale) ➔ la unidad principal te sale por solo {net_console_cost_after_extras:.0f}€ netos"`.
  3. En `send_discord_alert` (`core_engine.py` L4190–4320):
     - Mostrar en el bloque financiero el superávit de Vale CeX (`Vale CeX: {cex_voucher:.0f}€ [Neto Vale: {cex_voucher - landed_cost:+.0f}€]`) y, si existe `ai_data.get("bundle_split_strategy")`, añadirlo en la ficha de la alerta.

- **Verificación Empírica (SSH):**
  ```bash
  ssh servidor "cd /opt/wallapop-sniper && docker exec wallapop-sniper python3 -c '
  import core_engine
  res = core_engine.classify_deal_tier(
      total_buy_cost=245.0,
      cex_cash_total=210.0,
      cex_voucher_total=310.0,
      estimated_market_value=320.0,
      fraud_risk_score=0,
      buy_confidence=9
  )
  print(\"Tier result:\", res)
  assert \"voucher_arbitrage_net\" in res and res[\"cex_voucher_net\"] == 65.0
  assert res[\"deal_tier\"] == 2 and res[\"is_deal\"] is True
  print(\"TASK-B2 OK\")
  '"
  ```

---

## BLOQUE B3 (`TASK-B3`): Inteligencia de Mercado P2P Dinámica por Submodelo y Auto-Calibración con `price_history`

- **Estado:** `[ ] PENDIENTE`
- **Archivos a modificar:**
  - `z:\opt_servidor\core_engine.py` (`record_market_observation`, `get_local_market_stats`, `analyze_and_generate_kit`)
- **Problema Actual y Evolución Next-Level:**
  - Actualmente, `record_market_observation(radar_name, platform, price, conn)` guarda todas las observaciones de un radar bajo el nombre genérico del radar (ej. `"Familia Nintendo 3DS / 2DS"` o `"Consolas PlayStation 5 (Foco GTA VI)"`), mezclando en la misma mediana una `Nintendo 2DS` de `55€` con una `New Nintendo 3DS XL` de `190€`, o una `PS5 Digital` con una `PS5 Pro`.
  - Si normalizamos la clave de observación usando el submodelo identificado (o `cex_box_name` / submodelo canónico cuando está disponible, con fallback al `radar_id`), `get_local_market_stats` proporciona el **percentil 25 (`p25`), mediana (`p50`) y percentil 75 (`p75`) reales del mercado P2P español** por modelo e inyecta ese contexto real en el prompt de Puerta 2 (`analyze_and_generate_kit`) para que la IA no alucine el `estimated_resale_price`.

- **Pasos Exactos de Implementación:**
  1. En `get_local_market_stats(radar_name, days=14, conn=None)` (`core_engine.py` L3210–3255):
     - Además de `median`, `mean`, `std`, `count`, calcular `p25` (percentil 25 = precio de venta rápida P2P) y `p75` cuando `count >= 4`, devolviendo `{"median": ..., "mean": ..., "std": ..., "count": ..., "p25": ..., "p75": ...}` (manteniendo intactas las 4 claves existentes para retrocompatibilidad total).
  2. En `analyze_and_generate_kit` (`core_engine.py` L3350–3460):
     - Consultar `mkt_stats = get_local_market_stats(target.get("name", ""), days=21, conn=conn)`.
     - Si `mkt_stats.get("count", 0) >= 3` y `mkt_stats.get("median", 0) > 0`, inyectar una línea informativa en el prompt de valoración:
       `f"Estadística P2P real observada en los últimos 21 días ({mkt_stats['count']} uds): Mediana={mkt_stats['median']:.0f}€, P25={mkt_stats.get('p25', mkt_stats['median']):.0f}€.\n"`

- **Verificación Empírica (SSH):**
  ```bash
  ssh servidor "cd /opt/wallapop-sniper && docker exec wallapop-sniper python3 -c '
  import core_engine
  conn = core_engine.init_db()
  stats = core_engine.get_local_market_stats(\"Consola Nintendo Switch OLED\", days=30, conn=conn)
  print(\"Market stats:\", stats)
  assert all(k in stats for k in (\"median\", \"mean\", \"std\", \"count\", \"p25\", \"p75\"))
  print(\"TASK-B3 OK\")
  '"
  ```

---

## BLOQUE B4 (`TASK-B4`): Detección de Palabras Clave de Urgencia ("Motivated Seller Signals") + Priorización en Cola Fast-Lane y Mensajes de Regateo Adaptativos

- **Estado:** `[ ] PENDIENTE`
- **Archivos a modificar:**
  - `z:\opt_servidor\core_engine.py`
  - `z:\opt_servidor\multi_sniper.py`
  - `z:\opt_servidor\sniper.py`
  - `z:\opt_servidor\vinted_sniper.py`
- **Estándar SOTA de Flipping en España 2026:**
  - Los mejores chollos P2P y oportunidades de regateo (`Offer Sniping` con descuentos aceptados del `20%–35%`) provienen de vendedores particulares con **señales explícitas de urgencia o desinterés**: `"urge"`, `"urge venta"`, `"por mudanza"`, `"me mudo"`, `"por no usar"`, `"no lo uso"`, `"regalo y no"`, `"negociable"`, `"algo negociable"`, `"escucho ofertas"`, `"acepto ofertas"`, `"quitar de en medio"`, `"limpieza de trastero"`.
  - Actualmente el bot trata igual un anuncio rígido (`"Precio NO negociable, no contesto ofertas"`) que un anuncio con `"Urge por mudanza, escucho ofertas"`.

- **Pasos Exactos de Implementación:**
  1. En `core_engine.py`:
     - Crear la función determinista ` analyze_seller_motivation(title: str, description: str) -> dict`:
       - Detecta `URGENCY_SIGNALS` (`"urge"`, `"mudanza"`, `"me mudo"`, `"por no usar"`, `"no lo uso"`, `"no la uso"`, `"escucho ofertas"`, `"acepto ofertas"`, `"negociable"`, `"precio negociable"`, `"algo negociable"`, `"limpieza"`, `"falta de uso"`, `"necesito el dinero"`).
       - Detecta `RIGID_PRICE_SIGNALS` (`"no negociable"`, `"precio fijo"`, `"no bajo precio"`, `"no ofertas"`, `"no regateos"`, `"no leer ofertas"`, `"abstenerse ofertas"`, `"ultimo precio"`, `"último precio"`).
       - Devuelve `{"is_motivated": bool, "is_rigid_price": bool, "signals": list, "fast_lane_boost": float, "haggle_ flexibility_bonus": float}` (donde `fast_lane_boost = 0.85` si `is_motivated` para adelantar el anuncio en la cola Fast-Lane).
     - En `evaluate_deal_math_and_score` (`core_engine.py` L3779 y L3902):
       - Si el anuncio está en zona de oferta (`price > direct_max_cap`) y `analyze_seller_motivation` detecta `is_rigid_price == True` (vendedor dice explícitamente `"precio no negociable"` / `"no bajo precio"`) y el descuento requerido supera el `12%`, descartar la oportunidad de oferta (`"Vendedor indica explícitamente precio no negociable"`), ahorrando ruido de alertas de regateo que serán rechazadas.
       - Si `is_motivated == True`, permitir hasta `+5%` adicional en `max_haggle_discount_pct` y añadir el badge `"🔥 Vendedor Motivado / Negociable"` en la alerta.

- **Verificación Empírica (SSH):**
  ```bash
  ssh servidor "cd /opt/wallapop-sniper && docker exec wallapop-sniper python3 -c '
  import core_engine
  m1 = core_engine.analyze_seller_motivation(\"Consola PS5 Slim\", \"La vendo por mudanza y falta de uso, precio algo negociable\")
  m2 = core_engine.analyze_seller_motivation(\"Consola PS5 Slim\", \"Precio NO negociable, abstenerse ofertas absurdas\")
  print(\"Motivated:\", m1)
  print(\"Rigid:\", m2)
  assert m1[\"is_motivated\"] is True and m1[\"is_rigid_price\"] is False
  assert m2[\"is_rigid_price\"] is True
  print(\"TASK-B4 OK\")
  '"
  ```

---

## BLOQUE B5 (`TASK-B5`): Pipeline Unificado de Notificaciones Instantáneas (Deep-Links Nativos Móviles + 1-Tap `ntfy.sh` en Chollos Confirmados Nivel 1/2)

- **Estado:** `[ ] PENDIENTE`
- **Archivos a modificar:**
  - `z:\opt_servidor\core_engine.py` (`send_one_tap_ntfy_alert`, `send_discord_alert`, `send_telegram_alert`)
- **Estándar SOTA de Velocidad de Ejecución (<2 segundos hasta Checkout):**
  - En estos momentos, `send_discord_alert` envía el embed a Discord y a Telegram, pero **no dispara `send_one_tap_ntfy_alert`** cuando la IA Multimodal confirma un **NIVEL 1 (Arbitraje Directo CeX)** o un **NIVEL 2 con Score $\ge 9$** que no había disparado Flash Alert previamente (por ejemplo, cuando el chollo proviene de un lote con juegos extra valorados por la IA en Puerta 2, o en Vinted/Wallapop).
  - Además, incluir el esquema de **Deep-Link / Universal Link limpio** y deduplicar envíos a `ntfy.sh` por `item_id` (con un `set` en memoria `_NTFY_SENT_IDS`) garantiza que el móvil reciba exactamente 1 notificación push con botón `Comprar Ahora` y `Ver en CeX` en todo chollo de máxima prioridad sin duplicar si ya se envió en Flash Alert.

- **Pasos Exactos de Implementación:**
  1. En `core_engine.py`:
     - Añadir `_NTFY_SENT_ITEM_IDS = set()` protegido por lock en `send_one_tap_ntfy_alert` para garantizar idempotencia por `item_id` / `url` (que nunca suene dos veces el mismo anuncio si disparó primero Flash Alert y luego Alerta Confirmada).
     - Al final de `send_discord_alert` (`core_engine.py` L4395), si `deal_score >= 9` o `ai_data.get("deal_tier") == 1`:
       - Invocar `send_one_tap_ntfy_alert(...)` con el título enriquecido (`f"🏆 NIVEL {ai_data.get('deal_tier', 1)}: {title}"`), `net_profit`, `cex_cash` y `seller_trust_badge`.

- **Verificación Empírica (SSH):**
  ```bash
  ssh servidor "cd /opt/wallapop-sniper && docker exec wallapop-sniper python3 -c '
  import core_engine
  assert hasattr(core_engine, \"send_one_tap_ntfy_alert\")
  assert hasattr(core_engine, \"_NTFY_SENT_ITEM_IDS\")
  print(\"TASK-B5 OK\")
  '"
  ```

---

## BLOQUE B6 (`TASK-B6`): Comando `/arbitraje_cex` y `/salud_scrapers` en `bot.py` + Suite de Verificación Global

- **Estado:** `[ ] PENDIENTE`
- **Archivos a modificar:**
  - `z:\opt_servidor\bot.py`
- **Objetivo Next-Level en el Centro de Mando Discord (`bot.py`):**
  - Añadir a `bot.py` el comando slash `/salud_scrapers` (o ampliar `/estado`) para consultar en tiempo real desde el móvil el estado de las 6 tiendas (`Wallapop`, `Vinted`, `CashConverters`, `Milanuncios + FlareSolverr`, `eBay`, `TodoColección`), el número de fichas sincronizadas en `cex_catalog_mirror`, el ratio de aciertos de Puerta 0 / Pre-Gate CeX / Puerta 1, y el top de chollos de Arbitraje Efectivo vs Vale CeX.

- **Verificación Final de Todo el Proyecto y Reinicio Limpio en Producción (SSH):**
  ```bash
  ssh servidor "cd /opt/wallapop-sniper && python3 -m py_compile core_engine.py bot.py multi_sniper.py fraud_signals.py antiban.py sniper.py vinted_sniper.py && python3 -c \"import json; cfg=json.load(open('config.json', encoding='utf-8')); print('Targets OK:', len(cfg.get('targets', [])))\" && docker compose restart && docker compose ps"
  ```

