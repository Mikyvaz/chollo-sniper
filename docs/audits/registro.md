# Registro de auditorías
Estados: pendiente, aceptado, descartado, resuelto.

| ID | Fecha | Área | Ubicación | Título | Estado | Nota |
|---|---|---|---|---|---|---|
| A-001 | 2026-11-09 | datos | core_engine.py:3752 | La pausa por presupuesto se guarda como rechazo y bloquea duplicados 30 días | resuelto | 2026-11-10: la pausa aplaza el anuncio sin guardarlo ni marcarlo visto; las 131 filas antiguas se ignoran sin borrarlas. Sin desplegar hasta reiniciar contenedores |
| A-002 | 2026-11-09 | datos | core_engine.py:1181 | Al bajar de precio, el anuncio hereda su propio rechazo anterior | resuelto | 2026-11-10: `exclude_item_key` en los tres escáneres. Sin desplegar hasta reiniciar contenedores |
| A-003 | 2026-11-09 | datos | core_engine.py:3832 | La mediana de compras de price_history se usa como referencia de reventa | resuelto | 2026-11-10: la mediana se presenta como referencia de compra; caché de visión v45 y rechazos que citan la mediana ignorados al deduplicar. Desplegado |
| A-004 | 2026-11-09 | datos | fraud_signals.py:1008 | seller_reputation no caduca y veta de por vida a cuentas nuevas | resuelto | 2026-11-10: solo señales graves suman flag, caducan a 30 días y no vetan perfiles con ≥2 valoraciones y ≥4★. Flags antiguos anulados (copia en data/backups). Desplegado |
| A-005 | 2026-11-09 | datos | multi_sniper.py:616 | El limitador compartido frena todas las tiendas al ritmo de la peor | resuelto | 2026-11-10: turno propio por tienda y ventana de errores reiniciada al salir del cooldown. Desplegado |
| A-006 | 2026-11-09 | datos | core_engine.py:4280 | `deals` con INSERT OR REPLACE pierde compras y ventas al re-alertar | resuelto | 2026-11-10: upsert que conserva estado, precios y kit. Era la mejora menor del apéndice. Desplegado |
