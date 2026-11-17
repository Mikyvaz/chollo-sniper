# Conclusión de Auditoría Adversarial

- **Fecha:** 2026-11-12
- **Estado del Proyecto:** `ACTION_REQUIRED`
- **Resumen Ejecutivo:** La arquitectura cubre captura, filtrado determinista, evaluación multimodal, rentabilidad, alertas y seguimiento. La revisión confirma **13 fallos P1** por trazado estático y demostración matemática; **ningún P0 demostrado**. Afectan a reputación, cachés, identidad de productos, cotizaciones, márgenes, cobertura de búsquedas y entrega de avisos. Las correcciones propuestas son acotadas y preservan la infraestructura actual. Este informe no implementa ni despliega cambios.

**Alcance y validación.** Tres auditores independientes revisaron rendimiento/concurrencia, fiabilidad/seguridad y coherencia arquitectónica; después se realizaron exactamente dos rondas de contraste escéptico. Se leyeron AGENTS.md, ROADMAP.md, despliegue, configuración, módulos principales, pruebas y registros anteriores. El alias SSH `servidor` no resuelve en este entorno; usando la IP documentada, el servidor rechaza la autenticación. Por tanto, no se ejecutaron Python, Docker, tests, scrapers ni consultas a la BD viva. Los escenarios siguientes son reproducibles a partir del código, pero **no se han ejecutado en esta sesión**. Las frecuencias y latencias actuales de producción no están verificadas.

**Lectura funcional.** Hay 17 radares, 15 habilitados: uno de compra personal de HDD y 14 de reventa. Cinco servicios Docker separan Wallapop, Vinted, mercados secundarios, bot y seguimiento; comparten SQLite y configuración. Wallapop consulta búsqueda API; Vinted combina catálogo/ficha HTML y perfil; Milanuncios, Cash Converters, eBay y TodoColección dependen de estructuras web específicas. El pipeline aplica filtros, reputación, pre-gate, IA de texto, deduplicación, fotos, IA multimodal, cotización y matemáticas antes del aviso. Discord permite registrar compras/ventas, calificar oportunidades y preparar la reventa. No se encontró una compra automática en plataforma.

**Valor comercial de los datos.** ROADMAP registra 506 alertas, tres compras y una venta entre el 31 de octubre y el 10 de noviembre: son cifras históricas, no recalculadas aquí. `price_history` contiene precios pedidos de anuncios que la IA considera válidos, no importes de ventas cerradas; el código ya advierte de ello en el prompt (`core_engine.py:4390-4399`). Una desaparición tampoco acredita una venta ni revela su precio final. La reventa toma la estimación positiva de IA como `optimal_price`, sin cota externa obligatoria (`4684-4688`), y aplica una fricción fija del 8 %. Esto exige calibración con compras/ventas verificadas y feedback antes de interpretar el beneficio estimado como rentabilidad realizada. No se propone entrenar un predictor con la muestra comercial actual.

**Documentación y despliegue.** El resumen de ROADMAP del 11 de noviembre quedó por detrás del código: `docs/audits/registro.md`, hasta A-040, documenta cambios posteriores de fases 2 y 3. Esas entradas declaran que no están desplegados; la auditoría no pudo comprobarlo. Los 212 tests citados como correctos en A-040 son una verificación anterior, no un resultado de esta sesión.

## 1. Invariantes Respetadas (Extraídas de AGENTS.md)

- Ejecutar runtimes y validaciones por SSH desde `/opt/wallapop-sniper`; no ejecutar el proyecto localmente ni usar tests sobre contenedores/BD de producción.
- Mantener Gemini REST sin `thinkingConfig`, flash-lite para el flujo general y flash únicamente para lote complejo y ticket >=150 € con el interruptor habilitado, conservando OpenRouter.
- Mantener el fail-open de Puerta 1 sin cachear la respuesta de emergencia. El gateway actual detecta truncado y conserva fallback; no se reabre el fallo histórico ya corregido.
- Usar `get_db()`, WAL y `busy_timeout=30000`, con conexiones por hilo; mantener locks de limitadores, parser, sesiones y cachés.
- Conservar ratings Wallapop normalizados y la distinción entre perfil desconocido (`None`) y cero valoraciones confirmadas. El error específico Vinted se documenta en A12-01.
- Mantener embeddings locales deterministas de **64 dimensiones**, sin API externa; A12-04 corrige su función de mezcla, no esta decisión arquitectónica.
- Respetar tasas, backoff, flash desactivado y política de avisos. No reiniciar servicios ni modificar presupuestos, credenciales o límites de compra durante esta auditoría.

Fortalezas comprobadas: caché CeX de candidatos por consulta con selección contextual y frescura; caché negativa sin persistir errores de red; sesiones HTTP por hilo; mantenimiento periódico; métricas separadas por servicio; dependencias fijadas; feedback con contexto y cartera con eventos. El arnés `tools/run_isolated.sh` usa red desactivada, código de solo lectura, copia consistente de SQLite y ausencia de secretos. Son bases útiles que deben conservarse.

## 2. Plan de Ejecución Paso a Paso (Solo P0 y P1 Supervivientes)

Todos los puntos siguientes son **P1 pendientes**. Los comandos son criterios para el agente que implemente las regresiones indicadas, no pruebas ya superadas. A12-03 depende del versionado de A12-02; A12-11 debe conservar la semántica de entrega/push de A12-10. Los demás pueden abordarse de forma independiente mediante diffs mínimos.

### [A12-01] Normalizar una sola vez la reputación Vinted — Severidad: P1

- **Archivo y Líneas:** `vinted_sniper.py:423-429`; `fraud_signals.py:674-675,881-885,931-954`.
- **Detectado por:** Auditor 2, validado por Juez Escéptico.
- **Problema Real (Evidencia):** Para un vendedor con 20 valoraciones y `feedback_reputation=0.2`, el adaptador convierte a 1/5. El enriquecimiento vuelve a multiplicar ratings <=1 por cinco: el resultado es 5/5. Se pierde la señal de puntuación baja y puede asignarse un distintivo de confianza. Sucede tanto con datos SSR como con la consulta de perfil.
- **Veredicto del Debate Adversarial:** Error determinista en una señal antifraude, independiente de los fallos de red y distinto del caso ya resuelto de valoraciones desconocidas.
- **Instrucción Exacta para el Agente Ejecutor:**
  1. Convertir la escala por procedencia del campo; no inferirla exclusivamente a partir de que un número sea <=1.
  2. Conservar ratings ya normalizados, `None` desconocido y la conversión Wallapop.
- **Criterio de Verificación (Test de Aceptación):** Añadir en `test_seller_unknown.py` casos SSR/API con 0.19, 0.2 y 0.98 y valoraciones confirmadas: obtener 0.95, 1.0 y 4.9, con señal de baja reputación en los dos primeros. Ejecutar:
  
      ssh servidor "cd /opt/wallapop-sniper && tools/run_isolated.sh -m unittest discover -s tests/unit -p test_seller_unknown.py -v"

### [A12-02] Conservar negaciones y relaciones en claves de caché — Severidad: P1

- **Archivo y Líneas:** `core_engine.py:1142-1167,3872-3880,4297-4305`.
- **Detectado por:** Auditor 2, validado por Juez Escéptico.
- **Problema Real (Evidencia):** La normalización elimina `no`, `sin`, `ni` y `con`. Con igual radar, título y precio, «No incluye consola. Incluye dos mandos.» y «Incluye consola. Incluye dos mandos.» generan la misma clave. Una decisión legítima sobre el primer producto puede transferirse al segundo antes de consultar IA. «Con caja» y «sin caja» también colisionan.
- **Veredicto del Debate Adversarial:** No es un problema de estilo de normalización: se pierde información que decide si el producto existe o está completo.
- **Instrucción Exacta para el Agente Ejecutor:**
  1. Conservar las negaciones y relaciones relevantes para disponibilidad, estado y piezas incluidas.
  2. Versionar las claves de prescreen y visión; coordinar un namespace final con A12-03 para no reutilizar entradas antiguas contaminadas. No purgar datos financieros.
- **Criterio de Verificación (Test de Aceptación):** En `test_ai_gateway.py`, comprobar claves distintas y que un rechazo precargado no se hereda en el ejemplo positivo. Ejecutar:
  
      ssh servidor "cd /opt/wallapop-sniper && tools/run_isolated.sh -m unittest discover -s tests/unit -p test_ai_gateway.py -v"

### [A12-03] Hacer efectivo el reintento visual con más fotos — Severidad: P1

- **Archivo y Líneas:** `core_engine.py:4292-4305,4322-4332,4453-4460`; `sniper.py:723-733`; `vinted_sniper.py:974-986`; `multi_sniper.py:572-584`.
- **Detectado por:** Auditor 1 y orquestador, validado por Juez Escéptico.
- **Problema Real (Evidencia):** La primera evaluación con una foto y confianza baja se cachea. El retry descarga fotos adicionales, pero conserva la firma de la primera y devuelve la evaluación anterior antes del gateway. Además, lite con precio <60 € y sin lote mantiene un límite de una imagen aunque la llamada sea `vision_retry`: cambiar solamente la caché no soluciona ese caso.
- **Veredicto del Debate Adversarial:** El mecanismo de recuperación no inspecciona la evidencia adicional. Es distinto de la optimización histórica descartada de evitar redescargar la primera foto.
- **Instrucción Exacta para el Agente Ejecutor:**
  1. Firmar el conjunto ordenado de imágenes efectivamente enviado y distinguir el retry cuando cambie la inspección.
  2. Permitir evidencia ampliada en el retry normal, hasta el máximo previsto de tres fotos. Conservar una foto en `DEGRADED` y omitir reintentos que no añadan evidencia.
  3. Coordinar el versionado con A12-02.
- **Criterio de Verificación (Test de Aceptación):** En `test_vision_minimal.py`, gateway simulado y SQLite temporal: [foto1] devuelve baja confianza; [foto1,foto2] alcanza realmente el gateway y cambia el veredicto; una repetición idéntica usa caché. Capturar `images_b64` también con precio <60 € y en modo degradado. Ejecutar:
  
      ssh servidor "cd /opt/wallapop-sniper && tools/run_isolated.sh -m unittest discover -s tests/unit -p test_vision_minimal.py -v"

### [A12-04] Evitar colisiones estructurales del hashing local — Severidad: P1

- **Archivo y Líneas:** `core_engine.py:1279-1301,1407-1414,1625-1630`; flujo de visión `sniper.py:677-718`.
- **Detectado por:** Observaciones ciegas convergentes de Auditores 1 y 2, validadas matemáticamente por Juez Escéptico.
- **Problema Real (Evidencia):** Cada dimensión usa solo `crc32(... ) & 1`. Cambiar el último byte mediante XOR 1 cambia el CRC en `0x77073096`, con bit inferior cero. Los tokens `m4` y `m5`, del mismo peso y sin trigramas, aportan exactamente lo mismo. «Sony RX100 M4» y «Sony RX100 M5», con igual descripción, tienen el mismo vector. Si coinciden vendedor y rango de precio, se reutilizan identificación y `cex_search_query` del modelo anterior sin inspeccionar las fotos del nuevo.
- **Veredicto del Debate Adversarial:** Colisión exacta demostrable, no una queja genérica sobre la calidad semántica de 64 dimensiones. La ejecución remota de la regresión sigue pendiente.
- **Instrucción Exacta para el Agente Ejecutor:**
  1. Mantener hashing determinista local de 64 dimensiones, usando una mezcla adecuada con `hashlib` ya disponible.
  2. Versionar/aislar o regenerar los vectores anteriores antes de compararlos con los nuevos.
  3. Verificar que modelos incompatibles no evitan visión por este bypass; añadir una guarda acotada si la regresión lo requiere.
- **Criterio de Verificación (Test de Aceptación):** Añadir a `test_semantic_dedup_seller.py` los embeddings reales M4/M5, sin vectores artificiales, y el caso de un vendedor con dos generaciones a precios próximos. Deben distinguirse y evitar la herencia del modelo incorrecto. Ejecutar:
  
      ssh servidor "cd /opt/wallapop-sniper && tools/run_isolated.sh -m unittest discover -s tests/unit -p test_semantic_dedup_seller.py -v"

### [A12-05] No deduplicar productos distintos solo por título y precio — Severidad: P1

- **Archivo y Líneas:** `core_engine.py:1639-1655`; `sniper.py:521-523`; `vinted_sniper.py:774-776`; `multi_sniper.py:414-416`.
- **Detectado por:** Auditor 2, validado por Juez Escéptico.
- **Problema Real (Evidencia):** Durante 48 h, un título normalizado idéntico y una diferencia <=10 € bastan para descartar. Después de una Switch OLED a 95 €, otro ID y vendedor con el mismo título a 90 € se marcan vistos sin evaluar. Este filtro actúa antes de la deduplicación semántica que sí exige evidencia de identidad.
- **Veredicto del Debate Adversarial:** Títulos genéricos y precios próximos son entradas normales de un mercado; no demuestran que sea el mismo producto.
- **Instrucción Exacta para el Agente Ejecutor:**
  1. Exigir identidad de anuncio o evidencia suficiente de republicación del mismo vendedor/producto.
  2. Ante título/precio únicamente, continuar. Conservar la deduplicación por ID.
  3. Revisar la expectativa antigua de `tests/test_fase5.py:205-233` que valida el descarte insuficiente.
- **Criterio de Verificación (Test de Aceptación):** En `test_semantic_dedup_seller.py`, dos IDs/vendedores distintos con igual título/precio continúan; el mismo ID sigue siendo omitido por `market_seen_items`. Ejecutar el comando de A12-04.

### [A12-06] Exigir la generación correcta al seleccionar una ficha CeX — Severidad: P1

- **Archivo y Líneas:** `core_engine.py:2615-2623,2888-2910,3020-3033,3089-3092`; `config.json:646`.
- **Detectado por:** Auditor 2, validado por Juez Escéptico.
- **Problema Real (Evidencia):** Una consulta «Sony RX100 M4» pierde `M4` en la búsqueda SQL por su longitud. Si el espejo contiene solo «Sony RX100 M5», los guardas de cámara no la excluyen: solo manejan II/III. El scorer elige un candidato válido sin exigir todos los discriminantes, y el acierto de espejo impide buscar M4 en Algolia. El importe de otra generación llega al cálculo de rentabilidad.
- **Veredicto del Debate Adversarial:** La guía del radar exige distinguir esas generaciones. Tener una cotización de otra cámara es peor que declarar ausencia de referencia.
- **Instrucción Exacta para el Agente Ejecutor:**
  1. Conservar discriminantes cortos y validar la generación especificada antes de puntuar, incluidas formas M4–M7 y Mark IV–VII.
  2. Rechazar generaciones incompatibles también en los fallbacks de búsqueda; si falta una ficha válida, continuar por Algolia o devolver ausencia.
- **Criterio de Verificación (Test de Aceptación):** En `test_cex_lookup.py`, espejo fresco solo M5 y consulta M4 separada por espacio: nunca devolver M5; permitir el fallback simulado correcto o `None`. Con ambas fichas, elegir M4. Ejecutar:
  
      ssh servidor "cd /opt/wallapop-sniper && tools/run_isolated.sh -m unittest discover -s tests/unit -p test_cex_lookup.py -v"

### [A12-07] Descontar el cargador al calcular ofertas — Severidad: P1

- **Archivo y Líneas:** `core_engine.py:4666-4668,4746-4761,6239,6259`.
- **Detectado por:** Auditor 2 y orquestador; contrastado con Auditor 1 y Juez Escéptico.
- **Problema Real (Evidencia):** Los 12 € de reposición entran en compra directa y desaparecen en regateo. Ejemplo: anuncio 110 €, coste entregado 118,99 €, reventa 145 €, tope 98 € y mínimo 35 €. Sin cargador, recomienda 89 €, calcula 97,99 € de coste y anuncia 35,41 € de beneficio. Al incluir la reposición ya reconocida por el sistema, quedan **23,41 €**.
- **Veredicto del Debate Adversarial:** La pérdida de coste es algebraica; no depende de si la estimación de reventa es acertada.
- **Instrucción Exacta para el Agente Ejecutor:**
  1. Restar `charger_penalty` al calcular la oferta máxima y al comprobar el beneficio.
  2. Mantener `offer_landed_cost` como compra más envío: la presentación CeX ya añade el cargador; evitar descontarlo dos veces.
  3. Mantener coherentes los campos económicos mostrados para la oferta.
- **Criterio de Verificación (Test de Aceptación):** Añadir a `test_thresholds_coherence.py`: el caso debe ofrecer 77 € con límite de descuento 35 %, o rechazarse con límite 25 %. Comprobar siempre `beneficio = reventa*0.92 - offer_landed_cost - charger_penalty`. Ejecutar:
  
      ssh servidor "cd /opt/wallapop-sniper && tools/run_isolated.sh -m unittest discover -s tests/unit -p test_thresholds_coherence.py -v"

### [A12-08] No descartar una salida P2P viable por efectivo CeX insuficiente — Severidad: P1

- **Archivo y Líneas:** `core_engine.py:4033-4043,4746-4761`; persistencia del rechazo en `sniper.py:614-620`.
- **Detectado por:** Orquestador, contrastado con Auditor 1 y Juez Escéptico.
- **Problema Real (Evidencia):** Con precio 120 €, tope directo 98 €, efectivo CeX 60 € y PVP CeX 180 €, el pre-gate supone compra mínima 78 €, coste 85,39 € y descarta porque 60-85,39 no cubre 24,50 €. El motor final, con reventa P2P 150 € y coste actual 129,49 €, aprobaría oferta de 93 €, coste 102,49 €, beneficio **35,51 €** y rebaja 22,5 %. El título puede no contener ningún marcador de lote.
- **Veredicto del Debate Adversarial:** El efectivo de liquidación no es el techo de una salida P2P. La restricción contradice los niveles 2/3 y elimina candidatos antes de obtener su valoración.
- **Instrucción Exacta para el Agente Ejecutor:**
  1. No declarar imposibilidad de una oportunidad P2P únicamente por cash CeX insuficiente.
  2. Usar una referencia P2P coherente con la decisión final cuando exista; si no hay un techo fiable, permitir el análisis.
  3. No presentar `sell*0.85` como techo universal: tomar el máximo de dos aproximaciones rescata el ejemplo, pero no demuestra resolver el problema general.
  4. Revisar las expectativas de rechazo basadas solo en cash en `test_thresholds_coherence.py`, conservando la precedencia del umbral del radar sobre el global.
- **Criterio de Verificación (Test de Aceptación):** El ejemplo permite llegar al motor y obtiene 93 €/35,51 €. Una referencia exclusivamente CeX insuficiente no basta para descartar P2P. Mantener las pruebas de prioridad de umbrales. Ejecutar el comando de A12-07.

### [A12-09] Aplicar las reglas de juegos según el producto buscado — Severidad: P1

- **Archivo y Líneas:** `core_engine.py:2203-2214,2322-2323,3897-3899,4365`; `config.json:781-843`.
- **Detectado por:** Auditor 2; alcance de prompts validado por orquestador y Juez Escéptico.
- **Problema Real (Evidencia):** «Juego Pokémon Platino Nintendo DS ESP», 45 €, original con caja, se descarta por empezar con «Juego», aunque el radar activo busca precisamente ese producto. La definición efectiva del tuple está en core y oculta el import de `fraud_signals`. Además, las dos puertas IA reciben instrucciones generales de rechazar juegos sueltos, también cuando evalúan el radar Pokémon.
- **Veredicto del Debate Adversarial:** Cambiar únicamente el tuple importado o el primer filtro dejaría rutas contradictorias activas.
- **Instrucción Exacta para el Agente Ejecutor:**
  1. Condicionar al tipo de radar los prefijos de juegos, modificando la definición efectiva usada por `passes_filters`.
  2. Ajustar las instrucciones de texto y visión para aceptar el producto buscado en Pokémon.
  3. Conservar rechazo de cajas vacías, fundas, reproducciones y juegos sueltos en radares de consolas.
- **Criterio de Verificación (Test de Aceptación):** En `test_console_classifier.py`, Pokémon válido supera filtros y sus prompts no ordenan rechazar el juego por ser juego; Mario Kart sigue rechazado en Switch. Incluir negativos de repro/caja vacía. Ejecutar:
  
      ssh servidor "cd /opt/wallapop-sniper && tools/run_isolated.sh -m unittest discover -s tests/unit -p test_console_classifier.py -v"

### [A12-10] Recuperar avisos tras agotar los reintentos de Discord — Severidad: P1

- **Archivo y Líneas:** `core_engine.py:6210-6228,6465-6497`; `sniper.py:762,824-870`; `vinted_sniper.py:1014,1079-1123`; `multi_sniper.py:614,670-702`.
- **Detectado por:** Consenso ciego de Auditores 2 y 3, validado por Juez Escéptico.
- **Problema Real (Evidencia):** Los escáneres marcan visto y registran el deal antes de entregar. Si fallan todos los POST y el webhook, `send_discord_alert` devuelve `False`; sus llamadores lo ignoran y emiten `alerted`. Al recuperarse Discord, la deduplicación impide reenviar. En una alerta silenciosa, ntfy tampoco proporciona un aviso alternativo.
- **Veredicto del Debate Adversarial:** Los reintentos inmediatos no resuelven una interrupción más larga. Persistir un candidato no equivale a entregar su alerta.
- **Instrucción Exacta para el Agente Ejecutor:**
  1. Persistir entrega pendiente/fallida y reintentar de forma acotada con backoff desde SQLite, usando el análisis ya disponible.
  2. Separar entrega confirmada, fallo de transporte y `queued_digest`; conservar la decisión original de push.
  3. Evitar barreras de deduplicación en el reenvío y preservar compras, ventas y kits. No prometer ausencia absoluta de duplicados ante timeouts ambiguos.
- **Criterio de Verificación (Test de Aceptación):** En `test_discord_send.py`, fallo completo y recuperación en una pasada posterior: una fila, una evaluación IA y una entrega confirmada tras recuperar. Digest no se reenvía individualmente y `delivery_failed` no cuenta como `alerted`. Ejecutar:
  
      ssh servidor "cd /opt/wallapop-sniper && tools/run_isolated.sh -m unittest discover -s tests/unit -p test_discord_send.py -v"

### [A12-11] Despachar ntfy final para los mercados secundarios — Severidad: P1

- **Archivo y Líneas:** `multi_sniper.py:33-34,459,670-712`; comparación `sniper.py:862-868` y `vinted_sniper.py:1115-1121`.
- **Detectado por:** Auditor 3, validado por Juez Escéptico.
- **Problema Real (Evidencia):** Multi solo llama a ntfy para flash. Con `flash_mode=off`, los chollos de Cash Converters, Milanuncios, eBay y TodoColección nunca generan el ntfy final, aunque el canal esté configurado y la decisión de push sea positiva.
- **Veredicto del Debate Adversarial:** Omisión sistemática en cuatro fuentes del canal previsto. No se presupone que ntfy esté configurado actualmente.
- **Instrucción Exacta para el Agente Ejecutor:**
  1. Usar el constructor común `build_ntfy_analysis_payload` y el despacho final equivalente a los otros escáneres.
  2. Respetar `_alert_push`, URL/modelo/margen y flash desactivado; no cambiar umbrales.
- **Criterio de Verificación (Test de Aceptación):** Ampliar `test_alert_payload.py` recorriendo el despacho de Multi: candidato aprobado y flash off envía un ntfy final; push falso no lo envía. Ejecutar:
  
      ssh servidor "cd /opt/wallapop-sniper && tools/run_isolated.sh -m unittest discover -s tests/unit -p test_alert_payload.py -v"

### [A12-12] Garantizar cobertura del scheduler bajo carga real — Severidad: P1

- **Archivo y Líneas:** `antiban.py:718-750,883-925`; `config.json:11-22` y consultas de radares habilitados.
- **Detectado por:** Auditor 1, validado por Juez Escéptico.
- **Problema Real (Evidencia):** Wallapop solicita 21 consultas hot/20 s y 10 estándar/80 s: **1,175 consultas/s**, con límite 1. Vinted solicita 22/60 s y 8/240 s: **0,40 consultas/s**, con límite 0,30. Estas sumas excluyen fichas, perfiles y paginación. Las tareas vencidas se ordenan primero por intervalo: una estándar muy atrasada pierde frente a una hot recién vencida. Con reloj virtual, jitter 1 y carga Vinted, las hot pueden ocupar el servicio indefinidamente.
- **Veredicto del Debate Adversarial:** Priorizar hot es deliberado; permitir espera indefinida no garantiza la cobertura declarada. Los intervalos nominales son inviables con la capacidad actual.
- **Instrucción Exacta para el Agente Ejecutor:**
  1. Introducir envejecimiento o prioridad por retraso relativo que garantice servicio a estándar.
  2. Ajustar cadencias a las consultas y peticiones adicionales reales, conservando los límites de tasa y backoff.
  3. No elevar peticiones ni añadir workers para ocultar la sobrecarga.
- **Criterio de Verificación (Test de Aceptación):** En `test_scheduler.py`, simulación de una hora con tareas construidas desde la configuración real, ambos buckets, jitter fijo y semilla: todas las estándar se ejecutan repetidamente con demora acotada y sin superar la tasa. El test previo usa una carga más pequeña. Ejecutar:
  
      ssh servidor "cd /opt/wallapop-sniper && tools/run_isolated.sh -m unittest discover -s tests/unit -p test_scheduler.py -v"

### [A12-13] Evitar que el backoff de una plataforma bloquee las demás — Severidad: P1

- **Archivo y Líneas:** `outcome_tracker.py:370-388,531-555`; patrón correcto existente `400-417`.
- **Detectado por:** Auditor 3, validado por Juez Escéptico.
- **Problema Real (Evidencia):** El seguimiento selecciona 25 pendientes globales y después omite los de plataformas en backoff. Si los 25 primeros son Vinted, un Wallapop posterior nunca entra mientras dure esa retirada. Lo mismo ocurre con cinco vendedores. Los backoffs configurados son de 15 min, 30 min y 120 min; las comprobaciones temporales omitidas no se reconstruyen después.
- **Veredicto del Debate Adversarial:** La retirada debe aislar una plataforma. La muestra de fraude ya contiene la solución correcta, lo que permite un cambio pequeño y coherente.
- **Instrucción Exacta para el Agente Ejecutor:**
  1. Excluir plataformas en backoff en ambos SELECT antes de aplicar LIMIT, parametrizando como `check_pending_fraud_samples`.
  2. Conservar su plazo de retirada, ritmo de peticiones y checkpoints.
- **Criterio de Verificación (Test de Aceptación):** Añadir en `test_fraud_sampling.py` regresiones para las dos colas: 25 Vinted bloqueadas y un Wallapop vencido posterior deben permitir comprobar Wallapop; repetir con cinco vendedores Vinted y uno Wallapop. Ejecutar:
  
      ssh servidor "cd /opt/wallapop-sniper && tools/run_isolated.sh -m unittest discover -s tests/unit -p test_fraud_sampling.py -v"

Tras implementar los cambios, ejecutar la suite unitaria completa en aislado y comparar los scripts históricos con su baseline documentado. Los tests que codifican la conducta económica incorrecta deben actualizarse justificadamente, no usarse para preservar el bug. Ningún reinicio o despliegue forma parte de esta auditoría.

## 3. Registro de Críticas Descartadas (Bloqueo Anti-Bucle)

Se conservan los descartes del informe del 2026-11-09 y se incorporan los de esta revisión. No reabrirlos sin evidencia nueva que cambie su fundamento.

- **[Descartado — multi_sniper.py:301-319]:** Sustituir obligatoriamente `ALL_COMPLETED` por procesamiento inmediato de cada worker. El escenario de espera tras una tienda lenta existe, pero falta frecuencia y contribución actuales al retraso; queda como hipótesis de perfilado P2, fuera del plan.
- **[Descartado — arquitectura general]:** Reescribir a asyncio, dividir módulos por tamaño, introducir patrones abstractos, embeddings externos, Bloom filters, headless para Milanuncios o proxies/VPS sin medición. No se ha demostrado beneficio suficiente para justificar esos cambios.
- **[Descartado histórico — ThreadPoolExecutor]:** Gestión manual de `shutdown(wait=False)` por un supuesto bloqueo indefinido. Las peticiones tienen timeouts; A12-03 no reabre esta propuesta.
- **[Descartado histórico — bot.py]:** Pasar indiscriminadamente todas las consultas SQLite a otro hilo y rehacer sesiones interactivas. No hay evidencia nueva de un cuello de botella general; el bot ya dispone de `db_call` con conexión por trabajo.
- **[Descartado histórico — sesiones HTTP]:** Añadir cierres por preferencia o rehacer la rotación sin una fuga demostrada. Se mantiene la gestión actual por hilo.
- **[Descartado histórico — monitorización de parsers]:** Conectar monitores redundantes a cada fetcher. Multi ya registra éxito/vacío por plataforma.
- **[Descartado histórico — fraud_signals.py]:** Eliminar una segunda consulta de reputación por limpieza o adelantar CeX a Puerta 0. Sin beneficio nuevo demostrado; no altera el plan.
- **[Descartado histórico — logger.py]:** Reabrir la antigua crítica menor sobre locks de escritura. El código actual separa métricas por servicio y protege el handler.
- **[Descartado histórico — detalles P2]:** Evitar la redescarga de foto1, una segunda escritura de embedding, limpiar legado Telegram/tablas o cambiar resolución solo por preferencia. El cortocircuito funcional de A12-03 y la colisión matemática de A12-04 son evidencia distinta.
- **[Descartado — outcome_tracker.py:505-514 / core_engine.py:5911-5916]:** Restaurar en Discord una reserva liberada se mantiene como limitación menor de ciclo de vida, fuera del plan P0/P1.
- **[Descartado — core_engine.py:5341-5378]:** Refinar el digest según estado financiero no justifica ampliar esta intervención. La modalidad actual es `individual`.
- **[Descartado — documentación/canales]:** El desfase de ROADMAP, los tests antiguos de 768 dimensiones y el legado de Telegram no justifican refactorizaciones. Telegram no tiene despacho operativo en los escáneres examinados; se informa del alcance real sin eliminarlo.
- **[No reabrir — arreglos anteriores]:** Los seis hallazgos del informe del 2026-11-09 constaban como aplicados. Se conservan su contexto y pruebas históricas; A12-07 trata el coste del cargador, no deshace la corrección anterior de la fórmula de ofertas. Los IDs A12-* distinguen este plan de aquellos ID-01 a ID-06.
- **[Fuera del plan quirúrgico — calibración comercial]:** La valoración P2P, probabilidad de cierre, coste del tiempo y ROI realizado deben contrastarse con feedback y operaciones verificadas. El histórico disponible no demuestra por sí solo el precio final ni autoriza cambiar los topes de compra o el presupuesto IA.


