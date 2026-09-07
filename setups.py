"""Setup scanner — flags which common trade setups are active/forming on the
latest daily bar, each with a plain-English "why" so the reasoning is learnable
and transferable to any index. Educational context, not a trade instruction.

scan(df) takes the DataFrame from gold_signal_bot.fetch() (already has rsi,
macd, macd_sig, macd_hist, atr, sma_trend) and returns (findings, enriched_df).
Each finding: {name, key, direction: bull|bear|neutral, status: active|forming,
note, why}.
"""

import pandas as pd

# Educational "why this works" — general principle, transferable to any market.
EDU = {
    "trend_regime":
        "Markets move in trends. The 50-day average sitting above the 200-day means "
        "buyers have controlled the last several months, so dips tend to get bought — "
        "you lean long. Below, sellers are in charge — you lean short. On ANY index, "
        "check this 50-vs-200 relationship first: it tells you which side to favour "
        "before you look at anything else.",
    "breakout":
        "A close above the highest high of the last 20 days means everyone who bought "
        "recently is now in profit and the sellers who kept capping price have run out — "
        "supply is cleared, so the move often keeps going. Breakdowns below the 20-day "
        "low are the mirror. The tell to look for anywhere: a decisive close BEYOND the "
        "range, not just an intraday poke that snaps back.",
    "pullback":
        "Price rarely goes straight up. In an uptrend it dips back to a moving average, "
        "where fresh buyers who missed the first move step in, then it resumes. Buying "
        "that dip gets you a better price with the trend still intact. On any chart, "
        "look for a shallow pullback to the 20- or 50-day line that holds and turns up.",
    "rsi_reversal":
        "RSI measures how stretched a move is on a 0–100 scale. Under 30, selling has "
        "been extreme and usually exhausts itself; over 70, buying is overheated. It's a "
        "fade / mean-reversion cue — but it works best WITH the bigger trend (buy "
        "oversold in an uptrend), not fighting it.",
    "macd_cross":
        "MACD compares fast momentum against slow momentum. When the fast line crosses "
        "above the slow line, the pace of buying is accelerating — an early sign a swing "
        "up is starting (and the reverse for a cross down). On any index it's a timing "
        "nudge, best confirmed by the trend and RSI agreeing.",
    "bollinger":
        "Bollinger bands wrap price at ±2 standard deviations, so they measure "
        "volatility. When they pinch tight (a 'squeeze'), the market is coiled and a "
        "bigger move usually follows — you don't know the direction yet, so you wait for "
        "the break. A close OUTSIDE a band means price is unusually stretched and prone "
        "to snapping back.",
}

GLOSSARY = [
    ("Trend regime (50/200)", EDU["trend_regime"]),
    ("20-day breakout / breakdown", EDU["breakout"]),
    ("Trend pullback", EDU["pullback"]),
    ("RSI reversal", EDU["rsi_reversal"]),
    ("MACD momentum cross", EDU["macd_cross"]),
    ("Bollinger squeeze / stretch", EDU["bollinger"]),
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

    def add(name, key, direction, status, note):
        out.append({"name": name, "key": key, "direction": direction,
                    "status": status, "note": note, "why": EDU[key]})

    # 1. Trend regime (50 vs 200)
    if _ok(last.sma50, last.sma200):
        if last.sma50 > last.sma200:
            add("Trend regime (50/200)", "trend_regime", "bull", "active",
                "50-day above 200-day — uptrend backdrop; favour longs / buy-the-dip.")
        else:
            add("Trend regime (50/200)", "trend_regime", "bear", "active",
                "50-day below 200-day — downtrend backdrop; favour shorts / sell-the-rally.")

    # 2. Breakout / breakdown (20-day Donchian)
    if _ok(last.don_hi) and close > last.don_hi:
        add("20-day breakout", "breakout", "bull", "active",
            f"Close broke above the 20-day high ({last.don_hi:,.0f}) — continuation long.")
    elif _ok(last.don_lo) and close < last.don_lo:
        add("20-day breakdown", "breakout", "bear", "active",
            f"Close broke below the 20-day low ({last.don_lo:,.0f}) — continuation short.")
    elif _ok(last.don_hi) and close > 0.99 * last.don_hi:
        add("20-day breakout", "breakout", "bull", "forming", "Pressing the 20-day high — breakout brewing.")
    elif _ok(last.don_lo) and close < 1.01 * last.don_lo:
        add("20-day breakdown", "breakout", "bear", "forming", "Pressing the 20-day low — breakdown brewing.")

    # 3. Trend pullback (continuation)
    if _ok(last.sma200, last.sma20):
        if close > last.sma200 and close <= last.sma20 and 40 <= last.rsi <= 55 and last.rsi > prev.rsi:
            add("Bull pullback", "pullback", "bull", "active",
                "Uptrend + dip to the 20-day MA + RSI turning up — buy-the-dip.")
        elif close < last.sma200 and close >= last.sma20 and 45 <= last.rsi <= 60 and last.rsi < prev.rsi:
            add("Bear pullback", "pullback", "bear", "active",
                "Downtrend + bounce to the 20-day MA + RSI rolling over — sell-the-rally.")

    # 4. RSI reversal
    if last.rsi < 30:
        add("RSI oversold", "rsi_reversal", "bull", "active", f"RSI {last.rsi:.0f} (<30) — stretched down, bounce candidate.")
    elif last.rsi > 70:
        add("RSI overbought", "rsi_reversal", "bear", "active", f"RSI {last.rsi:.0f} (>70) — stretched up, fade candidate.")

    # 5. MACD momentum
    if prev.macd_hist <= 0 < last.macd_hist:
        add("MACD cross", "macd_cross", "bull", "active", "MACD crossed above its signal — momentum turning up.")
    elif prev.macd_hist >= 0 > last.macd_hist:
        add("MACD cross", "macd_cross", "bear", "active", "MACD crossed below its signal — momentum turning down.")
    elif last.macd_hist > 0 and last.macd_hist > prev.macd_hist:
        add("MACD momentum", "macd_cross", "bull", "forming", "Histogram positive & rising — bullish momentum building.")
    elif last.macd_hist < 0 and last.macd_hist < prev.macd_hist:
        add("MACD momentum", "macd_cross", "bear", "forming", "Histogram negative & falling — bearish momentum building.")

    # 6. Bollinger squeeze / stretch
    if _ok(last.bb_w):
        recent = d["bb_w"].dropna().tail(120)
        if len(recent) > 20 and last.bb_w <= recent.quantile(0.20):
            add("Bollinger squeeze", "bollinger", "neutral", "active",
                "Bands unusually tight — low volatility; a bigger move often follows (direction TBD).")
        if _ok(last.bb_up) and close >= last.bb_up:
            add("Upper-band stretch", "bollinger", "bear", "active",
                "Closed at/above the upper Bollinger band — extended; mean-reversion risk.")
        elif _ok(last.bb_lo) and close <= last.bb_lo:
            add("Lower-band stretch", "bollinger", "bull", "active",
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
