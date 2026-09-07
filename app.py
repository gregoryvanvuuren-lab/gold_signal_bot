"""Mobile-friendly web front-end for the gold signal bot (Streamlit).

Run locally:   streamlit run app.py
Deploy free:   push to GitHub, then share.streamlit.io -> app.py

Signal-only. Does not place or size any trades. Not financial advice.
"""

import pandas as pd
import streamlit as st

import gold_signal_bot as bot

st.set_page_config(page_title="Gold Signal Bot", page_icon="🥇", layout="centered")


@st.cache_data(ttl=900, show_spinner="Fetching gold data…")
def load(symbol: str, period: str) -> pd.DataFrame:
    return bot.fetch(symbol, period)


st.title("🥇 Gold Signal Bot")
st.caption("Daily-chart RSI + MACD momentum, ATR stop/target. Signal-only — not financial advice.")

with st.expander("⚙️ Settings", expanded=False):
    symbol = st.text_input("Symbol (yfinance)", bot.DEFAULT_SYMBOL)
    period = st.selectbox("History", ["1y", "2y", "3y", "5y"], index=2)
    col_a, col_b = st.columns(2)
    long_only = col_a.checkbox("Long-only", value=False)
    trend_filter = col_b.checkbox("200-DMA filter", value=True)
    sl_atr = st.slider("Stop-loss (× ATR)", 0.5, 3.0, bot.DEFAULT_SL_ATR, 0.25)
    tp_atr = st.slider("Take-profit (× ATR)", 1.0, 6.0, bot.DEFAULT_TP_ATR, 0.25)

try:
    df = load(symbol, period)
except SystemExit as exc:
    st.error(str(exc))
    st.stop()

events, trades, pos = bot.run_engine(df, sl_atr, tp_atr, not long_only, trend_filter)
last = df.iloc[-1]
last_date = df.index[-1].date()

st.subheader(f"As of {last_date}")
c1, c2, c3 = st.columns(3)
c1.metric("Close", f"{last.Close:,.2f}")
c2.metric("RSI", f"{last.rsi:.1f}")
c3.metric("MACD hist", f"{last.macd_hist:+.2f}")
if not pd.isna(last.sma_trend):
    arrow = "▲ uptrend" if last.Close > last.sma_trend else "▼ downtrend"
    st.caption(f"200-DMA {last.sma_trend:,.2f} — price {arrow}")

# Today's action
todays = [e for e in events if e["date"].date() == last_date]
if todays:
    e = todays[-1]
    msg = f"**TODAY: {e['action']}** @ {e['price']:,.2f} — {e['reason']}"
    if "ENTRY" in e["action"]:
        (st.success if "LONG" in e["action"] else st.error)(msg)
    else:
        st.warning(msg)
else:
    st.info("**TODAY: no new signal — HOLD**")

# Open position / trade plan
if pos:
    entry, stop, target = pos["entry"], pos["stop"], pos["target"]
    rr = abs(target - entry) / (abs(entry - stop) or float("nan"))
    unreal = (last.Close - entry) if pos["side"] == "LONG" else (entry - last.Close)
    unreal_r = unreal / (abs(entry - stop) or float("nan"))
    st.markdown(f"#### Open position: {pos['side']} (since {pos['entry_date'].date()})")
    p1, p2, p3 = st.columns(3)
    p1.metric("Entry", f"{entry:,.2f}")
    p2.metric("Stop-loss", f"{stop:,.2f}")
    p3.metric("Take-profit", f"{target:,.2f}", help=f"R:R {rr:.1f}")
    st.metric("Unrealised", f"{unreal:+.2f}", f"{unreal_r:+.2f}R")
else:
    st.markdown("#### Flat — waiting for the next setup.")

# Price chart with 200-DMA
st.markdown("#### Price & 200-DMA")
st.line_chart(df[["Close", "sma_trend"]].rename(columns={"sma_trend": "200-DMA"}))

# Recent signals
st.markdown("#### Recent signals")
if events:
    table = []
    for e in reversed(events[-15:]):
        table.append({
            "Date": e["date"].date(),
            "Signal": e["action"],
            "Price": round(e["price"], 2),
            "Result": f"{e['points']:+.1f} ({e['r_multiple']:+.2f}R)" if "r_multiple" in e else "—",
            "Reason": e["reason"],
        })
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
else:
    st.write("No signals in this window.")

# Backtest
with st.expander("📊 Backtest (this window · no costs/slippage)"):
    if trades:
        wins = [t for t in trades if t["points"] > 0]
        total_r = sum(t["r_multiple"] for t in trades)
        gross_win = sum(t["points"] for t in wins)
        gross_loss = -sum(t["points"] for t in trades if t["points"] <= 0)
        pf = (gross_win / gross_loss) if gross_loss else float("inf")
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Trades", len(trades))
        b2.metric("Win rate", f"{len(wins)/len(trades)*100:.0f}%")
        b3.metric("Total", f"{total_r:+.1f}R")
        b4.metric("Profit factor", f"{pf:.2f}")
        st.dataframe(pd.DataFrame([{
            "In": t["entry_date"].date(), "Out": t["exit_date"].date(),
            "Side": t["side"], "Entry": round(t["entry"], 2), "Exit": round(t["exit"], 2),
            "R": round(t["r_multiple"], 2), "Reason": t["reason"],
        } for t in reversed(trades)]), hide_index=True, width="stretch")
    else:
        st.write("No closed trades in this window.")

st.caption("Data via Yahoo Finance (yfinance). Educational tool, not investment advice.")
