# ROADMAP — Llevar el sniper al siguiente nivel

> Documento vivo. Actualízalo al terminar cada bloque de trabajo (estado, fecha y hallazgos).
> Última actualización: **2026-11-11**
>
> **Plan de ingeniería pendiente (2026-11-11):** [`docs/audits/2026-11-11-plan-maestro.md`](docs/audits/2026-11-11-plan-maestro.md). Son 34 tareas en 3 fases (bugs y coste, latencia y anti-detección, precisión y notificaciones), con evidencia en `docs/audits/2026-11-11-sondas/`. Cubre la «Velocidad de reacción» y parte del «Antifraude» de este documento. Ejecútalo en orden y anota en `docs/audits/registro.md` cada tarea cerrada.
>
> **Estado del plan (2026-11-11):** Fase 1 implementada y revisada en la rama `plan-fase-1` (T1.0–T1.7, T1.9–T1.13; T1.8 solo el código, sin fijar el valor de `AI_DAILY_BUDGET_USD`). **Sin desplegar**: en espera de la aprobación del usuario y de 24–48 h de observación antes de la Fase 2.

## Diagnóstico de partida (2026-11-10)

La parte técnica funciona. El cuello de botella está en **convertir alertas en dinero**.

| Métrica (31 oct – 10 nov) | Valor |
|---|---|
| Alertas generadas | 506 |
| Compradas / vendidas | 3 / 1 (0,6 % de conversión) |
| Beneficio estimado medio por alerta | 81–119 € (207 alertas con más de 100 €): **no está calibrado** |
| Alertas de regateo (`offer_opportunity`) | 68 % |
| Coste total de IA | ~0,90 $ en 10 días (no es un problema) |
| Notificaciones al día (antes del arreglo) | 500–700, por las flash previas a la IA (>90 % rechazadas después) |

Primeras señales: las PS5 buenas se reservan o venden en 1–2 h, con 10 o más conversaciones. Todo apunta a la **velocidad**.

---

## Nivel 1 — Medir la verdad ✅ en marcha desde el 2026-11-10

**Objetivo:** juntar 1–2 semanas de datos reales para saber si el cuello de botella es la precisión, la velocidad, el fraude o la capacidad de actuar.
**Revisión prevista: ~2026-11-24.**

### Hecho
- [x] **Calificación manual:** botón 📝 *Calificar* en cada alerta con veredicto 👍/🤔/👎, 23 motivos y nota libre. Comandos `/calificar` (alertas antiguas) y `/calificaciones` (resumen y CSV). Tabla `alert_feedback`.
- [x] **Seguimiento automático** (`outcome_tracker.py`, contenedor `outcome-tracker`): comprueba cada alerta a los 15 min, 1 h, 3 h, 6 h, 24 h y 72 h. Registra si está disponible, reservada, vendida o eliminada, junto con visitas, favoritos, conversaciones y precio. Comando `/resultados`, con un CSV que une resultado, calificación y vendedor. Tablas `alert_outcomes` y `alert_outcome_checks`.
- [x] **Retraso real** de la publicación a la alerta (`published_at`; en Vinted es aproximado).
- [x] **Contexto del vendedor** guardado en cada alerta (`deals.alert_context_json`).
- [x] **Etiqueta de verdad antifraude:** 24 h después se comprueba si la cuenta del vendedor fue suspendida o eliminada.
- [x] **Menos notificaciones** (`config.json` → `notifications`): flash desactivado y solo suenan las buenas, con un máximo de 4 por hora. El resto llega en silencio con el motivo 🔕.
- [x] **Antifraude Vinted:** antigüedad aproximada por la fecha de verificación, ventas y compras reales, perfil fantasma, denuncias y cuenta suspendida. Arreglado el fallo que convertía "perfil no consultado" en "0 valoraciones".

### Durante las 2 semanas (tarea del usuario)
- Califica **también las que no interesan**, aunque sea con un toque (👎 y un motivo). Si solo se califican las llamativas, el dataset queda sesgado.
- Objetivo: **150–200 calificaciones**.

### Al terminar: análisis (tarea del agente)
1. Descarga los CSV de `/resultados` y `/calificaciones`, o consulta las tablas directamente.
2. Responde con datos:
   - ¿Qué % de alertas desaparece en menos de 1 h por plataforma y radar? ¿Cuál es el retraso mediano publicación → alerta? → **velocidad**
   - ¿Qué motivos de 👎 dominan (margen inflado, modelo mal identificado, CeX erróneo…)? → **precisión**
   - ¿Qué % de vendedores con 0 valoraciones acaba suspendido, frente a los que tienen historial? → **fraude**
   - ¿Cuántos 👍 no se compraron, y por qué (capital, tiempo, no responde)? → **ejecución**
3. Recalibra con datos `net_profit`, `recommended_offer` y los umbrales de `notifications`.
4. Decide qué bloque del Nivel 2 va primero según el cuello de botella dominante.

---

## Nivel 2 — Menos alertas, mejores ⏳ pendiente (2–4 semanas)
- [ ] **Score de probabilidad de cierre** en lugar de los niveles fijos 1/2/3. Modelo simple (regresión logística o *gradient boosting*) entrenado con el dataset del Nivel 1, con estas señales: descuento frente a la mediana de cierre, reputación y antigüedad del vendedor, hora, plataforma, distancia, riesgo de fraude y demanda (favoritos y conversaciones).
- [ ] **Alertar por valor esperado:** `P(cierre) × margen − coste de tiempo`, por encima de un umbral.
- [ ] **Beneficio calibrado:** usar el precio de cierre P2P observado (anuncios que desaparecen) en vez del precio pedido o del efectivo de CeX. Añadir el envío internacional y el riesgo de devolución en Vinted FR/IT.
- [ ] **Canal separado de regateo:** mensaje listo para copiar y seguimiento de ofertas aceptadas, para afinar `recommended_offer`.
- [ ] **Resumen diario** de las alertas silenciosas en lugar de mensajes sueltos.
- [ ] Aprender umbrales de `notifications` por radar en lugar de globales.

## Nivel 3 — Cerrar el ciclo comercial ⏳ pendiente (1–2 meses)
- [ ] Ciclo de vida completo: compra → recepción → prueba → publicación → venta, con días en stock, rotación de capital y **ROI real por radar**. Las columnas ya existen en `deals`; falta el flujo y el panel.
- [ ] Reventa asistida: publicación semiautomática en Wallapop y Vinted con el precio de la mediana de cierre.
- [ ] Ajuste automático de radares: subir o bajar `max_price` según el ROI realizado y pausar los radares que no generan ventas.

---

## Velocidad de reacción (para más adelante)
- [ ] **VPS externa** (Azure u otra) para los escáneres. Repartiría la carga entre varias IP, bajaría la latencia y reduciría los 429. Valorar coste frente a latencia ganada con los datos de `/resultados`.
- [ ] Hoy una ronda de Wallapop tarda 15–20 s aunque el intervalo configurado es de 8 s, y una de Vinted unos 60 s. Perfilar por qué (enriquecimiento del vendedor, descargas, IA).
- [ ] Medir también la latencia interna desde la captura hasta la alerta, para separar la del escaneo de la de la IA.

## Antifraude (siguientes pasos)
- [ ] Con 2 semanas de "cuenta suspendida 24 h después", recalibrar los pesos de `evaluate_seller_risk`.
- [ ] Opcional: seguir también una muestra de anuncios **bloqueados** por fraude, para medir falsos positivos (hoy no se siguen porque no generan alerta).
- [ ] Wallapop: aprovechar `seller_type.verified` del endpoint de usuarios.

## Deuda técnica
- [ ] `core_engine.py` (~5.500 líneas): dividir en paquetes `cex/`, `ai/`, `alerts/`, `db/` y `scoring/`.
- [ ] Docker: fijar versiones en un `requirements.txt` y construir imágenes inmutables por commit. Hoy el código se monta como volumen y el `Dockerfile` además lo copia.
- [ ] Ojo: los montajes de **archivos sueltos** con `:ro` no ven las ediciones hechas por Samba hasta reiniciar el contenedor.
- [ ] Tests: renombrarlos por comportamiento y no por tarea. Usan la BD real (`/app/data/sniper.db`), así que deben ejecutarse sobre una copia. Fallos conocidos del 2026-11-10, ninguno nuevo: `test_verify_task06`, `test_bloque5`, `test_fase5` (dimensión del embedding), `test_task32`, `test_task41`, y `test_verify_all` (límite del texto base de `call_ai_copywriter`, que se amplió de 300 a 600 en cambios sin commit).
- [ ] Unificar `seen_items`, `vinted_seen_items` y `market_seen_items`.
- [ ] Observabilidad: panel con `metrics.jsonl` (anuncios por minuto, 403/429, latencia, embudo alerta → compra).
- [ ] Ordenar documentos: `PLAN_DEFINITIVO_V6.md` y `AUDIT_CONCLUSION.md` son históricos. Este ROADMAP y `AGENTS.md` son la fuente de verdad.

## Limitaciones conocidas
- **eBay** devuelve 403 o página de verificación al rastreador: su seguimiento es casi nulo (son menos del 3 % de las alertas).
- Cash Converters, Milanuncios y TodoColección no se siguen en `outcome_tracker`.
- Las alertas anteriores al 2026-11-10 no tienen hora de publicación ni vendedor guardado, y su primera comprobación fue tardía.
- **Cambios sin commit** en la rama `auditoria-datos-cartera`, incluidos todos los del Nivel 1.
