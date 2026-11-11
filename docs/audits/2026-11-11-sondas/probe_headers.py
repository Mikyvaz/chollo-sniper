import threading, http.server, socketserver, sys
sys.path.insert(0, "/src")
got = []
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        got.append(list(self.headers.items())); self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self, *a): pass
srv = socketserver.TCPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
from http_client import wallapop_client
from fraud_signals import _get_thread_local_session

wallapop_client.get(f"http://127.0.0.1:{port}/api/v3/search", kind="xhr", headers={"Host": "api.wallapop.com"}, timeout=5)
s = _get_thread_local_session()
s.get(f"http://127.0.0.1:{port}/api/v3/users/x/stats", timeout=5)
s.get(f"http://127.0.0.1:{port}/api/v3/users/x", timeout=5)
for name, h in zip(["fetch_wallapop (search)", "enrich stats (users/x/stats)", "enrich perfil (users/x)"], got):
    keep = {k: v for k, v in h if k.lower().startswith(("sec-fetch", "user-agent", "accept", "sec-ch-ua", "origin", "upgrade", "x-deviceos", "priority"))}
    print(f"== {name}"); [print(f"   {k}: {v[:95]}") for k, v in keep.items()]
    low = {k.lower(): v for k, v in h}
    assert low.get("sec-fetch-mode") == "cors", f"{name}: sec-fetch-mode={low.get('sec-fetch-mode')}"
    assert low.get("sec-fetch-dest") == "empty", f"{name}: sec-fetch-dest={low.get('sec-fetch-dest')}"
    assert low.get("sec-fetch-site") == "same-site", f"{name}: sec-fetch-site={low.get('sec-fetch-site')}"
    assert "sec-fetch-user" not in low, f"{name}: unexpected Sec-Fetch-User"
    assert "upgrade-insecure-requests" not in low, f"{name}: unexpected Upgrade-Insecure-Requests"
    assert "Chrome/152" in low.get("user-agent", ""), f"{name}: UA={low.get('user-agent')}"
    assert 'v="152"' in low.get("sec-ch-ua", ""), f"{name}: sec-ch-ua={low.get('sec-ch-ua')}"
    assert low.get("accept-language", "").startswith("es-ES"), f"{name}: accept-language={low.get('accept-language')}"

uas = [{k.lower(): v for k, v in h}["user-agent"] for h in got]
assert len(set(uas)) == 1, f"User-Agent mismatch across Wallapop calls: {uas}"
srv.shutdown()
srv.server_close()
print("ALL PROBE_HEADERS CHECKS PASSED")
