# Sonda aislada de comportamiento en runtime (sin red: contenedor --network none + mocks).
import os, sys, json, threading, http.server, socketserver, time
from unittest import mock
sys.path.insert(0, "/src")

# ---------- D) ntfy: cabeceras con emoji contra un servidor local en 127.0.0.1 ----------
received = []
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); self.rfile.read(n)
        received.append(dict(self.headers)); self.send_response(200); self.end_headers()
    def log_message(self, *a): pass
srv = socketserver.TCPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["NTFY_TOPIC_URL"] = f"http://127.0.0.1:{port}/topic"

import core_engine as ce
print("=" * 100)
print("D) send_one_tap_ntfy_alert con título real (emoji en cabecera 'Title')")
ok = ce.send_one_tap_ntfy_alert({"title": "🔥PS5 Slim digital", "price": 280, "platform": "WALLAPOP", "url": "https://es.wallapop.com/item/ps5-123"},
                                {"push": True, "deal_tier": 1, "p2p_net_profit": 60, "cex_cash": 330}, is_flash=False)
print(f"  return={ok} | peticiones recibidas por el servidor local={len(received)}")

# ---------- E) Contaminación de cex_cache por contexto ----------
print("=" * 100)
print("E) fetch_cex_live: la clave de caché ignora el contexto que decide la ficha")
cfg = ce.load_config(); T = {t['id']: t for t in cfg['targets']}
conn = ce.get_db()
q = "PlayStation 5 Slim"
key = f"v43:ps5_consoles_gta6:{q.lower()}:b0:l1"
conn.execute("DELETE FROM cex_cache WHERE search_query = ?", (key,)); conn.commit()
a = ce.fetch_cex_live(conn, q, reference_price=280, target=T["ps5_consoles_gta6"], title_context="PS5 Slim", desc_context="Versión digital sin lector, como nueva")
b = ce.fetch_cex_live(conn, q, reference_price=290, target=T["ps5_consoles_gta6"], title_context="PS5 Slim", desc_context="Con lector de discos, 1TB, como nueva")
conn.execute("DELETE FROM cex_cache WHERE search_query = ?", (key,)); conn.commit()
b_fresh = ce.fetch_cex_live(conn, q, reference_price=290, target=T["ps5_consoles_gta6"], title_context="PS5 Slim", desc_context="Con lector de discos, 1TB, como nueva")
fmt = lambda r: ((r or {}).get("box_name"), (r or {}).get("cash"), (r or {}).get("from_cache"))
print(f"  anuncio A (digital)         -> {fmt(a)}")
print(f"  anuncio B (lector) tras A   -> {fmt(b)}")
print(f"  anuncio B (lector) en limpio-> {fmt(b_fresh)}")

# ---------- F) enrich_seller_profile: perfil NO consultado (red caída) ----------
print("=" * 100)
print("F) enrich_seller_profile Wallapop sin red: ¿'desconocido' se convierte en 0 valoraciones?")
from fraud_signals import enrich_seller_profile, evaluate_seller_risk
item = {"platform": "WALLAPOP", "seller": {"id": "abc123xyz", "name": "pepe", "rating": None, "sales": None}}
t0 = time.time(); prof = enrich_seller_profile(item); dt = time.time() - t0
sr = evaluate_seller_risk(item["seller"], price=280, platform="WALLAPOP")
print(f"  seller_reviews_count={prof.get('seller_reviews_count')} rating={prof.get('seller_rating')} | hits={[h['code'] for h in sr['hits']]} | {dt:.2f}s")
print(f"  decide_alert_push -> {ce.decide_alert_push(conn, 'WALLAPOP', 'direct_chollo', 90, seller=item['seller'])}")

# ---------- G) eBay: parseo de precio >= 1.000 € ----------
print("=" * 100)
print("G) fetch_ebay_es con HTML simulado (precio 1.234,56 EUR)")
html_card = ('<html>' + ' ' * 6000 + '<li data-listingid="123456789012"><img alt="Nvidia RTX 4090 Founders Edition">'
             '<span class="s-card__price">1.234,56 EUR</span><span>+ 9,99 EUR envío</span></li></html>')
fake_resp = mock.MagicMock(status_code=200, text=html_card)
fake_sess = mock.MagicMock(); fake_sess.get.return_value = fake_resp
with mock.patch.object(ce._ebay_mgr, "get_session", return_value=fake_sess):
    res = ce.fetch_ebay_es(None, "rtx 4090", 50, 400)
print(f"  resultado: {[(r['title'], r['price']) for r in res]}  (rango de búsqueda 50-400 €)")

# ---------- H) Wallapop: ante un 429 se lanza una 2ª petición (fallback) en el acto ----------
print("=" * 100)
print("H) fetch_wallapop ante HTTP 429")
import sniper
calls = []
def fake_get(url, **kw):
    calls.append(url.split("?")[0]); return mock.MagicMock(status_code=429, text="", json=lambda: {})
with mock.patch.object(sniper.requests, "get", side_effect=fake_get), \
     mock.patch.object(sniper.health_monitor, "send_health_embed", return_value=None):
    sniper.fetch_wallapop("ps5", 200, 300)
print(f"  peticiones lanzadas con un solo 429: {len(calls)} -> {calls}")

# ---------- I) Discord: un 400 de la API se pierde sin registro ----------
print("=" * 100)
print("I) send_discord_alert cuando Discord responde 400 (bot y webhook)")
ce.BOT_TOKEN, ce.ALERTS_CHANNEL_ID, ce.WEBHOOK_URL = "x", "1", "http://127.0.0.1:9/webhook"
bad = mock.MagicMock(status_code=400, text='{"message": "Invalid Form Body"}')
import io, contextlib
buf = io.StringIO()
with mock.patch.object(ce.HTTP_SESSION, "post", return_value=bad) as p, contextlib.redirect_stdout(buf):
    ce.send_discord_alert(conn, "WALLAPOP", "zz1", "PS5", "desc", 280, 290, 10, T["ps5_consoles_gta6"],
                          {"deal_score": 8, "identified_model": "PS5 Slim", "deal_type": "direct_chollo"}, None,
                          item_url="https://es.wallapop.com/item/zz1", net_profit=60, optimal_price=360)
print(f"  POSTs intentados={p.call_count} | salida registrada={buf.getvalue().strip()!r}")
conn.execute("DELETE FROM deals WHERE deal_id='WAL_zz1'"); conn.commit()
