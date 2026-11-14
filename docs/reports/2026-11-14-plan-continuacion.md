# Continuación del plan maestro — 2026-11-14

Código preparado y probado en `plan-fase-3`. **Sin desplegar, sin reinicios ni llamadas de pago.**
Entorno confirmado: Linux, `miky-p6657es`, ejecución directa en `/opt/wallapop-sniper`.
Se preservan los cambios previos del usuario y la eliminación de `ROADMAP.md`.

| Tarea | Resultado | Evidencia y cierre pendiente |
|---|---|---|
| T1.8 | Tope solicitado de **0,10 USD/mes**, adicional al diario, en código, `.env.example` y `.env` local | 9 tests; mes civil Madrid, relectura antes de proveedores/fallbacks y tras semáforo; sin contabilidad no paga. Falta revisión independiente de la ampliación y cargar la configuración en producción. |
| T2.6 | Condición no demostrada; no se activa el refactor de workers | Últimas 48 h, con rotaciones: IA en Wallapop 78/2790 rondas (2,80 %), Vinted 115/1273 (9,03 %), Multi 4/932 (0,43 %). No hay medida de cola pura p90 >10 s. |
| T2.7 | Breaker por modelo y plazos del gateway: 10 s Puerta 1 / 20 s visión | 19 tests, incluido transporte real de cuerpo lento en loopback. Revisor independiente detectó bloqueo SQLite: 12,046 s con plazo 10 s. Corrección probada con bloqueo WAL, restaurando busy_timeout; falta segunda revisión independiente de esa corrección. No se afirma un límite de 30 s para todas las etapas externas al gateway. |
| T2.8 | Aprobada por revisor independiente | 7 tests; cola daemon y deduplicación 600 s, encolado <5 ms con POST simulado de 2 s. Casilla cerrada. |
| T3.2 | Pre-CeX local antes de antifraude/IA en los tres escáneres | 6 tests; PS5 de 90 € y vendedor sin valoraciones dispara anzuelo. Replay: **372/517 = 71,95 %** de familias coincidentes sobre todos los deals con ficha CeX; 372/372 entre cubiertos. Sin referencia fiable, no descarta por este criterio. Falta revisión independiente y KPI posterior al despliegue. |
| T3.3 | Reventa anclada y confianza persistida/visible en Discord | 8 tests; IA 400/CeX 300 → óptimo 255. Histórico p75 ×1,15 solo con ≥5 observaciones del submodelo/14d. Confianza baja exige mínimo sonoro ×1,5 sin rebajar el de ofertas. Oferta OLED de 110 € conserva ≥35 € de margen. Falta revisión independiente. |
| T3.10 | Soporte A/B corregido; experimento real pendiente | 6 tests de imagen (2 nuevos). Key en cabecera, contabilidad y presupuesto también en el A/B. Simulación: 20 fotos, sin red. Se conserva 384 px; no se inventan ratio de tokens ni concordancia reales. |

## Presupuesto y producción

Decisión expresa del usuario: `AI_MONTHLY_BUDGET_USD=0.10`. No se ha aumentado el límite diario.
La copia de BD mostraba aproximadamente **1,13 USD este mes**: al cargar el cambio, la IA quedará pausada hasta el mes siguiente.
Los procesos en ejecución aún deben cargar el código y la variable nuevos; guardar `.env` no reinicia servicios.

El control usa los costes estimados por el proyecto y comprueba antes de cada llamada; las ya iniciadas pueden terminar.
No es un límite de facturación impuesto en la cuenta del proveedor. Una escritura fallida de coste pausa nuevas llamadas en ese proceso.
El A/B real tampoco tiene presupuesto disponible este mes y sigue requiriendo aprobación específica.

## Verificación ejecutada

- Baseline: 212 tests unitarios; resultado final: **263/263 OK** en contenedor efímero, sin red ni secretos, con copia de SQLite.
- Compilación de los 12 módulos principales y las tres herramientas nuevas/actualizadas: OK.
- `config.json`: 17 targets; ambos agentes TOML pasan `tomllib`.
- `tests/test_fase7_2.py`: 5/5 antes y después del ancla, con la versión anterior cargada solo en el contenedor aislado. La copia temporal del código se retiró.
- Comparación anterior T2.8: `test_fase4.py` pasa antes/después; `test_task41.py` mantiene su fallo/error previos sin cambios.
- `tools/replay_valuation.py`: reconstrucción informativa de 624 reventas; 22 óptimos distintos. Se usa el óptimo histórico como proxy de la estimación IA original, ausente en el registro. Los 360 beneficios distintos y 61 rechazos bajo configuración actual **no se atribuyen exclusivamente al ancla**.
- `git diff --check` del trabajo previo del usuario señala espacios finales en `AUDIT_CONCLUSION.md`; no se han corregido por estar fuera de estas tareas. Los commits de esta continuación se comprueban por separado.

## Agentes y revisión

Definiciones: [.codex/agents/README.md](../../.codex/agents/README.md).
Implementador T2.7: Astra alto; T2.8: Sol alto; revisión independiente: Astra alto.
T3.2 tuvo preparación de análisis en Astra. Tras agotarse la cuota de subagentes, el padre terminó las implementaciones restantes y la corrección SQLite.
**No se presenta la verificación del padre como revisión independiente.** Las casillas pendientes se mantienen abiertas.

## Commits locales

| Commit | Cambio |
|---|---|
| `0ba3773` | T2.8: avisos de salud asíncronos |
| `1ade184` | T2.7: breaker, transporte y plazo acumulado |
| `5e83b0e` | T1.8: presupuesto mensual 0,10 USD |
| `6dbba12` | T3.2: pre-CeX determinista |
| `a716184` | T3.3: valoración anclada y confianza |
| `efc6da7` | T1.8: plazo y fallo de contabilidad |
| `3162d3d` | T3.10: presupuesto y clave protegida en A/B |

## Siguiente paso

Cuando haya cuota, revisor independiente sobre los commits de T1.8/T2.7/T3.2/T3.3/T3.10 y el diff de fase.
Después, aprobación del usuario antes de desplegar: la habilidad [ejecutar-plan](../../.agents/skills/ejecutar-plan/SKILL.md), §3, indica «Detente antes de desplegar» y el plan §0 reserva esa decisión al usuario.

Comando propuesto, **no ejecutado**, solo después de revisión y aprobación, desde `/opt/wallapop-sniper`:

```sh
docker compose up -d --build wallapop-sniper vinted-sniper multi-sniper relister-bot outcome-tracker
```

T2.6 sigue condicionado a métricas; T3.10 real sigue pendiente de permiso y presupuesto. La fase no se declara cerrada.
