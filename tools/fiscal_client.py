"""Fiscal.ai API client for the Financial tab (deterministic fetch + local cache).

Usage:
    python tools/fiscal_client.py NASDAQ_AVGO income-statement --period annual
    python tools/fiscal_client.py NASDAQ_AVGO ratios --refresh

Statements: income-statement | balance-sheet | cash-flow-statement | ratios | adjusted-metrics
            | segments-and-kpis | profile | stock-prices
Output: JSON on stdout; raw responses cached in .tmp/fiscal/ (disposable).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".tmp" / "fiscal"
BASE_URL = "https://api.fiscal.ai"
CACHE_TTL_SECONDS = 6 * 3600

ENDPOINTS = {
    "income-statement": "/v1/company/financials/income-statement/standardized",
    "balance-sheet": "/v1/company/financials/balance-sheet/standardized",
    "cash-flow-statement": "/v1/company/financials/cash-flow-statement/standardized",
    "ratios": "/v1/company/ratios",
    "adjusted-metrics": "/v1/company/adjusted-metrics",
    "segments-and-kpis": "/v2/company/segments-and-kpis",
    "profile": "/v3/company/profile",
    "stock-prices": "/v3/company/stock-prices",
    "companies-list": "/v3/companies-list",
}


# Free plan (docs.fiscal.ai/docs/guides/free-trial, /rate-limits): no key-level endpoint restriction is
# documented; the limit is 100 companies, 50 req/min and 250 req/day. Only the financial-data kinds are
# enabled here, and calls are counted locally so the daily quota is never exceeded.
FREE_KINDS = {
    "income-statement", "balance-sheet", "cash-flow-statement",
    "ratios", "adjusted-metrics", "segments-and-kpis", "profile", "stock-prices", "companies-list",
}
DAILY_CALL_LIMIT = 240  # 10 below the free plan's 250/day
MIN_SECONDS_BETWEEN_CALLS = 1.3  # stays under 50 req/min


def _count_call():
    """Increment today's persistent call counter; abort if the free quota is used up."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    counter = CACHE_DIR / "_calls.json"
    today = time.strftime("%Y-%m-%d")
    state = json.loads(counter.read_text()) if counter.exists() else {}
    if state.get("date") != today:
        state = {"date": today, "count": 0}
    if state["count"] >= DAILY_CALL_LIMIT:
        raise SystemExit(f"Local daily cap of {DAILY_CALL_LIMIT} Fiscal.ai calls reached. Try tomorrow or use cache.")
    state["count"] += 1
    counter.write_text(json.dumps(state))
    time.sleep(MIN_SECONDS_BETWEEN_CALLS)


def load_env():
    """Read .env into os.environ (no external dependency)."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def fetch(kind, company_key, period_type=None, currency=None, refresh=False, ttl=CACHE_TTL_SECONDS, **extra):
    if kind not in FREE_KINDS:
        raise SystemExit(
            f"'{kind}' is not on the confirmed free-tier list (FREE_KINDS). Refusing to call the API."
        )
    load_env()
    api_key = os.environ.get("FISCAL_API_KEY")
    if not api_key:
        raise SystemExit("FISCAL_API_KEY is missing. Add it to .env.")
    if kind not in ENDPOINTS:
        raise SystemExit(f"Unknown kind '{kind}'. Choose from: {', '.join(ENDPOINTS)}")

    params = {"companyKey": company_key} if company_key else {}
    if period_type:
        params["periodType"] = period_type
    if currency:
        params["currency"] = currency
    params.update({k: v for k, v in extra.items() if v is not None})

    cache_name = f"{kind}__{'_'.join(f'{k}-{v}' for k, v in sorted(params.items()))}.json"
    cache_file = CACHE_DIR / cache_name
    if not refresh and cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
        return json.loads(cache_file.read_text(encoding="utf-8"))

    url = f"{BASE_URL}{ENDPOINTS[kind]}?{urllib.parse.urlencode({**params, 'apiKey': api_key})}"
    last_error = None
    for attempt in range(3):
        _count_call()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "curl/8.5.0"}), timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data), encoding="utf-8")
            return data
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:500]
            if e.code in (429, 500, 502, 503, 504):
                last_error = f"HTTP {e.code}: {body}"
                time.sleep(2 ** attempt)
                continue
            if e.code in (403, 404) and "cloudflare" not in body.lower():
                body += " (free plan only covers 100 companies; this one may not be included)"
            raise SystemExit(f"HTTP {e.code} from Fiscal.ai: {body}")
        except urllib.error.URLError as e:
            last_error = str(e)
            time.sleep(2 ** attempt)
    raise SystemExit(f"Failed after retries: {last_error}")


LOGO_DIR = ROOT / ".tmp" / "logos"


def fetch_logo(company_key, variant="icon"):
    """Company logo image bytes from /v2/company/logo (free plan; cached on disk, 1 call per new logo)."""
    if variant not in ("icon", "logo", "logo-dark", "tile"):
        raise SystemExit(f"Unknown logo variant '{variant}'")
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    meta = LOGO_DIR / f"{company_key}__{variant}.json"
    img = LOGO_DIR / f"{company_key}__{variant}.bin"
    if img.exists() and meta.exists():
        return img.read_bytes(), json.loads(meta.read_text())["content_type"]
    load_env()
    api_key = os.environ.get("FISCAL_API_KEY")
    if not api_key:
        raise SystemExit("FISCAL_API_KEY is missing. Add it to .env.")
    _count_call()
    url = f"{BASE_URL}/v2/company/logo?" + urllib.parse.urlencode(
        {"companyKey": company_key, "variant": variant, "apiKey": api_key})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "curl/8.5.0"}), timeout=30) as resp:
            body, ctype = resp.read(), resp.headers.get("Content-Type", "image/png")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code} fetching logo for {company_key}")
    img.write_bytes(body)
    meta.write_text(json.dumps({"content_type": ctype}))
    return body, ctype


def main():
    p = argparse.ArgumentParser(description="Fetch data from the Fiscal.ai API")
    p.add_argument("company_key", help="e.g. NASDAQ_AVGO")
    p.add_argument("kind", choices=ENDPOINTS.keys())
    p.add_argument("--period", dest="period_type", help="periodType value (see workflow for accepted values)")
    p.add_argument("--currency", default=None)
    p.add_argument("--refresh", action="store_true", help="bypass the local cache")
    args = p.parse_args()
    data = fetch(args.kind, args.company_key, args.period_type, args.currency, args.refresh)
    json.dump(data, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
