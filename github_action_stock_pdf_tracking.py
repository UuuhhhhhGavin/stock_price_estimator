###Tool for analyzing the entire Snp500 options chain via Probability Density Functions
# %%
from oipd import cli
from datetime import datetime
import matplotlib.pyplot as plt
import yfinance as yf
import pandas as pd
import tempfile
import os
import time
#import multiprocessing
from yfinance.exceptions import YFRateLimitError
#import requests
import numpy as np
from scipy.stats import norm
import requests


def get_dir():
    project_path = os.getcwd()
    return project_path


def safe_get_history(ticker: str, period: str = '1d', retries=5, delay=5) -> pd.DataFrame:
    stock=yf.Ticker(ticker)
    for i in range(retries):
        try:
            hist=stock.history(period=period)
            return stock, hist
        except YFRateLimitError:
            wait=delay*(2**i) #exponential backoff
            print(f'Rate limit hit for {ticker}, waiting {wait} seconds')
            time.sleep(wait)
        except Exception as e:
            print(f'Other error for {ticker}: {e}')
            break
    return None, None

def bulk_get_history(tickers, period='1d', retries=5, delay=5):
    for i in range(retries):
        try:
            df = yf.download(tickers, period=period, group_by='ticker', threads=True)
            return df
        except YFRateLimitError:
            wait = delay * (2 ** i)
            print(f'Rate limit hit for bulk download, waiting {wait} seconds')
            time.sleep(wait)
        except Exception as e:
            print(f'Other error for bulk download: {e}')
            break
    return None




#hist=safe_get_history("APPL")
#if hist is not None:
#    current_price=hist['Close'].iloc[-1]

# %%
def get_sp500_tickers():
    """Fetches the list of S&P 500 tickers from Wikipedia"""
    ##Outputs list of tickers: ['MMM', 'AOS'.....]
    url='https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    header = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.75 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest"
    }
    r=requests.get(url, headers=header)
    table=pd.read_html(r.text)
    df=table[0]
    return df['Symbol'].tolist()
#tickers=get_sp500_tickers()
#%%
def get_options_chain(stock, stock_hist, expiration_week:int)-> pd.DataFrame:
    expirations = stock.options ##tuple of expiration dates: ('2025-05-02', '2025-05-09', '2025-05-16', ...... ) 

    #if len(expirations) < 5:
    #    return None  # skip if not enough options data
    expiration = expirations[expiration_week] #generally 1 weeks away
    options_chain = stock.option_chain(expiration) #provides lots of data... all options contracts that occurred today
    calls = options_chain.calls # read the options chain!
    calls = calls[['strike', 'bid', 'ask', 'lastPrice', 'impliedVolatility']].dropna().copy()  #choose which data from the options chain to keep
    calls.rename(columns={'lastPrice': 'last_price'}, inplace=True) #PDF needs the last price renamed

    return options_chain, calls, expirations

#%%

def analyze_ticker(ticker, stock, stock_hist,options_chain, calls, expiration,):
    '''generates a PDF for the given ticker and expiration date'''
    ##ticker: 
    ##stock: from safe_get_history()
    ##stock_hist: from safe_get_history()
    ##options_chain: from get_options_chain()
    ##expiration: expiration date for the options chain, used for days_difference

    try:
        if stock is None:
            print(f"[{ticker}] Failed to fetch stock data.")
            return None
        ##Option for period are: '1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', '10y', 'ytd', 'max'
        # Robustly get the latest close and volume whether stock_hist is a DataFrame, Series, or has MultiIndex columns
        current_price = None
        current_volume = None
        try:
            if stock_hist is not None:
                # Case 1: DataFrame with simple columns
                if isinstance(stock_hist, pd.DataFrame) and 'Close' in stock_hist.columns:
                    col = stock_hist['Close']
                    current_price = float(col.iloc[-1]) if hasattr(col, 'iloc') else float(col)
                # Case 2: DataFrame with MultiIndex columns from yf.download when auto_adjust=True
                elif isinstance(stock_hist, pd.DataFrame) and isinstance(stock_hist.columns, pd.MultiIndex):
                    # find first 'Close' column
                    close_cols = [c for c in stock_hist.columns if c[0] == 'Close']
                    if close_cols:
                        current_price = float(stock_hist[close_cols[0]].iloc[-1])
                    else:
                        # fallback to last row's 'Close' if present
                        try:
                            current_price = float(stock_hist.iloc[-1]['Close'])
                        except Exception:
                            current_price = None
                # Case 3: Series-like (single column dataframe or series)
                elif hasattr(stock_hist, 'iloc'):
                    try:
                        # If it's a series of close prices
                        current_price = float(stock_hist['Close'].iloc[-1]) if 'Close' in stock_hist else float(stock_hist.iloc[-1])
                    except Exception:
                        current_price = None
        except Exception:
            current_price = None

        # Volume
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

        # Check for empty DataFrame
        if calls.empty:
            return None
        
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as temp_file: #OIPD library needs a CSV to run the PDF through
            temp_csv_path = temp_file.name #I don't want to store data in a CSV!!! it's a temp file now
            calls.to_csv(temp_csv_path, index=False)

        current_date = datetime.today().strftime('%Y-%m-%d')
        days_difference = (datetime.strptime(expiration, "%Y-%m-%d") - datetime.today()).days

        ticker_pdf = cli.generate_pdf.run(
            input_csv_path=temp_csv_path,
            current_price=float(current_price),
            days_forward=max(days_difference*5/7,1), #adjusts for weekends
            risk_free_rate=0.03,
            fit_kernel_pdf=True,
        )
        os.remove(temp_csv_path)

        ##Normalize the PDF
        normalized_pdf = ticker_pdf.PDF / ticker_pdf.PDF.sum()
        expected_price = float((ticker_pdf.Price * normalized_pdf).sum())

        # Expected standard deviation & percent (for calibration/regime filtering)
        expected_std = float(np.sqrt(((ticker_pdf.Price - expected_price) ** 2 * normalized_pdf).sum()))
        expected_std_pct = float(expected_std / current_price) if current_price else None

        # Percentiles (25%, 50%, 75%) for interval checks
        cdf = np.cumsum(normalized_pdf)
        p25 = float(ticker_pdf.Price[np.searchsorted(cdf, 0.25)])
        p50 = float(ticker_pdf.Price[np.searchsorted(cdf, 0.50)])
        p75 = float(ticker_pdf.Price[np.searchsorted(cdf, 0.75)])
        liquid_calls=calls[(calls['bid']>0) &(calls['ask']>0) &(calls['impliedVolatility']>0)]
        if liquid_calls.empty:
            return None
        #Find the option strike closest to the current price
        atm_option = liquid_calls.iloc[(liquid_calls['strike'] - current_price).abs().argsort()[:1]]
        atm_strike = float(atm_option['strike'].values[0]) #strike price for at-the-money option
        iv = float(atm_option['impliedVolatility'].values[0]) #implied volatility for at-the-money option
        atm_cost = float(atm_option['last_price'].values[0]) #cost of at-the-money option
        #Compute the delta
        T=days_difference/365  #days in a year
        sigma=iv
        atm_delta = float(compute_stock_delta(current_price,atm_strike,T,0.03,sigma))


        return {
            'date' : current_date,
            'ticker': ticker,
            'analyzed option expiration': expiration,
            'weeks from today': round(days_difference/7,2),
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
            'pdf_directional_correct': None,
            'z_score': None,
            }

    except Exception as e:
        print(f"[{ticker}] Failed: {e}")
        return None

#analyze_ticker('TSLA')
# %%

def compute_stock_delta(S,K,T,r,sigma,option_type='call'):
    """Computes the delta of a stock based on its options chain"""
    d1= (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    if option_type=='call':
        return norm.cdf(d1)
    else:
        return -norm.cdf(-d1)


def load_log(path: str) -> pd.DataFrame:
    """Load the existing Excel log and return a DataFrame (or empty DataFrame if missing/invalid)."""
    if os.path.exists(path):
        try:
            return pd.read_excel(path)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def merge_results(existing: pd.DataFrame, new_results: list) -> pd.DataFrame:
    """Merge new results into the existing DataFrame and de-duplicate by (date, ticker, analyzed option expiration).

    New results overwrite older rows for the same (date, ticker, expiration).
    """
    new_df = pd.DataFrame(new_results)
    new_df['logged_at'] = datetime.today().strftime('%Y-%m-%d %H:%M:%S')

    if existing.empty:
        combined = new_df
    else:
        combined = pd.concat([existing, new_df], ignore_index=True)

    dedup_cols = ['date', 'ticker', 'analyzed option expiration']
    combined = combined.drop_duplicates(subset=dedup_cols, keep='last')
    return combined


def save_log(df: pd.DataFrame, path: str):
    """Atomically save the DataFrame to an Excel file by writing to a temp file then replacing.
    Uses a temp file with a proper '.xlsx' suffix (required by Excel writer engine).
    """
    import tempfile as _tempfile
    # Create a real temporary file with the required .xlsx extension
    tmp_file = _tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
    tmp_path = tmp_file.name
    tmp_file.close()
    with pd.ExcelWriter(tmp_path, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    # Atomic replace
    os.replace(tmp_path, path)

def get_realized_price(ticker, expiration_date, lookback_days=5):
    """
    Returns the close price on expiration date,
    or last trading day before expiration.
    """
    """
    (Legacy) Single-ticker realized price lookup kept for compatibility.
    Prefer using `bulk_get_realized_prices` for many tickers.
    """
    expiration = pd.to_datetime(expiration_date)
    start = expiration - pd.Timedelta(days=lookback_days)
    end = expiration + pd.Timedelta(days=1)

    hist = yf.download(
        ticker,
        start=start.strftime('%Y-%m-%d'),
        end=end.strftime('%Y-%m-%d'),
        progress=False
    )

    if hist is None or getattr(hist, 'empty', True):
        return None

    hist = hist.sort_index()
    valid = hist[hist.index <= expiration]

    if valid.empty:
        return None

    return float(valid['Close'].iloc[-1])


def update_realized_prices(df: pd.DataFrame) -> pd.DataFrame:
    """
    Backfills realized prices and evaluation metrics for rows
    where expiration has passed and realized_price is missing.
    """
    if df.empty:
        return df
    today = pd.Timestamp.today().normalize()
    df['analyzed option expiration'] = pd.to_datetime(
        df['analyzed option expiration'], errors='coerce'
    )

    # Rows eligible for update (expired and missing realized_price)
    mask = (
        df['realized_price'].isna() &
        df['analyzed option expiration'].notna() &
        (df['analyzed option expiration'] < today)
    )
    if not mask.any():
        return df

    subset = df.loc[mask].copy()
    # Prepare download window: earliest start and latest end across all expirations
    lookback_days = 5
    subset['start'] = subset['analyzed option expiration'] - pd.Timedelta(days=lookback_days)
    overall_start = subset['start'].min().strftime('%Y-%m-%d')
    overall_end = (subset['analyzed option expiration'] + pd.Timedelta(days=1)).max().strftime('%Y-%m-%d')
    tickers = subset['ticker'].unique().tolist()

    try:
        # Bulk download for all required tickers in one call (much faster than per-row queries)
        hist_all = yf.download(tickers, start=overall_start, end=overall_end, progress=False, group_by='ticker', threads=True)
    except Exception as e:
        print(f"Failed to bulk download realized prices: {e}")
        return df

    def _get_close_series(hist_all_obj, tk):
        if hist_all_obj is None or getattr(hist_all_obj, 'empty', False):
            return None
        # If multi-ticker download with per-ticker grouping
        if isinstance(hist_all_obj.columns, pd.MultiIndex):
            try:
                return hist_all_obj[tk]['Close']
            except Exception:
                # fallback if ticker not present as top-level column
                return None
        # Single-ticker or other format
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

    # Apply realized prices in bulk
    idxs = list(realized_map.keys())
    df.loc[idxs, 'realized_price'] = [realized_map[i] for i in idxs]

    # Vectorized metrics updates for affected rows
    cur = df.loc[idxs, 'current_price']
    exp_price = df.loc[idxs, 'expected_price']
    realized = df.loc[idxs, 'realized_price']

    df.loc[idxs, 'pdf_directional_correct'] = ((exp_price - cur) * (realized - cur)) > 0

    # landed_in_50_pct_interval (use apply across affected rows; small compared to many network calls)
    def _in_50(row):
        if pd.notna(row.get('p25')) and pd.notna(row.get('p75')):
            return row['p25'] <= row['realized_price'] <= row['p75']
        return None

    df.loc[idxs, 'landed_in_50_pct_interval'] = df.loc[idxs].apply(_in_50, axis=1)

    df.loc[idxs, 'abs_error_pct'] = (abs(df.loc[idxs, 'realized_price'] - df.loc[idxs, 'expected_price']) / df.loc[idxs, 'current_price']) * 100

    # z-score where expected_std > 0
    stds = df.loc[idxs, 'expected_std'].fillna(0)
    nonzero = stds > 0
    z = pd.Series(index=idxs, dtype=float)
    z.loc[nonzero.index[nonzero]] = (df.loc[idxs, 'realized_price'] - df.loc[idxs, 'expected_price'])[nonzero] / stds[nonzero]
    df.loc[idxs, 'z_score'] = z

    return df


def append_daily_log(result: dict, output_path: str, output_filename:str):
    """Append a single result dict to an Excel log, but only once per day per ticker+expiration.

    Unique key: (`date`, `ticker`, `analyzed option expiration`). If an entry already exists
    for that key, the function will skip appending to avoid duplicates when the script is
    run multiple times in the same day.
    """
    try:
        row = pd.DataFrame([result])
        row['logged_at'] = datetime.today().strftime('%Y-%m-%d %H:%M:%S')

        if os.path.exists(output_path):
            try:
                existing = pd.read_excel(output_path)
            except Exception:
                existing = pd.DataFrame()
            if not existing.empty:
                mask = (
                    (existing.get('date') == result.get('date')) &
                    (existing.get('ticker') == result.get('ticker')) &
                    (existing.get('analyzed option expiration') == result.get('analyzed option expiration'))
                )
                if mask.any():
                    print(f"Log entry already exists for {result.get('ticker')} on {result.get('date')} (expiration {result.get('analyzed option expiration')}). Skipping.")
                    return
                combined = pd.concat([existing, row], ignore_index=True)
            else:
                combined = row
        else:
            combined = row

        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            combined.to_excel(writer, index=False)
        print(f"Logged result to {output_path}")
    except Exception as e:
        print(f"Failed to append log: {e}")


if __name__ == '__main__':
    stock_overview = []
    #tickers = ['TSLA', 'AAPL', 'MSFT', 'AMZN', 'GOOGL', 'META', 'NVDA', 'JPM', 'V', 'UNH']
    tickers=get_sp500_tickers()
    print(f'Analyzing {len(tickers)} tickers...')

    # 1. Bulk price data
    bulk_hist = bulk_get_history(tickers, period='1d')

    for ticker in tickers:
        try:
            # 2. Create Ticker object (needed for options)
            stock = yf.Ticker(ticker)
            # 3. Extract that ticker’s price history from the bulk DataFrame
            stock_hist = bulk_hist[ticker]
            # 4. Options chain
            options_chain, calls, expirations = get_options_chain(
                stock, stock_hist, expiration_week=1
            )
            print(f'[{ticker}] Expirations: {expirations}')
            expiration = expirations[1]
            # 5. Analysis
            result = analyze_ticker(
                ticker, stock, stock_hist, options_chain, calls, expiration
            )
            if result is not None:
                for key, value in result.items():
                    print(f'{key.title()}: {value}')
                stock_overview.append(result)
                print()
        except Exception as e:
            print(f"[{ticker}] Error: {e}")
            continue
        
    #%%
    # Save the results to a CSV file
    #stock_overview=[1,2,3,4,5]
    save=True
    if not save:
        print("Saving is disabled, not saving the data to a file.")
        stock_overview=None
    else:

        print("Saving is enabled, saving the data to a file.")
        if stock_overview:
            project_dir = get_dir()
            output_filename = "sp500_options_analysis.xlsx"
            file_path = os.path.join(project_dir, output_filename)

            # Load existing sheet once, merge all new results, then save atomically
            try:
                existing = load_log(file_path)
            except Exception:
                existing = pd.DataFrame()

            try:
                merged = merge_results(existing, stock_overview)
                merged=update_realized_prices(merged)
                save_log(merged, file_path)
                print(f"Saved merged log to {file_path} ({len(merged)} rows).")
            except Exception as e:
                print(f"Failed to save merged log: {e}")
           
        else:
            print("No data to save.....")
