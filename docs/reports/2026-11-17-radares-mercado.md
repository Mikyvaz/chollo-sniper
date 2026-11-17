# Revisión de radares y mercado — 2026-11-17

Datos: alertas, costes y embudo desde el 01/11 (BD de producción, solo lectura) y una sonda del 17/11. En Wallapop: 1 página de novedades por producto, precios pedidos tras filtrar accesorios. En CeX España: Algolia, efectivo de la peor a la mejor ficha. Los precios pedidos no son precios de venta.

## Cambios aplicados (A-053)

| Radar | Antes | Ahora | Motivo |
|---|---|---|---|
| Switch estándar/Lite | standard, V2 ≤62 € | **hot**, V2 ≤70 € | Hasta hoy solo se buscaba de noche y aun así: 12 alertas, 58 € de margen. ~390 anuncios/día; CeX V2 83 € |
| 3DS XL | ≤76 € | ≤95 € | CeX paga 151–170 €; pedidos p50 90 €. Mejor radar (79 % de alertas vendidas en <1 h) |
| Switch OLED | ≤98 € | ≤130 € | Pedidos p10 187 €, p50 200 €: el tope solo cazaba anzuelos |
| Logitech G29 | ≤65 € (radar ≤82 €) | ≤85 € | Pedidos p10 120 €; CeX 68 €; reventa ~140 € |
| Apple | sin búsqueda de iPad Air (tope 198 €) | «ipad air» en las 3 plataformas, tope 220 € | CeX ~268 € por un iPad Air 5 64 GB |
| Compactas, sin espejo, Pokémon | hot | standard | 3 + 8 alertas en noviembre; 0 evaluaciones en Vinted en 72 h; Pokémon: réplicas y 4.000 anuncios en 3 días |
| Kindle | activo | **desactivado** | 0 alertas en noviembre; pedidos por encima del tope; CeX paga poco |
| **Nintendo Switch 2** | — | nuevo, hot, ≤300 € | ~240 anuncios/día; pedidos p10 366 €, p50 400 €; CeX 288–308 €. Ticket alto: antifraude estricto en la guía de la IA |
| **PSP** | — | nuevo, standard, ≤50 € (E1000 ≤25 €) | ~50 anuncios/día; pedidos p25 50 €, p50 80 €; CeX 3000 ~70–83 €, E1000 37 € |
| **Meta Quest 3/3S** | — | nuevo, standard, ≤230 € (3S ≤130 €) | ~37 anuncios/día; CeX Quest 3 512 GB 257–306 €, 3S hasta 181 € |

Sin cambios: PS5 (GTA 6 sale el 19/11; buen momento para vender), Steam Deck, Vita, retro portátiles, RTX y discos duros (los decide el usuario).

## Código

- **Fallo corregido:** al editar `config.json` (también desde el bot), Wallapop y Vinted recargaban los radares sin ampliar `max_price` ×1,30, y dejaban de buscar la zona de regateo hasta reiniciar. `QueryScheduler` recibe `config_loader=load_config`.
- CeX: Switch 2 y Switch 1 ya no se cruzan en ningún sentido. Antes, una búsqueda de Switch 1 podía casar con una ficha de Switch 2, mucho más cara. Las PSP se emparejan por modelo, sin cruzar la E1000 con la 3000. En Quest solo cuentan las gafas (no correas ni baterías) y la 3 no se cruza con la 3S.
- Familias `switch2`, `psp` y `meta_quest` en la referencia CeX previa a la IA, y consultas maestras nuevas para el espejo de CeX.

## Candidatos descartados por ahora

| Producto | Motivo |
|---|---|
| Xbox Series X | ~450 € de inversión; margen solo por debajo de ~420 € |
| DJI Mini 3/4 Pro | Buen margen, pero riesgo de drones robados |
| N64 / Game Boy Color / GameCube | Poco margen frente a CeX en las versiones estándar |
| AirPods Pro 2 | A 70 € son mayoritariamente falsificaciones |
| PS5 Pro, MacBook Air M1, X100V, ROG Ally X, Legion Go, Osmo Pocket 3, GoPro, Sony XM5 | Precio pedido ≥ CeX, o demasiado caros para la cartera actual |

## Verificación

- Suite unitaria aislada: 329/329 OK (13 tests nuevos en `test_radar_market_update.py`).
- Reproducción de las 723 alertas de los últimos 17 días (`tools/replay_filters.py`): mismo resultado que el código en producción (689 siguen pasando). Los cambios no excluyen ninguna alerta real.
