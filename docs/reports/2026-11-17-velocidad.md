# Velocidad de las alertas — 2026-11-17

Trabajo en el árbol `/opt/wallapop-sniper/.worktrees/velocidad` (base `plan-fase-3` @ `48ef1da`). **Sin commit, sin desplegar y sin llamadas de pago.** Producción sigue con el código del 13/11.

## 1. Dónde se pierde el tiempo (medido en producción, 48-72 h)

| Tramo | Wallapop | Vinted | Origen |
|---|---|---|---|
| Publicación → aparece en el buscador | **4-6 min** (sondeo: el anuncio más nuevo de «iphone» tenía 8,6 min; de «nintendo», 5,8 min) | casi inmediato | Índice de la plataforma: no depende de nosotros |
| Entre dos sondeos de una consulta hot | 32 s (config 20 s) | 73 s (config 60 s) | Cuota saturada |
| Consultas standard de día | **nunca** (10 consultas) | **nunca** (8 consultas) | Prioridad estricta a hot con la cuota saturada |
| Captura → Puerta 1 (ficha/perfil esperando token) | mediana 2,0 s · p90 3,3 s | mediana 3,2 s · p90 15,1 s | Cola de búsquedas |
| Puerta 1 + visión + CeX + envío | ~3 s | ~3 s | IA y Discord |

Por qué Wallapop va a 32 s en vez de 20 s: 17 de sus 31 consultas piden una 2.ª página cuando la 1.ª trae menos de 8 anuncios. Entre las dos páginas suman de mediana 0-9 anuncios, así que la 2.ª casi nunca aporta nada y gasta un token (~1 s de espera) en cada sondeo. Además, la configuración pide 70,5 sondeos por minuto en Wallapop contra un tope de 60, y 24 contra 18 en Vinted.

**Cuánto dura un chollo** (alertas desde el 10/11): en Vinted, el 65 % de las alertas **sonoras** ya no está disponible a los 20 min. Tus calificaciones «llegué tarde» son alertas sonoras compradas por otro en 1-2 min. En Wallapop el margen es mayor: mediana de 3 h.

## 2. Qué cambia

1. **Tokens prioritarios para ficha y perfil** (`antiban.TokenBucket.acquire(priority=True)`, `acquire_item_token`). La ficha de Vinted y el perfil del vendedor de un anuncio recién visto ya no esperan detrás de la cola de búsquedas: toman el token a crédito (deuda ≤ 3) y lo pagan las búsquedas siguientes. **La tasa media hacia la plataforma no cambia.**
2. **Planificador que no deja consultas sin sondear** (`QueryScheduler`):
   - Si la configuración pide más de lo que cabe, todas las cadencias se alargan en la misma proporción: ×1,21 en Wallapop y ×1,37 en Vinted.
   - Entre las consultas vencidas elige la más atrasada en proporción a su propia cadencia.
   - **Reparto adaptativo** (`config.json → scheduling.adaptive`, activado): con la misma cuota, sondea más a menudo las consultas que traen candidatos. El peso es √(candidatos/h) más 6 puntos por alerta enviada, con límites de ×0,5 a ×1,6 sobre la cadencia base, una media exponencial de 3 h y el estado guardado 12 h en `data/scheduler_state.*.json`. Se puede apagar en caliente con `"enabled": false`.
3. **Sin 2.ª página inútil en Wallapop** (`SecondPagePolicy`). Tras 2 segundas páginas sin anuncios nuevos, se deja de pedir durante 40 sondeos y luego se vuelve a comprobar. Si alguna vez trae anuncios, se sigue pidiendo.
4. **Foto en paralelo con la Puerta 1** (`make_image_hash_prefetcher(eager=True)`) en los tres escáneres.
5. **El push al móvil no espera a Discord.** `send_discord_alert` lanza ntfy en un hilo antes de enviar a Discord, sin duplicados. La alerta final de Multi (Cash Converters, eBay, etc.) tiene ahora push ntfy; antes no tenía.
6. **Métricas nuevas** en `query_completed`: `candidates`, `alerts`, `interval_eff_s`, `yield_per_h` y `fresh_age_min_s`/`fresh_age_med_s` (edad del anuncio al verlo por primera vez, que en Wallapop mide el retraso de su índice). Las resume la herramienta nueva `tools/cadence_report.py`.
7. Detalle menor: Vinted ya no resuelve el HTML de 3,7 MB en cada carácter del bucle que extrae la lista de anuncios.

### Añadido después: valoraciones de Vinted y compra asistida

8. **Fallo de las «12 valoraciones» de Vinted, corregido.** En 91 de 143 alertas de Vinted el vendedor salía con exactamente 12 valoraciones, y eran 90 vendedores distintos. El código leía el ancho del icono de estrella (`width:12px`) en vez del contador, que está en la ficha con las comillas escapadas. Ahora se lee el contador real junto con la reputación y la marca de vendedor profesional. Si no se encuentra, se consulta la API. Comprobado con fichas reales: 4 de 4 coinciden con la API. **Al desplegar cambiará el comportamiento en Vinted:** los vendedores con 1-11 valoraciones dejan de pasar por «12», así que alguna alerta que antes sonaba o pasaba el antifraude puede dejar de hacerlo, y habrá alguna consulta más a la API para conocer la antigüedad de esas cuentas.
9. **Compra asistida.** Una compra directa con nota 10 (arbitraje CeX de nivel 1) ahora suena aunque ya se haya llegado al tope de 4 avisos por hora, y su margen mínimo para sonar es 25 € en lugar de 40 €. En ntfy llega con prioridad máxima y el botón «⚡ Comprar ya (X €)», que abre el anuncio. No suenan si el vendedor tiene 0 valoraciones ni si el precio es anómalo. Se desactiva con `notifications.push_top_deals_always: false`.
10. **No hay compra automática.** `tools/top_deals_report.py` mide la precisión de cada nota según tus calificaciones y comprueba si una clase estricta (nota 10 · Vinted · ≤150 € · ≥20 valoraciones reales · sin avisos · no profesional) alcanza ≥95 % de aciertos en ≥30 casos calificados. Hoy: 105 nota 10, solo 5 calificadas, y 94 con valoraciones desconocidas por el fallo anterior → «NO LISTO».

## 3. Resultado esperado (simulación, no medición)

Simulación sin red con la configuración real, las tasas de candidatos observadas y el código de producción frente al nuevo, durante 24 h de día. Mide el retraso desde que el anuncio aparece en el buscador hasta tener la ficha y el perfil:

| | Wallapop actual | Wallapop nuevo | Vinted actual | Vinted nuevo |
|---|---|---|---|---|
| Consultas hot: retraso medio | 16,6 s | **10,6 s** | 45,0 s | **30,4 s** |
| Consultas standard: candidatos/día que no se detectan de día | 14 | **0** (≈53 s) | 54 (21 %) | **0** (≈130 s) |

Intervalo entre sondeos de las consultas más productivas: «playstation 5» en Vinted, de 73 a 45 s; «pokemon» en Wallapop, de 32 a 16 s; «disco duro 2tb» en Wallapop, de 32 a 18 s. Las que no traen nada se alargan como mucho ×1,6.

El retraso de 4-6 min del índice de Wallapop sigue ahí: en Wallapop la mejora es de segundos sobre minutos. En Vinted, donde un chollo dura 1-2 min, 15 s menos sí cuentan.

## 4. Verificación hecha

- `py_compile` de los 8 módulos y `config.json` (17 radares): OK.
- Tests unitarios en contenedor aislado sin red: **311/311 OK** (antes 263). Nuevos: `test_cadence.py` (22), `test_fast_alert_path.py` (12) y `test_vinted_seller_and_top_deals.py` (14). `test_alert_payload.py` se adaptó a que ntfy sale desde `send_discord_alert`.
- Parser de Vinted contra fichas reales: 4 de 4 vendedores coinciden con `/api/v2/users/{id}`.
- `PIPELINE_VERSION = 2026.11.17-v1`, para comparar alertas antes y después.

## 5. Antes de desplegar: el presupuesto de IA lo bloquea todo

`plan-fase-3` incluye el tope **mensual** de 0,10 USD (T1.8; por defecto en el código y, según el informe del 14/11, también en `.env`). Este mes ya van **1,29 USD**: en cuanto se cargue cualquier código de esta rama, la IA se pausa hasta el 1 de diciembre y **no saldrá ninguna alerta**. Al ritmo actual (0,05-0,08 USD/día), mantener la IA todo el mes cuesta ~2-2,5 USD. Hace falta subir `AI_MONTHLY_BUDGET_USD` (por ejemplo a 3) o aceptar la pausa.

Despliegue propuesto, **solo con tu aprobación**: hacer commit en una rama, integrarla en `plan-fase-3` y, desde `/opt/wallapop-sniper`, ejecutar:

```sh
docker compose up -d --build wallapop-sniper vinted-sniper multi-sniper relister-bot outcome-tracker
```

Comprobación a las 2 h: `python3 tools/cadence_report.py --since 2h --by-query`. Línea base de hoy: Wallapop hot 30,6 s y Vinted hot 73,4 s entre sondeos. Vigilar `COOLDOWN` en los logs: no ha habido ninguno desde el 10/11.

## 6. Decisiones tuyas con más efecto que el código

1. **Avisos sonoros.** Solo suenan ~3,8 alertas al día; el resto llega en silencio y la ves tarde.

   | Silenciadas por… | al día | se van en ≤20 min |
   |---|---|---|
   | Vinted, regateo con margen 35-50 € | 8,4 | 46 % |
   | Vinted, vendedor sin valoraciones | 6,6 | 70 % (riesgo de estafa) |
   | Wallapop, regateo con margen 35-50 € | 11,1 | 22 % |
   | Regateo con margen 50-80 € (ambas) | 1,7 | 25-57 % |

   Bajar `notifications.push_offer_min_profit_eur` de 80 a 50 € añade ~1,7 avisos al día. Bajarlo a 35 € añade ~20. El tope de 4 por hora sigue aplicándose.
2. **Cuota de Vinted** (0,30 peticiones/s), que es el cuello de botella. Dos opciones: quitar de Vinted las consultas sin ninguna evaluación IA en 72 h (las 4 de cámaras sin espejo, las 3 compactas y «rog ally»), que son ~1/3 de la demanda de Vinted; o, tras una semana estable, subir a 0,36 (+20 %). El reparto adaptativo ya reduce la frecuencia de esas consultas, pero no las elimina.
3. Pendiente de sesiones anteriores: revisión independiente de T1.8/T2.7/T3.2/T3.3/T3.10, y el análisis del Nivel 1 previsto hacia el 24/11.

`ROADMAP.md` no existe (se eliminó el 14/11), aunque `AGENTS.md` lo sigue enlazando. No lo he recreado.
