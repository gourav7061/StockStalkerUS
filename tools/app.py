"""Local web app for the US Stock research UI (stdlib only; localhost unless hosted with PORT + APP_PASSWORD).

Run:   python tools/app.py            then open http://127.0.0.1:8000
API:   GET /api/companies             free-plan, US-listed companies (autocomplete list)
       GET /api/company/<companyKey>  all Financial-tab data + live quote for one company
       GET /api/logo/<companyKey>     company logo image (cached on disk)

Only companies on the Fiscal.ai free-plan list are served, so no call can hit a paid company.
Loading one uncached company costs about 8 API calls (see workflows/fetch_financials.md).
"""
import base64
import hmac
import json
import os
import re
import sys
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from fiscal_client import fetch, fetch_logo  # noqa: E402
from build_ui import build_data  # noqa: E402

US_EXCHANGES = {"NASDAQ", "NYSE"}
WEB_DIR = ROOT / "tools" / "web"
# Public-hosting guards (all optional; unset = local dev behaviour)
APP_PASSWORD = os.environ.get("APP_PASSWORD")  # if set, every request needs HTTP Basic auth (any username)
VISITOR_DAILY_COMPANIES = int(os.environ.get("VISITOR_DAILY_COMPANIES", "5"))  # new companies per visitor per day
_seen = defaultdict(set)  # (ip, day) -> company keys already loaded by that visitor

KEY_RE = re.compile(r"^[A-Z0-9]+_[A-Z0-9.\-]+$")


def free_us_companies():
    rows = fetch("companies-list", None, compact="true", ttl=24 * 3600)["data"]
    out = []
    for c in rows:
        listing = c.get("primaryListing") or {}
        if listing.get("exchangeCode") in US_EXCHANGES:
            out.append({
                "key": c["companyKey"], "name": c.get("displayNameEnglish"), "ticker": listing.get("ticker"),
                "exchange": listing.get("exchangeCode"), "sector": c.get("sector"),
            })
    return sorted(out, key=lambda x: x["ticker"] or "")


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type="application/json", no_cache=False):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if no_cache:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self):
        if not APP_PASSWORD:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                supplied = base64.b64decode(header[6:]).decode("utf-8").partition(":")[2]
                return hmac.compare_digest(supplied.encode(), APP_PASSWORD.encode())
            except Exception:  # noqa: BLE001
                return False
        return False

    def _visitor(self):
        fwd = self.headers.get("X-Forwarded-For", "")
        return (fwd.split(",")[0].strip() or self.client_address[0], time.strftime("%Y-%m-%d"))

    def do_GET(self):
        path = self.path.split("?")[0]
        if not self._authorized():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Stock Stalker"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            if path in ("/", "/index.html"):
                return self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html", no_cache=True)
            if path == "/api/companies":
                return self._send(200, free_us_companies())
            if path.startswith("/api/company/"):
                key = path[len("/api/company/"):]
                allowed = {c["key"] for c in free_us_companies()}
                if not KEY_RE.match(key) or key not in allowed:
                    return self._send(404, {"error": f"{key} is not on the free-plan company list."})
                visitor = self._visitor()
                if key not in _seen[visitor] and len(_seen[visitor]) >= VISITOR_DAILY_COMPANIES:
                    return self._send(429, {"error": f"Daily limit reached: {VISITOR_DAILY_COMPANIES} new companies per visitor. Try again tomorrow."})
                data = build_data(key)
                _seen[visitor].add(key)
                return self._send(200, data)
            if path.startswith("/api/logo/"):
                key = path[len("/api/logo/"):]
                if not KEY_RE.match(key) or key not in {c["key"] for c in free_us_companies()}:
                    return self._send(404, {"error": "not on the free-plan company list."})
                body, ctype = fetch_logo(key)
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(body)
                return
            return self._send(404, {"error": "not found"})
        except SystemExit as e:  # fiscal_client reports failures via SystemExit(message)
            return self._send(502, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s\n" % (fmt % args))


if __name__ == "__main__":
    hosted = "PORT" in os.environ  # hosts like Render set PORT; bind publicly only then
    port = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8000))
    if hosted and not APP_PASSWORD:
        raise SystemExit("Refusing to serve publicly without APP_PASSWORD set.")
    host = "0.0.0.0" if hosted else "127.0.0.1"
    print(f"Serving on http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
