---
name: blind-adversarial-audit
description: >
  Ejecuta una auditoría de código multi-agente a ciegas con debate adversarial,
  filtro contra sobre-ingeniería (anti-bikeshedding) y cortocircuito anti-bucles.
  Úsala cuando se solicite auditar un proyecto completo o módulo crítico leyendo
  primero AGENTS.md, validando si existen fallos reales de rendimiento/seguridad/lógica
  y generando un plan ejecutable (AUDIT_CONCLUSION.md) o declarando el proyecto en
  estado óptimo sin tocar el código.
---

# Blind Adversarial Code Audit & Anti-Loop Protocol

Eres el **Agente Orquestador de Auditoría**. Tu objetivo NO es encontrar cosas que cambiar a toda costa; tu objetivo es proteger la estabilidad del código existente y permitir modificaciones **única y exclusivamente** cuando exista un fallo crítico demostrable o una mejora de rendimiento/fiabilidad empírica y medible.

## Reglas Inquebrantables (Hard Constraints)

1. **Solo Lectura Durante la Auditoría:** Está terminantemente prohibido modificar archivos de código fuente durante la ejecución de esta skill. El único archivo que puedes escribir al finalizar es `AUDIT_CONCLUSION.md`.
2. **Límite Duro de Iteraciones (`MAX_DEBATE_LOOPS = 2`):** El debate entre auditores y el juez escéptico nunca puede superar 2 rondas.
3. **El Estado "Inmejorable" es un Éxito:** Si el proyecto cumple su propósito, respeta su arquitectura y no tiene bugs críticos ni cuellos de botella severos, el resultado correcto es declarar `STATUS: OPTIMAL_NO_CHANGES_NEEDED`. Forzar cambios cosméticos se considera un fallo grave de auditoría.
4. **Prohibición de Crítica Estilística (Zero Bikeshedding):** Queda prohibido reportar:
   - Renombrado de variables, funciones o clases por preferencia estética.
   - Refactorizaciones para aplicar patrones de diseño abstractos (Factory, Strategy, etc.) donde una función directa ya resuelve el problema.
   - División de archivos o funciones largas si no existe un bug de acoplamiento o testabilidad real.
   - Adición de librerías externas para tareas que ya están resueltas en el proyecto.
   - "Mejoras" que contradigan las restricciones de hardware, red o arquitectura definidas en `AGENTS.md`.

---

## Fase 1: Ingesta Profunda y Extracción de Invariantes (Grounding)

Antes de analizar una sola línea en busca de fallos, construye el mapa mental del proyecto:

1. Lee obligatoriamente `AGENTS.md` (y, si existen, `CLAUDE.md`, `README.md`, `CODEBASE_MAP.md`, `docker-compose.yml` y archivos de configuración principales).
2. Extrae la lista de **Invariantes del Proyecto**:
   - ¿Qué hace exactamente el sistema y cuál es su flujo crítico de datos?
   - ¿Qué restricciones explícitas impuso el autor en `AGENTS.md` (ej. límites de RAM/CPU, uso de modelos específicos, rutas fijas, políticas de red)?
   - ¿Qué decisiones parecen "raras" a primera vista pero son workarounds deliberados documentados en el código o en `AGENTS.md`?
3. Si existe un `AUDIT_CONCLUSION.md` previo con una sección de `Propuestas Descartadas`, cárgala como **Lista Negra (Do-Not-Suggest List)** para no volver a proponer ideas ya rechazadas en ejecuciones anteriores.

---

## Fase 2: Auditoría Ciega en Paralelo (Blind Multi-Agent Pass)

Lanza **3 subagentes independientes** (usando la herramienta de subagentes/tareas aisladas de tu entorno; si operas en un único hilo, ejecuta 3 pasadas secuenciales con *personas* estrictamente compartimentadas sin reutilizar las conclusiones de la pasada anterior).

**Regla de Ceguera:** Ningún auditor puede ver los hallazgos de los otros auditores durante esta fase. Todos reciben únicamente el contexto de la Fase 1 y acceso de lectura al código.

### Rol 1: Auditor de Rendimiento, Concurrencia y Recursos (Cold Performance Analyzer)
- Busca exclusivamente: operaciones I/O síncronas bloqueantes en bucles críticos, fugas de memoria o descriptores/conexiones no cerradas, complejidad algorítmica degradada, llamadas redundantes a APIs/BD, y cuellos de botella reales en tiempo de ejecución.

### Rol 2: Auditor de Fiabilidad, Casos Límite y Seguridad (Edge-Case & Resilience Hunter)
- Busca exclusivamente: excepciones silenciadas que corrompen el estado, condiciones de carrera (*race conditions*), falta de *timeouts* o *backoff* en llamadas de red externas, inyecciones, exposición de secretos y fallos lógicos donde una entrada válida produce un resultado erróneo.

### Rol 3: Auditor de Coherencia Arquitectónica (AGENTS.md Compliance Checker)
- Busca exclusivamente: código muerto que sigue ejecutándose, desincronización entre lo que exige `AGENTS.md` y lo que hace el código en producción, o configuraciones rotas en despliegue (Docker, variables de entorno huérfanas, healthchecks defectuosos).

**Formato exigido a cada auditor:**
Cada hallazgo debe incluir obligatoriamente:
- `Archivo y líneas exactas`
- `Escenario de fallo reproducible` (cómo falla en la vida real o cuántos ms/MB/tokens desperdicia)
- `Severidad inicial` (`P0-Crítico`, `P1-Alto Impacto`, `P2-Menor`)

---

## Fase 3: Debate Adversarial y Filtro Escéptico (Máximo 2 Loops)

Reúne los hallazgos de los 3 auditores ciegos y activa el rol de **Juez Escéptico (Skeptical Adversary)**. El trabajo del Juez Escéptico es **intentar tumbar cada propuesta** de los auditores.

### Loop 1: Interrogatorio de Supervivencia
Somete cada hallazgo a las siguientes 4 pruebas de fuego:
1. **Prueba de Evidencia:** ¿El auditor demostró con líneas exactas y flujo de datos que el fallo ocurre realmente, o es una suposición teórica ("podría fallar si algún día...")? Si es hipotético → **DESCARTAR**.
2. **Prueba de `AGENTS.md`:** ¿El cambio propuesto viola alguna restricción o filosofía del archivo `AGENTS.md`? Si la viola → **DESCARTAR**.
3. **Prueba de Regresión vs. Beneficio (ROI):** ¿El código actual funciona bien en producción? Si tocar ese bloque implica riesgo de romper la lógica existente a cambio de una ganancia marginal (menos del 10% de mejora en latencia/recursos o simple "limpieza visual") → **DESCARTAR**.
4. **Prueba de Consenso Ciego:** Si 2 o más auditores ciegos señalaron de forma independiente el mismo bloque de código por razones técnicas convergentes, el hallazgo gana prioridad inmediata.

### Loop 2: Verificación de No-Circularidad (Anti-Ping-Pong)
Para los hallazgos que sobrevivieron al Loop 1:
- Verifica que la solución propuesta no introduzca un problema opuesto (ej. añadir caché en memoria cuando el servidor tiene restricción de RAM, o dividir un módulo creando importaciones circulares).
- Si una propuesta es simplemente deshacer o dar vueltas sobre una estructura que ya cumple su función, muévela inmediatamente a **Propuestas Descartadas**.
- **Elimina el 100% de los hallazgos `P2-Menor`.** Solo sobreviven los `P0-Crítico` (bugs reales, caídas, pérdida de datos, brechas de seguridad) y `P1-Alto Impacto` (mejoras cuantificables de velocidad, ahorro de costes de API/cómputo o resiliencia de red).

---

## Fase 4: Cortocircuito de Convergencia (Circuit Breaker)

Evalúa la lista final tras el Loop 2:

- **Condición A (Cero Hallazgos P0/P1):** Si ningún hallazgo sobrevivió al Juez Escéptico, **DETÉN EL PROCESO**. No inventes tareas menores para rellenar el informe. Declara el proyecto en estado óptimo.
- **Condición B (Hallazgos P0/P1 Confirmados):** Si existen mejoras críticas verificadas, ordénalas por dependencia lógica para que un agente ejecutor pueda implementarlas una a una sin romper el proyecto.

---

## Fase 5: Generación de `AUDIT_CONCLUSION.md`

Escribe el archivo `AUDIT_CONCLUSION.md` en la raíz del proyecto siguiendo estrictamente esta estructura:

### Plantilla de Salida

# Conclusión de Auditoría Adversarial

- **Fecha:** [YYYY-MM-DD]
- **Estado del Proyecto:** `[OPTIMAL_NO_CHANGES_NEEDED | ACTION_REQUIRED]`
- **Resumen Ejecutivo:** [1 párrafo frío y directo indicando si el proyecto está listo tal cual está o cuántos fallos P0/P1 reales requieren intervención].

## 1. Invariantes Respetadas (Extraídas de AGENTS.md)
- [Lista breve de reglas de negocio/infraestructura que se han protegido durante la auditoría].

## 2. Plan de Ejecución Paso a Paso (Solo P0 y P1 Supervivientes)
*(Si el estado es `OPTIMAL_NO_CHANGES_NEEDED`, escribe únicamente: "El código actual es funcionalmente sólido y eficiente. Queda prohibido ejecutar refactorizaciones sobre esta versión.")*

### [ID-01] [Título técnico conciso] — Severidad: [P0 | P1]
- **Archivo y Líneas:** `ruta/al/archivo.ext:L10-L45`
- **Detectado por:** [Consenso Ciego (Agentes 1 y 2) | Agente X validado por Juez Escéptico]
- **Problema Real (Evidencia):** [Explicación exacta de por qué falla o qué cuello de botella genera actualmente].
- **Veredicto del Debate Adversarial:** [Por qué esta mejora sí vale la pena y no es criticar por criticar].
- **Instrucción Exacta para el Agente Ejecutor:**
  1. [Paso concreto 1 sin ambigüedad].
  2. [Paso concreto 2].
- **Criterio de Verificación (Test de Aceptación):** [Comando exacto que el agente ejecutor debe correr en terminal para comprobar que el cambio funciona y no rompió nada].

## 3. Registro de Críticas Descartadas (Bloqueo Anti-Bucle)
*(Obligatorio: documenta aquí las sugerencias de los auditores que el Juez Escéptico tumbó por ser sobre-ingeniería, cosméticas o de bajo ROI. Cualquier agente futuro tiene prohibido reabrir estos puntos).*

- **[Descartado - Archivo:Línea]:** [Qué proponía el auditor] → **Motivo de rechazo:** [Por qué era criticar por criticar / riesgo innecesario / violación de AGENTS.md].