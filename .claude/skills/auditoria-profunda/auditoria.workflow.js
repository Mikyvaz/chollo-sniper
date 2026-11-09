export const meta = {
  name: 'auditoria-profunda',
  description: 'Audita fallos y mejoras internas con auditores aislados, escépticos y parada si el área funciona bien',
  whenToUse: 'Invocado por la skill /auditoria-profunda; args: {area: "all"|clave, dryRun?: true}',
  phases: [
    { title: 'Evidencia', detail: 'datos del servidor en solo lectura, compartidos por todos', model: 'sonnet' },
    { title: 'Auditar', detail: 'dos auditores independientes por área' },
    { title: 'Agrupar', detail: 'une hallazgos con la misma causa', model: 'sonnet' },
    { title: 'Refutar', detail: 'un escéptico por cada hallazgo importante' },
  ],
}

// ---- Configuración ---------------------------------------------------------

const MODELO_RAZONAMIENTO = 'opus'
const MODELO_MECANICO = 'sonnet'
const MAX_RONDAS = 2
const MAX_MENORES_POR_AREA = 5

const AREAS = [
  { clave: 'captura', nombre: 'Captura',
    alcance: 'sniper.py, vinted_sniper.py, multi_sniper.py, antiban.py',
    foco: 'anuncios perdidos o duplicados, paginación, parsers que devuelven vacío sin avisar, sesiones y cookies caducadas, reintentos, cola Fast-Lane, rate limiting que frena de más o de menos' },
  { clave: 'antifraude', nombre: 'Antifraude y vendedores',
    alcance: 'fraud_signals.py y su uso desde los escáneres y core_engine.py',
    foco: 'chollos legítimos bloqueados, estafas que pasan, normalización de ratings, falta de datos del vendedor tratada como dato negativo, dHash, reputación histórica' },
  { clave: 'ia', nombre: 'Pipeline de IA',
    alcance: 'core_engine.py: prescreen_item_text, analyze_and_generate_kit, deduplicación semántica, cachés de IA, ai_cost_log; model routing en config.json',
    foco: 'fallbacks, fail-open, cachés envenenadas o que no aciertan, respuestas mal parseadas, llamadas redundantes, coste por chollo útil, routing por ticket' },
  { clave: 'rentabilidad', nombre: 'Rentabilidad',
    alcance: 'core_engine.py: fetch_cex_live, cex_cache, 3 niveles de chollo, price_history; config.json: precios tope y radares',
    foco: 'emparejamiento con el modelo equivocado de CeX, márgenes y comisiones mal calculados, datos caducados, topes de config incoherentes con el mercado, niveles mal asignados' },
  { clave: 'datos', nombre: 'Datos y concurrencia',
    alcance: 'init_db, get_db y todo acceso a SQLite; locks, hilos y estado compartido en todos los módulos',
    foco: 'conexiones compartidas entre hilos, carreras, escrituras perdidas, tablas que crecen sin límite, índices ausentes en consultas frecuentes, degradación tras días de funcionamiento continuo' },
  { clave: 'alertas', nombre: 'Alertas y operación',
    alcance: 'bot.py, alertas ntfy/Telegram/Discord en core_engine.py, logger.py, healthcheck.py, Dockerfile, docker-compose.yml',
    foco: 'alertas perdidas o duplicadas, botones 1-Tap rotos, estados de deals incoherentes, healthchecks que dan verde con el servicio parado, arranque y reinicio, logs que ocultan fallos' },
]

const ENFOQUES = [
  { clave: 'recorrido',
    texto: 'RECORRIDO DE PRINCIPIO A FIN. Sigue un anuncio real o verosímil a través de esta área, desde que entra hasta que sale, y comprueba en cada paso que el dato y la decisión siguen siendo correctos.' },
  { clave: 'resistencia',
    texto: 'RESISTENCIA MÓDULO A MÓDULO. Recorre cada función relevante y pregúntate qué ocurre con datos ausentes o raros, errores de red, cuotas agotadas, ejecuciones simultáneas y semanas de funcionamiento continuo.' },
]

const CRITERIOS = 'pierde_o_inventa_chollos, corrompe_datos_o_caida, rompe_invariante, impacto_claro'

const ENTORNO = `ENTORNO
- Lee el código con Read, Glob y Grep en Z:\\opt_servidor.
- Windows no tiene el runtime: cualquier comando va por SSH desde la herramienta Bash: ssh servidor "cd /opt/wallapop-sniper && <comando>".
- La base de datos real es /opt/wallapop-sniper/data/sniper.db. Consúltala siempre en solo lectura, con Python y un heredoc:
  ssh servidor python3 - <<'PY'
  import sqlite3
  c = sqlite3.connect('file:/opt/wallapop-sniper/data/sniper.db?mode=ro', uri=True)
  print(c.execute('SELECT count(*) FROM deals').fetchall())
  PY

LÍMITES (producción está en marcha; un error aquí perdería chollos reales)
- Permitido: leer archivos, docker compose ps, docker compose logs, git status, git diff, SELECT en modo solo lectura.
- Prohibido: editar archivos; reiniciar, parar o reconstruir contenedores; escribir en la base de datos; lanzar escáneres; llamar a tiendas o a APIs de IA; instalar paquetes.
- No copies valores de .env, tokens ni datos personales de vendedores en tu respuesta.`

// ---- Esquemas --------------------------------------------------------------

const EVIDENCIA_SCHEMA = {
  type: 'object',
  properties: {
    codigo_desplegado: { type: 'string', description: 'si los contenedores ejecutan el código actual del directorio o una imagen anterior, y cómo lo sabes' },
    servicios: { type: 'string' },
    datos_observados: { type: 'array', items: { type: 'string' }, description: 'hechos con cifras de logs y base de datos, sin interpretar' },
    tests_ejecutados: { type: 'array', items: { type: 'object', properties: { nombre: { type: 'string' }, resultado: { type: 'string' } }, required: ['nombre', 'resultado'] } },
    tests_omitidos: { type: 'array', items: { type: 'object', properties: { nombre: { type: 'string' }, motivo: { type: 'string' } }, required: ['nombre', 'motivo'] } },
  },
  required: ['codigo_desplegado', 'servicios', 'datos_observados', 'tests_ejecutados', 'tests_omitidos'],
}

const AUDITORIA_SCHEMA = {
  type: 'object',
  properties: {
    conclusion: { type: 'string', enum: ['sin_problemas_relevantes', 'hay_hallazgos'] },
    comprobado: { type: 'array', items: { type: 'string' }, minItems: 1, description: 'qué verificaste concretamente y qué resultado dio' },
    no_comprobado: { type: 'array', items: { type: 'string' }, description: 'lo que no pudiste verificar y por qué' },
    hallazgos: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          titulo: { type: 'string' },
          tipo: { type: 'string', enum: ['bug', 'fallo_silencioso', 'decision_negocio', 'fiabilidad', 'coste_rendimiento', 'mejora'] },
          archivo: { type: 'string' },
          linea: { type: 'integer', minimum: 1 },
          cita: { type: 'string', description: 'línea o fragmento literal del código' },
          escenario: { type: 'string', description: 'entrada o estado concreto y resultado erróneo' },
          evidencia: { type: 'string', description: 'qué código, log o dato lo demuestra' },
          importancia: { type: 'string', enum: ['importante', 'menor'] },
          criterio: { type: 'string', enum: ['pierde_o_inventa_chollos', 'corrompe_datos_o_caida', 'rompe_invariante', 'impacto_claro', 'ninguno'] },
          beneficio_medible: { type: 'string', description: 'solo mejoras: beneficio estimado con magnitud; vacío en otro caso' },
          propuesta: { type: 'string' },
        },
        required: ['titulo', 'tipo', 'archivo', 'linea', 'cita', 'escenario', 'evidencia', 'importancia', 'criterio', 'beneficio_medible', 'propuesta'],
      },
    },
  },
  required: ['conclusion', 'comprobado', 'no_comprobado', 'hallazgos'],
}

const GRUPOS_SCHEMA = {
  type: 'object',
  properties: { grupos: { type: 'array', items: { type: 'array', items: { type: 'integer' } } } },
  required: ['grupos'],
}

const VEREDICTO_SCHEMA = {
  type: 'object',
  properties: {
    veredicto: { type: 'string', enum: ['confirmado', 'refutado', 'dudoso'] },
    importante: { type: 'boolean', description: 'si, siendo real, cumple alguno de los criterios de importancia' },
    criterio: { type: 'string', enum: ['pierde_o_inventa_chollos', 'corrompe_datos_o_caida', 'rompe_invariante', 'impacto_claro', 'ninguno'] },
    razon: { type: 'string' },
    evidencia: { type: 'string', description: 'código o dato que has leído tú para llegar al veredicto' },
  },
  required: ['veredicto', 'importante', 'criterio', 'razon', 'evidencia'],
}

// ---- Reglas deterministas ---------------------------------------------------

// Un hallazgo sin ubicación, escenario o beneficio no es accionable: no llega al informe.
function esValido(h) {
  if (!h || !h.archivo || !(h.linea >= 1) || !h.escenario || !h.escenario.trim()) return false
  if (h.tipo === 'mejora' && !(h.beneficio_medible && h.beneficio_medible.trim())) return false
  return true
}

// Sin criterio concreto no puede ser importante, opine lo que opine el auditor.
function normalizar(h) {
  return { ...h, importancia: h.importancia === 'importante' && h.criterio !== 'ninguno' ? 'importante' : 'menor' }
}

// Un solo auditor refutado se descarta; dos auditores independientes contra el escéptico se llevan a disputa.
function clasificar(coincide, v) {
  if (!v || v.veredicto === 'dudoso') return 'disputa'
  if (v.veredicto === 'refutado') return coincide ? 'disputa' : 'descartado'
  return v.importante && v.criterio !== 'ninguno' ? 'confirmado' : 'menor'
}

function estadoArea(confirmados, coberturaCompleta) {
  if (confirmados > 0) return 'con_hallazgos'
  return coberturaCompleta ? 'satisfactorio' : 'incompleto'
}

function debeContinuar(confirmadosRonda, ronda, algunAuditorRespondio) {
  return algunAuditorRespondio && confirmadosRonda > 0 && ronda < MAX_RONDAS
}

// Todo índice aparece exactamente una vez, aunque el agente agrupador se equivoque.
function repararGrupos(grupos, total) {
  const vistos = new Set()
  const limpios = []
  for (const g of grupos || []) {
    const ok = []
    for (const i of g) {
      if (!Number.isInteger(i) || i < 0 || i >= total || vistos.has(i)) continue
      vistos.add(i)
      ok.push(i)
    }
    if (ok.length) limpios.push(ok)
  }
  for (let i = 0; i < total; i++) if (!vistos.has(i)) limpios.push([i])
  return limpios
}

function autotest() {
  const base = { archivo: 'a.py', linea: 3, escenario: 'x', tipo: 'bug', importancia: 'importante', criterio: 'impacto_claro' }
  const casos = [
    ['válido', esValido(base), true],
    ['sin escenario', esValido({ ...base, escenario: ' ' }), false],
    ['sin línea', esValido({ ...base, linea: 0 }), false],
    ['mejora sin beneficio', esValido({ ...base, tipo: 'mejora', beneficio_medible: '' }), false],
    ['mejora con beneficio', esValido({ ...base, tipo: 'mejora', beneficio_medible: '-20% coste' }), true],
    ['sin criterio baja a menor', normalizar({ ...base, criterio: 'ninguno' }).importancia, 'menor'],
    ['con criterio sigue importante', normalizar(base).importancia, 'importante'],
    ['uno refutado', clasificar(false, { veredicto: 'refutado' }), 'descartado'],
    ['dos refutados', clasificar(true, { veredicto: 'refutado' }), 'disputa'],
    ['dudoso', clasificar(false, { veredicto: 'dudoso' }), 'disputa'],
    ['sin veredicto', clasificar(false, null), 'disputa'],
    ['confirmado importante', clasificar(false, { veredicto: 'confirmado', importante: true, criterio: 'impacto_claro' }), 'confirmado'],
    ['real pero menor', clasificar(false, { veredicto: 'confirmado', importante: false, criterio: 'ninguno' }), 'menor'],
    ['área sin hallazgos', estadoArea(0, true), 'satisfactorio'],
    ['sin hallazgos ni cobertura', estadoArea(0, false), 'incompleto'],
    ['área con hallazgos', estadoArea(2, true), 'con_hallazgos'],
    ['para sin confirmados', debeContinuar(0, 1, true), false],
    ['sigue con confirmados', debeContinuar(1, 1, true), true],
    ['para en máximo de rondas', debeContinuar(3, 2, true), false],
    ['para si nadie responde', debeContinuar(1, 1, false), false],
    ['repara grupos', JSON.stringify(repararGrupos([[0, 0, 9], [1]], 3)), '[[0],[1],[2]]'],
  ]
  const fallos = casos.filter(([, real, esperado]) => real !== esperado).map(([n, real]) => `${n}: ${real}`)
  return { total: casos.length, fallos }
}

// ---- Prompts ----------------------------------------------------------------

const promptEvidencia = () => `Recoge hechos del servidor para una auditoría. No opines ni propongas nada: otros agentes interpretarán estos datos.

${ENTORNO}

Recoge:
1. Estado de los servicios y si ejecutan el código actual de /opt/wallapop-sniper o una imagen construida antes (compara Dockerfile, docker-compose.yml y volúmenes).
2. Últimas 24 h de logs por servicio: recuentos de errores, excepciones, 403/429 y fallos de IA, con los mensajes más repetidos.
3. Base de datos: filas por tabla, tamaño, últimas escrituras, deals por estado y coste en ai_cost_log.
4. Tests en tests/. Lee cada uno antes de ejecutarlo. Ejecuta solo los que puedas demostrar que no usan red ni escriben en data/sniper.db ni en ningún archivo persistente. En cualquier otro caso, omítelo e indica el motivo; test_all_stores.py va omitido salvo prueba de lo contrario. Usa el intérprete con las dependencias del contenedor si hace falta y un timeout de 120 s por test.`

const promptAuditor = (area, enfoque, evidencia, yaTratados) => `Eres un auditor sénior de una plataforma Python 24/7 que detecta chollos en mercados de segunda mano. Audita un área real que ya funciona en producción.

ÁREA: ${area.nombre}
ALCANCE: ${area.alcance}
ASPECTOS A VIGILAR (son orientativos, no una lista que debas rellenar): ${area.foco}
TU ENFOQUE: ${enfoque.texto}

QUÉ CUENTA
- Bugs reales y fallos silenciosos.
- Decisiones de negocio erróneas: chollos perdidos, falsos positivos, márgenes mal calculados.
- Fiabilidad 24/7, concurrencia, coste de IA y rendimiento.
- Mejoras internas con beneficio medible y magnitud estimada.
QUÉ NO CUENTA: seguridad, estilo, nombres, longitud de archivos, refactors estéticos, ni riesgos puramente teóricos.

IMPORTANTE solo si cumple uno de estos criterios (${CRITERIOS}):
- pierde_o_inventa_chollos: hace perder chollos reales o avisar de falsos.
- corrompe_datos_o_caida: corrompe datos o provoca caídas.
- rompe_invariante: viola una invariante de la sección 5 de AGENTS.md.
- impacto_claro: efecto estimado y notable, como más de un 10 % de coste de IA o un retraso apreciable en las alertas.
Todo lo demás es menor. Reporta como mucho los menores que de verdad merezcan la pena.

CÓMO TRABAJAR
- Concluir «sin_problemas_relevantes» es un resultado bueno y plenamente válido. No se te valora por el número de hallazgos: un hallazgo inventado o exagerado es peor que ninguno. Cuando hayas comprobado lo esencial y no aparezca nada relevante, para y dilo.
- Cada hallazgo necesita archivo, línea exacta, cita literal y un escenario concreto de fallo. Si no puedes construir el escenario, no lo reportes.
- Antes de reportar, busca si otro punto del código ya lo protege y, cuando sea posible, confírmalo con logs o datos.
- Indica qué comprobaste y qué no pudiste comprobar.
- Trabaja solo con el código y los hechos de abajo. No leas docs/audits/, AUDIT_CONCLUSION.md, PLAN_DEFINITIVO_V6.md ni otros informes o planes: tu valor es tu criterio independiente. AGENTS.md sí puedes leerlo, aunque puede estar desactualizado; manda el código.

${ENTORNO}

HECHOS RECOGIDOS DEL SERVIDOR
${evidencia ? JSON.stringify(evidencia, null, 1) : 'No disponibles: recoge lo que necesites.'}
${yaTratados.length ? `\nYA TRATADOS EN UNA RONDA ANTERIOR (no los repitas; busca en lo que aún no se ha mirado):\n${yaTratados.map(t => `- ${t}`).join('\n')}` : ''}`

const promptAgrupar = (hallazgos) => `Agrupa estos hallazgos cuando describan la misma causa raíz, aunque estén redactados de otra forma. No juzgues si son correctos. Devuelve grupos de índices; cada índice debe aparecer exactamente una vez.

${JSON.stringify(hallazgos.map((h, i) => ({ i, titulo: h.titulo, archivo: h.archivo, linea: h.linea, escenario: h.escenario })), null, 1)}`

const promptEsceptico = (h, area) => `Eres un revisor escéptico e independiente. Alguien afirma que existe el problema siguiente en una plataforma Python en producción. Comprueba por ti mismo, leyendo el código, si es cierto.

ÁREA: ${area.nombre}
AFIRMACIÓN: ${h.titulo}
UBICACIÓN: ${h.archivo}:${h.linea}
CITA: ${h.cita}
ESCENARIO DE FALLO: ${h.escenario}

Busca lo que lo desmentiría: validaciones previas, rutas inalcanzables, llamadores que nunca pasan ese valor, configuración o datos reales que lo descarten.

VEREDICTO
- confirmado: has seguido el código y el escenario puede ocurrir de verdad.
- refutado: tienes una razón concreta, con ubicación, de que no puede ocurrir o de que no provoca ese resultado.
- dudoso: el código y los datos no bastan para afirmarlo ni para negarlo. No fuerces una conclusión.

Decide además si, siendo real, es importante según estos criterios (${CRITERIOS}): perder o inventar chollos; corromper datos o provocar caídas; violar una invariante de la sección 5 de AGENTS.md; o tener un impacto estimado y notable. Si no cumple ninguno, importante=false y criterio=ninguno.

No leas informes de auditoría ni planes anteriores.

${ENTORNO}`

// ---- Orquestación ------------------------------------------------------------

async function auditarArea(area, evidencia) {
  const res = { clave: area.clave, nombre: area.nombre, confirmados: [], disputas: [], menores: [], descartados: [], comprobado: [], no_comprobado: [] }
  const yaTratados = []
  let coberturaCompleta = true
  let ronda = 0

  while (true) {
    ronda++
    const etiqueta = `${area.clave}:r${ronda}`

    // Barrera por área: se necesitan los dos informes para contrastarlos.
    // Cada auditor recibe un prompt propio y no conoce la existencia del otro.
    const informes = await parallel(ENFOQUES.map(enfoque => () =>
      agent(promptAuditor(area, enfoque, evidencia, yaTratados), {
        label: `auditor:${etiqueta}:${enfoque.clave}`, phase: 'Auditar', schema: AUDITORIA_SCHEMA,
        model: MODELO_RAZONAMIENTO, effort: 'high',
      })))

    const validos = informes.map((inf, a) => inf ? { inf, a } : null).filter(Boolean)
    if (validos.length < ENFOQUES.length) {
      coberturaCompleta = false
      log(`${area.nombre}: ronda ${ronda}, respondieron ${validos.length} de ${ENFOQUES.length} auditores`)
    }

    const hallazgos = []
    for (const { inf, a } of validos) {
      res.comprobado.push(...inf.comprobado.map(c => `[${ENFOQUES[a].clave}] ${c}`))
      res.no_comprobado.push(...inf.no_comprobado.map(c => `[${ENFOQUES[a].clave}] ${c}`))
      const aceptables = inf.hallazgos.filter(esValido)
      if (aceptables.length < inf.hallazgos.length) log(`${area.nombre}: ${inf.hallazgos.length - aceptables.length} hallazgos sin ubicación, escenario o beneficio eliminados`)
      hallazgos.push(...aceptables.map(h => ({ ...normalizar(h), auditor: a })))
    }

    const auditoresConHallazgos = new Set(hallazgos.map(h => h.auditor)).size
    let indices = hallazgos.map((_, i) => [i])
    if (auditoresConHallazgos > 1) {
      const r = await agent(promptAgrupar(hallazgos), {
        label: `agrupar:${etiqueta}`, phase: 'Agrupar', schema: GRUPOS_SCHEMA,
        model: MODELO_MECANICO, effort: 'low',
      })
      indices = repararGrupos(r ? r.grupos : null, hallazgos.length)
    }

    const grupos = indices.map(idx => {
      const miembros = idx.map(i => hallazgos[i])
      return {
        principal: miembros.find(m => m.importancia === 'importante') || miembros[0],
        miembros,
        coincide: new Set(miembros.map(m => m.auditor)).size > 1,
      }
    })

    for (const g of grupos) yaTratados.push(`${g.principal.archivo}:${g.principal.linea} - ${g.principal.titulo}`)

    const importantes = grupos.filter(g => g.principal.importancia === 'importante')
    const noImportantes = grupos.filter(g => g.principal.importancia !== 'importante')

    // El escéptico solo recibe la afirmación: nunca el razonamiento, el autor ni si hubo coincidencia.
    const juzgados = await parallel(importantes.map((g, i) => () =>
      agent(promptEsceptico(g.principal, area), {
        label: `esceptico:${etiqueta}:${i + 1}`, phase: 'Refutar', schema: VEREDICTO_SCHEMA,
        model: MODELO_RAZONAMIENTO, effort: 'high',
      }).then(v => ({ g, v, destino: clasificar(g.coincide, v) }))))

    let confirmadosRonda = 0
    juzgados.forEach((j, i) => {
      // parallel() devuelve null si la tarea falló: el hallazgo no se pierde, pasa a disputa.
      const { g, v, destino } = j || { g: importantes[i], v: null, destino: 'disputa' }
      const ficha = {
        ...g.principal, ronda,
        confianza: g.coincide ? 'alta (dos auditores independientes)' : 'media (un auditor y escéptico)',
        argumentos_auditores: g.miembros.map(m => ({ enfoque: ENFOQUES[m.auditor].clave, escenario: m.escenario, evidencia: m.evidencia })),
        esceptico: v || { veredicto: 'sin_veredicto', razon: 'El escéptico no pudo completar la revisión' },
      }
      if (destino === 'confirmado') { res.confirmados.push(ficha); confirmadosRonda++ }
      else if (destino === 'disputa') res.disputas.push(ficha)
      else if (destino === 'menor') res.menores.push(ficha)
      else res.descartados.push({ titulo: ficha.titulo, ubicacion: `${ficha.archivo}:${ficha.linea}`, razon: ficha.esceptico.razon })
    })

    res.menores.push(...noImportantes.map(g => ({ ...g.principal, ronda, verificado: false })))

    log(`${area.nombre}: ronda ${ronda}, ${confirmadosRonda} importantes confirmados`)
    if (!debeContinuar(confirmadosRonda, ronda, validos.length > 0)) {
      if (validos.length === 0) coberturaCompleta = false
      break
    }
  }

  if (res.menores.length > MAX_MENORES_POR_AREA) {
    log(`${area.nombre}: apéndice limitado a ${MAX_MENORES_POR_AREA} de ${res.menores.length} menores`)
    res.menores_omitidos = res.menores.length - MAX_MENORES_POR_AREA
    res.menores = res.menores.slice(0, MAX_MENORES_POR_AREA)
  }

  res.rondas = ronda
  res.cobertura_completa = coberturaCompleta
  res.estado = estadoArea(res.confirmados.length, coberturaCompleta)
  return res
}

// ---- Entrada ------------------------------------------------------------------

const entrada = typeof args === 'string' ? { area: args } : (args || {})
const pedida = (entrada.area || 'all').trim().toLowerCase()
const seleccion = ['all', 'todo', 'todas'].includes(pedida) ? AREAS : AREAS.filter(a => a.clave === pedida)

if (!seleccion.length) {
  return { error: `Área desconocida: ${pedida}`, areas_validas: AREAS.map(a => a.clave) }
}

if (entrada.dryRun) {
  return {
    dryRun: true,
    autotest: autotest(),
    areas: seleccion.map(a => a.clave),
    agentes_fijos_primera_ronda: 1 + seleccion.length * ENFOQUES.length,
    prompts_ok: seleccion.every(a => promptAuditor(a, ENFOQUES[0], null, []).includes(a.alcance)),
  }
}

phase('Evidencia')
const evidencia = await agent(promptEvidencia(), {
  label: 'evidencia', phase: 'Evidencia', schema: EVIDENCIA_SCHEMA,
  model: MODELO_MECANICO, effort: 'medium',
})
if (!evidencia) log('No se pudo recoger la evidencia común; cada auditor recogerá la suya')

const resultados = await pipeline(seleccion, area => auditarArea(area, evidencia))

const areas = seleccion.map((a, i) => resultados[i] || {
  clave: a.clave, nombre: a.nombre, estado: 'incompleto', cobertura_completa: false, rondas: 0,
  confirmados: [], disputas: [], menores: [], descartados: [], comprobado: [], no_comprobado: ['La auditoría del área falló antes de terminar'],
})

return { evidencia, areas }
