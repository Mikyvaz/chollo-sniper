---
name: ejecutar-plan
description: Orquesta de forma autónoma la ejecución del plan maestro (docs/audits/2026-11-11-plan-maestro.md), enrutando cada tarea entre Gemini 3.8 Max (velocidad/volumen) y Argon 4 (alta complejidad, concurrencia, evasión TLS y revisión estricta).
argument-hint: "[1|2|3|Tn.m]"
disable-model-invocation: true
---

# Ejecutar el Plan Maestro (Orquestación Dual: Gemini 3.8 Max + Argon 4)

Argumento: `$ARGUMENTS`. Un número (`1`, `2`, `3`) ejecuta una fase completa; `Tn.m` ejecuta esa tarea concreta. Vacío = continúa desde la primera tarea pendiente.

Actúas como **Arquitecto Principal y Orquestador**. Delegas la implementación en `implementador`, pasas la revisión independiente con `revisor` (**Argon 4**), ejecutas la verificación aislada y actualizas el progreso en `docs/audits/2026-11-11-plan-maestro.md` y `docs/audits/registro.md`.

---

## 0. Matriz de Enrutamiento de Modelos (Fase 2 y Fase 3)

### A. Tareas asignadas a **Gemini 3.8 Max** [Volumen, Velocidad y Cambios Acotados]
*Regla:* Prompts ultra-directos, prohibición estricta de tocar archivos fuera del bloque `**Archivos:**`, verificación obligatoria con `tools/run_isolated.sh` y **cortocircuito a los 2 intentos** (si falla tras 2 intentos, escala automáticamente a **Argon 4**).

| Tarea | Descripción | Revisión Argon 4 |
| :--- | :--- | :--- |
| **T2.1** | Latencia por etapa (`perf_counter`) y métricas por proceso (`metrics.{service}.jsonl` + `latency_report.py`) | Obligatoria (rotación multiproceso en `logger.py`) |
| **T2.4** | Vinted: ficha y vendedor robustos (`fetch_item_details` -> `None`, reintentos y chequeo de cooldown) | Obligatoria |
| **T2.9** | `multi-sniper`: frecuencia por tienda según rendimiento (`interval_s` + jitter) | Estándar |
| **T3.1** | Deduplicación semántica sin herencia entre vendedores distintos (`seller_id` / `dHash` / filtro de motivos) | Obligatoria (Riesgo) |
| **T3.4** | Vinted: coste puesto en casa según país (`vinted_shipping_eur`) | Estándar |
| **T3.5** | Reservados: re-chequeo al caer la reserva (`market_seen_items.state`) | Obligatoria (SQLite schema) |
| **T3.6** | Payload de alerta: primera línea <=110 chars, sin deeplinks rotos y test snapshot | Estándar |
| **T3.7** | Ciclo de vida de la alerta en Discord (`PATCH` reservado/vendido en `outcome_tracker.py`) | Obligatoria |
| **T3.8** | Resumen diario de alertas silenciosas (`tasks.loop` 21:00 Madrid + consulta SQL) | Obligatoria (Discord loop) |
| **T3.9** | Muestreo determinista (10% SHA-1) de anuncios bloqueados por antifraude (`fraud_samples`) | Obligatoria (SQLite schema) |
| **T3.10** | Script A/B de resolución de imagen (`tools/ab_image_resolution.py` y `ai.image_max_dim`; ejecución real bloqueada sin permiso) | Estándar |
| **T3.11** | Coherencia de umbrales (`resolve_min_profit`, `offer_scan_multiplier`) y sincronización de `AGENTS.md` | Estándar |

### B. Tareas asignadas a **Argon 4** [Alta Complejidad, Concurrencia, Evasión TLS y Arquitectura]
*Regla:* Ejecución directa en **Argon 4** + Revisión cruzada en **Argon 4**. También asume cualquier tarea donde Gemini 3.8 Max devuelva `ESCALAR_ARGON4` tras 2 intentos fallidos.

| Tarea | Descripción | Motivo Crítico |
| :--- | :--- | :--- |
| **T2.2** | Capa HTTP coherente (`http_client.py`, `PlatformClient` con `threading.local`, huella `chrome152`/`safari260`, limpieza XHR `Sec-Fetch-*`) | Evasión TLS/JA3, sesiones por hilo y cabeceras CORS |
| **T2.3** | Cadencia por consulta con `TokenBucket` thread-safe y `QueryScheduler` (`heapq` + multiplicador nocturno) | Concurrencia, rate-limiting adaptativo y bucle central de Wallapop/Vinted |
| **T2.5** | Vendedor Wallapop: identidad coherente (`PlatformClient`), límite y caché persistente SQLite (`seller_profile_cache` con TTL por tramo) | Arquitectura de caché multinivel (RAM + SQLite) y evasión |
| **T2.6** | Pipeline común (`pipeline.py`) y workers concurrentes (`PriorityQueue`, `EVAL_WORKERS=2`, `BEGIN IMMEDIATE`) | Condicional a T2.1; concurrencia multi-hilo y transacciones SQLite |
| **T2.7** | Gateway de IA: `ModelBreaker` por modelo y `deadline_s` máximo por ítem | Resiliencia del core de IA y control de tiempos acumulados |
| **T2.8** | Embeds de salud asíncronos (`HealthNotifier` daemon queue + deduplicación 10 min + `threading.Lock` en `AntiBanHealthMonitor`) | Concurrencia multi-hilo (Invariante 6) |
| **T3.2** | Pre-CeX determinista por título antes de la IA (vocabulario del espejo en RAM + acoplamiento con Puerta 0 y pre-gate) | Arquitectura de caché en memoria y heurísticas antifraude |
| **T3.3** | Valoración P2P anclada (`cex_sell * 0.85` / `p75 * 1.15`) y nivel de confianza (`valuation_confidence`) | Motor matemático financiero crítico |

---

## 1. Preparación de Rama

1. Ejecuta por SSH `git status`. Verifica que el árbol de trabajo esté limpio (o haz commit de las actualizaciones de agentes/documentación).
2. Para la Fase `N`, sitúate en la rama `plan-fase-N` (créala con `git switch -c plan-fase-N` desde el cierre de la fase anterior si aún no existe).
3. Comprueba `docs/audits/registro.md` para identificar las tareas ya resueltas.

## 2. Flujo de Ejecución por Tarea (`Implementador -> Revisor -> Verificación -> Check`)

Para cada tarea pendiente `Tn.m` en el orden del §3:

1. **Comprobar dependencias y condiciones:**
   - Si depende de una tarea fallida/bloqueada, sáltala y anótala.
   - Si es **T2.6**, evalúa primero la condición del plan con los datos reales de `tools/latency_report.py` tras T2.1 (solo se ejecuta si la espera en cola p90 > 10 s o > 20% de las rondas llaman a IA; hoy Wallapop = 0,02 y Vinted = 0,13).
   - Si es **T3.10**, implementa el soporte en código y el script en modo seguro, dejando la ejecución con red real como `BLOQUEADO` pendiente de aprobación del usuario.
2. **Paso 1 — Implementación (`implementador`):**
   - Selecciona el modelo según la Matriz de Enrutamiento (**Gemini 3.8 Max** o **Argon 4**).
   - El implementador escribe primero el test en `tests/unit/test_*.py`, verifica que falla en `tools/run_isolated.sh`, implementa el cambio estrictamente dentro de los archivos permitidos, pasa la suite aislada + `py_compile`, registra en `docs/audits/registro.md` y crea el commit `Tn.m: <resumen>`.
   - Si un implementador en **Gemini 3.8 Max** devuelve `ESCALAR_ARGON4` tras 2 intentos fallidos, relanza inmediatamente la tarea con **Argon 4**.
3. **Paso 2 — Revisión Independiente (`revisor` en Argon 4):**
   - Lanza el agente `revisor` sobre el commit de `Tn.m`.
   - Si devuelve `CAMBIOS`, envía la lista exacta de problemas al `implementador` (en **Argon 4** si el fallo es de concurrencia/arquitectura) para un commit de corrección y re-ejecuta el `revisor`.
4. **Paso 3 — Test de Verificación y Check en `plan-maestro.md`:**
   - Una vez `APROBADO` por el revisor, verifica que la casilla `[x]` esté marcada en `docs/audits/2026-11-11-plan-maestro.md` y reporta al usuario:
     `Tn.m ✅ [Modelo] — <resumen y métrica>`.

## 3. Cierre de Fase

1. Ejecuta el `revisor` (**Argon 4**) sobre el diff completo de la fase (`git diff <base-fase>..HEAD`).
2. Actualiza `ROADMAP.md` con el estado de la fase y haz commit.
3. **Detente antes de desplegar:** presenta la tabla resumen de tareas, commits, métricas, decisiones pendientes del usuario y el comando exacto de despliegue en `/opt/wallapop-sniper`.
