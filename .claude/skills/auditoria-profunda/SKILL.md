---
name: auditoria-profunda
description: Auditoría profunda de fallos y mejoras internas del buscador de chollos, con auditores aislados, escépticos independientes y parada cuando un área funciona bien. Genera solo un informe, sin tocar el código.
argument-hint: "[all|captura|antifraude|ia|rentabilidad|datos|alertas]"
disable-model-invocation: true
---

# Auditoría profunda

Busca problemas reales, no motivos para criticar. Que un área salga satisfactoria es un resultado tan valioso como un bug: dice al dueño del proyecto dónde no hace falta gastar tiempo.

Solo se ejecuta cuando la persona la invoca, porque una auditoría completa consume muchos tokens.

Área solicitada: `$ARGUMENTS` (si está vacío, `all`).

## 1. Ejecutar el workflow

El proyecto está en una unidad de red y Workflow no acepta scripts en esas rutas, así que primero copia el script a local:

1. Con Bash, copia `auditoria.workflow.js`, de la carpeta base de esta skill, al directorio scratchpad de la sesión y comprueba con `cmp` que la copia es idéntica. Copiar el archivo evita los errores de transcribir un script largo.
2. Llama a la herramienta Workflow con:
   - `scriptPath`: la ruta absoluta de esa copia local.
   - `args`: `{"area": "<área solicitada>"}`.

Si no hay scratchpad, lee el script y pásalo completo en el campo `script`.

Avisa en una línea de que está en marcha y espera su notificación. El script ya aplica el aislamiento, el contraste y la parada; no repitas esas decisiones ni lances agentes adicionales.

Si devuelve `error`, enseña las áreas válidas y detente.

## 2. Consolidar el resultado

Lee `docs/audits/registro.md` solo en este paso, si existe. Los auditores no lo ven para que su criterio siga siendo independiente.

Compara cada hallazgo con el registro por causa, no por texto exacto:

| Estado en el registro | Qué hacer |
|---|---|
| `descartado` | No lo incluyas; cuenta cuántos se filtraron |
| `pendiente` o `aceptado` | Muévelo a «Ya conocidos» |
| `resuelto` | Mantenlo y márcalo como **posible regresión** |

Une también los hallazgos que aparezcan en dos áreas por la misma causa. No cambies ningún veredicto ni la clasificación del workflow: si te parece mal, añade una nota aparte.

## 3. Escribir el informe

Guárdalo en `docs/audits/AAAA-MM-DD-<área>.md`. Si ya existe, añade `-2`, `-3`, etc. No sobrescribas informes anteriores.

Usa esta plantilla y omite las secciones vacías, salvo «Estado por área» y «Límites»:

```markdown
# Auditoría profunda: <área> (<fecha>)

## Veredicto
<2-4 frases: si el proyecto está sano y qué merece atención primero>

## Estado por área
| Área | Estado | Rondas | Confirmados | En disputa |

## Áreas que funcionan satisfactoriamente
### <Área>
Se comprobó: <resumen de `comprobado`>

## Hallazgos confirmados
Ordenados por impacto. Primero lo que pierde o inventa chollos y lo que corrompe datos.

### H1. <Título>
- **Dónde:** `archivo:línea`
- **Criterio:** <criterio> · **Confianza:** <confianza>
- **Qué falla:** <escenario>
- **Evidencia:** <evidencia del auditor y comprobación del escéptico>
- **Propuesta:** <propuesta>

## En disputa
Para cada uno: la afirmación, los argumentos de los auditores, la objeción del escéptico y qué dato lo resolvería.

## Ya conocidos

## Apéndice: mejoras menores
Sin verificar por el escéptico salvo que se indique. Máximo 5 por área.

## Límites de esta auditoría
- Código desplegado frente a código auditado: <`codigo_desplegado`>
- Tests ejecutados y omitidos con su motivo
- Lo que no se pudo comprobar
- Áreas incompletas, si las hay
```

Un área `incompleto` nunca se presenta como satisfactoria. Sé fiel a los datos: no suavices los hallazgos ni añadas opiniones sin respaldo.

## 4. Actualizar el registro

Crea `docs/audits/registro.md` si no existe y añade solo los hallazgos confirmados y en disputa que sean nuevos, con estado `pendiente`:

```markdown
# Registro de auditorías
Estados: pendiente, aceptado, descartado, resuelto.

| ID | Fecha | Área | Ubicación | Título | Estado | Nota |
```

Los IDs siguen la serie `A-001`, `A-002`, etc. No cambies el estado de filas existentes salvo que la persona lo pida.

## 5. Cerrar

Resume el veredicto, enlaza el informe e indica qué se omitió. Después pregunta qué hallazgos quiere arreglar. No modifiques código ni hagas commits en esta skill.
