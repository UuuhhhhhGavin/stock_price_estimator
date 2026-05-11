# S&P 500 Options Analysis Dashboard

This directory contains the static GitHub Pages site that visualizes the
output of the S&P 500 options analysis pipeline.

---

## How the Codebase Works

The project is a daily-running pipeline that pulls the S&P 500 options
chain, derives a probability distribution for each ticker's future price
from the option market, scores each prediction's reliability, and
publishes the results as an interactive web dashboard.

### High-Level Data Flow

```
GitHub Actions (cron, daily 21:30 UTC / 3:30 CST)
        │
        ▼
stock_analyzer.py                           ← single combined pipeline
  ├─ 1. TRACKING            yfinance + oipd → option-implied PDFs per ticker
  ├─ 2. INDICATOR           direction / confidence / reliability per ticker
  ├─ 3. Z-SCORE ANALYSIS    calibration diagnostics (normality, tails)
  └─ 4. ACCURACY ANALYSIS   feature ↔ accuracy correlations, top/bottom lists
        │
        ▼
sp500_options_analysis.xlsx                 ← persistent log (one row per
                                              ticker × expiry × run date)
        │
        ▼
scripts/convert_excel_to_json.py            ← flatten Excel + attach summary
        │
        ▼
docs/data.json                              ← consumed by the front end
        │
        ▼
docs/index.html                             ← deployed via peaceiris/actions-gh-pages
```

### Repository Layout

| Path | Purpose |
|---|---|
| `stock_analyzer.py` | The combined pipeline. Runs tracking, indicator scoring, z-score analysis, and accuracy analysis end-to-end. |
| `github_action_stock_pdf_tracking.py` | Stand-alone version of the tracking step (kept for reference / manual runs). |
| `scripts/convert_excel_to_json.py` | Reads the Excel log, sanitizes types (NaN → null, datetimes → ISO strings, floats rounded), and writes `docs/data.json` along with a `summary` block produced by the analysis routines. |
| `sp500_options_analysis.xlsx` | The append-only log written by the pipeline. Source of truth for everything the dashboard shows. |
| `docs/index.html` | Single-file dashboard (HTML + CSS + vanilla JS). Fetches `data.json` and renders all tables and charts client-side. |
| `docs/data.json` | The data payload the front end loads (regenerated each run). |
| `requirements.txt` | Python dependencies: `oipd`, `yfinance`, `pandas`, `scipy`, `openpyxl`, etc. |
| `.github/workflows/python-app.yml` | The scheduled CI workflow that runs the pipeline and deploys the site. |

### Pipeline Steps (`stock_analyzer.py`)

1. **Tracking** — for every S&P 500 ticker:
   - Download recent price history (`yfinance`, with rate-limit backoff).
   - Pull the option chain for an upcoming expiration.
   - Use `oipd` to build an option-implied probability density function
     (PDF) over the underlying's price at expiry.
   - Extract metrics: expected price, expected std dev, p25/p50/p75,
     ATM IV / delta / contract cost, put-call ratios, IV skew, etc.
   - Append one row per ticker × expiration to the Excel log.
   - On subsequent runs, fill in `realized_price`, `z_score`,
     `abs_error_pct`, `landed_in_50_pct_interval`, and
     `pdf_directional_correct` for rows whose expiration has now passed.

2. **Indicator** — for each ticker with enough history (`MIN_TICKER_OBS`):
   - Combines the latest IV skew, put/call ratios, expected return, and
     historical hit-rate to produce three columns:
     - `indicator_direction` ∈ {`UP`, `DOWN`}
     - `indicator_confidence` (numeric percentage, 0–100)
     - `indicator_reliability` ∈ {`HIGH`, `MODERATE`, `LOW`,
       `INSUFFICIENT DATA`}
   - These are the *forward-looking* signals the dashboard's "Top
     Confident Predictions" table is built from.

3. **Z-Score Analysis** — diagnostic only (no per-row writes):
   - Bucket realized z-scores; run Shapiro-Wilk, D'Agostino K², Anderson-
     Darling, Jarque-Bera, Kolmogorov-Smirnov.
   - Compare empirical vs. normal tail mass at |z| > 2 and |z| > 3.
   - Surfaced under "Model Statistics → Normality Testing / Tail
     Behavior" in the dashboard.

4. **Accuracy Analysis** — feature ranking:
   - For each numeric feature (IV, skew, PCR, etc.), compute its
     correlation/t-test against:
     - whether the ticker's predictions are *reliable*, and
     - whether the prediction direction was *correct*.
   - Produces the two correlation tables shown under "Model Statistics".

### What the Dashboard Renders

`docs/index.html` is a single-page app (no build step, no framework). On
load it `fetch()`s `./data.json` and populates:

- **Header cards** — total record count, unique ticker count, last
  updated timestamp.
- **Confidence Distribution** — bucketed bar chart from the precomputed
  `summary.confidence_distribution`.
- **Top 15 Most Confident Predictions** — built client-side from raw
  rows, sorted by `indicator_reliability` tier (`HIGH` → `MODERATE` →
  `LOW` → `INSUFFICIENT DATA`), then by `indicator_confidence`
  descending. Only the most recent row per ticker is kept.
- **10 Least Reliable Predictions** — same source, reverse sort.
- **Stock Detail View** — pick any ticker to see its latest realized
  z-score, abs-error %, direction-correct flag, 50%-interval hit, and
  the 10 most recent history rows. Forward-looking rows whose option
  hasn't expired yet will legitimately have `null` z-scores.
- **Model Statistics** — normality tests, tail behavior, and feature
  correlations from steps 3 and 4 of the pipeline.
- **Data Table** — full filterable / sortable / paginated view of every
  row in `data.json`. Hidden by default; revealed by selecting a stock,
  searching, filtering, or clicking *Show full dataset*.

### Important Field Conventions

These caught the dashboard out previously and are worth knowing if you
change the pipeline or the front end:

- `indicator_confidence` is **already a percentage (0–100)**, not a
  fraction. Do not multiply by 100 when displaying.
- `indicator_reliability` is a **categorical string**
  (`HIGH` / `MODERATE` / `LOW` / `INSUFFICIENT DATA`), not a number.
  Display it as-is; sort it via a rank lookup.
- `z_score`, `realized_price`, `abs_error_pct`,
  `landed_in_50_pct_interval`, and `pdf_directional_correct` are
  **only populated after the option's expiration has passed**. Forward-
  looking prediction rows will have `null` here — this is expected, not
  a bug.
- Date-like cells are serialized as `'YYYY-MM-DD HH:MM:SS'` strings by
  the converter; the front end splits on space when only the date is
  needed (e.g. expiry column).

---

## Local Development

### Generate / refresh `data.json`

```powershell
python scripts/convert_excel_to_json.py
```

Requires an existing `sp500_options_analysis.xlsx` in the repo root. To
generate one from scratch, run the full pipeline first:

```powershell
python stock_analyzer.py
```

### Serve the dashboard locally

The page **must** be served over HTTP — opening `index.html` via
`file://` will fail because browsers block `fetch('./data.json')` under
the file protocol (the dashboard will appear stuck on "Loading...").

```powershell
cd docs
python -m http.server 8000
```

Then open <http://localhost:8000/index.html>.

The VS Code *Live Server* extension also works.

---

## Continuous Deployment

`.github/workflows/python-app.yml` runs on push to `main`, on PRs, and
on a daily cron (`30 21 * * *` UTC). Each scheduled run:

1. Installs `requirements.txt`.
2. Runs `python stock_analyzer.py` (refreshes the Excel log).
3. Runs `python scripts/convert_excel_to_json.py` (regenerates
   `docs/data.json`).
4. Commits `sp500_options_analysis.xlsx` and `docs/data.json` back to
   `main`.
5. Publishes the `docs/` directory to the `gh-pages` branch via
   `peaceiris/actions-gh-pages@v3`.

---

## Troubleshooting

| Symptom | Likely Cause / Fix |
|---|---|
| Dashboard stuck on "Loading data..." | Page opened via `file://`. Serve over HTTP (see *Local Development*). |
| All header cards show `-` | `fetch('./data.json')` failed. Check the browser console; confirm `data.json` exists next to `index.html`. |
| Top-Confident table only has one row | The precomputed `summary.top_confident_predictions` was sparse. The dashboard now rebuilds the list from raw rows, so re-deploy after pulling the latest code. |
| Confidence values look like `5,000%+` | Code multiplied a 0–100 percentage by 100. Display the raw `indicator_confidence` value with a `%` suffix. |
| Reliability column is blank | `indicator_reliability` is a string, not a number. Render the string directly. |
| Detail view shows "10 historical rows, 0 z-scores" | Those rows are *forward-looking* — the option hasn't expired yet, so `z_score` / `realized_price` legitimately have not been computed. |
| GitHub Action commits but Pages doesn't update | Confirm the `gh-pages` branch was pushed. Re-check **Settings → Pages** source. |
| `data.json` not updating | Inspect the latest workflow run under the **Actions** tab. The "Generate JSON for GitHub Pages" step writes the file; subsequent `git push` deploys it. |

---

## Extending the Dashboard

- **New columns** — they flow through automatically: any column added to
  the Excel log appears in `data.json` (`columns` array + each row) and
  becomes selectable in the *Filter by column* dropdown and the full
  data table. Custom rendering in the analysis cards requires a small
  edit to the relevant `render*` function in `index.html`.
- **New summary metrics** — add them to the `summary` dict in
  `scripts/convert_excel_to_json.py`, then read them in
  `renderAnalysisSummary` / `renderStatistics` in `index.html`.
- **Styling** — all CSS lives at the top of `index.html`. The page is
  intentionally framework-free for zero-dependency hosting on GitHub
  Pages.
