"""Local web app for the US Stock research UI (stdlib only, localhost only).

Run:   python tools/app.py            then open http://127.0.0.1:8000
API:   GET /api/companies             free-plan, US-listed companies (autocomplete list)
       GET /api/company/<companyKey>  all Financial-tab data + live quote for one company
       GET /api/logo/<companyKey>     company logo image (cached on disk)

Only companies on the Fiscal.ai free-plan list are served, so no call can hit a paid company.
Loading one uncached company costs about 8 API calls (see workflows/fetch_financials.md).
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from fiscal_client import fetch, fetch_logo  # noqa: E402
from build_ui import build_data  # noqa: E402

US_EXCHANGES = {"NASDAQ", "NYSE"}
WEB_DIR = ROOT / "tools" / "web"
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
    def _send(self, status, body, content_type="application/json"):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                return self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html")
            if path == "/api/companies":
                return self._send(200, free_us_companies())
            if path.startswith("/api/company/"):
                key = path[len("/api/company/"):]
                allowed = {c["key"] for c in free_us_companies()}
                if not KEY_RE.match(key) or key not in allowed:
                    return self._send(404, {"error": f"{key} is not on the free-plan company list."})
                return self._send(200, build_data(key))
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
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Serving on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
