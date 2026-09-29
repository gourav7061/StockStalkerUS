"""Turn cached Fiscal.ai responses into the UI page (deterministic, no API calls).

Usage:
    python tools/build_ui.py NASDAQ_AVGO

Reads the cache written by fiscal_client.py (.tmp/fiscal/), reshapes it into display-ready tables
(last 10 annual periods, oldest -> newest) and injects it into tools/ui_template.dc.html.
Output: .tmp/ui/Main.dc.html  (publish it as project/Main.dc.html on the design canvas).
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from fiscal_client import fetch  # noqa: E402  (cache-first; only calls the API on a cache miss)

N_PERIODS = 10
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
DASH = "—"


def col_label(report_date):
    y, m, _ = (int(x) for x in report_date.split("-"))
    return f"{MONTHS[m - 1]} '{str(y)[2:]}"


def pick_periods(payload):
    annual = [p for p in payload["data"] if p.get("periodType") == "Annual"]
    annual.sort(key=lambda p: p["reportDate"])
    return annual[-N_PERIODS:]


def fmt_number(v, is_currency, per_share=False):
    if v is None:
        return DASH
    if per_share:
        return f"${v:,.2f}"
    if is_currency:
        return f"{v / 1e9:,.1f}"
    if abs(v) >= 1e6:
        return f"{v / 1e6:,.0f}M"
    return f"{v:,.2f}"


def fmt_ratio_value(v, m):
    if v is None:
        return DASH
    f = m.get("metricFormat")
    if f == "%":
        return f"{v * 100:,.1f}%"
    if f == "ratio":
        return f"${v:,.2f}" if m.get("isCurrency") else f"{v:,.1f}x"
    if f == "number":
        if m.get("isCurrency"):
            return f"${v / 1e9:,.1f}B"
        return f"{v / 1e6:,.0f}M" if abs(v) >= 1e6 else f"{v:,.2f}"
    return f"{v:,.2f}"


def statement_tab(kind, company_key, cols):
    payload = fetch(kind, company_key)
    periods = pick_periods(payload)
    by_date = {p["reportDate"]: p["metricsValues"] for p in periods}
    rows = []
    for m in payload["metrics"]:
        raw = [by_date.get(d, {}).get(m["standardizedMetricId"], {}).get("value") for d in cols["dates"]]
        if all(v is None for v in raw):
            continue
        per_share = m["metricFormat"] == "ratio"
        rows.append({
            "kind": "row", "label": m["metricName"], "bold": bool(m.get("isTotal")),
            "cells": [fmt_number(v, m.get("isCurrency"), per_share) for v in raw],
        })
    return rows


def ratios_tab(company_key, cols):
    payload = fetch("ratios", company_key)
    by_date = {p["reportDate"]: p["metricValues"] for p in payload["data"] if p.get("periodType") == "Annual"}
    groups = {}
    for m in payload["metrics"]:
        raw = [by_date.get(d, {}).get(m["ratioId"]) for d in cols["dates"]]
        if all(v is None for v in raw):
            continue
        groups.setdefault(m.get("category") or "Other", []).append({
            "kind": "row", "label": m["metricName"], "bold": False,
            "cells": [fmt_ratio_value(v, m) for v in raw],
        })
    return flatten_groups(groups)


def key_metrics_tab(company_key, cols):
    rows_by_group = {}
    seg = fetch("segments-and-kpis", company_key)
    seg_by_date = {p["reportDate"]: p["metricsValues"] for p in seg["data"] if p.get("periodType") == "Annual"}
    names = {m["metricId"]: m for m in seg["metrics"]}
    for g in seg.get("segmentGroups", []):
        rows = []
        for gm in g["metrics"]:
            m = names.get(gm["metricId"], {})
            raw = [seg_by_date.get(d, {}).get(gm["metricId"]) for d in cols["dates"]]
            if all(v is None for v in raw):
                continue
            rows.append({"kind": "row", "label": gm["metricName"], "bold": False,
                         "cells": [fmt_number(v, m.get("isCurrency", True)) for v in raw]})
        if rows:
            rows_by_group[g["title"]] = rows
    adj = fetch("adjusted-metrics", company_key)
    adj_by_date = {p["reportDate"]: p["metricValues"] for p in adj["data"] if p.get("periodType") == "Annual"}
    rows = []
    for m in adj["metrics"]:
        raw = [(adj_by_date.get(d, {}).get(m["metricId"]) or {}).get("value") for d in cols["dates"]]
        if all(v is None for v in raw):
            continue
        rows.append({"kind": "row", "label": m["metricName"], "bold": False,
                     "cells": [fmt_number(v, m.get("isCurrency"), m.get("isPerShare")) for v in raw]})
    if rows:
        rows_by_group["Adjusted Metrics"] = rows
    return flatten_groups(rows_by_group)


def flatten_groups(groups):
    flat = []
    for i, (title, rows) in enumerate(groups.items()):
        gid = f"g{i}"
        flat.append({"kind": "group", "label": title, "gid": gid, "open": i == 0})
        for r in rows:
            r["gid"] = gid
            flat.append(r)
    return flat


def price_info(company_key):
    """Latest close and day change from a short date window (1 call, cached 15 min)."""
    end = date.today()
    start = end - timedelta(days=10)
    try:
        payload = fetch("stock-prices", company_key, ttl=900,
                        startDate=start.isoformat(), endDate=end.isoformat())
    except SystemExit:
        return None
    prices = sorted(payload.get("prices", []), key=lambda p: p["date"])
    if not prices:
        return None
    last = prices[-1]
    info = {"price": last["closePrice"], "date": last["date"], "currency": payload.get("tradingCurrency", "USD")}
    if len(prices) > 1 and prices[-2].get("closePrice"):
        prev = prices[-2]["closePrice"]
        info["change"] = last["closePrice"] - prev
        info["changePct"] = (last["closePrice"] - prev) / prev * 100
    return info


def build_data(company_key):
    ref = fetch("income-statement", company_key)
    periods = pick_periods(ref)
    cols = {"dates": [p["reportDate"] for p in periods]}
    cols["labels"] = [col_label(d) for d in cols["dates"]]

    profile = fetch("profile", company_key)
    exchange, _, ticker = company_key.partition("_")
    return {
        "company": {
            "key": company_key,
            "name": profile.get("tradeNameEnglish") or profile.get("displayNameEnglish") or ticker,
            "ticker": ticker, "exchange": exchange,
            "country": profile.get("headquartersCountryCode") or "",
        },
        "quote": price_info(company_key),
        "asOf": date.today().isoformat(),
        "cols": cols["labels"],
        "tabs": {
            "income": {"title": "Income Statement", "unit": "USD · Billions",
                       "rows": statement_tab("income-statement", company_key, cols)},
            "balance": {"title": "Balance Sheet", "unit": "USD · Billions",
                        "rows": statement_tab("balance-sheet", company_key, cols)},
            "cashflow": {"title": "Cash Flow Statement", "unit": "USD · Billions",
                         "rows": statement_tab("cash-flow-statement", company_key, cols)},
            "ratios": {"title": "Ratios", "unit": "",
                       "rows": ratios_tab(company_key, cols)},
            "keymetrics": {"title": "Key Metrics", "unit": "USD · Billions",
                           "rows": key_metrics_tab(company_key, cols)},
        },
    }


def main(company_key):
    data = build_data(company_key)
    template = (ROOT / "tools" / "ui_template.dc.html").read_text(encoding="utf-8")
    out = template.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False))
    out_dir = ROOT / ".tmp" / "ui"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "Main.dc.html").write_text(out, encoding="utf-8")
    counts = {k: len(v["rows"]) for k, v in data["tabs"].items()}
    print(f"Wrote {out_dir / 'Main.dc.html'}  columns={data['cols']}  rows={counts}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "NASDAQ_AVGO")
