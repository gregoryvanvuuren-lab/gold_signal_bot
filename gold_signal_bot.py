#!/usr/bin/env python3
"""Commodity signal bot (default WTI crude oil): RSI + MACD momentum with ATR stop/target.

Signal-only. It does NOT place, size, or execute any trades — it prints trade
setups for you to act on (or not) yourself. Not financial advice.
"""

import argparse
import sys
from datetime import datetime

import pandas as pd
import yfinance as yf

# ----------------------------------------------------------------------------
# Defaults (override via CLI)
# ----------------------------------------------------------------------------
DEFAULT_SYMBOL = "CL=F"        # WTI crude oil futures (USD). Alt: "BZ=F" (Brent), "USO"
DEFAULT_PERIOD = "3y"          # history to pull for the daily chart
RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
ATR_PERIOD = 14
SMA_TREND = 200                # trend filter: only long above it, only short below
DEFAULT_SL_ATR = 1.5           # stop-loss distance in ATR multiples
DEFAULT_TP_ATR = 3.0           # take-profit distance in ATR multiples
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
LOG_FILE = "signals.log"
DISPLAY_TZ = "Europe/London"   # intraday timestamps shown in UK local time (auto GMT/BST)


# ----------------------------------------------------------------------------
# Indicators (pure pandas, Wilder smoothing where standard)
# ----------------------------------------------------------------------------
def rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def macd(close: pd.Series):
    ema_fast = close.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
    line = ema_fast - ema_slow
    signal = line.ewm(span=MACD_SIGNAL, adjust=False).mean()
    return line, signal, line - signal


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = ATR_PERIOD) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


# ----------------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------------
def fetch(symbol: str, period: str, interval: str = "1d") -> pd.DataFrame:
    df = yf.download(symbol, period=period, interval=interval, auto_adjust=False, progress=False)
    if df is None or df.empty:
        raise SystemExit(f"No data returned for {symbol!r} ({interval}). Check the ticker / connection.")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close"]].dropna()
    if df.index.tz is not None:                      # intraday comes back tz-aware (US Eastern)
        df.index = df.index.tz_convert(DISPLAY_TZ)   # -> UK local time for display
    df["rsi"] = rsi(df["Close"])
    line, sig, hist = macd(df["Close"])
    df["macd"], df["macd_sig"], df["macd_hist"] = line, sig, hist
    df["atr"] = atr(df["High"], df["Low"], df["Close"])
    df["sma_trend"] = df["Close"].rolling(SMA_TREND).mean()  # kept even while NaN (warmup)
    return df.dropna(subset=["rsi", "macd", "macd_sig", "macd_hist", "atr"])


# ----------------------------------------------------------------------------
# Signal engine — RSI + MACD, position-aware, with ATR SL/TP
# ----------------------------------------------------------------------------
def run_engine(df: pd.DataFrame, sl_atr: float, tp_atr: float, allow_shorts: bool,
               trend_filter: bool = True):
    """Walk the series bar-by-bar, producing a position state machine.

    Returns (events, trades, open_position). Long/short entries fire on RSI+MACD
    momentum turns; exits fire on the opposite momentum turn OR SL/TP being hit.
    With trend_filter on, longs only fire above the 200-day SMA and shorts only
    below it (counter-trend entries are suppressed).
    """
    events, trades = [], []
    pos = None  # dict: side, entry_date, entry, stop, target

    rows = df.itertuples()
    prev = next(rows, None)
    if prev is None:
        return events, trades, None

    for r in rows:
        price = r.Close
        # crossover flags (this bar vs previous)
        macd_up = prev.macd_hist <= 0 < r.macd_hist
        macd_dn = prev.macd_hist >= 0 > r.macd_hist
        hist_rising = r.macd_hist > prev.macd_hist
        hist_falling = r.macd_hist < prev.macd_hist
        rsi_up_30 = prev.rsi <= RSI_OVERSOLD < r.rsi
        rsi_dn_70 = prev.rsi >= RSI_OVERBOUGHT > r.rsi

        if pos is None:
            # trend gate: NaN during 200-SMA warmup => allow either side
            no_trend = (not trend_filter) or pd.isna(r.sma_trend)
            trend_ok_long = no_trend or price > r.sma_trend
            trend_ok_short = no_trend or price < r.sma_trend
            long_trig = trend_ok_long and (
                (macd_up and RSI_OVERSOLD < r.rsi < RSI_OVERBOUGHT) or (rsi_up_30 and hist_rising)
            )
            short_trig = allow_shorts and trend_ok_short and (
                (macd_dn and RSI_OVERSOLD < r.rsi < RSI_OVERBOUGHT) or (rsi_dn_70 and hist_falling)
            )
            if long_trig:
                stop = price - sl_atr * r.atr
                target = price + tp_atr * r.atr
                pos = {"side": "LONG", "entry_date": r.Index, "entry": price,
                       "stop": stop, "target": target}
                reason = "MACD bullish cross" if macd_up else "RSI reclaim of oversold"
                events.append({**pos, "action": "LONG ENTRY", "date": r.Index,
                               "price": price, "reason": reason})
            elif short_trig:
                stop = price + sl_atr * r.atr
                target = price - tp_atr * r.atr
                pos = {"side": "SHORT", "entry_date": r.Index, "entry": price,
                       "stop": stop, "target": target}
                reason = "MACD bearish cross" if macd_dn else "RSI rejection from overbought"
                events.append({**pos, "action": "SHORT ENTRY", "date": r.Index,
                               "price": price, "reason": reason})
        else:
            side = pos["side"]
            exit_price, reason = None, None
            if side == "LONG":
                if r.Low <= pos["stop"]:            # stop checked first (conservative)
                    exit_price, reason = pos["stop"], "stop-loss hit"
                elif r.High >= pos["target"]:
                    exit_price, reason = pos["target"], "take-profit hit"
                elif macd_dn or rsi_dn_70:
                    exit_price, reason = price, "MACD/RSI momentum flip down"
            else:  # SHORT
                if r.High >= pos["stop"]:
                    exit_price, reason = pos["stop"], "stop-loss hit"
                elif r.Low <= pos["target"]:
                    exit_price, reason = pos["target"], "take-profit hit"
                elif macd_up or rsi_up_30:
                    exit_price, reason = price, "MACD/RSI momentum flip up"

            if exit_price is not None:
                pts = (exit_price - pos["entry"]) if side == "LONG" else (pos["entry"] - exit_price)
                risk = abs(pos["entry"] - pos["stop"]) or float("nan")
                trades.append({**pos, "exit_date": r.Index, "exit": exit_price,
                               "reason": reason, "points": pts, "r_multiple": pts / risk})
                events.append({"action": f"EXIT {side}", "date": r.Index,
                               "price": exit_price, "reason": reason, "points": pts,
                               "r_multiple": pts / risk})
                pos = None
        prev = r

    return events, trades, pos


# ----------------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------------
def money(x: float) -> str:
    return f"{x:,.2f}"


def build_report(df: pd.DataFrame, events, trades, pos, symbol: str) -> str:
    last = df.iloc[-1]
    last_date = df.index[-1].date()
    out = []
    out.append("=" * 64)
    out.append(f" CRUDE OIL SIGNAL BOT  ·  {symbol}  ·  daily chart")
    out.append(f" As of {last_date}  (signals confirm on the daily close)")
    out.append("=" * 64)
    out.append(
        f" Close {money(last.Close)} | RSI {last.rsi:.1f} | "
        f"MACD hist {last.macd_hist:+.3f} | ATR {money(last.atr)}"
    )
    if not pd.isna(last.sma_trend):
        trend = "ABOVE (uptrend)" if last.Close > last.sma_trend else "BELOW (downtrend)"
        out.append(f" 200-DMA {money(last.sma_trend)} — price {trend}")
    out.append("")

    # today's action = event dated on the latest bar, if any
    todays = [e for e in events if e["date"].date() == last_date]
    if todays:
        e = todays[-1]
        out.append(f" >>> TODAY: {e['action']}  @ {money(e['price'])}  ({e['reason']})")
    else:
        out.append(" >>> TODAY: no new signal — HOLD")
    out.append("")

    # current position / trade plan
    if pos:
        entry = pos["entry"]
        rr = abs(pos["target"] - entry) / (abs(entry - pos["stop"]) or float("nan"))
        unreal = (last.Close - entry) if pos["side"] == "LONG" else (entry - last.Close)
        unreal_r = unreal / (abs(entry - pos["stop"]) or float("nan"))
        out.append(f" OPEN POSITION: {pos['side']}  since {pos['entry_date'].date()}")
        out.append(f"   {'Entry':<12}{money(entry)}")
        out.append(f"   {'Stop-loss':<12}{money(pos['stop'])}   ({abs(entry - pos['stop']):.2f} risk)")
        out.append(f"   {'Take-profit':<12}{money(pos['target'])}   (R:R {rr:.1f})")
        out.append(f"   {'Unrealised':<12}{unreal:+.2f}  ({unreal_r:+.2f}R)")
    else:
        out.append(" OPEN POSITION: flat — waiting for the next setup.")
    out.append("")

    # last few signals
    out.append(" Recent signals:")
    for e in events[-5:]:
        extra = ""
        if "r_multiple" in e:
            extra = f"  ->  {e['points']:+.2f} pts ({e['r_multiple']:+.2f}R)"
        out.append(f"   {e['date'].date()}  {e['action']:<12} @ {money(e['price'])}  ({e['reason']}){extra}")
    if not events:
        out.append("   (none in this window)")
    out.append("=" * 64)
    return "\n".join(out)


def backtest_summary(trades) -> str:
    if not trades:
        return "\nBacktest: no closed trades in this window."
    wins = [t for t in trades if t["points"] > 0]
    total_r = sum(t["r_multiple"] for t in trades)
    gross_win = sum(t["points"] for t in wins)
    gross_loss = -sum(t["points"] for t in trades if t["points"] <= 0)
    pf = (gross_win / gross_loss) if gross_loss else float("inf")
    lines = [
        "",
        "-" * 64,
        " BACKTEST (this data window, signal-close fills, no costs/slippage)",
        "-" * 64,
        f" Trades {len(trades)} | Win rate {len(wins)/len(trades)*100:.0f}% | "
        f"Total {total_r:+.1f}R | Profit factor {pf:.2f}",
        "",
    ]
    for t in trades[-12:]:
        lines.append(
            f"   {t['entry_date'].date()} -> {t['exit_date'].date()}  "
            f"{t['side']:<5} {money(t['entry'])} -> {money(t['exit'])}  "
            f"{t['points']:+.2f} pts ({t['r_multiple']:+.2f}R)  {t['reason']}"
        )
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="Commodity daily RSI+MACD signal bot (signal-only).")
    p.add_argument("--symbol", default=DEFAULT_SYMBOL, help=f"ticker (default {DEFAULT_SYMBOL})")
    p.add_argument("--period", default=DEFAULT_PERIOD, help=f"history window (default {DEFAULT_PERIOD})")
    p.add_argument("--sl-atr", type=float, default=DEFAULT_SL_ATR, help="stop-loss in ATR mult")
    p.add_argument("--tp-atr", type=float, default=DEFAULT_TP_ATR, help="take-profit in ATR mult")
    p.add_argument("--no-shorts", action="store_true", help="long-only signals")
    p.add_argument("--no-trend-filter", action="store_true", help="disable 200-DMA trend filter")
    p.add_argument("--backtest", action="store_true", help="print trade-by-trade backtest")
    p.add_argument("--no-log", action="store_true", help="do not append to signals.log")
    args = p.parse_args()

    df = fetch(args.symbol, args.period)
    events, trades, pos = run_engine(
        df, args.sl_atr, args.tp_atr, not args.no_shorts, not args.no_trend_filter
    )
    report = build_report(df, events, trades, pos, args.symbol)
    print(report)
    if args.backtest:
        print(backtest_summary(trades))

    if not args.no_log:
        last_date = df.index[-1].date()
        todays = [e for e in events if e["date"].date() == last_date]
        action = todays[-1]["action"] if todays else ("HOLD " + (pos["side"] if pos else "FLAT"))
        with open(LOG_FILE, "a") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M}  {args.symbol}  "
                     f"close={df.iloc[-1].Close:.2f}  {action}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
