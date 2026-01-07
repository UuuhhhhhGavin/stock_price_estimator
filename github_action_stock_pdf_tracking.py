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



# %%


def get_dir():
    project_path = os.getcwd()
    return project_path


def safe_get_history(ticker: str, period: str = '1d', retries=5, delay=5) -> pd.DataFrame:
    for i in range(retries):
        try:
            stock = yf.Ticker(ticker)
            hist=stock.history(period=period)
            time.sleep(1)
            return stock, hist
        except YFRateLimitError:
            wait=delay*(2**i) #exponential backoff
            print(f'Rate limit hit for {ticker}, waiting {wait} seconds')
            time.sleep(wait)
        except Exception as e:
            print(f'Other error for {ticker}: {e}')
            break
    return None, None

#hist=safe_get_history("APPL")
#if hist is not None:
#    current_price=hist['Close'][0]

# %%
def get_sp500_tickers():
    """Fetches the list of S&P 500 tickers from Wikipedia"""
    ##Outputs list of tickers: ['MMM', 'AOS'.....]
    url='https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    table=pd.read_html(url)
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
        current_price = stock_hist['Close'][0] if stock_hist is not None else None # gets the current price of the stock (closing 1day price)
        # Extract trading volume if available
        current_volume = None
        try:
            if stock_hist is not None and 'Volume' in stock_hist.columns:
                current_volume = int(stock_hist['Volume'][0])
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
            days_forward=int(days_difference),
            risk_free_rate=0.03,
            fit_kernel_pdf=True,
        )
        os.remove(temp_csv_path)

        ##Normalize the PDF
        normalized_pdf = ticker_pdf.PDF / ticker_pdf.PDF.sum()
        expected_price=(ticker_pdf.Price * normalized_pdf).sum()


        #Find the option strike closest to the current price
        atm_option = calls.iloc[(calls['strike'] - current_price).abs().argsort()[:1]]
        atm_strike = atm_option['strike'].values[0] #strike price for at-the-money option
        iv = atm_option['impliedVolatility'].values[0] #implied volatility for at-the-money option
        atm_cost = atm_option['last_price'].values[0] #cost of at-the-money option
        #Compute the delta
        T=days_difference/365
        sigma=iv
        atm_delta=compute_stock_delta(current_price,atm_strike,T,0.03,sigma)  


        return {
            'date' : current_date,
            'ticker': ticker,
            'analyzed option expiration': expiration,
            'weeks from today': round(days_difference/7,2),
            'current_price': current_price,
            'current_volume': current_volume,
            'expected_price': expected_price,
            'percent increase %': (expected_price - current_price) / current_price * 100,
            'ATM Strike Price': atm_strike,
            'ATM IV': iv,
            'ATM Contract Cost': atm_cost,
            'ATM Delta': atm_delta,
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


if __name__=='__main__':
    stock_overview=[]
    #tickers=list(set(get_sp500_tickers()))
    tickers=['TSLA',"AAPL","MSFT","AMZN","GOOGL","META","NVDA","JPM","SMR","OKLO"] #For testing purposes
    print(f'Analyzing {len(tickers)} tickers...')
    for ticker in tickers:
         try:
             stock, stock_hist = safe_get_history(ticker, period='1d') #gets the 1Day trading information on the stock
             if stock is None or stock_hist is None:
                print(f'[{ticker}] Failed to fetch stock data.')
                continue
             options_chain, calls, expirations = get_options_chain(stock, stock_hist, expiration_week=1)
             print(f'[{ticker}] Expirations: {expirations}')
             if options_chain is None or calls is None:
                print(f'[{ticker}] No options data available.')
                continue
             expiration=expirations[1]
             result = analyze_ticker(ticker, stock, stock_hist, options_chain, calls, expiration)
             if result is not None:
                for key, value in result.items():
                    print(f'{key.title()}:{value}')
                stock_overview.append(result)
                print("\n")
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
            project_dir=get_dir()
            output_filename = f"sp500_options_analysis.xlsx"
            file_path = os.path.join(project_dir,output_filename)
            for result in stock_overview:
                # Append to daily log (will skip duplicates for same date+ticker+expiration)
                try:
                    append_daily_log(result, file_path, output_filename)
                except Exception as e:
                    print(f"Failed to log result for {ticker}: {e}")
           
        else:
            print("No data to save.....")




