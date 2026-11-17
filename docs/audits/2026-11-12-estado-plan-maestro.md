# Estado del plan maestro y agentes Codex

Fecha: 2026-11-12. Alcance: [plan maestro](2026-11-11-plan-maestro.md), agentes Claude implementador/revisor y contraste de sus tareas con el código y el registro existentes.

**Resultado: 34 tareas; 27 con implementación localizada, 2 parciales y 5 sin la implementación solicitada.** El plan marca 28 casillas completas y deja 6 abiertas. La diferencia se explica porque T1.8 está marcada pero conserva una decisión pendiente, mientras T3.10 está abierta aunque su soporte ya existe.

Esta clasificación identifica trabajo entregado en código, no certifica ausencia de bugs, aceptación dinámica, activación o despliegue. Se han leído los tests y sus resultados históricos; **no se han ejecutado tests, scripts, runtimes, comandos Git, experimentos ni contenedores** en esta revisión. Tampoco se han lanzado los agentes nuevos.

## Cómo interpretar los estados

- **Implementada:** el núcleo pedido aparece en código integrado y dispone de evidencia documental y/o pruebas de comportamiento existentes. No debe reimplementarse por una casilla o línea de documentación antigua.
- **Parcial:** existe soporte, pero falta una decisión o experimento que forma parte del cierre.
- **Pendiente:** faltan los mecanismos específicos solicitados. No basta una función previa que tenga un objetivo parecido.
- **Pruebas:** los archivos citados existen y se han inspeccionado. Los OK de registro.md son históricos, no resultados nuevos.
- **Despliegue/KPI:** no comprobados. Las entradas recientes de registro.md indican “Sin desplegar”. No se consultó la BD viva ni se conectó a producción.

Fuentes principales: tabla del apartado 3 y especificaciones del plan; [registro de auditorías](registro.md), A-007 a A-040; código y tests del montaje actual. ROADMAP.md no está presente actualmente; no se ha restaurado ni se ha inventado su contenido.

## Fase 1 — 13 implementadas y 1 parcial

| Tarea | Estado contrastado | Evidencia de implementación y cobertura | Registro |
|---|---|---|---|
| T1.0 Arnés y versión | Implementada | tools/run_isolated.sh: copia SQLite, Docker sin red, repo solo lectura y sin .env; tests/unit/_harness.py; core_engine.py:79 y save_alert_context; Compose sin montaje de tests. test_harness.py y test_deploy_modules.py. | A-007 |
| T1.1 Límites de palabra y negación | Implementada | text_rules.py:22-100; integración en passes_filters y evaluate_fraud_risk; blacklists corregidas; tools/replay_filters.py y test_text_rules.py. A-009 separa la decisión de precio iPad del matcher. | A-008, A-009 |
| T1.2 Clasificador consola/juego/accesorio | Implementada | text_rules.py:119, integración core_engine.py:2353-2356, test_console_classifier.py y datasets console_pos.json/console_neg.json presentes. | A-010 |
| T1.3 Caché y frescura CeX | Implementada | core_engine.py:2880-3121: espejo <=36 h, hits por consulta 24 h/negativos 6 h, selección contextual y alias compatible; purgas y contador del bot actualizados. test_cex_lookup.py. | A-011, A-040 |
| T1.4 Perfil desconocido distinto de cero | Implementada | fraud_signals.py:914-976 y evaluate_seller_risk; estados ok/unavailable/not_applicable, None preservado y perfiles no disponibles sin cachear. test_seller_unknown.py. | A-012 |
| T1.5 Envío Discord, fallback e ID | Implementada en el alcance especificado | core_engine.py:5772-5887 y send_discord_alert: reintentos, fallback mínimo, validación de URLs, redacción, logs e ID persistido. test_discord_send.py y test_phase1_fixes.py. No incluye una cola persistente de recuperación tras agotar todos los intentos. | A-013, A-022 |
| T1.6 MAX_TOKENS y salida por modelo | Implementada | ai_gateway_call detecta truncado, registra finish_reason/thoughts y usa 2048 en no-lite; test_ai_gateway.py. No equivale al breaker/deadline de T2.7. | A-014 |
| T1.7 Clave en cabecera y redacción | Implementada en el gateway | Gemini principal usa x-goog-api-key; text_rules.redact_secrets y test_secret_redaction.py. El script de A/B añadido después tiene un matiz indicado abajo. | A-015 |
| T1.8 Presupuesto diario | **Parcial** | core_engine.py:705-726 y check_ai_budget_status: día local, caché 60 s y aviso 80 %; TestBudgetLocalDay en test_phase1_fixes.py. La elección del tope sigue pendiente. | A-023 |
| T1.9 Wallapop 403/429 y Retry-After | Implementada | fetch_wallapop y AntiBanHealthMonitor: no fallback inmediato en 401/403/429, fallback limitado a 400/404/410 y Retry-After puede alargar cooldown. test_wallapop_block.py. | A-016 |
| T1.10 Breaker por tienda | Implementada | store_breaker.py:22, persistencia atómica, cooldown exponencial hasta 12 h, avisos acotados e integración Multi/config. test_store_breaker.py. | A-017 |
| T1.11 Precio eBay | Implementada | core_engine.py:3512-3530 y fetch_ebay_es: importes con miles/decimales y exclusión del precio tachado. test_ebay_price.py. | A-018 |
| T1.12 ntfy JSON | Implementada | core_engine.py:1943-2024: JSON UTF-8, topic, click, acción y token opcional; test_ntfy_json.py. La disponibilidad efectiva del canal no se ha comprobado. | A-019 |
| T1.13 Visión mínima y modalidad de tokens | Implementada | _prompt_tokens_by_modality, log_ai_cost y prompts de analyze_and_generate_kit; test_vision_minimal.py. Reducción real de coste pendiente de observar tras despliegue. | A-020 |

### Qué falta realmente en T1.8

El código no necesita rehacer el cómputo del día ni la caché. Falta resolver la decisión sobre AI_DAILY_BUDGET_USD; el plan propone 0,50 USD y A-023 la deja pendiente. No se ha leído ni modificado .env, por lo que no se afirma cuál es el valor efectivo desplegado. El default del módulo sigue en 0,10 USD. La casilla [x] significa aquí “código preparado”, no cierre operativo completo.

## Fase 2 — 6 implementadas y 3 pendientes

| Tarea | Estado contrastado | Evidencia de implementación y cobertura | Registro |
|---|---|---|---|
| T2.1 Métricas por servicio y latencia | Implementada | logger.py:33-112: archivo por servicio y lock; compute_pipeline_ms integrado en los tres escáneres y contexto de alerta; tools/metrics_report.py y tools/latency_report.py. test_latency_metrics.py. | A-024 |
| T2.2 HTTP coherente | Implementada | http_client.PlatformClient con sesiones por hilo y cabeceras por tipo; perfiles de antiban y clientes integrados; requirements.txt fijado y Dockerfile actualizado. test_http_client.py y probe_headers.py existentes. | A-025 |
| T2.3 Scheduler y token bucket | Implementada; ajuste operativo no certificado | antiban.py:608-989 y uso por Wallapop/Vinted/fichas/vendedores; test_scheduler.py. Se han eliminado las esperas fijas de esos bucles. HDD sigue hot; la rampa y los KPIs no se han verificado. | A-026 |
| T2.4 Ficha Vinted y reintentos | Implementada | fetch_item_details devuelve None ante fallo/cooldown; handle_vinted_details_result decide reintentos; bucle evita evaluar con ficha fallida; vendedor aprovecha ficha suficiente. test_vinted_details.py. | A-027 |
| T2.5 Caché persistente de vendedor Wallapop | Implementada | fraud_signals.py:393-578, enrich_seller_profile y cliente común: RAM -> SQLite -> red, TTL 24/6/1 h, desconocidos sin cachear y manejo de cooldown/rotación. test_seller_cache_wallapop.py. | A-028, A-039 |
| T2.6 Pipeline común y workers | **Pendiente y condicional** | No existe pipeline.py, ItemCtx/evaluate_item, PriorityQueue global ni EVAL_WORKERS. La evaluación sigue integrada en cada escáner. Los pools de fetch/fotos no implementan esta tarea. | Sin cierre |
| T2.7 Breaker IA y deadline | **Pendiente** | ai_gateway_call tiene semáforo, timeouts por intento, fallback y truncado, pero no ModelBreaker ni deadline_s global compartido por intentos. | Sin cierre |
| T2.8 Salud asíncrona y locks | **Pendiente** | antiban.py:321-462: send_health_embed hace POST síncrono con timeout 10 s; no hay HealthNotifier/cola acotada/dedup de títulos ni locks en ese monitor. | Sin cierre |
| T2.9 Cadencia por tienda | Implementada | multi_sniper.py: get_store_interval_s, compute_next_fetch_at y schedule_stores_after_round; config CC=600, TC=1800, eBay=300 s con jitter. test_multi_sniper_schedule.py. | A-029 |

### T2.6 no es una tarea que deba activarse por estar abierta

El plan exige antes evidencia de T2.1: espera en cola p90 >10 s o más del 20 % de rondas con IA. No se ha medido esa condición en esta sesión. Por tanto, su estado es “sin implementar y pendiente de justificar”, no “siguiente refactor obligatorio”.

Tampoco procede confundir ThreadPoolExecutor de tiendas o fotos con los dos workers de evaluación solicitados: faltan el contexto común, la cola global, inflight protegido, conexiones por worker y el control transaccional del límite de push.

### T2.7 y T2.8 no están resueltas por los mecanismos anteriores

- T2.7 pide memoria de tres fallos por modelo, apertura 300 s/Retry-After, deadlines 10/20 s y timeouts por intento 6/12 s, más tratamiento explícito del error OpenRouter con HTTP 200. El gateway actual recorre modelos con timeout individual; su semáforo no impone un plazo total.
- T2.8 pide cola de 100, daemon que despache con el helper común, deduplicación de diez minutos y locks en el estado del monitor. El envío actual es directo y bloqueante. El breaker por tienda de T1.10 no sustituye ese trabajo.

### Matiz de T2.3

El mecanismo está escrito y tiene tests. Eso no acredita las cadencias reales del conjunto configurado. HDD permanece hot, cuya modificación era decisión del usuario; las consultas actuales demandan más capacidad que los límites nominales de búsquedas. Antes de cerrar sus KPIs debe comprobarse el reparto entre hot/standard, incluyendo peticiones de fichas y perfiles. No se ha cambiado la tasa ni el tier del radar.

## Fase 3 — 8 implementadas, 1 parcial y 2 pendientes

| Tarea | Estado contrastado | Evidencia de implementación y cobertura | Registro |
|---|---|---|---|
| T3.1 Herencia semántica por vendedor/foto | Implementada en su alcance | core_engine.py:1484-1636 y llamadas de los tres escáneres: vendedor no vacío o foto idéntica, nunca motivo de precio. test_semantic_dedup_seller.py. | A-030 |
| T3.2 Pre-CeX por título antes de IA | **Pendiente** | No aparece estimate_cex_from_title ni vocabulario/alias/confianza con propagación a fraude y prompt. pre_gate_cex_offer_margin sigue limitado a la zona de oferta y omite la zona directa. | Sin cierre |
| T3.3 Valoración anclada y confianza | **Pendiente** | core_engine.py:4681-4688 conserva prioridad de una estimación IA positiva. No hay valuation_confidence, ancla p75 ni snapshot valuation o regla de push por esa confianza. | Sin cierre |
| T3.4 Envío Vinted por país | Implementada | get_vinted_shipping_eur, parse_vinted_price y recálculo tras ficha; tabla ES/PT/FR/IT/BE/NL/default en config. test_vinted_shipping.py. | A-031 |
| T3.5 Reservas liberadas | Implementada | market_seen_items.state, mark_item_seen y check_item_seen_or_dropped; handle_seen_and_reserved conserva reentrada/rebajas; test_reserved_recheck.py. | A-032, A-039 |
| T3.6 Payload y enlaces | Implementada en los helpers especificados | format_alert_headline, send_discord_alert, flash y ntfy: primera línea <=110, URL https, motivo silencioso al final y cuatro campos. test_alert_payload.py. La integración ntfy final de Multi sigue ligada al pendiente de T2.6. | A-033, A-039 |
| T3.7 Estado vivo en Discord | Implementada | _discord_edit_message, discord_state y record_check: PATCH, prefijos, eliminación del botón buy y control de edición por estado. test_alert_lifecycle.py. | A-034 |
| T3.8 Resumen diario | Implementada, con extensión documentada | core_engine.py:5341 y bot.py:1947: selección, límite diez, beneficio descendente y envío a las 21:00 Europe/Madrid. test_silent_digest.py. Config individual conserva los avisos individuales; digest es una opción disponible. | A-035, A-039 |
| T3.9 Muestra de bloqueados | Implementada | should_sample_fraud_block y record_fraud_sample, los tres capturadores, check_pending_fraud_samples y comparación del bot. test_fraud_sampling.py. La tasa de falsos positivos aún requiere observación. | A-036, A-039 |
| T3.10 A/B de imágenes | **Parcial** | resolve_image_max_dim, ai.image_max_dim, tools/ab_image_resolution.py y test_image_resolution.py existen. Falta el experimento real y la decisión de resolución. Config sigue 384. | A-037 |
| T3.11 Umbrales y AGENTS | Implementada en el alcance del plan | resolve_min_profit en pre-gate/matemáticas; load_config propaga multiplicador y passes_filters usa techo expandido; AGENTS refleja hashing local, imágenes, rutas y routing. test_thresholds_coherence.py. | A-038 |

### Por qué T3.2 y T3.3 no están terminadas

Tener lookup_cex_catalog_mirror o un pre-gate previo no implementa T3.2. Faltan la estimación por tokens de modelo, alias y confianza, la aplicación en compra directa, el efectivo enviado a fraude antes de IA y la referencia CeX en el prompt.

Tener price_history y guardar observaciones por submodelo tampoco implementa T3.3. Falta usar el ancla como límite: con ai_est=400 y cex_sell=300, el plan exige optimal=255; el camino actual asigna optimal=400. Este es un contraste estático de las asignaciones, no un test ejecutado. También faltan el p75, valuation_confidence, snapshot y umbral de push correspondiente.

T3.3 depende de T3.2. No hay motivo para recrear las tablas o helpers auxiliares ya presentes.

### Qué falta en T3.10

El script ya selecciona hasta 20 fotos, compara 384/768 con el mismo prompt, resume tokens/concordancia y ofrece modo dry-run. No se ha ejecutado siquiera dry-run en esta sesión. A-037 declara pendiente la prueba real y no hay un resultado nuevo que permita elegir 768 o mediaResolution.

Antes de autorizar el experimento debe resolverse además una diferencia visible con T1.7: tools/ab_image_resolution.py:50-51 construye la URL con la API key en query, mientras el gateway principal ya la envía por cabecera. Se señala como parte de preparar correctamente este pendiente; no se ha modificado el script.

### Matices para no confundir implementación con alcance global

- T1.5 entrega reintentos/fallback/logs/ID tal como los especifica; no demuestra recuperación persistente después de agotar los intentos.
- T3.1 limita la herencia semántica. No certifica otras barreras de deduplicación anteriores ni todas las colisiones del embedding.
- T3.6 entrega el formato. El propio T2.6 ya identificaba la falta de ntfy final de Multi.
- T3.8 amplía la selección a pending y tiendas sin outcome mediante LEFT JOIN, según A-039. No todas las entradas del digest tienen disponibilidad confirmada.
- Las expectativas de ahorro, reducción de bloqueos, precisión o latencia requieren datos posteriores al despliegue; una casilla o un helper no los acredita.

## Agentes equivalentes de Codex

Se han leído las definiciones de .claude/agents/implementador.md y revisor.md, junto con sus duplicados en .agents/agents. Los equivalentes nuevos son:

- [.codex/agents/implementador.toml](../../.codex/agents/implementador.toml): una tarea autorizada, cambios mínimos, test previo, verificación aislada, máximo dos intentos, escalado y cierre acotado.
- [.codex/agents/revisor.toml](../../.codex/agents/revisor.toml): revisión independiente, sin edición del repositorio, aceptación/invariantes y reejecución aislada solo cuando esté autorizada.
- [Guía de uso y adaptaciones](../../.codex/agents/README.md).

Ambos heredan modelo y razonamiento de la sesión. Los nombres Gemini 3.8 Max/Argon 4 de Claude no se han convertido en proveedores ficticios ni en un fallback de Codex. No se ha modificado la configuración de los modelos del producto.

La adaptación conserva el ciclo implementación -> revisión y hace explícitos tres límites:

1. Un encargo de análisis no autoriza ejecutar tareas, tests, commits o despliegues.
2. El revisor puede declarar SOLO_ANALISIS/BLOQUEADO: no aprueba pruebas que no pudo ejecutar.
3. La compilación aislada dirige los .pyc a /work/pycache, porque el montaje /src es de solo lectura. El comando original de Claude no indicaba este destino.

Formato contrastado con la [documentación oficial de subagentes](https://learn.chatgpt.com/docs/agent-configuration/subagents#custom-agents). Se han preparado las definiciones; no se ha probado su carga ni se han activado.

## Punto de parada

La sesión termina en análisis y preparación. No se han modificado el plan original, sus casillas, registro.md, código de aplicación, configuración operativa o los agentes Claude. No se ha hecho commit ni despliegue.

Antes de una ejecución posterior debe comunicarse la tarea concreta, el alcance de archivos y la validación prevista. Los pendientes identificados son T2.7, T2.8, T3.2 -> T3.3; T2.6 conserva su condición de medición. T1.8 y T3.10 no deben rehacerse: requieren resolver sus pendientes explícitos.
