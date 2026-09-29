# Stock Stalker (US)

A small stock-research app: search a US-listed company and browse its Income Statement, Balance Sheet, Cash Flow, Ratios and Key Metrics, with a latest price and logo. Technical and Strategy Builder tabs are work in progress.

Data comes from the [Fiscal.ai](https://fiscal.ai) API using the **free plan** (99 companies, 250 calls/day).

## Run it locally
1. Get a free API key from Fiscal.ai (no credit card).
2. Create a `.env` file in the project folder: `FISCAL_API_KEY=your_key_here`
3. `python tools/app.py` (Python 3.9+, no extra packages), then open http://127.0.0.1:8000

Only free-plan companies appear in search. Loading a new company uses about 8 of your 250 daily calls; results are cached in `.tmp/`.

## Feedback
Please open an issue with what you tried, what you expected and what happened.
