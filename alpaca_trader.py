"""
alpaca_trader.py — Turn today's indicator signals into Alpaca PAPER share trades.

Reads the Excel log written by stock_analyzer.py, selects today's actionable
UP/DOWN signals that pass the filters below, sizes them, and submits bracket
orders (stop-loss / take-profit scaled by the option-implied expected move).
Positions older than --max-hold-days are closed. Every decision is appended to
a CSV so results can be compared against the simulated columns in the log.

Safety: paper endpoint only (live URLs are refused) and DRY-RUN by default;
pass --execute to actually submit orders.

Env vars: ALPACA_API_KEY, ALPACA_SECRET_KEY
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
EXCEL_FILE = "sp500_options_analysis.xlsx"
TRADE_LOG = "alpaca_trade_log.csv"

# Entry filters
MIN_CONFIDENCE = 60.0
MAX_ATM_SPREAD_PCT = 0.10      # skip illiquid option markets (proxy for noisy signal)
MIN_ATM_OI = 100
SKIP_EARNINGS_IN_WINDOW = True
MIN_STOCK_PRICE = 5.0

# Risk / sizing
RISK_PER_TRADE_PCT = 0.005     # equity risked per trade if stop is hit
MAX_POSITION_PCT = 0.05        # cap per-position notional as % of equity
MAX_OPEN_POSITIONS = 10
STOP_STD_MULT = 0.75           # stop distance = mult * expected_std
TARGET_STD_MULT = 1.0          # take-profit distance = mult * expected_std


class Alpaca:
    def __init__(self, key, secret, base_url=PAPER_BASE_URL):
        if "paper" not in base_url:
            raise ValueError("Refusing to use a non-paper Alpaca endpoint.")
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})

    def _req(self, method, path, **kw):
        r = self.s.request(method, f"{self.base}{path}", timeout=30, **kw)
        r.raise_for_status()
        return r.json() if r.text else None

    def account(self):
        return self._req("GET", "/v2/account")

    def positions(self):
        return self._req("GET", "/v2/positions")

    def open_orders(self):
        return self._req("GET", "/v2/orders", params={"status": "open", "limit": 500})

    def asset(self, symbol):
        return self._req("GET", f"/v2/assets/{symbol}")

    def submit(self, payload):
        return self._req("POST", "/v2/orders", json=payload)

    def close_position(self, symbol):
        return self._req("DELETE", f"/v2/positions/{symbol}")


def alpaca_symbol(ticker: str) -> str:
    return ticker.replace("-", ".")


def select_signals(df: pd.DataFrame, today: str, allow_short: bool) -> pd.DataFrame:
    """Today's UP/DOWN signals that pass the quality filters."""
    d = df[df["date"].astype(str).str[:10] == today].copy()
    d = d[d["indicator_direction"].isin(["UP", "DOWN"] if allow_short else ["UP"])]
    d = d[d["indicator_confidence"] >= MIN_CONFIDENCE]
    d = d[d["current_price"] >= MIN_STOCK_PRICE]
    if "atm_call_spread_pct" in d:
        d = d[d["atm_call_spread_pct"].isna() | (d["atm_call_spread_pct"] <= MAX_ATM_SPREAD_PCT)]
    if "atm_call_oi" in d:
        d = d[d["atm_call_oi"].isna() | (d["atm_call_oi"] >= MIN_ATM_OI)]
    if SKIP_EARNINGS_IN_WINDOW and "earnings_in_window" in d:
        d = d[~d["earnings_in_window"].fillna(False).astype(bool)]
    d = d[d["expected_std"] > 0]
    return d.sort_values("indicator_confidence", ascending=False)


def build_order(row, equity, buying_power):
    """Bracket order sized so a stop-out loses ~RISK_PER_TRADE_PCT of equity."""
    price, std = float(row["current_price"]), float(row["expected_std"])
    long = row["indicator_direction"] == "UP"
    stop_dist, tgt_dist = STOP_STD_MULT * std, TARGET_STD_MULT * std
    if stop_dist <= 0:
        return None
    qty = int(min(equity * RISK_PER_TRADE_PCT / stop_dist,
                  equity * MAX_POSITION_PCT / price,
                  buying_power / price))
    if qty < 1:
        return None
    sign = 1 if long else -1
    stop = round(price - sign * stop_dist, 2)
    take = round(price + sign * tgt_dist, 2)
    if stop <= 0:
        return None
    return {
        "symbol": alpaca_symbol(row["ticker"]),
        "qty": str(qty),
        "side": "buy" if long else "sell",
        "type": "market",
        "time_in_force": "day",
        "order_class": "bracket",
        "take_profit": {"limit_price": str(take)},
        "stop_loss": {"stop_price": str(stop)},
    }


def close_stale_positions(api, trade_log_path, max_hold_days, execute):
    """Close positions opened more than max_hold_days ago per our trade log."""
    if not os.path.exists(trade_log_path):
        return
    log = pd.read_csv(trade_log_path)
    log = log[log["action"] == "ENTER"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_hold_days)
    held = {p["symbol"] for p in api.positions()}
    for _, r in log.iterrows():
        sym = alpaca_symbol(r["ticker"])
        opened = pd.Timestamp(r["timestamp"]).tz_localize("UTC") if pd.Timestamp(r["timestamp"]).tzinfo is None \
            else pd.Timestamp(r["timestamp"])
        if sym in held and opened <= cutoff:
            print(f"[{sym}] held > {max_hold_days}d, closing")
            if execute:
                api.close_position(sym)
            append_log(trade_log_path, {"timestamp": datetime.now(timezone.utc).isoformat(),
                                        "ticker": r["ticker"], "action": "EXIT_TIME", "executed": execute})
            held.discard(sym)


def append_log(path, record):
    pd.DataFrame([record]).to_csv(path, mode="a", header=not os.path.exists(path), index=False)


def run(excel_path, trade_log_path, execute, allow_short, max_hold_days):
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        sys.exit("Set ALPACA_API_KEY and ALPACA_SECRET_KEY (paper account keys).")
    api = Alpaca(key, secret, os.environ.get("ALPACA_BASE_URL", PAPER_BASE_URL))

    acct = api.account()
    equity, bp = float(acct["equity"]), float(acct["buying_power"])
    print(f"Paper account equity=${equity:,.2f} buying_power=${bp:,.2f} "
          f"mode={'EXECUTE' if execute else 'DRY-RUN'}")

    close_stale_positions(api, trade_log_path, max_hold_days, execute)

    df = pd.read_excel(excel_path)
    for c in ("indicator_direction", "indicator_confidence"):
        if c not in df.columns:
            sys.exit(f"Missing '{c}' in log; run stock_analyzer.py (indicator stage) first.")
    today = datetime.today().strftime("%Y-%m-%d")
    signals = select_signals(df, today, allow_short)
    print(f"{len(signals)} signals passed filters for {today}")

    held = {p["symbol"] for p in api.positions()} | {o["symbol"] for o in api.open_orders()}
    slots = MAX_OPEN_POSITIONS - len({p["symbol"] for p in api.positions()})

    for _, row in signals.iterrows():
        if slots <= 0:
            break
        sym = alpaca_symbol(row["ticker"])
        if sym in held:
            continue
        order = build_order(row, equity, bp)
        if order is None:
            continue
        if order["side"] == "sell":
            try:
                a = api.asset(sym)
                if not (a.get("shortable") and a.get("easy_to_borrow")):
                    continue
            except requests.HTTPError:
                continue
        rec = {"timestamp": datetime.now(timezone.utc).isoformat(), "ticker": row["ticker"],
               "action": "ENTER", "side": order["side"], "qty": order["qty"],
               "signal_price": row["current_price"], "stop": order["stop_loss"]["stop_price"],
               "target": order["take_profit"]["limit_price"],
               "confidence": row["indicator_confidence"], "strategy": row.get("strategy_recommendation"),
               "expiration": row["analyzed option expiration"], "executed": execute, "order_id": None}
        print(f"[{sym}] {order['side']} {order['qty']} stop={rec['stop']} target={rec['target']}")
        if execute:
            try:
                rec["order_id"] = api.submit(order)["id"]
            except requests.HTTPError as e:
                print(f"[{sym}] order rejected: {e.response.text}")
                continue
        append_log(trade_log_path, rec)
        bp -= float(order["qty"]) * float(row["current_price"])
        slots -= 1


def main():
    p = argparse.ArgumentParser(description="Alpaca PAPER trading from options signals")
    p.add_argument("--file", "-f", default=EXCEL_FILE)
    p.add_argument("--trade-log", default=TRADE_LOG)
    p.add_argument("--execute", action="store_true", help="Submit orders (default: dry run)")
    p.add_argument("--allow-short", action="store_true", help="Also trade DOWN signals")
    p.add_argument("--max-hold-days", type=int, default=7)
    a = p.parse_args()
    run(a.file, a.trade_log, a.execute, a.allow_short, a.max_hold_days)


if __name__ == "__main__":
    main()
