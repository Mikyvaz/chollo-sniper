# Estadísticas reales guardadas del funcionamiento de los anuncios

Corte de la base de datos: **13/11/2026, 20:49:10 (Europe/Madrid)**. Consultas ejecutadas en `miky-p6657es`, desde `/opt/wallapop-sniper`. Fuente: instantánea consistente de `data/sniper.db`, incluyendo su WAL, abierta para lectura. Se consultaron también todos los logs conservados, sus rotaciones y la copia de seguridad del 10 de noviembre.

Los recuentos describen lo que se conserva en los registros. Los estados de plataforma son el último estado observado, no una consulta en vivo al abrir este informe. Los costes en USD proceden del cálculo registrado por la aplicación; no se ha contrastado una factura del proveedor.

Archivos: [datos completos y base de datos](../../data/reports/2026-11-13-204910/datos_completos.zip), [resumen estructurado](../../data/reports/2026-11-13-204910/resumen.json), [detalle de todas las alertas](../../data/reports/2026-11-13-204910/alertas_detalle.csv). Los CSV usan UTF-8 y separador `;`.

## 1. Volumen de anuncios y alertas

Se conservan **71.681 anuncios distintos por plataforma e identificador**. Se ha excluido un registro explícito de prueba (`TEST_STORE / test_item_9999`). Las tablas antiguas de Wallapop y Vinted no aportan identificadores adicionales: sus filas ya están en la tabla común. El registro comienza el 27/10/2026 en Wallapop y el 29/10/2026 en Vinted.

| Plataforma | Anuncios guardados | Alertas | Compra directa | Regateo | Alto riesgo | Mediana precio guardado € |
| --- | --- | --- | --- | --- | --- | --- |
| WALLAPOP | 40.486 | 286 | 40 | 243 | 3 | 39,99 |
| VINTED | 29.739 | 328 | 126 | 193 | 9 | 59,00 |
| EBAY | 708 | 13 | 6 | 7 | 0 | 109,00 |
| MILANUNCIOS | 422 | 0 | 0 | 0 | 0 | 60,00 |
| CASHCONVERTERS | 253 | 0 | 0 | 0 | 0 | 104,95 |
| TODOCOLECCION | 73 | 0 | 0 | 0 | 0 | 53,36 |

Son anuncios que el sistema marcó como vistos o reservados; no todos los resultados devueltos por las búsquedas. `seen_at` se actualiza al volver a marcar un anuncio y no acredita su primera aparición. Hay 12 registros de Wallapop con estado interno `reserved`; este estado de deduplicación es independiente del seguimiento de alertas.

Las **627 alertas** abarcan del 31/10/2026 al corte. 590 pertenecen al modo de reventa y 37 al uso personal. Hay 16 marcadas como locales. Estados en la cartera: {'alerted': 622, 'bought': 2, 'discarded': 2, 'sold': 1}. El estado `alerted` se conserva aunque la plataforma venda después el anuncio: esa evolución se guarda en `alert_outcomes`.

| Tipo de alerta | N | Porcentaje | Precio pedido medio € | Margen estimado medio de reventa € |
| --- | --- | --- | --- | --- |
| direct_chollo | 172 | 27,4 % | 134,16 | 92,36 |
| offer_opportunity | 443 | 70,7 % | 262,63 | 86,11 |
| high_risk_opportunity | 12 | 1,9 % | 99,04 | 52,55 |

Los márgenes son estimaciones guardadas. En `offer_opportunity` dependen de conseguir la oferta propuesta; no son beneficios realizados. En las 100 alertas de regateo que conservan el descuento, la mediana propuesta es del 20 % y el intervalo va del 7 % al 34 %. Los campos de precio y beneficio de cada alerta están en el CSV.

## 2. Alertas por día y por radar

| Día en Madrid | Alertas | Registros consumo IA | Coste registrado USD |
| --- | --- | --- | --- |
| 2026-10-31 | 53 | sin registro | sin registro |
| 2026-11-01 | 17 | sin registro | sin registro |
| 2026-11-02 | 97 | sin registro | sin registro |
| 2026-11-03 | 108 | 588 | 0,034110 |
| 2026-11-04 | 73 | 858 | 0,066467 |
| 2026-11-05 | 59 | 1.222 | 0,123957 |
| 2026-11-06 | 20 | 754 | 0,038490 |
| 2026-11-07 | 14 | 844 | 0,251546 |
| 2026-11-08 | 10 | 685 | 0,259881 |
| 2026-11-09 | 24 | 859 | 0,084867 |
| 2026-11-10 | 47 | 546 | 0,057371 |
| 2026-11-11 | 57 | 930 | 0,090746 |
| 2026-11-12 | 20 | 271 | 0,036315 |
| 2026-11-13 | 28 | 293 | 0,039185 |

El 13 de noviembre es un día parcial. No hay registros de coste anteriores al 3 de noviembre, aunque sí existen alertas anteriores.

| Radar | Alertas | Directas | Regateo | Riesgo alto | Precio pedido medio € | Margen estimado medio reventa € |
| --- | --- | --- | --- | --- | --- | --- |
| ps5_consoles_gta6 | 357 | 55 | 299 | 3 | 304,36 | 103,10 |
| new_3ds_family | 49 | 32 | 12 | 5 | 68,98 | 51,80 |
| apple_high_liquidity | 45 | 10 | 35 | 0 | 163,02 | 47,85 |
| hdd_server_storage | 35 | 8 | 26 | 1 | 74,15 | sin dato |
| steam_deck_handhelds | 34 | 12 | 22 | 0 | 280,75 | 95,33 |
| switch_oled | 23 | 9 | 14 | 0 | 108,09 | 62,16 |
| switch_std_lite | 23 | 15 | 8 | 0 | 44,06 | 59,16 |
| pokemon_ds_gba | 19 | 14 | 2 | 3 | 37,44 | 64,52 |
| mirrorless_cameras | 10 | 4 | 6 | 0 | 211,04 | 88,94 |
| simracing_wheels | 8 | 7 | 1 | 0 | 76,88 | 58,80 |
| retro_handhelds | 7 | 0 | 7 | 0 | 58,98 | 43,35 |
| ps_vita | 7 | 2 | 5 | 0 | 70,14 | 37,60 |
| digicams_premium | 3 | 0 | 3 | 0 | 162,10 | 113,64 |
| rtx_gpus | 3 | 0 | 3 | 0 | 192,51 | 41,49 |
| graphing_calculators | 2 | 2 | 0 | 0 | 30,00 | 151,51 |
| ssd_sata_server | 2 | 2 | 0 | 0 | 67,50 | sin dato |

El radar PS5 concentra 56,9 % de las alertas. Las puntuaciones guardadas van de 6 a 10, con mediana 7,00. Solo 40 alertas conservan `pipeline_version=2026.11.11-f1`; las otras 587 no tienen versión guardada, por lo que no permiten una comparación fiable entre despliegues.

## 3. Resultado observado en las plataformas

Hay 627 alertas registradas para seguimiento, 612 con alguna comprobación y 854 comprobaciones guardadas desde el 10/11/2026. Son 453 comprobaciones de Wallapop y 401 de Vinted. 506 alertas se incorporaron tarde al seguimiento; las otras 121 se incorporaron sin la marca de seguimiento tardío.

| Plataforma | Seguidas | Con comprobación | Vendido | Eliminado/404 | Reservado | Disponible | Pendiente |
| --- | --- | --- | --- | --- | --- | --- | --- |
| WALLAPOP | 286 | 284 | 28 | 182 | 36 | 38 | 2 |
| EBAY | 13 | 0 | 0 | 0 | 0 | 0 | 13 |
| VINTED | 328 | 328 | 211 | 87 | 1 | 29 | 0 |
| TOTAL | 627 | 612 | 239 | 269 | 37 | 67 | 15 |

**239 figuran como vendidos y 269 como eliminados. Un 404 no demuestra una venta.** Las 37 reservas son el último estado guardado; 52 anuncios estuvieron reservados en algún momento. `done=1` aparece en 578 seguimientos, bien por estado final o por agotar sus puntos de revisión.

eBay conserva 13 alertas pendientes y ninguna comprobación de anuncio guardada. El rastreador registra respuestas 403 y páginas de verificación con pausas de 120 minutos. Los bloqueos que provocan una pausa no crean una fila en `alert_outcome_checks`: tener cero comprobaciones `unknown` no significa que no hubiera errores de acceso.

Para los anuncios incorporados sin seguimiento tardío hay 52 vendidos, 26 eliminados, 10 reservados, 30 disponibles y 3 pendientes. La tabla siguiente usa únicamente esa cohorte y los casos con evidencia suficiente para cada ventana.

| Ventana desde alerta | Casos evaluables | Observado no disponible dentro de ventana | Proporción entre evaluables |
| --- | --- | --- | --- |
| 60 min | 95 | 45 | 47,37 % |
| 360 min | 98 | 63 | 64,29 % |
| 1440 min | 82 | 68 | 82,93 % |
| 4320 min | 80 | 77 | 96,25 % |

“No disponible” agrupa vendido, eliminado y el estado `unavailable`. Un caso se considera evaluable si se observó no disponible antes del límite, o si se observó disponible después. Las comprobaciones son discretas y hay censura; estas proporciones no estiman por sí solas una tasa de venta de todas las alertas.

| Plataforma | Vendidos sin seguimiento tardío (n) | Primera observación vendido: mediana min | p90 min |
| --- | --- | --- | --- |
| WALLAPOP | 9 | 180,10 | 2.431,28 |
| EBAY | 0 | sin dato | sin dato |
| VINTED | 43 | 15,40 | 324,50 |

Estos tiempos indican cuándo el rastreador vio por primera vez el estado vendido. Su primer punto programado es a los 15 minutos; no se conoce el instante exacto de la operación.

## 4. Retraso entre publicación y alerta

| Plataforma | Alertas con fecha de publicación | Media min | Mediana min | p90 min | Mínimo min | Máximo min |
| --- | --- | --- | --- | --- | --- | --- |
| WALLAPOP | 59 | 52,59 | 4,30 | 170,58 | 1,80 | 574,40 |
| EBAY | 0 | sin dato | sin dato | sin dato | sin dato | sin dato |
| VINTED | 59 | 67,74 | 1,10 | 300,10 | 0,00 | 360,10 |
| TOTAL | 118 | 60,17 | 3,35 | 300,10 | 0,00 | 574,40 |

Solo 118 de las 627 alertas tienen este dato (18,8 %). En Vinted parte de la fecha se calcula a partir de textos de antigüedad relativa y puede estar redondeada. No debe extenderse la mediana de estas 118 observaciones a todas las alertas históricas.

## 5. Demanda y cambios de precio

| Plataforma / contador | Anuncios con dato | Media | Mediana | p90 | Mínimo | Máximo |
| --- | --- | --- | --- | --- | --- | --- |
| WALLAPOP / views | 115 | 160,83 | 68,00 | 511,40 | 2,00 | 1.415,00 |
| WALLAPOP / favorites | 115 | 8,28 | 2,00 | 31,00 | 0,00 | 85,00 |
| WALLAPOP / conversations | 115 | 13,31 | 4,00 | 41,80 | 0,00 | 163,00 |
| VINTED / favorites | 248 | 11,62 | 2,00 | 23,30 | 0,00 | 640,00 |

Son los últimos contadores guardados de cada anuncio. Las visitas y conversaciones solo se han obtenido de Wallapop. En Vinted hay favoritos para 248 anuncios, sin visitas ni conversaciones guardadas. No se registran clics del usuario en la alerta ni impresiones de Discord.

Se guardaron 12 cambios de precio en 10 anuncios de Wallapop. Comparando primer y último precio, 2 bajaron y 8 subieron. Vinted y eBay no tienen precios obtenidos en las comprobaciones: su ausencia de cambios registrados no demuestra estabilidad de precio.

| Anuncio | Precio inicial € | Último precio € | Cambios observados |
| --- | --- | --- | --- |
| Nintendo 3DS Azul | 40,00 | 80,00 | 1 |
| Disco Duro HDD Toshiba 6TB SATA | 70,00 | 500,00 | 1 |
| Nintendo 3DS Negra | 40,00 | 0,00 | 1 |
| Disco Duro 6TB WD Red Pro | 100,00 | 150,00 | 1 |
| Consola PlayStation 5 (PS5) con mando | 325,00 | 375,00 | 1 |
| Nintendo Game Boy Micro con juegos y cargador | 60,00 | 140,00 | 1 |
| Consola PlayStation 5 (PS5) | 375,00 | 400,00 | 1 |
| PlayStation 5 con 2 mandos y juego | 300,00 | 1,00 | 2 |
| Sony PlayStation 5 (PS5) 1TB | 300,00 | 400,00 | 1 |
| PS5 Slim 1T (PlayStation 5) con Lector de Disco | 350,00 | 490,00 | 2 |

## 6. Tus valoraciones manuales

Hay 12 calificaciones, correspondientes a **11 anuncios distintos**. Una alerta se calificó dos veces. Usando la última valoración por anuncio: **6 buenas, 4 dudosas y 1 mala**. Cobertura: 1,8 % de las alertas. Son opiniones sobre una muestra elegida por el usuario, no una medida de precisión de todo el sistema.

| Último veredicto | Anuncios | Porcentaje de los 11 calificados |
| --- | --- | --- |
| bad | 1 | 9,1 % |
| good | 6 | 54,5 % |
| risky | 4 | 36,4 % |

| Motivo estructurado | Anuncios con ese motivo |
| --- | --- |
| seller_new | 1 |
| already_sold | 3 |
| too_slow | 2 |
| good_price | 1 |
| cex_wrong | 1 |

| Anuncio | Último veredicto | Motivos | Nota guardada |
| --- | --- | --- | --- |
| PlayStation 5 Como nueva | risky | seller_new | no aparece fecha de la creación de la cuenta, pero tiene 0 ventas, 0 seguidores, sin foto de perfil, solo la play como chollazo subida, es raro |
| Volante Logitech G923 con Pedales y Shifter | risky |  | No se el valor real al ser varios productos, un volante y la caja de cambios creo o algo así. Si la notificación de discord no diera tantos rodeos y desglosara el valor completo del anuncio y me diese precios (aunque no estuviesen en cex) pero de los componentes por separado y por lo que se venden y tal si que me lanzaría más |
| scuf REFLEX ps5 | risky |  | No se que producto es y en CeX no aparece. De nuevo falta de claridad en la notificación y falta de información del valor real del producto. |
| Sony PlayStation Vita OLED | good | already_sold | Se me pasó pero era buena compra incluso con menos oferta m |
| PlayStation Portal Remote Player | risky |  | No se si es muy fiable esta compra por la reventa que pueda tener. Le he hecho la oferta de todas formas |
| Asus ROG Ally Z1 Extreme | good |  | Llegué tarde pero porque lo vi tarde |
| New Nintendo 3DS XL blanc + 2 jeux Animal Crossing + chargeur | good |  | No me ha dado tiempo |
| nintendo 3ds | good | already_sold,too_slow,good_price | No me ha dado tiempo |
| Nintendo 3DS XL Mario | good |  | No me llegó notificación |
| Pokewalker Nintendo (Pokémon) | bad | cex_wrong |  |
| Titre : Nintendo 3DS XL bleue - lire description | good | already_sold,too_slow |  |

Los motivos pueden coexistir en un mismo anuncio. “No me llegó notificación” aparece como nota del usuario; no acredita por sí sola que fallara el envío. El CSV incluye la decisión sonora guardada para contrastarla.

## 7. Notificaciones y actualización de mensajes

| Decisión sonora guardada | Alertas |
| --- | --- |
| Sonora (push=true) | 13 |
| Silenciosa (push=false) | 104 |
| Sin decisión conservada | 510 |

| Motivo de silencio guardado | Alertas |
| --- | --- |
| alto riesgo / precio anómalo | 3 |
| margen < 40€ | 2 |
| regateo con margen < 80€ | 70 |
| vendedor sin valoraciones | 29 |

Hay ID de mensaje de Discord guardado para 40 alertas. No se puede interpretar su ausencia en las otras 587 como un fallo de envío, porque las alertas antiguas no conservaban ese dato. El seguimiento tiene `discord_state` para 23 alertas: 16 vendido, 4 eliminado y 3 reservado. No hay acuse guardado de recepción en el móvil ni contador fiable de clics o aperturas; `push` guarda la decisión de notificar.

## 8. Descartes antifraude, imágenes y vendedores

`fraud_log` conserva **1.223 descartes**, que afectan a **1.216 anuncios distintos**. Incluye descartes de riesgo moderado combinados con incertidumbre de la IA y descartes por imagen reutilizada. Son decisiones del filtro; no fraudes confirmados.

| Plataforma | Registros de descarte | Anuncios distintos |
| --- | --- | --- |
| VINTED | 355 | 353 |
| WALLAPOP | 853 | 848 |
| EBAY | 5 | 5 |
| MILANUNCIOS | 10 | 10 |

| Riesgo guardado | Registros |
| --- | --- |
| 2 | 512 |
| 3 | 49 |
| 4 | 7 |
| 5 | 655 |

| Señal guardada | Descartes en los que aparece |
| --- | --- |
| SELLER_ZERO_REVIEWS | 955 |
| SELLER_NEW_ACCOUNT_ZERO_REVIEWS | 598 |
| HIGH_TICKET_ZERO_REP_SCAM_RISK | 560 |
| SELLER_CREATED_TODAY | 353 |
| SELLER_GHOST_PROFILE | 310 |
| SELLER_REPORTED | 81 |
| SELLER_LOW_RATING | 51 |
| SELLER_BRAND_NEW | 51 |
| REUSED_IMAGE | 48 |
| PRODUCT_DEFECT | 14 |
| SELLER_KNOWN_REPEAT_OFFENDER | 11 |

Varias señales pueden corresponder al mismo descarte. Se conservan 2.121 huellas de imagen para 2.118 anuncios; 2.029 hashes son distintos y 68 hashes aparecen en más de un anuncio. Una coincidencia perceptual no acredita por sí sola una foto robada. La señal `REUSED_IMAGE` consta en 48 descartes.

Hay **8 anuncios bloqueados muestreados**: 6 de Wallapop y 2 de Vinted. Ninguno había cumplido 24 horas en la fecha de corte; ninguno estaba comprobado. Por tanto, todavía no hay una tasa medida de falsos positivos del antifraude.

| Valoraciones del vendedor en la alerta | Alertas |
| --- | --- |
| 0 confirmadas | 29 |
| con valoraciones | 87 |
| sin dato | 511 |

Los 511 casos sin dato no se cuentan como vendedores con cero valoraciones. La antigüedad está guardada en 67 alertas: 58 con fuente `register_date` y 9 con fuente `verification` (aproximada). Hay 29 alertas cuyo vendedor tenía denuncias registradas.

| Grupo de reputación en alerta | Comprobaciones de cuenta por alerta | Activa | Eliminada | Suspendida |
| --- | --- | --- | --- | --- |
| sin dato | 0 | 0 | 0 | 0 |
| con valoraciones | 58 | 47 | 11 | 0 |
| 0 confirmadas | 18 | 18 | 0 | 0 |

Las cuentas se comprobaron en 76 alertas: 65 activas y 11 eliminadas. “Eliminada” no demuestra estafa; no hay ninguna cuenta con estado `banned` guardado. Son observaciones por alerta y pueden repetirse vendedores. La tabla de reputación tiene 3.795 vendedores, 5.429 observaciones acumuladas y `fraud_flags=0` en todas sus filas. El riesgo medio guardado por vendedor tiene mediana 0 y p90 de 5.

## 9. Consumo y costes de IA registrados

Hay **7.850 registros de consumo**, por **1,082935 USD**. Tokens de entrada: 5.343.135; tokens de salida: 820.003. La salida ya incluye 4.843 tokens de razonamiento: no hay que sumarlos otra vez. La aplicación calcula el coste con su tabla de tarifas y lo guarda en USD, aunque la guía del proyecto mencione euros.

Se identificaron dos llamadas etiquetadas expresamente como pruebas (`test_ipad` y `radar_test`), con coste conjunto de 0,000185 USD. Excluyéndolas: 7.848 registros y 1,082750 USD. El resto de tablas de coste conserva todos los registros para cuadrar el gasto guardado, incluidas esas pruebas.

| Tipo de llamada | Registros | Coste USD | Tokens entrada | Tokens salida |
| --- | --- | --- | --- | --- |
| vision | 2.052 | 0,883618 | 3.762.834 | 619.395 |
| embedding | 1.655 | 0,001621 | 80.006 | 0 |
| prescreen | 4.128 | 0,194016 | 1.486.371 | 195.704 |
| bot_description | 15 | 0,003680 | 13.924 | 4.904 |

| Plataforma / origen | Registros | Coste USD |
| --- | --- | --- |
| VINTED | 4.150 | 0,727629 |
| WALLAPOP | 1.815 | 0,321939 |
| SYSTEM | 1.656 | 0,001646 |
| EBAY | 87 | 0,016458 |
| TODOCOLECCION | 3 | 0,000264 |
| MILANUNCIOS | 122 | 0,011179 |
| CASHCONVERTERS | 2 | 0,000140 |
| DISCORD_BOT | 15 | 0,003680 |

| Modelo guardado | Registros | Coste USD |
| --- | --- | --- |
| gemini-3.5-flash-lite | 3.973 | 0,375280 |
| gemini-3.8-flash | 83 | 0,326912 |
| gemini-3.5-flash | 737 | 0,260723 |
| google/gemini-2.5-flash | 382 | 0,066304 |
| gemini-flash-latest | 106 | 0,026416 |
| gemini-flash-lite-latest | 670 | 0,016744 |
| google/gemini-2.5-flash-lite | 232 | 0,005569 |
| google/gemini-3.5-flash | 7 | 0,002293 |
| models/gemini-embedding-2 | 1.653 | 0,001618 |
| openai/gpt-4o-mini | 5 | 0,001073 |
| models/gemini-embedding-001 | 2 | 0,000003 |

Los nombres de modelo son los guardados históricamente; no se han sustituido por los modelos de la configuración actual. Hay 1.655 registros históricos de embeddings remotos, con 0,001621 USD de coste registrado. Esto puede coexistir con el hashing local actual.

`finish_reason` existe en 408 registros: 407 `STOP` y 1 `MAX_TOKENS`; está ausente en 7.442. No permite calcular una tasa histórica completa de truncamientos o fallos. El desglose por modalidad existe en 408 registros: 142.715 tokens de imagen y 188.843 de texto. No cubre todo el historial.

## 10. Decisiones guardadas en las cachés e histórico recuperable

| Caché actual | Entradas / decisión |
| --- | --- |
| Texto: total | 3.681 |
| Texto: proceed | 964 |
| Texto: reject | 2.671 |
| Texto: unsure | 46 |
| Visión: total | 876 |
| Visión is_valid_deal=False | 514 |
| Visión is_valid_deal=True | 362 |
| Prompts | 2.881 |

En la caché de texto actual hay 2.671 rechazos de 3.681 entradas (72,6 %). En visión hay 514 respuestas inválidas de 876 (58,7 %), 316 respuestas que marcan defectos o piezas faltantes y 10 que marcan vendedor profesional. Confianza visual: 714 alta, 129 media y 33 baja. Son entradas conservadas por clave de caché; no son anuncios únicos ni todos los intentos históricos.

| Caché | Claves actuales | Claves en backup | Solo en backup | Claves distintas recuperables |
| --- | --- | --- | --- | --- |
| ai_prescreen_cache | 3.681 | 3.750 | 1.635 | 5.316 |
| ai_eval_cache | 876 | 1.746 | 1.306 | 2.182 |
| ai_prompt_cache | 2.881 | 873 | 0 | 2.881 |

Uniendo por clave y dando prioridad al valor actual: texto conserva 2.288 `proceed`, 2.973 `reject` y 55 `unsure`; visión conserva 787 válidas y 1.395 inválidas. No se suman directamente las dos copias. Los 2.941 registros que solo existen en el backup se exportaron en `cache_historico_solo_en_backup.csv`. La unión no recupera evaluaciones sobrescritas que no estén en ninguna copia.

Hay 2.016 embeddings: 941 con 64 dimensiones, 1.073 con 768 y 2 con 3.072. La etiqueta de modelo de esa tabla se conserva; no demuestra que cada fila requiriese una llamada remota. Hay 348 perfiles en caché, 179 de Wallapop y 169 de Vinted, todos con estado `ok`. No se guarda un contador completo de aciertos/fallos de caché, por lo que su tasa de acierto no es calculable a partir de estas tablas.

## 11. Precios observados y referencias CeX

`price_history` conserva **943 registros**: 538 observaciones del radar y 405 registros por submodelo. El código puede escribir ambos para una misma evaluación. No son 943 anuncios diferentes ni 943 ventas. Son precios pedidos de anuncios que pasaron la validación de IA, antes de la comprobación final de rentabilidad.

| Radar (solo observaciones base) | N registros | Mínimo € | Media € | Mediana € | p75 € | p90 € | Máximo € |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ps5_consoles_gta6 | 220 | 170,00 | 314,49 | 320,00 | 350,00 | 378,00 | 390,00 |
| hdd_server_storage | 57 | 15,00 | 53,35 | 40,00 | 55,00 | 119,40 | 180,00 |
| switch_std_lite | 53 | 25,00 | 43,68 | 50,00 | 50,00 | 55,00 | 80,00 |
| retro_handhelds | 43 | 20,00 | 55,16 | 60,00 | 69,50 | 70,00 | 70,00 |
| new_3ds_family | 41 | 30,00 | 86,46 | 90,00 | 100,00 | 120,00 | 130,00 |
| apple_high_liquidity | 37 | 100,00 | 187,41 | 185,00 | 200,00 | 235,00 | 300,00 |
| steam_deck_handhelds | 30 | 238,00 | 277,38 | 260,00 | 317,25 | 330,00 | 330,00 |
| pokemon_ds_gba | 22 | 20,00 | 49,97 | 50,00 | 55,00 | 65,00 | 77,50 |
| ps_vita | 16 | 40,00 | 68,19 | 70,00 | 80,00 | 83,00 | 85,00 |
| mirrorless_cameras | 7 | 120,00 | 202,85 | 200,00 | 230,00 | 279,98 | 324,95 |
| switch_oled | 6 | 65,00 | 93,50 | 93,50 | 120,00 | 121,50 | 123,00 |
| simracing_wheels | 3 | 100,00 | 100,00 | 100,00 | 100,00 | 100,00 | 100,00 |
| rtx_gpus | 1 | 200,00 | 200,00 | 200,00 | 200,00 | 200,00 | 200,00 |
| kindle_ereaders | 1 | 59,00 | 59,00 | 59,00 | 59,00 | 59,00 | 59,00 |
| digicams_premium | 1 | 199,00 | 199,00 | 199,00 | 199,00 | 199,00 | 199,00 |

Cada radar puede mezclar submodelos y estados; estas medianas no son valor de reventa acreditado. Los registros por submodelo permanecen en el JSON y en el CSV de observaciones, separados por su clave original.

CeX: 588 productos en el espejo; 550 con precio de compra en efectivo positivo. 506 sincronizados en las últimas 36 horas y 82 más antiguos. Caché puntual: 40 entradas (35 actualizadas en 72 h). Caché de consultas: 39. En 505 alertas se guardó CeX cash positivo; 122 no lo tienen.

| Categoría CeX guardada | Productos |
| --- | --- |
| 3DS Consolas | 52 |
| 3DS Juegos | 12 |
| Accesorios de Camaras | 1 |
| Apple iPad | 69 |
| Camaras Digitales Compactas | 40 |
| DS Juegos | 17 |
| Figuras NFC | 1 |
| GBA Juegos | 5 |
| Game Boy Color Juegos | 3 |
| Game Boy Juegos | 3 |
| Gamecube Juegos | 1 |
| PC Accesorios | 35 |
| PC Gaming Portatil | 19 |
| PC Sobremesa - Apple | 53 |
| PC Sobremesa - Windows | 2 |
| PS Vita Consolas | 16 |
| PS Vita Juegos | 39 |
| PS4 Accesorios | 1 |
| PS5 Consolas | 51 |
| Switch 2 Juegos | 2 |
| Switch Consolas | 60 |
| Switch Juegos | 12 |
| Tarjetas Graficas - PCi-E | 93 |
| Wii Juegos | 1 |

## 12. Cartera y resultado económico realizado

| Producto | Estado registrado | Compra € | Venta € | Comisiones venta € | Resultado realizado € |
| --- | --- | --- | --- | --- | --- |
| Switch Oled Nintendo | bought | 145,74 | sin dato | 0,00 | sin dato |
| Calcolatrice scientifica TI-84 Plus CE-T | bought | 45,99 | sin dato | 0,00 | sin dato |
| Pokémon Edición Platino (Nintendo DS) | sold | 45,49 | 45,00 | 0,00 | -0,49 |

La cartera registra **3 compras históricas**, contando la ya vendida. Quedan 2 productos en stock con coste conjunto de **191,73 €**. La única venta registrada deja **−0,49 €** (45,00 − 45,49 − 0,00). Las fechas `bought_at` y `sold_at` están vacías en estas tres filas, por lo que no se puede calcular el tiempo real de rotación del capital. `deal_events` está vacío.

## 13. Funcionamiento medido del escaneo y de la evaluación

Se leyeron **84.795 eventos estructurados**: 34.381 rondas, 50.114 consultas y 300 resultados por etapa. El archivo antiguo abarca del 03/11 a las 17:31 al 12/11 a las 12:31; los archivos por servicio comienzan el 12/11 hacia las 21:16. Hay un hueco en la telemetría conservada entre ambas series.

Se recuperaron dos eventos eliminando únicamente bytes nulos iniciales durante la lectura: una ronda Multi y un resultado `math_reject` de Wallapop. Los logs originales siguen intactos. No quedaron líneas JSON no interpretables en la lectura final.

| Fichero de métricas | Eventos hasta el corte | Recuperados tras bytes nulos |
| --- | --- | --- |
| data/logs/metrics.jsonl | 32.379 | 0 |
| data/logs/metrics.multi-sniper.jsonl | 378 | 1 |
| data/logs/metrics.vinted-sniper.jsonl | 16.251 | 0 |
| data/logs/metrics.wallapop-sniper.jsonl | 35.787 | 1 |

| Plataforma / histórico conservado | Rondas | Nuevos (contador) | Evaluaciones (contador) | Duración ronda mediana s | p90 s |
| --- | --- | --- | --- | --- | --- |
| WALLAPOP | 24.480 | 28.801 | 692 | 17,80 | 19,50 |
| VINTED | 4.540 | 18.198 | 1.300 | 91,05 | 108,61 |
| MULTI | 5.361 | 1.651 | 104 | 48,30 | 93,90 |

| Plataforma / últimas 24 h conservadas | Rondas | Nuevos (contador) | Evaluaciones (contador) | Duración ronda mediana s | p90 s |
| --- | --- | --- | --- | --- | --- |
| MULTI | 371 | 88 | 5 | 28,20 | 45,70 |
| VINTED | 517 | 1.537 | 69 | 100,20 | 106,94 |
| WALLAPOP | 1.114 | 1.948 | 57 | 48,00 | 52,00 |

Los contadores de rondas y consultas no se suman entre sí: describen procesamiento repetido. La duración de ronda no equivale a la frecuencia de búsqueda de cada producto; hay planificador, intervalos y reinicios. El CSV `consultas_por_radar.csv` conserva la distribución de intervalos observados y duración por radar.

| Plataforma / consultas detalladas | Consultas | Apariciones de anuncios devueltas | Nuevos (contador) | Evaluaciones (contador) | Mediana consulta s | p90 s |
| --- | --- | --- | --- | --- | --- | --- |
| WALLAPOP | 34.569 | 540.375 | 1.948 | 57 | 1,07 | 1,09 |
| VINTED | 15.545 | 1.050.899 | 1.539 | 69 | 0,69 | 0,84 |

La telemetría de consultas detalladas solo existe en la parte reciente. Las apariciones incluyen el mismo anuncio repetidas veces y no se suman a los anuncios únicos guardados.

| Plataforma / logs de texto conservados | Mensajes con contador | Suma de anuncios revisados (con repeticiones) | Suma de nuevos comunicados |
| --- | --- | --- | --- |
| CASHCONVERTERS | 3.998 | 124.251 | 181 |
| MILANUNCIOS | 1.740 | 34.024 | 409 |
| EBAY | 3.271 | 70.046 | 1.038 |
| TODOCOLECCION | 3.235 | 99.321 | 23 |
| WALLAPOP | 315.817 | 10.306.227 | 28.818 |
| VINTED | 55.932 | 8.218.440 | 18.263 |

Estas sumas proceden de los mensajes guardados del 3 al 13 de noviembre. Se presentan como otra fuente de medición y no se añaden a los contadores JSONL.

| Resultado medido del pipeline | Wallapop | Vinted | eBay | Total |
| --- | --- | --- | --- | --- |
| Alerta registrada al terminar pipeline | 22 | 18 | 0 | 40 |
| Descartado por rentabilidad | 8 | 10 | 2 | 20 |
| Descartado por texto | 46 | 112 | 2 | 160 |
| Duplicado semántico descartado | 1 | 5 | 0 | 6 |
| Descartado por visión | 27 | 42 | 3 | 72 |
| Descartado por imagen reutilizada | 0 | 2 | 0 | 2 |

Los 300 resultados `item_pipeline` no incluyen todos los filtros locales ni todos los bloqueos antifraude. Sus 40 resultados `alerted` corresponden al intervalo con esta instrumentación; no sustituyen las 627 alertas históricas. No hay un embudo completo con todos los descartes y anuncios únicos trazados de extremo a extremo.

| Plataforma | Etapa medida | Muestras >0 | Mediana ms | p90 ms | Máximo ms |
| --- | --- | --- | --- | --- | --- |
| WALLAPOP | Captura → inicio texto (incluye enriquecimiento/esperas) | 104 | 1.969,15 | 12.687,37 | 43.015,90 |
| WALLAPOP | Evaluación de texto | 104 | 743,90 | 953,81 | 1.335,70 |
| WALLAPOP | Evaluación visual | 57 | 1.480,70 | 2.126,72 | 15.177,40 |
| WALLAPOP | Consulta CeX | 23 | 67,50 | 119,66 | 164,30 |
| WALLAPOP | Envío de alerta | 22 | 351,80 | 603,23 | 775,50 |
| VINTED | Captura → inicio texto (incluye enriquecimiento/esperas) | 189 | 3.214,80 | 29.373,88 | 85.108,10 |
| VINTED | Evaluación de texto | 189 | 692,00 | 989,58 | 5.798,20 |
| VINTED | Evaluación visual | 69 | 1.405,40 | 1.874,44 | 7.321,50 |
| VINTED | Consulta CeX | 26 | 110,45 | 181,55 | 263,90 |
| VINTED | Envío de alerta | 18 | 370,35 | 1.155,39 | 4.122,10 |
| EBAY | Captura → inicio texto (incluye enriquecimiento/esperas) | 7 | 9.065,10 | 14.452,20 | 15.803,10 |
| EBAY | Evaluación de texto | 7 | 578,40 | 1.213,24 | 1.587,10 |
| EBAY | Evaluación visual | 5 | 1.497,50 | 1.712,94 | 1.841,30 |

Se muestran solo duraciones positivas: los ceros no distinguen una etapa omitida de una etapa sin medición. No se suman percentiles de etapas con muestras diferentes. Antes de iniciar el texto, el p90 medido es aproximadamente 13,1 s en Wallapop y 29,4 s en Vinted; el dato incluye esperas y enriquecimiento, no mide únicamente una cola de trabajo.

## 14. Estado de los servicios y errores conservados

| Servicio | Estado | Salud | Arranque UTC | CPU puntual | Memoria puntual |
| --- | --- | --- | --- | --- | --- |
| wallapop-sniper | running | healthy | 2026-11-13T10:36:14.661442595Z | 0.53% | 53.23MiB / 15.57GiB |
| vinted-sniper | running | healthy | 2026-11-13T10:36:14.379803283Z | 0.00% | 68.5MiB / 15.57GiB |
| multi-sniper | running | healthy | 2026-11-13T10:36:14.677240548Z | 0.00% | 213.3MiB / 15.57GiB |
| relister-bot | running | healthy | 2026-11-13T10:36:35.357323266Z | 0.00% | 55.8MiB / 15.57GiB |
| outcome-tracker | running | healthy | 2026-11-13T10:36:14.592247255Z | 0.00% | 32.78MiB / 15.57GiB |

Los cinco servicios estaban `running` y `healthy` al consultarlos. Los recursos son una observación puntual posterior al corte de datos, no medias históricas. No se reiniciaron ni reconstruyeron contenedores para preparar este informe.

| Texto localizado en logs conservados | Líneas coincidentes |
| --- | --- |
| warning_label | 126 |
| http_403 | 89 |
| budget | 967 |
| http_429 | 26 |
| error_label | 4 |
| timeout | 55 |

Son coincidencias textuales reproducibles con las expresiones guardadas en el JSON: no equivalen a incidentes únicos ni al número total de peticiones fallidas. Una misma incidencia puede generar varias líneas; un número puede aparecer en otro contexto. Los bloqueos del rastreador de eBay constan además en `outcome-tracker-ultimos-300.log`.

## 15. Inventario, alcance histórico y comprobaciones

| Tabla en la instantánea | Filas | Campo de fecha | Primera fecha UTC | Última fecha UTC |
| --- | --- | --- | --- | --- |
| ai_cost_log | 7.850 | created_at | 2026-11-03 14:40:03 | 2026-11-13 19:46:55 |
| ai_eval_cache | 876 | evaluated_at | 2026-11-06 16:44:47 | 2026-11-13 19:46:55 |
| ai_prescreen_cache | 3.681 | evaluated_at | 2026-11-06 16:39:23 | 2026-11-13 19:46:54 |
| ai_prompt_cache | 2.881 | created_at | 2026-11-09 00:08:44 | 2026-11-13 19:46:55 |
| alert_feedback | 12 | created_at | 2026-11-10 15:18:04 | 2026-11-13 17:10:58 |
| alert_outcome_checks | 854 | checked_at | 2026-11-10 15:14:41 | 2026-11-13 19:46:16 |
| alert_outcomes | 627 | alert_at | 2026-10-31 01:45:51 | 2026-11-13 19:46:55 |
| cex_cache | 40 | updated_at | 2026-11-10 17:11:07 | 2026-11-12 10:56:53 |
| cex_catalog_mirror | 588 | last_synced | 2026-11-08 03:04:03 | 2026-11-13 10:36:33 |
| cex_query_hits | 39 | fetched_at | 2026-11-12 20:17:12 | 2026-11-13 19:29:51 |
| deal_events | 0 | created_at | sin dato | sin dato |
| deals | 627 | created_at | 2026-10-31 01:45:51 | 2026-11-13 19:46:55 |
| fraud_log | 1.223 | created_at | 2026-11-05 19:35:39 | 2026-11-13 19:47:55 |
| fraud_samples | 8 | created_at | 2026-11-12 20:17:55 | 2026-11-13 15:16:44 |
| item_embeddings | 2.016 | created_at | 2026-11-03 16:31:09 | 2026-11-13 19:46:55 |
| market_seen_items | 71.682 | seen_at | 2026-10-27 09:08:27 | 2026-11-13 19:48:36 |
| price_history | 943 | recorded_at | 2026-11-05 19:24:03 | 2026-11-13 19:46:55 |
| seen_image_hashes | 2.121 | seen_at | 2026-11-03 14:55:42 | 2026-11-13 19:46:54 |
| seen_items | 12.321 | seen_at | 2026-10-27 09:08:27 | 2026-11-03 19:05:41 |
| seller_profile_cache | 348 | fetched_at | 2026-11-12 20:16:16 | 2026-11-13 19:47:55 |
| seller_reputation | 3.795 | last_seen | 2026-11-05 19:25:37 | 2026-11-13 19:47:55 |
| vinted_seen_items | 12.224 | seen_at | 2026-10-29 11:27:10 | 2026-11-03 19:07:13 |

Las filas físicas incluyen el registro TEST_STORE. Según el mantenimiento implementado, vistos se purgan a los 45 días, cachés de IA a los 7, costes y descartes a los 60, imágenes/embeddings a los 30 y precios a los 90. Por ello, los datos conservados no acreditan toda la historia de ejecución del proyecto. El backup amplía algunas cachés, pero no recupera información que no estuviera guardada.

Se contrastó `data/backups/sniper_pre_cartera_20261110.db`: conserva 476 alertas, 5.828 costes, 788 descartes y 479 registros de precios. Todos esos identificadores existen también en la base actual; sumarlos duplicaría datos. Las diferencias recuperables de caché se incluyeron por separado.

Validaciones ejecutadas: `PRAGMA quick_check` sobre la instantánea → `ok`; los totales por plataforma cuadran; la suma de comprobaciones por alerta coincide con las 854 filas de comprobación; exportaciones leídas y archivo ZIP comprobado. La sintaxis Python del proyecto y `config.json` también se verificaron: 17 radares configurados.

Se actualizó `AGENTS.md` para comprobar sistema operativo, hostname y ruta canónica antes de decidir ejecución directa en el servidor o SSH desde el portátil. `ROADMAP.md` estaba ausente y marcado como eliminado; no se recreó.

Datos que no están guardados de forma suficiente: clics o aperturas de las alertas, confirmación de recepción móvil, aceptación de ofertas, precio final de ventas de terceros, todas las decisiones de filtros locales, tasa real de acierto de caché, falsos positivos antifraude ya comprobados y tiempo exacto de venta.

SHA-256 de la instantánea SQLite: `3e1b6218829e1b22b060fbb0507fc85dbe32ea07a0d5073c755dcb12bd4e49be`. Copia permanente incluida en la carpeta de datos y en el ZIP.
