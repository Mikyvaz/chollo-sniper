import threading, http.server, socketserver, sys
sys.path.insert(0, "/src")
got = []
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        got.append(list(self.headers.items())); self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self, *a): pass
srv = socketserver.TCPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
from curl_cffi import requests
from antiban import BROWSER_PROFILES
p = BROWSER_PROFILES[0]
hdr = {"Host": "api.wallapop.com", "X-DeviceOS": "0", "User-Agent": p["user_agent"], "Accept": "application/json, text/plain, */*",
       "Accept-Language": "es-ES,es;q=0.9,en;q=0.8", "Origin": "https://es.wallapop.com", "Referer": "https://es.wallapop.com/",
       "sec-ch-ua": p["sec_ch_ua"], "sec-ch-ua-platform": p["sec_ch_ua_platform"], "sec-ch-ua-mobile": p["sec_ch_ua_mobile"]}
requests.get(f"http://127.0.0.1:{port}/api/v3/search", headers=hdr, impersonate=p["impersonate"], timeout=5)
s = requests.Session(impersonate="chrome124")
s.get(f"http://127.0.0.1:{port}/api/v3/users/x/stats", headers={"User-Agent": "Mozilla/5.0", "X-DeviceOS": "0"}, timeout=5)
s.get(f"http://127.0.0.1:{port}/api/v3/users/x", timeout=5)
for name, h in zip(["fetch_wallapop (search)", "enrich stats (users/x/stats)", "enrich perfil (users/x)"], got):
    keep = {k: v for k, v in h if k.lower().startswith(("sec-fetch", "user-agent", "accept", "sec-ch-ua", "origin", "upgrade", "x-deviceos", "priority"))}
    print(f"== {name}"); [print(f"   {k}: {v[:95]}") for k, v in keep.items()]
