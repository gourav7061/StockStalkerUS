# Fetch financial data from Fiscal.ai and show it in the UI

## Objective
Let a user search a US-listed company and see its Financial tab (Income Statement, Balance Sheet, Cash Flow Statement, Ratios, Key Metrics) plus a latest price, using ONLY the Fiscal.ai free plan.

## Free-plan rules (no paid usage allowed)
Source: docs.fiscal.ai free-trial and rate-limits guides.
- 99 companies only; 67 are US-listed (NASDAQ/NYSE). Others return 403/404. The app only offers and serves this list (`/v3/companies-list`, cached 24h).
- 50 requests/minute, 250 requests/day. `fiscal_client.py` caps itself at 240/day (counter in `.tmp/fiscal/_calls.json`) and spaces calls ~1.3s.
- No documented per-endpoint restriction on free keys; every endpoint used below worked.

## Inputs
- `FISCAL_API_KEY` in `.env`
- `companyKey` as `EXCHANGE_TICKER` (e.g. `NASDAQ_AVGO`)

## Tools
| Tool | Purpose |
|---|---|
| `tools/fiscal_client.py <companyKey> <kind> [--refresh]` | Cached, rate-limited API client. Kinds: income-statement, balance-sheet, cash-flow-statement, ratios, adjusted-metrics, segments-and-kpis, profile, stock-prices, companies-list |
| `tools/build_ui.py` | `build_data(companyKey)` reshapes cached responses into display tables (last 10 annual periods, oldest to newest) plus the quote |
| `tools/app.py [port]` | Local web server (127.0.0.1 only). `GET /` UI, `GET /api/companies`, `GET /api/company/<companyKey>`, `GET /api/logo/<companyKey>` |
| `tools/web/index.html` | The UI: Stock Stalker branding and sidebar icons, search with autocomplete, header with company logo and price, sub-tabs, collapsible groups on Ratios and Key Metrics. Technical and Strategy Builder are disabled with a "Work in progress" badge |

Run the app: `python tools/app.py` then open http://127.0.0.1:8000.

## Cost per company
An uncached company load = 8 calls: income statement, balance sheet, cash flow, ratios, adjusted metrics, segments/KPIs, profile, price. About 30 new companies per day. Statements/ratios/profile are cached 6h; the price is cached 15 min, so reopening a company costs at most 1 call. Cache lives in `.tmp/fiscal/` (disposable).

## Endpoint to tab mapping
| Tab | kind(s) |
|---|---|
| Income Statement / Balance Sheet / Cash Flow | `income-statement`, `balance-sheet`, `cash-flow-statement` (standardized) |
| Ratios | `ratios`, grouped by each ratio's `category` |
| Key Metrics | `segments-and-kpis` groups + `adjusted-metrics` |
| Header | `profile` (name), `stock-prices` (price) |

## Learned so far
- Logos: `GET /v2/company/logo?companyKey=..&variant=icon` returns image bytes (works on the free plan). `fetch_logo` caches them forever in `.tmp/logos/`, so each company's logo costs 1 call once.
- Cloudflare blocks Python's default User-Agent (HTTP 403, error 1010). The client sends `User-Agent: curl/8.5.0`.
- Statement responses: `{reportingTemplate, metrics[], data[]}`; `data[].metricsValues[<standardizedMetricId>].value` in raw dollars. Ratios use `data[].metricValues[<ratioId>]`; the `metricFormat` (`%`, `ratio`, `number`) says how to display. Segments use `data[].metricsValues[<metricId>]`.
- Default calls return `Annual` periods only (up to ~20 years); only Annual has been used. Quarterly/TTM is intentionally out of scope for now (accepted `periodType` values still unknown).
- `stock-prices` with `startDate`/`endDate` returns `{ticker, ..., prices:[{date, openPrice, closePrice, volume}]}`. The tool requests a 10-day window and derives change from the last two closes. The free feed's last close can differ from other sites' closes (AVGO returned 356.00 with a very small volume), so treat it as indicative.
- Financial companies (e.g. JPMorgan) have a different set of statement rows (fewer/other lines), and row counts differ per company; the UI renders whatever rows exist and drops rows that are empty in every column.
- Row counts vary per company, e.g. ratios 142-202, key metrics 24-48.

## Verified
Loaded via the local API: Broadcom, Microsoft, JPMorgan, Apple: all five tabs returned rows plus a price. A non-free company (NYSE_XOM) is rejected with 404 without using an API call.

## Deployment
GitHub only stores the code; it cannot run this Python server. Hosting uses `render.yaml` (Render free web service, connect the GitHub repo). Required env vars on the host: `FISCAL_API_KEY`, `APP_PASSWORD` (shared password, HTTP Basic auth, any username). Optional: `VISITOR_DAILY_COMPANIES` (default 5 new companies per visitor per day). When `PORT` is set (hosted) the server binds publicly and refuses to start without `APP_PASSWORD`. The local 240-calls/day cap still protects the free quota, but the cache is lost on host restarts (free instances sleep), so expect re-fetching. `.env` and `.tmp/` are gitignored and must never be committed.

## Open items
- Visual check of every tab in a browser (only the JSON API was verified so far).
- Quarterly and TTM views (deferred by choice).
- Technical and Strategy Builder tabs (disabled placeholders).
- Row-level formatting (e.g. growth or margin percentage rows under statements) is not added; Fiscal.ai supplies them in Ratios.
- The design-canvas wireframe (`tools/ui_template.dc.html` / `build_ui.py main`) is now superseded by the local app.
