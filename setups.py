"""Setup scanner — flags which common trade setups are active/forming on the
latest daily bar. Educational context, not a trade instruction.

scan(df) takes the DataFrame from gold_signal_bot.fetch() (already has rsi,
macd, macd_sig, macd_hist, atr, sma_trend) and returns (findings, enriched_df).
Each finding: {name, direction: bull|bear|neutral, status: active|forming, note}.
"""

import pandas as pd

GLOSSARY = [
    ("Trend regime (50/200)",
     "Is the 50-day average above or below the 200-day? Above = uptrend backdrop "
     "(lean long); below = downtrend backdrop (lean short)."),
    ("20-day breakout / breakdown",
     "Price closing above the highest high of the last 20 days (breakout, long) or "
     "below the lowest low (breakdown, short) — a momentum continuation move."),
    ("Trend pullback",
     "In an uptrend, price dips back to a short moving average then turns up "
     "(buy-the-dip). Mirror in a downtrend (sell-the-rally)."),
    ("RSI reversal",
     "RSI below 30 = oversold (bounce candidate); above 70 = overbought (fade "
     "candidate). Strongest when it agrees with the trend."),
    ("MACD momentum cross",
     "MACD line crossing its signal line — momentum turning up (bull) or down "
     "(bear). This is the core trigger of the signal engine."),
    ("Bollinger squeeze / stretch",
     "Bands unusually tight = low volatility, a bigger move often follows. A close "
     "outside a band = over-extended, watch for mean reversion."),
]


def _enrich(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["sma20"] = d["Close"].rolling(20).mean()
    d["sma50"] = d["Close"].rolling(50).mean()
    d["sma200"] = d["Close"].rolling(200).mean()
    d["don_hi"] = d["High"].rolling(20).max().shift(1)   # prior 20-day high
    d["don_lo"] = d["Low"].rolling(20).min().shift(1)
    mid = d["sma20"]
    std = d["Close"].rolling(20).std()
    d["bb_up"] = mid + 2 * std
    d["bb_lo"] = mid - 2 * std
    d["bb_w"] = (d["bb_up"] - d["bb_lo"]) / mid
    return d


def _ok(*vals) -> bool:
    return not any(pd.isna(v) for v in vals)


def scan(df: pd.DataFrame):
    d = _enrich(df)
    if len(d) < 2:
        return [], d
    last, prev = d.iloc[-1], d.iloc[-2]
    close = last.Close
    out = []

    def add(name, direction, status, note):
        out.append({"name": name, "direction": direction, "status": status, "note": note})

    # 1. Trend regime (50 vs 200)
    if _ok(last.sma50, last.sma200):
        if last.sma50 > last.sma200:
            add("Trend regime (50/200)", "bull", "active",
                "50-day above 200-day — uptrend backdrop; favour longs / buy-the-dip.")
        else:
            add("Trend regime (50/200)", "bear", "active",
                "50-day below 200-day — downtrend backdrop; favour shorts / sell-the-rally.")

    # 2. Breakout / breakdown (20-day Donchian)
    if _ok(last.don_hi) and close > last.don_hi:
        add("20-day breakout", "bull", "active",
            f"Close broke above the 20-day high ({last.don_hi:,.0f}) — continuation long.")
    elif _ok(last.don_lo) and close < last.don_lo:
        add("20-day breakdown", "bear", "active",
            f"Close broke below the 20-day low ({last.don_lo:,.0f}) — continuation short.")
    elif _ok(last.don_hi) and close > 0.99 * last.don_hi:
        add("20-day breakout", "bull", "forming", "Pressing the 20-day high — breakout brewing.")
    elif _ok(last.don_lo) and close < 1.01 * last.don_lo:
        add("20-day breakdown", "bear", "forming", "Pressing the 20-day low — breakdown brewing.")

    # 3. Trend pullback (continuation)
    if _ok(last.sma200, last.sma20):
        if close > last.sma200 and close <= last.sma20 and 40 <= last.rsi <= 55 and last.rsi > prev.rsi:
            add("Bull pullback", "bull", "active",
                "Uptrend + dip to the 20-day MA + RSI turning up — buy-the-dip.")
        elif close < last.sma200 and close >= last.sma20 and 45 <= last.rsi <= 60 and last.rsi < prev.rsi:
            add("Bear pullback", "bear", "active",
                "Downtrend + bounce to the 20-day MA + RSI rolling over — sell-the-rally.")

    # 4. RSI reversal
    if last.rsi < 30:
        add("RSI oversold", "bull", "active", f"RSI {last.rsi:.0f} (<30) — stretched down, bounce candidate.")
    elif last.rsi > 70:
        add("RSI overbought", "bear", "active", f"RSI {last.rsi:.0f} (>70) — stretched up, fade candidate.")

    # 5. MACD momentum
    if prev.macd_hist <= 0 < last.macd_hist:
        add("MACD cross", "bull", "active", "MACD crossed above its signal — momentum turning up.")
    elif prev.macd_hist >= 0 > last.macd_hist:
        add("MACD cross", "bear", "active", "MACD crossed below its signal — momentum turning down.")
    elif last.macd_hist > 0 and last.macd_hist > prev.macd_hist:
        add("MACD momentum", "bull", "forming", "Histogram positive & rising — bullish momentum building.")
    elif last.macd_hist < 0 and last.macd_hist < prev.macd_hist:
        add("MACD momentum", "bear", "forming", "Histogram negative & falling — bearish momentum building.")

    # 6. Bollinger squeeze / stretch
    if _ok(last.bb_w):
        recent = d["bb_w"].dropna().tail(120)
        if len(recent) > 20 and last.bb_w <= recent.quantile(0.20):
            add("Bollinger squeeze", "neutral", "active",
                "Bands unusually tight — low volatility; a bigger move often follows (direction TBD).")
        if _ok(last.bb_up) and close >= last.bb_up:
            add("Upper-band stretch", "bear", "active",
                "Closed at/above the upper Bollinger band — extended; mean-reversion risk.")
        elif _ok(last.bb_lo) and close <= last.bb_lo:
            add("Lower-band stretch", "bull", "active",
                "Closed at/below the lower Bollinger band — extended; bounce potential.")

    return out, d


def bias(findings):
    """Overall tilt from active setups: (label, score) where score = bull - bear."""
    score = sum((f["direction"] == "bull") - (f["direction"] == "bear")
                for f in findings if f["status"] == "active")
    if score >= 2:
        return "Bullish", score
    if score <= -2:
        return "Bearish", score
    return "Mixed / neutral", score
