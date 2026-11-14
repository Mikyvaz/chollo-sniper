# Agentes Codex para el plan maestro

Definiciones preparadas el 2026-11-12 a partir de:

- [.claude/agents/implementador.md](../../.claude/agents/implementador.md).
- [.claude/agents/revisor.md](../../.claude/agents/revisor.md).

Agentes nativos de proyecto: [implementador.toml](implementador.toml) y [revisor.toml](revisor.toml). Replican alcance, invariantes, validación aislada y dictamen independiente de los originales de Claude.

## Uso y límite vigente

El usuario ha autorizado continuar la implementación del plan maestro y elegir modelos según dificultad. Antes de cada bloque, el padre comunica tarea y validación prevista. Esta autorización permite cambios y tests aislados; el despliegue sigue requiriendo aprobación específica (§0 del plan).

Ejemplo de encargo:

> Usa implementador para T2.7 con los archivos de esa tarea y su test unitario de aceptación. Después usa revisor para revisar el diff y verificarlo de forma independiente. No despliegues.

La unidad es una tarea. Implementador escribe y verifica; revisor examina y emite dictamen sin editar archivos. El padre orquesta el orden del plan y registra evidencias. La matriz [estado del plan](../../docs/audits/2026-11-12-estado-plan-maestro.md) es el inventario histórico del 12 de noviembre; el plan y el registro contienen la continuación.

Enrutamiento aplicado el 2026-11-14 (modelos disponibles en esta sesión):

| Demanda | Implementador | Revisor |
|---|---|---|
| Gateway, concurrencia, heurísticas y valoración (T2.7, T3.2, T3.3) | `gpt-6-astra`, razonamiento alto | `gpt-6-astra`, alto |
| Trabajo acotado y herramientas (T2.8, T3.10) | `gpt-6.1-sol`, alto/medio | `gpt-6-astra`, alto |

Se ejecutaron implementadores T2.7/T2.8 y revisión independiente de ambos; T2.8 fue aprobada. El revisor pidió una corrección SQLite en T2.7, ya corregida y probada por el padre. Después se agotó la cuota de subagentes: el padre continuó T1.8/T3.2/T3.3/T3.10, sin atribuirse una revisión independiente. Sus casillas permanecen abiertas cuando falta esa revisión o medición real.

## Adaptaciones frente a Claude

1. Formato TOML con name, description y developer_instructions; no se ha copiado el frontmatter de Claude como si fuera configuración Codex.
2. Los TOML omiten model y model_reasoning_effort: el orquestador los asigna explícitamente al delegar según dificultad, o se heredan si no hay selección. Gemini 3.8 Max / Argon 4 del plan no se simulan como modelos Codex. Esto no cambia los modelos Gemini/OpenRouter del producto.
3. Se conserva el límite de dos intentos y el rescate acotado, con escalado al padre en vez de un fallback automático a Argon 4.
4. Los estados SOLO_ANALISIS, PARCIAL y BLOQUEADO evitan afirmar una implementación o una revisión verificada cuando solo se ha leído código.
5. La compilación aislada dirige bytecode a /work/pycache: /src está montado como solo lectura.
6. Las prohibiciones de edición del revisor son instrucciones de comportamiento. Ambos agentes heredan las herramientas y permisos de la sesión; estos archivos no crean una sandbox de solo lectura ni elevan permisos.
7. Los originales de Claude y .agents/agents no se han modificado. No se ha cambiado configuración global ni se han fijado modelos.
8. Primero se detecta el entorno: en Linux/miky-p6657es se ejecuta directamente en `/opt/wallapop-sniper`; desde el portátil se usa SSH. `ROADMAP.md` está eliminado por el usuario: no se recrea.
9. Decisión de presupuesto recibida: `AI_MONTHLY_BUDGET_USD=0.10`. No se aumenta el límite diario ni se ejecuta el A/B de pago por continuar el plan.

Formato e herencia contrastados con la [documentación oficial de subagentes de Codex](https://learn.chatgpt.com/docs/agent-configuration/subagents#custom-agents).

Los dos TOML pasan `tomllib` en el arnés aislado. Los roles nativos se han usado mediante la herramienta de subagentes; no se ha arrancado otra instancia de Codex ni modificado configuración global.
