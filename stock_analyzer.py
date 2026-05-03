"""
stock_analysis.py — Combined S&P 500 Options PDF Pipeline

Runs sequentially, in order:
    1. TRACKING        — Pull S&P 500 options chains, build options-implied PDFs,
                         compute sentiment metrics, and append/refresh the Excel log.
    2. INDICATOR       — Score pending predictions (direction + confidence) and
                         persist indicator columns back to the Excel log.
    3. Z-SCORE ANALYSIS — Diagnose calibration of the model via z-score buckets,
                         normality tests, and empirical-vs-normal filtering.
    4. ACCURACY ANALYSIS — Identify which features distinguish reliable vs
                         unreliable tickers and rank top/bottom performers.

This file is a direct merge of:
    - github_action_stock_pdf_tracking.py
    - indicator.py
    - zscore_analysis.py
    - accuracy_analysis.py
"""

# ─── Imports ─────────────────────────────────────────────────────────────────
import argparse
import os
import sys
import tempfile
import time
from datetime import datetime

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from oipd import cli
from scipy import stats
from scipy.stats import (
    anderson,
    jarque_bera,
    kstest,
    normaltest,
    norm,
    shapiro,
)
from yfinance.exceptions import YFRateLimitError


# ─── Configuration ───────────────────────────────────────────────────────────
EXCEL_FILE = "sp500_options_analysis.xlsx"

# Indicator settings
MIN_TICKER_OBS = 5
DEFAULT_MIN_CONFIDENCE = 0
COL_IND_DIRECTION = "indicator_direction"
COL_IND_CONFIDENCE = "indicator_confidence"
COL_IND_RELIABILITY = "indicator_reliability"

# Empirical |z| → directional accuracy lookup (from zscore_analysis findings)
Z_ACCURACY_TABLE = [
    (0.25, 0.824),
    (0.50, 0.708),
    (0.75, 0.647),
    (1.00, 0.601),
    (1.25, 0.560),
    (1.50, 0.548),
    (2.00, 0.520),
    (3.00, 0.500),
]


# =============================================================================
# SECTION 1: TRACKING  (from github_action_stock_pdf_tracking.py)
# =============================================================================

def get_dir():
    return os.getcwd()


def safe_get_history(ticker: str, period: str = '1d', retries=5, delay=5):
    stock = yf.Ticker(ticker)
    for i in range(retries):
        try:
            hist = stock.history(period=period, auto_adjust=False)
            if hist is not None and not hist.empty:
                return stock, hist
            wait = delay * (2 ** i)
            print(f'Empty history for {ticker}, waiting {wait} seconds')
            time.sleep(wait)
        except YFRateLimitError:
            wait = delay * (2 ** i)
            print(f'Rate limit hit for {ticker}, waiting {wait} seconds')
            time.sleep(wait)
        except Exception as e:
            print(f'Other error for {ticker}: {e}')
            break
    return None, None


def normalize_download_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize yf.download output to MultiIndex columns of (Ticker, Price)."""
    if df is None or df.empty or not isinstance(df.columns, pd.MultiIndex):
        return df
    level0 = set(df.columns.get_level_values(0))
    price_fields = {'Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume'}
    if level0.intersection(price_fields):
        df = df.swaplevel(0, 1, axis=1)
    return df.sort_index(axis=1)


def single_ticker_history_to_bulk_frame(ticker: str, hist: pd.DataFrame) -> pd.DataFrame:
    if hist is None or hist.empty:
        return pd.DataFrame()
    available_cols = [
        col for col in ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume']
        if col in hist.columns
    ]
    frame = hist[available_cols].copy()
    frame.columns = pd.MultiIndex.from_product([[ticker], frame.columns], names=['Ticker', 'Price'])
    return frame


def bulk_get_history(tickers, period='1d', retries=5, delay=5):
    for i in range(retries):
        try:
            return yf.download(tickers, period=period)
        except YFRateLimitError:
            wait = delay * (2 ** i)
            print(f'Rate limit hit for bulk download, waiting {wait} seconds')
            time.sleep(wait)
        except Exception as e:
            print(f'Other error for bulk download: {e}')
            break
    return None


def extract_ticker_history(bulk_df, ticker):
    if bulk_df is None or bulk_df.empty:
        return None
    bulk_df = normalize_download_columns(bulk_df)
    if isinstance(bulk_df.columns, pd.MultiIndex):
        if ticker in bulk_df.columns.get_level_values(0):
            ticker_df = bulk_df[ticker].copy()
            return ticker_df.dropna(how='all')
    if ticker in bulk_df.columns:
        return bulk_df[ticker]
    return None


def get_sp500_tickers():
    """Fetches the list of S&P 500 tickers from Wikipedia."""
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    header = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.75 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
    }
    r = requests.get(url, headers=header)
    table = pd.read_html(r.text)
    return table[0]['Symbol'].tolist()


def get_options_chain(stock, stock_hist, expiration_week: int):
    expirations = stock.options
    expiration = expirations[expiration_week]
    options_chain = stock.option_chain(expiration)

    calls = options_chain.calls[['strike', 'bid', 'ask', 'lastPrice', 'impliedVolatility', 'volume', 'openInterest']].copy()
    calls = calls.dropna(subset=['strike', 'lastPrice', 'impliedVolatility'])
    calls.rename(columns={'lastPrice': 'last_price'}, inplace=True)

    puts = options_chain.puts[['strike', 'bid', 'ask', 'lastPrice', 'impliedVolatility', 'volume', 'openInterest']].copy()
    puts = puts.dropna(subset=['strike', 'lastPrice', 'impliedVolatility'])
    puts.rename(columns={'lastPrice': 'last_price'}, inplace=True)

    return options_chain, calls, puts, expirations


def compute_put_call_ratio(calls, puts):
    call_volume = calls['volume'].fillna(0).sum()
    put_volume = puts['volume'].fillna(0).sum()
    call_oi = calls['openInterest'].fillna(0).sum()
    put_oi = puts['openInterest'].fillna(0).sum()
    pcr_volume = float(put_volume / call_volume) if call_volume > 0 else None
    pcr_oi = float(put_oi / call_oi) if call_oi > 0 else None
    return {'pcr_volume': pcr_volume, 'pcr_oi': pcr_oi}


def compute_iv_skew(calls, puts, current_price):
    otm_calls = calls[calls['strike'] > current_price].copy()
    otm_puts = puts[puts['strike'] < current_price].copy()
    otm_calls = otm_calls[otm_calls['impliedVolatility'] > 0]
    otm_puts = otm_puts[otm_puts['impliedVolatility'] > 0]

    if not otm_calls.empty:
        call_weights = otm_calls['volume'].fillna(1).clip(lower=1)
        avg_otm_call_iv = float(np.average(otm_calls['impliedVolatility'], weights=call_weights))
    else:
        avg_otm_call_iv = None

    if not otm_puts.empty:
        put_weights = otm_puts['volume'].fillna(1).clip(lower=1)
        avg_otm_put_iv = float(np.average(otm_puts['impliedVolatility'], weights=put_weights))
    else:
        avg_otm_put_iv = None

    if avg_otm_put_iv is not None and avg_otm_call_iv is not None:
        iv_skew = avg_otm_put_iv - avg_otm_call_iv
    else:
        iv_skew = None

    return {'iv_skew': iv_skew, 'avg_otm_put_iv': avg_otm_put_iv, 'avg_otm_call_iv': avg_otm_call_iv}


def convert_puts_to_synthetic_calls(puts, current_price, days_forward, risk_free_rate=0.03):
    T = days_forward / 365
    discount = np.exp(-risk_free_rate * T)
    synthetic = puts.copy()
    synthetic['last_price'] = synthetic['last_price'] + current_price - synthetic['strike'] * discount
    if 'bid' in synthetic.columns:
        synthetic['bid'] = (synthetic['bid'] + current_price - synthetic['strike'] * discount).clip(lower=0)
    if 'ask' in synthetic.columns:
        synthetic['ask'] = (synthetic['ask'] + current_price - synthetic['strike'] * discount).clip(lower=0)
    return synthetic[synthetic['last_price'] > 0]


def build_combined_options_for_pdf(calls, puts, current_price, days_forward, risk_free_rate=0.03):
    synthetic_calls = convert_puts_to_synthetic_calls(puts, current_price, days_forward, risk_free_rate)

    real = calls[['strike', 'bid', 'ask', 'last_price', 'impliedVolatility']].copy()
    real['source'] = 'call'
    synth = synthetic_calls[['strike', 'bid', 'ask', 'last_price', 'impliedVolatility']].copy()
    synth['source'] = 'put_synthetic'

    combined = pd.concat([real, synth], ignore_index=True)
    combined = combined.groupby('strike').agg({
        'bid': 'mean',
        'ask': 'mean',
        'last_price': 'mean',
        'impliedVolatility': 'mean',
    }).reset_index()
    combined = combined.sort_values('strike').reset_index(drop=True)
    combined = combined.dropna(subset=['last_price'])
    return combined[combined['last_price'] > 0]


def compute_stock_delta(S, K, T, r, sigma, option_type='call'):
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    if option_type == 'call':
        return norm.cdf(d1)
    return -norm.cdf(-d1)


def analyze_ticker(ticker, stock, stock_hist, options_chain, calls, puts, expiration):
    """Generate a PDF for the given ticker+expiration using calls AND puts."""
    try:
        if stock is None:
            print(f"[{ticker}] Failed to fetch stock data.")
            return None

        current_price = None
        current_volume = None
        try:
            if stock_hist is not None:
                if isinstance(stock_hist, pd.DataFrame) and 'Close' in stock_hist.columns:
                    col = stock_hist['Close']
                    current_price = float(col.iloc[-1]) if hasattr(col, 'iloc') else float(col)
                elif isinstance(stock_hist, pd.DataFrame) and isinstance(stock_hist.columns, pd.MultiIndex):
                    close_cols = [c for c in stock_hist.columns if c[0] == 'Close']
                    if close_cols:
                        current_price = float(stock_hist[close_cols[0]].iloc[-1])
                    else:
                        try:
                            current_price = float(stock_hist.iloc[-1]['Close'])
                        except Exception:
                            current_price = None
                elif hasattr(stock_hist, 'iloc'):
                    try:
                        current_price = float(stock_hist['Close'].iloc[-1]) if 'Close' in stock_hist else float(stock_hist.iloc[-1])
                    except Exception:
                        current_price = None
        except Exception:
            current_price = None

        try:
            if stock_hist is not None:
                if isinstance(stock_hist, pd.DataFrame) and 'Volume' in stock_hist.columns:
                    vol = stock_hist['Volume']
                    current_volume = int(vol.iloc[-1]) if hasattr(vol, 'iloc') else int(vol)
                elif isinstance(stock_hist, pd.DataFrame) and isinstance(stock_hist.columns, pd.MultiIndex):
                    vol_cols = [c for c in stock_hist.columns if c[0] == 'Volume']
                    if vol_cols:
                        current_volume = int(stock_hist[vol_cols[0]].iloc[-1])
                    else:
                        try:
                            current_volume = int(stock_hist.iloc[-1]['Volume'])
                        except Exception:
                            current_volume = None
                elif hasattr(stock_hist, 'iloc'):
                    try:
                        current_volume = int(stock_hist['Volume'].iloc[-1]) if 'Volume' in stock_hist else None
                    except Exception:
                        current_volume = None
        except Exception:
            current_volume = None

        if current_price is None:
            print(f"[{ticker}] No current price data available.")
            return None
        if calls.empty:
            return None

        current_date = datetime.today().strftime('%Y-%m-%d')
        days_difference = (datetime.strptime(expiration, "%Y-%m-%d") - datetime.today()).days

        pcr = compute_put_call_ratio(calls, puts)
        iv_skew_data = compute_iv_skew(calls, puts, current_price)

        combined_options = build_combined_options_for_pdf(
            calls, puts, current_price, days_difference, risk_free_rate=0.03
        )
        if combined_options.empty:
            return None

        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as temp_file:
            temp_csv_path = temp_file.name
            combined_options.to_csv(temp_csv_path, index=False)

        ticker_pdf = cli.generate_pdf.run(
            input_csv_path=temp_csv_path,
            current_price=float(current_price),
            days_forward=max(days_difference, 1),
            risk_free_rate=0.03,
            fit_kernel_pdf=True,
        )
        os.remove(temp_csv_path)

        normalized_pdf = ticker_pdf.PDF / ticker_pdf.PDF.sum()
        expected_price = float((ticker_pdf.Price * normalized_pdf).sum())
        expected_std = float(np.sqrt(((ticker_pdf.Price - expected_price) ** 2 * normalized_pdf).sum()))
        expected_std_pct = float(expected_std / current_price) if current_price else None

        cdf = np.cumsum(normalized_pdf)
        p25 = float(ticker_pdf.Price[np.searchsorted(cdf, 0.25)])
        p50 = float(ticker_pdf.Price[np.searchsorted(cdf, 0.50)])
        p75 = float(ticker_pdf.Price[np.searchsorted(cdf, 0.75)])

        liquid_calls = calls[(calls['bid'] > 0) & (calls['ask'] > 0) & (calls['impliedVolatility'] > 0)]
        if liquid_calls.empty:
            return None
        atm_option = liquid_calls.iloc[(liquid_calls['strike'] - current_price).abs().argsort()[:1]]
        atm_strike = float(atm_option['strike'].values[0])
        iv = float(atm_option['impliedVolatility'].values[0])
        atm_cost = float(atm_option['last_price'].values[0])

        T = days_difference / 365
        atm_delta = float(compute_stock_delta(current_price, atm_strike, T, 0.03, iv))

        return {
            'date': current_date,
            'ticker': ticker,
            'analyzed option expiration': expiration,
            'weeks from today': round(days_difference / 7, 2),
            'current_price': current_price,
            'current_volume': current_volume,
            'expected_price': expected_price,
            'expected_std': expected_std,
            'expected_std_pct': expected_std_pct,
            'p25': p25,
            'p50': p50,
            'p75': p75,
            'percent change %': (expected_price - current_price) / current_price * 100,
            'realized_price': None,
            'landed_in_50_pct_interval': None,
            'ATM Strike Price': atm_strike,
            'ATM IV': iv,
            'ATM Contract Cost': atm_cost,
            'ATM Delta': atm_delta,
            'pcr_volume': pcr['pcr_volume'],
            'pcr_oi': pcr['pcr_oi'],
            'iv_skew': iv_skew_data['iv_skew'],
            'avg_otm_put_iv': iv_skew_data['avg_otm_put_iv'],
            'avg_otm_call_iv': iv_skew_data['avg_otm_call_iv'],
            'pdf_directional_correct': None,
            'z_score': None,
        }

    except Exception as e:
        print(f"[{ticker}] Failed: {e}")
        return None


def load_log(path: str) -> pd.DataFrame:
    if os.path.exists(path):
        try:
            return pd.read_excel(path)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def merge_results(existing: pd.DataFrame, new_results: list) -> pd.DataFrame:
    new_df = pd.DataFrame(new_results)
    new_df['logged_at'] = datetime.today().strftime('%Y-%m-%d %H:%M:%S')

    combined = new_df if existing.empty else pd.concat([existing, new_df], ignore_index=True)
    dedup_cols = ['date', 'ticker', 'analyzed option expiration']
    return combined.drop_duplicates(subset=dedup_cols, keep='last')


def save_log(df: pd.DataFrame, path: str):
    """Atomically save the DataFrame to an Excel file."""
    tmp_file = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
    tmp_path = tmp_file.name
    tmp_file.close()
    with pd.ExcelWriter(tmp_path, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    os.replace(tmp_path, path)


def update_realized_prices(df: pd.DataFrame) -> pd.DataFrame:
    """Backfill realized prices and metrics for expired predictions."""
    if df.empty:
        return df
    today = pd.Timestamp.today().normalize()
    df['analyzed option expiration'] = pd.to_datetime(df['analyzed option expiration'], errors='coerce')

    mask = (
        df['realized_price'].isna()
        & df['analyzed option expiration'].notna()
        & (df['analyzed option expiration'] < today)
    )
    if not mask.any():
        return df

    subset = df.loc[mask].copy()
    lookback_days = 5
    subset['start'] = subset['analyzed option expiration'] - pd.Timedelta(days=lookback_days)
    overall_start = subset['start'].min().strftime('%Y-%m-%d')
    overall_end = (subset['analyzed option expiration'] + pd.Timedelta(days=1)).max().strftime('%Y-%m-%d')
    tickers = subset['ticker'].unique().tolist()

    try:
        hist_all = yf.download(tickers, start=overall_start, end=overall_end,
                               progress=False, group_by='ticker', threads=True)
    except Exception as e:
        print(f"Failed to bulk download realized prices: {e}")
        return df

    def _get_close_series(hist_all_obj, tk):
        if hist_all_obj is None or getattr(hist_all_obj, 'empty', False):
            return None
        if isinstance(hist_all_obj.columns, pd.MultiIndex):
            try:
                return hist_all_obj[tk]['Close']
            except Exception:
                return None
        if 'Close' in hist_all_obj.columns:
            return hist_all_obj['Close']
        return None

    realized_map = {}
    for ticker, group in subset.groupby('ticker'):
        try:
            close_series = _get_close_series(hist_all, ticker)
            if close_series is None or getattr(close_series, 'empty', True):
                continue
            close_series = close_series.sort_index()
            for idx, row in group.iterrows():
                exp = row['analyzed option expiration']
                valid = close_series[close_series.index <= exp]
                if valid.empty:
                    continue
                realized_map[idx] = float(valid.iloc[-1])
        except Exception as e:
            print(f"[{ticker}] Failed while extracting realized prices: {e}")
            continue

    if not realized_map:
        return df

    idxs = list(realized_map.keys())
    df.loc[idxs, 'realized_price'] = [realized_map[i] for i in idxs]

    cur = df.loc[idxs, 'current_price']
    exp_price = df.loc[idxs, 'expected_price']
    realized = df.loc[idxs, 'realized_price']

    df.loc[idxs, 'pdf_directional_correct'] = ((exp_price - cur) * (realized - cur)) > 0

    def _in_50(row):
        if pd.notna(row.get('p25')) and pd.notna(row.get('p75')):
            return row['p25'] <= row['realized_price'] <= row['p75']
        return None

    df.loc[idxs, 'landed_in_50_pct_interval'] = df.loc[idxs].apply(_in_50, axis=1)
    df.loc[idxs, 'abs_error_pct'] = (
        abs(df.loc[idxs, 'realized_price'] - df.loc[idxs, 'expected_price'])
        / df.loc[idxs, 'current_price']
    ) * 100

    stds = df.loc[idxs, 'expected_std'].fillna(0)
    nonzero = stds > 0
    z = pd.Series(index=idxs, dtype=float)
    z.loc[nonzero.index[nonzero]] = (
        df.loc[idxs, 'realized_price'] - df.loc[idxs, 'expected_price']
    )[nonzero] / stds[nonzero]
    df.loc[idxs, 'z_score'] = z

    return df


def run_tracking(excel_path: str, save: bool = True, ticker_subset=None):
    """Run the tracking pipeline. Returns the merged DataFrame, or None on failure."""
    print("\n" + "=" * 80)
    print("STAGE 1 / 4 — TRACKING: building options-implied PDFs")
    print("=" * 80)

    stock_overview = []
    tickers = ticker_subset if ticker_subset else get_sp500_tickers()
    print(f'Analyzing {len(tickers)} tickers...')

    bulk_hist = bulk_get_history(tickers, period='1d')
    if bulk_hist is None or bulk_hist.empty:
        print('ERROR: No price data was downloaded. Cannot proceed.')
        return None

    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            stock_hist = extract_ticker_history(bulk_hist, ticker)
            if stock_hist is None or stock_hist.empty:
                print(f"[{ticker}] No price history available, skipping")
                continue
            options_chain, calls, puts, expirations = get_options_chain(stock, stock_hist, expiration_week=1)
            print(f'[{ticker}] Expirations: {expirations}')
            expiration = expirations[1]
            result = analyze_ticker(ticker, stock, stock_hist, options_chain, calls, puts, expiration)
            if result is not None:
                for key, value in result.items():
                    print(f'{key.title()}: {value}')
                stock_overview.append(result)
                print()
            time.sleep(0.5)
        except Exception as e:
            print(f"[{ticker}] Error: {e}")
            continue

    if not save:
        print("Saving is disabled, not saving the data to a file.")
        return None
    if not stock_overview:
        print("No data to save.....")
        return None

    print("Saving is enabled, saving the data to a file.")
    try:
        existing = load_log(excel_path)
    except Exception:
        existing = pd.DataFrame()

    try:
        merged = merge_results(existing, stock_overview)
        merged = update_realized_prices(merged)
        save_log(merged, excel_path)
        print(f"Saved merged log to {excel_path} ({len(merged)} rows).")
        return merged
    except Exception as e:
        print(f"Failed to save merged log: {e}")
        return None


# =============================================================================
# SECTION 2: INDICATOR  (from indicator.py)
# =============================================================================

def lookup_z_accuracy(abs_z: float) -> float:
    for max_z, acc in Z_ACCURACY_TABLE:
        if abs_z <= max_z:
            return acc
    return 0.50


def load_indicator_data(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        print(f"ERROR: {path} not found. Run tracking stage first.")
        sys.exit(1)
    df = pd.read_excel(path)
    df["analyzed option expiration"] = pd.to_datetime(df["analyzed option expiration"], errors="coerce")
    return df


def build_ticker_profiles(df: pd.DataFrame) -> pd.DataFrame:
    realized = df[df["pdf_directional_correct"].notna()].copy()
    if realized.empty:
        return pd.DataFrame(columns=["ticker", "hist_n", "hist_accuracy",
                                     "hist_avg_abs_z", "hist_median_abs_z"])
    return realized.groupby("ticker").agg(
        hist_n=("pdf_directional_correct", "count"),
        hist_accuracy=("pdf_directional_correct", "mean"),
        hist_avg_abs_z=("z_score", lambda x: x.abs().mean()),
        hist_median_abs_z=("z_score", lambda x: x.abs().median()),
        hist_landed_50=("landed_in_50_pct_interval", "mean"),
    ).reset_index()


def _empty_indicator_result():
    return {"direction": "N/A", "confidence": 0.0, "reliability": "N/A", "components": {}}


def compute_confidence(row: pd.Series, profiles: pd.DataFrame) -> dict:
    ticker = row["ticker"]
    current_price = row["current_price"]
    expected_price = row["expected_price"]
    expected_std_pct = row.get("expected_std_pct", None)
    pct_change = row["percent change %"]
    pcr_volume = row.get("pcr_volume", None)
    iv_skew = row.get("iv_skew", None)

    if pd.isna(expected_price) or pd.isna(current_price):
        return _empty_indicator_result()

    direction = "UP" if expected_price > current_price else "DOWN"

    profile = profiles[profiles["ticker"] == ticker]
    has_history = not profile.empty and profile.iloc[0]["hist_n"] >= MIN_TICKER_OBS

    if has_history:
        p = profile.iloc[0]
        hist_accuracy = p["hist_accuracy"]
        hist_avg_abs_z = p["hist_avg_abs_z"]
        hist_n = int(p["hist_n"])
        z_based_accuracy = lookup_z_accuracy(hist_avg_abs_z)
        weight = min(hist_n / 30.0, 1.0)
        blended_accuracy = weight * hist_accuracy + (1 - weight) * z_based_accuracy
        if hist_avg_abs_z <= 0.50:
            reliability = "HIGH"
        elif hist_avg_abs_z <= 1.00:
            reliability = "MODERATE"
        else:
            reliability = "LOW"
    else:
        blended_accuracy = 0.485
        hist_avg_abs_z = None
        hist_accuracy = None
        hist_n = 0
        reliability = "INSUFFICIENT DATA"

    abs_pct_change = abs(pct_change) if pd.notna(pct_change) else 0
    if abs_pct_change >= 2.0:
        magnitude_bonus = 0.02
    elif abs_pct_change >= 1.0:
        magnitude_bonus = 0.01
    else:
        magnitude_bonus = 0.00

    sentiment_adjustment = 0.0
    if pd.notna(pcr_volume) and pcr_volume > 0:
        if direction == "DOWN" and pcr_volume > 1.2:
            sentiment_adjustment += 0.015
        elif direction == "UP" and pcr_volume < 0.7:
            sentiment_adjustment += 0.015
        elif direction == "UP" and pcr_volume > 1.5:
            sentiment_adjustment -= 0.015
        elif direction == "DOWN" and pcr_volume < 0.5:
            sentiment_adjustment -= 0.015

    if pd.notna(iv_skew):
        if direction == "DOWN" and iv_skew > 0.05:
            sentiment_adjustment += 0.01
        elif direction == "UP" and iv_skew < -0.02:
            sentiment_adjustment += 0.01
        elif direction == "UP" and iv_skew > 0.10:
            sentiment_adjustment -= 0.01
        elif direction == "DOWN" and iv_skew < -0.05:
            sentiment_adjustment -= 0.01

    vol_penalty = 0.0
    if pd.notna(expected_std_pct):
        if expected_std_pct > 0.08:
            vol_penalty = -0.03
        elif expected_std_pct > 0.05:
            vol_penalty = -0.015

    raw_confidence = blended_accuracy + magnitude_bonus + sentiment_adjustment + vol_penalty
    raw_confidence = max(0.30, min(0.95, raw_confidence))
    confidence_pct = round(raw_confidence * 100, 1)

    return {
        "direction": direction,
        "confidence": confidence_pct,
        "reliability": reliability,
        "components": {
            "blended_accuracy": round(blended_accuracy * 100, 1),
            "magnitude_bonus": round(magnitude_bonus * 100, 1),
            "sentiment_adj": round(sentiment_adjustment * 100, 1),
            "vol_penalty": round(vol_penalty * 100, 1),
            "hist_accuracy": round(hist_accuracy * 100, 1) if hist_accuracy is not None else None,
            "hist_avg_abs_z": round(hist_avg_abs_z, 3) if hist_avg_abs_z is not None else None,
            "hist_n": hist_n,
        },
    }


def save_indicator_columns(df: pd.DataFrame, scored_rows: list, excel_path: str):
    for col in [COL_IND_DIRECTION, COL_IND_CONFIDENCE, COL_IND_RELIABILITY]:
        if col not in df.columns:
            df[col] = np.nan
    for row in scored_rows:
        idx = row["row_index"]
        df.at[idx, COL_IND_DIRECTION] = row["direction"]
        df.at[idx, COL_IND_CONFIDENCE] = row["confidence"]
        df.at[idx, COL_IND_RELIABILITY] = row["reliability"]
    df.to_excel(excel_path, index=False)


def display_indicator_results(results: pd.DataFrame, min_confidence: float):
    filtered = results[results["confidence"] >= min_confidence].copy()
    if filtered.empty:
        print(f"No predictions meet the minimum confidence of {min_confidence}%.")
        return

    filtered = filtered.sort_values("confidence", ascending=False)
    n_up = (filtered["direction"] == "UP").sum()
    n_down = (filtered["direction"] == "DOWN").sum()
    avg_conf = filtered["confidence"].mean()
    high_conf = filtered[filtered["confidence"] >= 60]

    print()
    print("=" * 100)
    print("  STOCK DIRECTION INDICATOR — Based on Options-Implied PDF Analysis")
    print("=" * 100)
    print(f"  Predictions: {len(filtered)}  |  UP: {n_up}  DOWN: {n_down}  "
          f"|  Avg confidence: {avg_conf:.1f}%  |  High confidence (>=60%): {len(high_conf)}")
    print("=" * 100)
    print()

    if not high_conf.empty:
        print("─── HIGH CONFIDENCE PICKS (>= 60%) ───")
        print()
        print(f"  {'Ticker':<7s}  {'Dir':>4s}  {'Conf':>6s}  {'Reliability':<18s}  "
              f"{'Price':>8s}  {'Expected':>9s}  {'Chg%':>6s}  {'Expiry':<12s}  "
              f"{'Hist Acc':>8s}  {'Hist |z|':>8s}  {'Hist n':>6s}")
        print(f"  {'─' * 7}  {'─' * 4}  {'─' * 6}  {'─' * 18}  "
              f"{'─' * 8}  {'─' * 9}  {'─' * 6}  {'─' * 12}  "
              f"{'─' * 8}  {'─' * 8}  {'─' * 6}")
        for _, r in high_conf.iterrows():
            comp = r["components"]
            hist_acc_str = f"{comp['hist_accuracy']:.1f}%" if comp.get("hist_accuracy") is not None else "   N/A"
            hist_z_str = f"{comp['hist_avg_abs_z']:.3f}" if comp.get("hist_avg_abs_z") is not None else "   N/A"
            dir_symbol = "▲" if r["direction"] == "UP" else "▼"
            exp_str = r["expiration"].strftime("%Y-%m-%d") if pd.notna(r["expiration"]) else "N/A"
            print(f"  {r['ticker']:<7s}  {dir_symbol} {r['direction']:<2s}  {r['confidence']:>5.1f}%  "
                  f"{r['reliability']:<18s}  "
                  f"${r['current_price']:>7.2f}  ${r['expected_price']:>8.2f}  "
                  f"{r['pct_change']:>+5.1f}%  {exp_str:<12s}  "
                  f"{hist_acc_str:>8s}  {hist_z_str:>8s}  {comp['hist_n']:>6d}")
        print()

    print("─── ALL PREDICTIONS ───")
    print()
    print(f"  {'Ticker':<7s}  {'Dir':>4s}  {'Conf':>6s}  {'Reliability':<18s}  "
          f"{'Price':>8s}  {'Expected':>9s}  {'Chg%':>6s}  {'Expiry':<12s}")
    print(f"  {'─' * 7}  {'─' * 4}  {'─' * 6}  {'─' * 18}  "
          f"{'─' * 8}  {'─' * 9}  {'─' * 6}  {'─' * 12}")
    for _, r in filtered.iterrows():
        dir_symbol = "▲" if r["direction"] == "UP" else "▼"
        exp_str = r["expiration"].strftime("%Y-%m-%d") if pd.notna(r["expiration"]) else "N/A"
        print(f"  {r['ticker']:<7s}  {dir_symbol} {r['direction']:<2s}  {r['confidence']:>5.1f}%  "
              f"{r['reliability']:<18s}  "
              f"${r['current_price']:>7.2f}  ${r['expected_price']:>8.2f}  "
              f"{r['pct_change']:>+5.1f}%  {exp_str:<12s}")
    print()

    print("─── CONFIDENCE DISTRIBUTION ───")
    print()
    for lo, hi, label in [(80, 101, "Very High (80-95%)"),
                          (60, 80, "High (60-80%)"),
                          (50, 60, "Moderate (50-60%)"),
                          (0, 50, "Low (<50%)")]:
        bucket = filtered[(filtered["confidence"] >= lo) & (filtered["confidence"] < hi)]
        bar = "█" * len(bucket)
        print(f"  {label:<25s}  {len(bucket):>4d}  {bar}")
    print()


def run_indicator(excel_path: str, tickers=None, min_confidence: float = 0, save: bool = True):
    print("\n" + "=" * 80)
    print("STAGE 2 / 4 — INDICATOR: scoring pending predictions")
    print("=" * 80)

    df = load_indicator_data(excel_path)
    profiles = build_ticker_profiles(df)
    pending = df[df["realized_price"].isna()].copy()

    if pending.empty:
        print("No pending (unrealized) predictions found.")
        return pd.DataFrame()

    pending = pending.sort_values("date", ascending=False).drop_duplicates(subset=["ticker"], keep="first")

    if tickers:
        tickers_upper = [t.upper() for t in tickers]
        pending = pending[pending["ticker"].isin(tickers_upper)]
        if pending.empty:
            print(f"No pending predictions found for: {', '.join(tickers_upper)}")
            return pd.DataFrame()

    results = []
    for _, row in pending.iterrows():
        signal = compute_confidence(row, profiles)
        results.append({
            "row_index": row.name,
            "ticker": row["ticker"],
            "direction": signal["direction"],
            "confidence": signal["confidence"],
            "reliability": signal["reliability"],
            "current_price": row["current_price"],
            "expected_price": row["expected_price"],
            "pct_change": row["percent change %"],
            "expiration": row["analyzed option expiration"],
            "atm_iv": row.get("ATM IV"),
            "components": signal["components"],
        })

    if save:
        save_indicator_columns(df, results, excel_path)

    results_df = pd.DataFrame(results)
    display_indicator_results(results_df, min_confidence)
    return results_df


# =============================================================================
# SECTION 3: Z-SCORE ANALYSIS  (from zscore_analysis.py)
# =============================================================================

def run_zscore_analysis(excel_path: str):
    print("\n" + "=" * 80)
    print("STAGE 3 / 4 — Z-SCORE ANALYSIS: model calibration diagnostics")
    print("=" * 80)

    df = pd.read_excel(excel_path)
    realized = df[df['pdf_directional_correct'].notna() & df['z_score'].notna()].copy()
    if realized.empty:
        print("No realized observations with z-scores yet — skipping z-score analysis.")
        return None
    realized['abs_z'] = realized['z_score'].abs()

    print(f"Observations with both directional accuracy and z-score: {len(realized)}")
    print()

    # 1. Accuracy by |z| bucket
    print("=" * 80)
    print("ACCURACY BY |Z-SCORE| BUCKET")
    print("=" * 80)
    bins = [0, 0.25, 0.50, 0.75, 1.0, 1.25, 1.50, 2.0, 3.0, 5.0, 100]
    labels = ['0-0.25', '0.25-0.5', '0.5-0.75', '0.75-1.0', '1.0-1.25',
              '1.25-1.5', '1.5-2.0', '2.0-3.0', '3.0-5.0', '>5.0']
    realized['z_bucket'] = pd.cut(realized['abs_z'], bins=bins, labels=labels)
    bucket_stats = realized.groupby('z_bucket', observed=True).agg(
        n=('pdf_directional_correct', 'count'),
        accuracy=('pdf_directional_correct', 'mean'),
        avg_abs_error=('abs_error_pct', 'mean'),
        avg_iv=('ATM IV', 'mean'),
    ).reset_index()
    for _, r in bucket_stats.iterrows():
        bar = '#' * int(r['accuracy'] * 50)
        print(f"  |z| {r['z_bucket']:<10s}  n={r['n']:>5.0f}  accuracy={r['accuracy']:.1%}  "
              f"avg_err={r['avg_abs_error']:.1f}%  {bar}")
    print()

    # 2. Cumulative
    print("=" * 80)
    print("CUMULATIVE: accuracy when |z| <= threshold")
    print("=" * 80)
    for t in [0.25, 0.50, 0.75, 1.0, 1.25, 1.50, 2.0, 2.5, 3.0, 999]:
        subset = realized[realized['abs_z'] <= t]
        if len(subset) > 0:
            acc = subset['pdf_directional_correct'].mean()
            pct_of_data = len(subset) / len(realized) * 100
            label = f"|z| <= {t:.2f}" if t < 100 else "ALL"
            bar = '#' * int(acc * 50)
            print(f"  {label:<14s}  n={len(subset):>5d} ({pct_of_data:>5.1f}%)  accuracy={acc:.1%}  {bar}")
    print()

    # 3. Per-ticker stats
    print("=" * 80)
    print("PER-TICKER: avg |z-score| vs directional accuracy (min 8 obs)")
    print("=" * 80)
    ticker_stats = realized.groupby('ticker').agg(
        n=('pdf_directional_correct', 'count'),
        dir_accuracy=('pdf_directional_correct', 'mean'),
        avg_abs_z=('abs_z', 'mean'),
        median_abs_z=('abs_z', lambda x: x.median()),
        std_z=('z_score', 'std'),
        mean_z=('z_score', 'mean'),
    ).reset_index()
    ticker_stats = ticker_stats[ticker_stats['n'] >= 8]
    if not ticker_stats.empty:
        corr, p = stats.pearsonr(ticker_stats['avg_abs_z'], ticker_stats['dir_accuracy'])
        print(f"  Pearson r(avg|z|, accuracy) = {corr:.4f}, p = {p:.6f}")
        corr2, p2 = stats.pearsonr(ticker_stats['median_abs_z'], ticker_stats['dir_accuracy'])
        print(f"  Pearson r(median|z|, accuracy) = {corr2:.4f}, p = {p2:.6f}")
    print()

    # 4. Sign analysis
    print("=" * 80)
    print("Z-SCORE SIGN: Does the model consistently over/under-predict?")
    print("=" * 80)
    pos_z = realized[realized['z_score'] > 0]
    neg_z = realized[realized['z_score'] < 0]
    print(f"  z > 0 (model underestimated): n={len(pos_z)}, accuracy={pos_z['pdf_directional_correct'].mean():.1%}")
    print(f"  z < 0 (model overestimated):  n={len(neg_z)}, accuracy={neg_z['pdf_directional_correct'].mean():.1%}")
    print(f"  Overall mean z: {realized['z_score'].mean():.4f}")
    print(f"  Overall median z: {realized['z_score'].median():.4f}")
    print()

    # 5. Optimal |z| threshold
    print("=" * 80)
    print("OPTIMAL |Z| FILTER: trade-off between accuracy and coverage")
    print("=" * 80)
    print(f"  {'Threshold':<12s}  {'Kept':>6s}  {'Dropped':>7s}  {'Accuracy':>9s}  {'Lift':>6s}  {'Coverage':>9s}")
    baseline_acc = realized['pdf_directional_correct'].mean()
    print(f"  {'(baseline)':<12s}  {len(realized):>6d}  {0:>7d}  {baseline_acc:>8.1%}  {'+0.0%':>6s}  {'100.0%':>9s}")
    for t in [0.25, 0.50, 0.75, 1.0, 1.25, 1.50, 2.0, 2.5, 3.0]:
        kept = realized[realized['abs_z'] <= t]
        dropped = len(realized) - len(kept)
        if len(kept) > 0:
            acc = kept['pdf_directional_correct'].mean()
            lift = acc - baseline_acc
            coverage = len(kept) / len(realized) * 100
            print(f"  |z| <= {t:<5.2f}  {len(kept):>6d}  {dropped:>7d}  {acc:>8.1%}  {lift:>+5.1%}  {coverage:>8.1f}%")
    print()

    # 6. Normality testing
    z_scores = realized['z_score'].dropna().values
    if len(z_scores) < 20:
        print("Not enough z-scores for normality testing — skipping section 6.")
        return None

    print("=" * 80)
    print("NORMALITY TESTING: Is the z-score distribution actually Gaussian?")
    print("=" * 80)
    print(f"  N            = {len(z_scores)}")
    print(f"  Mean         = {np.mean(z_scores):+.4f}  (normal: 0)")
    print(f"  Median       = {np.median(z_scores):+.4f}  (normal: 0)")
    print(f"  Std Dev      = {np.std(z_scores):.4f}  (normal: 1)")
    print(f"  Skewness     = {stats.skew(z_scores):+.4f}")
    print(f"  Kurtosis     = {stats.kurtosis(z_scores):+.4f}")
    print()

    if len(z_scores) > 5000:
        rng = np.random.default_rng(42)
        shapiro_sample = rng.choice(z_scores, size=5000, replace=False)
    else:
        shapiro_sample = z_scores
    sw_stat, sw_p = shapiro(shapiro_sample)
    dag_stat, dag_p = normaltest(z_scores)
    ad_result = anderson(z_scores, dist='norm')
    ad_rejects = [sl for sl, cv in zip(ad_result.significance_level, ad_result.critical_values)
                  if ad_result.statistic > cv]
    jb_stat, jb_p = jarque_bera(z_scores)
    ks_stat, ks_p = kstest(z_scores, 'norm', args=(np.mean(z_scores), np.std(z_scores)))

    print(f"  Shapiro-Wilk:        stat={sw_stat:.6f}  p={sw_p:.6f}  "
          f"{'REJECT normality' if sw_p < 0.05 else 'consistent w/ normal'}")
    print(f"  D'Agostino K2:       stat={dag_stat:.4f}  p={dag_p:.6f}  "
          f"{'REJECT normality' if dag_p < 0.05 else 'consistent w/ normal'}")
    print(f"  Anderson-Darling:    stat={ad_result.statistic:.4f}  "
          f"{'REJECT at ' + str(min(ad_rejects)) + '%' if ad_rejects else 'consistent w/ normal'}")
    print(f"  Jarque-Bera:         stat={jb_stat:.4f}  p={jb_p:.6f}  "
          f"{'REJECT normality' if jb_p < 0.05 else 'consistent w/ normal'}")
    print(f"  Kolmogorov-Smirnov:  stat={ks_stat:.6f}  p={ks_p:.6f}  "
          f"{'REJECT normality' if ks_p < 0.05 else 'consistent w/ normal'}")
    print()

    # Tail behavior
    beyond_2 = np.mean(np.abs(z_scores) > 2) * 100
    beyond_3 = np.mean(np.abs(z_scores) > 3) * 100
    expected_beyond_2 = (1 - (stats.norm.cdf(2) - stats.norm.cdf(-2))) * 100
    expected_beyond_3 = (1 - (stats.norm.cdf(3) - stats.norm.cdf(-3))) * 100
    print("─── Tail Behavior ───")
    print(f"  Beyond |z|>2:  empirical={beyond_2:.2f}%  normal={expected_beyond_2:.2f}%  "
          f"ratio={beyond_2/expected_beyond_2:.1f}x")
    print(f"  Beyond |z|>3:  empirical={beyond_3:.2f}%  normal={expected_beyond_3:.2f}%  "
          f"ratio={beyond_3/expected_beyond_3:.1f}x")
    print()

    # Empirical percentile recommendations
    print("─── Empirical percentile thresholds (recommended over normal z-tables) ───")
    for conf in [50, 68, 80, 90, 95, 99]:
        lower = np.percentile(z_scores, (100 - conf) / 2)
        upper = np.percentile(z_scores, 100 - (100 - conf) / 2)
        normal_lower = stats.norm.ppf((100 - conf) / 200)
        normal_upper = stats.norm.ppf(1 - (100 - conf) / 200)
        print(f"  {conf}% interval:  empirical=[{lower:+.3f}, {upper:+.3f}]  "
              f"normal=[{normal_lower:+.3f}, {normal_upper:+.3f}]")

    # Return computed statistics
    return {
        'normality_testing': {
            'n': len(z_scores),
            'mean': float(np.mean(z_scores)),
            'median': float(np.median(z_scores)),
            'std_dev': float(np.std(z_scores)),
            'skewness': float(stats.skew(z_scores)),
            'kurtosis': float(stats.kurtosis(z_scores)),
            'shapiro_wilk_p': float(sw_p),
            'dagostino_k2_p': float(dag_p),
            'anderson_darling_reject': bool(ad_rejects),
            'jarque_bera_p': float(jb_p),
            'kolmogorov_smirnov_p': float(ks_p)
        },
        'tail_behavior': {
            'beyond_z2_empirical': float(beyond_2),
            'beyond_z2_normal': float(expected_beyond_2),
            'beyond_z3_empirical': float(beyond_3),
            'beyond_z3_normal': float(expected_beyond_3)
        },
        'overall_stats': {
            'mean_z': float(realized['z_score'].mean()),
            'median_z': float(realized['z_score'].median())
        }
    }


# =============================================================================
# SECTION 4: ACCURACY ANALYSIS  (from accuracy_analysis.py)
# =============================================================================

def run_accuracy_analysis(excel_path: str, min_obs: int = 8):
    print("\n" + "=" * 80)
    print("STAGE 4 / 4 — ACCURACY ANALYSIS: per-ticker reliability & feature drivers")
    print("=" * 80)

    df = pd.read_excel(excel_path)
    realized = df[df['pdf_directional_correct'].notna()].copy()
    if realized.empty:
        print("No realized observations yet — skipping accuracy analysis.")
        return

    print(f"Total realized observations: {len(realized)}")
    print(f"Unique tickers: {realized['ticker'].nunique()}")
    print(f"Date range: {realized['date'].min()} to {realized['date'].max()}")
    print()

    acc = realized.groupby('ticker').agg(
        n_obs=('pdf_directional_correct', 'count'),
        dir_accuracy=('pdf_directional_correct', 'mean'),
        avg_atm_iv=('ATM IV', 'mean'),
        avg_expected_std_pct=('expected_std_pct', 'mean'),
        avg_abs_z_score=('z_score', lambda x: x.abs().mean()),
        avg_abs_error_pct=('abs_error_pct', 'mean'),
        avg_percent_change=('percent change %', 'mean'),
        avg_abs_percent_change=('percent change %', lambda x: x.abs().mean()),
        avg_atm_delta=('ATM Delta', 'mean'),
        avg_atm_cost=('ATM Contract Cost', 'mean'),
        avg_volume=('current_volume', 'mean'),
        avg_pcr_volume=('pcr_volume', 'mean'),
        avg_pcr_oi=('pcr_oi', 'mean'),
        avg_iv_skew=('iv_skew', 'mean'),
        avg_otm_put_iv=('avg_otm_put_iv', 'mean'),
        avg_otm_call_iv=('avg_otm_call_iv', 'mean'),
        avg_price=('current_price', 'mean'),
        landed_in_50pct=('landed_in_50_pct_interval', 'mean'),
    ).reset_index()
    acc = acc[acc['n_obs'] >= min_obs].copy()
    print(f"Tickers with >= {min_obs} observations: {len(acc)}")
    print()

    if acc.empty:
        print("Not enough per-ticker data for ranking yet.")
        return

    acc_sorted = acc.sort_values('dir_accuracy', ascending=False)
    print("=" * 80)
    print("TOP 15 MOST DIRECTIONALLY ACCURATE")
    print("=" * 80)
    for _, r in acc_sorted.head(15).iterrows():
        print(f"  {r['ticker']:6s}  accuracy={r['dir_accuracy']:.1%}  n={r['n_obs']:.0f}  "
              f"avg_IV={r['avg_atm_iv']:.3f}  avg_|z|={r['avg_abs_z_score']:.2f}  "
              f"avg_std%={r['avg_expected_std_pct']:.3f}  avg_price=${r['avg_price']:.1f}")
    print()

    print("=" * 80)
    print("BOTTOM 15 LEAST DIRECTIONALLY ACCURATE")
    print("=" * 80)
    for _, r in acc_sorted.tail(15).iterrows():
        print(f"  {r['ticker']:6s}  accuracy={r['dir_accuracy']:.1%}  n={r['n_obs']:.0f}  "
              f"avg_IV={r['avg_atm_iv']:.3f}  avg_|z|={r['avg_abs_z_score']:.2f}  "
              f"avg_std%={r['avg_expected_std_pct']:.3f}  avg_price=${r['avg_price']:.1f}")
    print()

    reliable = acc[acc['dir_accuracy'] >= 0.65]
    unreliable = acc[acc['dir_accuracy'] <= 0.35]
    middle = acc[(acc['dir_accuracy'] > 0.40) & (acc['dir_accuracy'] < 0.60)]
    print(f"Reliable group (>=65%): {len(reliable)} tickers")
    print(f"Unreliable group (<=35%): {len(unreliable)} tickers")
    print(f"Middle group (40-60%): {len(middle)} tickers")
    print()

    features = [
        ('avg_atm_iv', 'ATM Implied Volatility'),
        ('avg_expected_std_pct', 'Expected Std Dev (% of price)'),
        ('avg_abs_z_score', 'Avg |Z-Score|'),
        ('avg_abs_error_pct', 'Avg Absolute Error %'),
        ('avg_abs_percent_change', 'Avg |Predicted % Change|'),
        ('avg_percent_change', 'Avg Predicted % Change (signed)'),
        ('avg_atm_delta', 'ATM Delta'),
        ('avg_atm_cost', 'ATM Contract Cost ($)'),
        ('avg_volume', 'Avg Trading Volume'),
        ('avg_price', 'Avg Stock Price ($)'),
        ('avg_pcr_volume', 'Put-Call Ratio (Volume)'),
        ('avg_pcr_oi', 'Put-Call Ratio (OI)'),
        ('avg_iv_skew', 'IV Skew (put-call)'),
        ('landed_in_50pct', 'Landed in 50% Interval'),
        ('n_obs', 'Number of Observations'),
    ]

    # Collect feature correlations for return
    feature_correlations_reliability = []
    feature_correlations_accuracy = []

    print("=" * 100)
    print(f"{'Feature':<35s}  {'Reliable(>=65%)':<18s}  {'Middle(40-60%)':<18s}  "
          f"{'Unreliable(<=35%)':<18s}  {'t-stat':>8s}  {'p-value':>8s}")
    print("=" * 100)
    for feat, label in features:
        r_vals = reliable[feat].dropna()
        u_vals = unreliable[feat].dropna()
        m_vals = middle[feat].dropna()
        r_mean = r_vals.mean() if len(r_vals) > 0 else float('nan')
        u_mean = u_vals.mean() if len(u_vals) > 0 else float('nan')
        m_mean = m_vals.mean() if len(m_vals) > 0 else float('nan')
        if len(r_vals) >= 2 and len(u_vals) >= 2:
            t_stat, p_val = stats.ttest_ind(r_vals, u_vals, equal_var=False)
        else:
            t_stat, p_val = float('nan'), float('nan')
        sig = "***" if p_val < 0.001 else "**" if p_val < 0.01 else "*" if p_val < 0.05 else ""
        print(f"  {label:<33s}  {r_mean:>16.4f}  {m_mean:>16.4f}  {u_mean:>16.4f}  "
              f"{t_stat:>8.2f}  {p_val:>7.4f} {sig}")
        feature_correlations_reliability.append({
            'feature': label,
            't_stat': float(t_stat) if not np.isnan(t_stat) else None,
            'p_value': float(p_val) if not np.isnan(p_val) else None
        })
    print()

    print("=" * 80)
    print("CORRELATION: Feature vs Directional Accuracy (across all tickers)")
    print("=" * 80)
    for feat, label in features:
        valid = acc[[feat, 'dir_accuracy']].dropna()
        if len(valid) >= 10:
            corr, p = stats.pearsonr(valid[feat], valid['dir_accuracy'])
            sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""
            print(f"  {label:<35s}  r={corr:>+.4f}  p={p:.4f} {sig}")
            feature_correlations_accuracy.append({
                'feature': label,
                'r': float(corr),
                'p': float(p)
            })
    print()

    # Accuracy by predicted move size
    print("=" * 80)
    print("ACCURACY BY SIZE OF PREDICTED MOVE")
    print("=" * 80)
    realized['abs_pct_change'] = realized['percent change %'].abs()
    bins = [0, 0.5, 1.0, 2.0, 5.0, 100]
    labels_bin = ['<0.5%', '0.5-1%', '1-2%', '2-5%', '>5%']
    realized['move_bucket'] = pd.cut(realized['abs_pct_change'], bins=bins, labels=labels_bin)
    bucket_acc = realized.groupby('move_bucket', observed=True).agg(
        n=('pdf_directional_correct', 'count'),
        accuracy=('pdf_directional_correct', 'mean'),
        avg_iv=('ATM IV', 'mean'),
    ).reset_index()
    for _, r in bucket_acc.iterrows():
        print(f"  {r['move_bucket']:<10s}  n={r['n']:>5.0f}  accuracy={r['accuracy']:.1%}  "
              f"avg_IV={r['avg_iv']:.3f}")
    print()

    # Accuracy by IV bucket
    print("=" * 80)
    print("ACCURACY BY ATM IV LEVEL")
    print("=" * 80)
    iv_bins = [0, 0.15, 0.25, 0.35, 0.50, 5.0]
    iv_labels = ['<15%', '15-25%', '25-35%', '35-50%', '>50%']
    realized['iv_bucket'] = pd.cut(realized['ATM IV'], bins=iv_bins, labels=iv_labels)
    iv_acc = realized.groupby('iv_bucket', observed=True).agg(
        n=('pdf_directional_correct', 'count'),
        accuracy=('pdf_directional_correct', 'mean'),
        avg_abs_pct_change=('percent change %', lambda x: x.abs().mean()),
    ).reset_index()
    for _, r in iv_acc.iterrows():
        print(f"  IV {r['iv_bucket']:<8s}  n={r['n']:>5.0f}  accuracy={r['accuracy']:.1%}  "
              f"avg_|pred_move|={r['avg_abs_pct_change']:.2f}%")
    print()

    # Logistic-style importance
    print("=" * 80)
    print("FEATURE IMPORTANCE (point-biserial correlation, per-observation)")
    print("=" * 80)
    obs_features = ['ATM IV', 'expected_std_pct', 'percent change %',
                    'ATM Delta', 'current_price', 'ATM Contract Cost']
    obs_data = realized[obs_features + ['pdf_directional_correct']].dropna()
    if not obs_data.empty:
        obs_data['abs_pct_change'] = obs_data['percent change %'].abs()
        y = obs_data['pdf_directional_correct']
        for feat in ['ATM IV', 'expected_std_pct', 'abs_pct_change', 'ATM Delta', 'current_price']:
            corr, p = stats.pointbiserialr(y, obs_data[feat])
            sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""
            print(f"  {feat:<25s}  r={corr:>+.4f}  p={p:.4f} {sig}")

    # Return computed statistics
    return {
        'feature_correlations_reliability': feature_correlations_reliability,
        'feature_correlations_accuracy': feature_correlations_accuracy
    }


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Combined S&P 500 options PDF pipeline")
    parser.add_argument("--file", "-f", type=str, default=EXCEL_FILE,
                        help=f"Path to the Excel log file (default: {EXCEL_FILE})")
    parser.add_argument("--no-tracking", action="store_true",
                        help="Skip the tracking stage (use existing Excel data)")
    parser.add_argument("--no-indicator", action="store_true",
                        help="Skip the indicator stage")
    parser.add_argument("--no-zscore", action="store_true",
                        help="Skip the z-score analysis stage")
    parser.add_argument("--no-accuracy", action="store_true",
                        help="Skip the accuracy analysis stage")
    parser.add_argument("--ticker", "-t", nargs="+", default=None,
                        help="Restrict tracking + indicator to these tickers only")
    parser.add_argument("--min-confidence", "-c", type=float, default=DEFAULT_MIN_CONFIDENCE,
                        help="Minimum indicator confidence %% to display (default: 0)")
    parser.add_argument("--no-save", action="store_true",
                        help="Read-only mode: do not write Excel updates")
    args = parser.parse_args()

    excel_path = os.path.join(get_dir(), args.file) if not os.path.isabs(args.file) else args.file

    if not args.no_tracking:
        run_tracking(excel_path, save=not args.no_save, ticker_subset=args.ticker)
    else:
        print("Skipping tracking stage (--no-tracking).")

    if not args.no_indicator:
        run_indicator(excel_path, tickers=args.ticker,
                      min_confidence=args.min_confidence, save=not args.no_save)
    else:
        print("Skipping indicator stage (--no-indicator).")

    if not args.no_zscore:
        run_zscore_analysis(excel_path)
    else:
        print("Skipping z-score analysis stage (--no-zscore).")

    if not args.no_accuracy:
        run_accuracy_analysis(excel_path)
    else:
        print("Skipping accuracy analysis stage (--no-accuracy).")


if __name__ == "__main__":
    main()
