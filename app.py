"""Mobile-friendly web front-end for the gold signal bot (Streamlit).

Run locally:   streamlit run app.py
Deploy free:   push to GitHub, then share.streamlit.io -> app.py

Signal-only. Does not place or size any trades. Not financial advice.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import gold_signal_bot as bot
import setups as su

st.set_page_config(page_title="Gold Signal Bot", page_icon="🥇", layout="centered")

DIR_COLOR = {"bull": "#26a269", "bear": "#e01b24", "neutral": "#9a9996"}
DIR_ICON = {"bull": "▲", "bear": "▼", "neutral": "◆"}
BIAS_COLOR = {"Bullish": "#26a269", "Bearish": "#e01b24", "Mixed / neutral": "#9a9996"}

# Timeframe tabs → (yfinance interval, history period, is-intraday).
# Daily uses the History selector in Settings; intraday history is fixed by Yahoo's
# per-interval limits (kept ample so the 200-bar SMA trend filter has warmup).
TIMEFRAMES = {
    "Daily":  ("1d",  None,  False),
    "1 hour": ("60m", "6mo", True),
    "30 min": ("30m", "1mo", True),
    "15 min": ("15m", "1mo", True),
    "5 min":  ("5m",  "10d", True),
}


@st.cache_data(ttl=900, show_spinner="Fetching gold data…")
def load(symbol: str, period: str, interval: str) -> pd.DataFrame:
    return bot.fetch(symbol, period, interval)


st.title("🥇 Gold Signal Bot")
st.caption("Multi-timeframe setup radar with RSI + MACD and ATR stop/target. "
           "Signal-only — not financial advice.")

# Timeframe tabs — drive the WHOLE analysis (radar, signals, chart, backtest).
tf = st.segmented_control("⏱ Timeframe", list(TIMEFRAMES), default="Daily") or "Daily"
interval, tf_period, intraday = TIMEFRAMES[tf]

with st.expander("⚙️ Settings", expanded=False):
    symbol = st.text_input("Symbol (yfinance)", bot.DEFAULT_SYMBOL)
    if intraday:
        period = tf_period
        st.caption(f"History fixed at {tf_period} on the {tf} chart (Yahoo intraday limit).")
    else:
        period = st.selectbox("History", ["1y", "2y", "3y", "5y"], index=2)
    col_a, col_b = st.columns(2)
    long_only = col_a.checkbox("Long-only", value=False)
    trend_filter = col_b.checkbox("Trend filter (200-bar SMA)", value=True)
    sl_atr = st.slider("Stop-loss (× ATR)", 0.5, 3.0, bot.DEFAULT_SL_ATR, 0.25)
    tp_atr = st.slider("Take-profit (× ATR)", 1.0, 6.0, bot.DEFAULT_TP_ATR, 0.25)
    chart_bars = st.slider("Chart window (bars)", 60, 400, 180, 20)

try:
    df = load(symbol, period, interval)
except SystemExit as exc:
    st.error(str(exc))
    st.stop()


def fmt_ts(ts):
    return ts.strftime("%d %b %H:%M") if intraday else ts.date()

events, trades, pos = bot.run_engine(df, sl_atr, tp_atr, not long_only, trend_filter)
findings, d = su.scan(df)
last = d.iloc[-1]
last_ts = d.index[-1]
last_date = last_ts.date()
stamp = last_ts.strftime("%d %b %Y · %H:%M (UK)") if intraday else str(last_date)
bias_label, bias_score = su.bias(findings)

# ============================================================================
# HEADLINE — Setup radar
# ============================================================================
st.markdown("## 🎯 Setup radar")
st.caption(f"As of {stamp} · gold ({symbol}) · {tf} chart")
if intraday:
    st.caption("⏱ Intraday view — in the notes below, 'day'/'DMA' means one **bar** on this "
               "timeframe (e.g. '200-day' = 200 bars, '20-day high' = 20-bar high).")

bc = BIAS_COLOR[bias_label]
n_active = sum(1 for f in findings if f["status"] == "active")
st.markdown(
    f"<div style='background:{bc};padding:12px 16px;border-radius:8px;color:#fff;margin-bottom:8px'>"
    f"<span style='font-size:1.4em;font-weight:700'>Bias: {bias_label}</span>"
    f"<span style='opacity:0.85'> &nbsp;({bias_score:+d} · {n_active} active setup"
    f"{'s' if n_active != 1 else ''})</span></div>",
    unsafe_allow_html=True,
)

if findings:
    for s in findings:
        c = DIR_COLOR[s["direction"]]
        st.markdown(
            f"<div style='border-left:4px solid {c};padding:8px 12px;margin:6px 0;"
            f"background:rgba(127,127,127,0.08);border-radius:4px'>"
            f"<b>{DIR_ICON[s['direction']]} {s['name']}</b> "
            f"<span style='opacity:0.6'>· {s['status']}</span><br>"
            f"<span style='font-size:0.92em'>{s['note']}</span><br>"
            f"<span style='font-size:0.85em;opacity:0.7;font-style:italic'>💡 {s['why']}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
else:
    st.write("No notable setups on the latest bar — quiet market.")

st.divider()

# ============================================================================
# Supporting detail
# ============================================================================
st.markdown("### Snapshot")
c1, c2, c3 = st.columns(3)
c1.metric("Close", f"{last.Close:,.2f}")
c2.metric("RSI", f"{last.rsi:.1f}")
c3.metric("MACD hist", f"{last.macd_hist:+.2f}")
if not pd.isna(last.sma_trend):
    arrow = "▲ uptrend" if last.Close > last.sma_trend else "▼ downtrend"
    st.caption(f"200-SMA {last.sma_trend:,.2f} — price {arrow}  ·  "
               f"ATR ({'bar' if intraday else 'daily'} range) {last.atr:,.2f}")

# Latest-bar mechanical action
now_lbl = "LATEST BAR" if intraday else "TODAY"
todays = [e for e in events if e["date"] == last_ts]
if todays:
    e = todays[-1]
    msg = f"**{now_lbl}: {e['action']}** @ {e['price']:,.2f} — {e['reason']}"
    (st.success if "LONG" in e["action"] else st.error if "SHORT" in e["action"] else st.warning)(msg)
else:
    st.info(f"**{now_lbl}: no new signal — HOLD** (the engine only acts when a fresh trigger fires)")

# Open position / trade plan
if pos:
    entry, stop, target = pos["entry"], pos["stop"], pos["target"]
    rr = abs(target - entry) / (abs(entry - stop) or float("nan"))
    unreal = (last.Close - entry) if pos["side"] == "LONG" else (entry - last.Close)
    unreal_r = unreal / (abs(entry - stop) or float("nan"))
    held = d.index[-1] - pos["entry_date"]
    if intraday:
        hrs = held.total_seconds() / 3600
        held_str = f"{hrs:.0f}h in" if hrs >= 1 else f"{held.total_seconds() / 60:.0f}m in"
    else:
        held_str = f"{held.days} days in"
    to_stop = (stop - last.Close) / last.Close * 100
    to_tgt = (target - last.Close) / last.Close * 100
    st.markdown(f"#### Open position: {pos['side']} · {held_str}")
    p1, p2, p3 = st.columns(3)
    p1.metric("Entry", f"{entry:,.2f}")
    p2.metric("Stop-loss", f"{stop:,.2f}", f"{to_stop:+.1f}% away")
    p3.metric("Take-profit", f"{target:,.2f}", f"{to_tgt:+.1f}% away")
    st.metric("Unrealised", f"{unreal:+.2f}", f"{unreal_r:+.2f}R  (R:R {rr:.1f})")
else:
    st.markdown("#### Flat — waiting for the next entry trigger.")

# ---- Chart: candles + MAs + markers, RSI, MACD ------------------------------
st.markdown(f"### 📈 Chart · {tf}")
st.caption("Zoom: tap the top-right toolbar's +/− (or scroll / pinch on the plot); drag to pan; "
           "double-tap to reset. Tip: the 'Chart window (bars)' slider in ⚙️ Settings also zooms.")
view = d.tail(chart_bars)
start = view.index[0]
fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.09,
                    row_heights=[0.56, 0.22, 0.22], subplot_titles=("Price", "RSI", "MACD"))

fig.add_trace(go.Candlestick(x=view.index, open=view.Open, high=view.High, low=view.Low,
                             close=view.Close, name="Gold", showlegend=False), row=1, col=1)
for col, color, label in [("sma20", "#f6c744", "MA20"), ("sma50", "#62a0ea", "MA50"),
                          ("sma200", "#dddddd", "MA200")]:
    if col in view:
        fig.add_trace(go.Scatter(x=view.index, y=view[col], line=dict(width=1, color=color),
                                 name=label), row=1, col=1)

marker_specs = [("LONG ENTRY", "triangle-up", "#26a269", "Long"),
                ("SHORT ENTRY", "triangle-down", "#e01b24", "Short"),
                ("EXIT", "x", "#ffa348", "Exit")]
for key, sym, color, label in marker_specs:
    pts = [(e["date"], e["price"]) for e in events
           if e["action"].startswith(key) and e["date"] >= start]
    if pts:
        fig.add_trace(go.Scatter(x=[a for a, _ in pts], y=[b for _, b in pts], mode="markers",
                                 marker=dict(symbol=sym, size=11, color=color, line=dict(width=1, color="#111")),
                                 name=label), row=1, col=1)

if pos:
    for level, txt, color in [(pos["entry"], "Entry", "#aaaaaa"),
                              (pos["stop"], "Stop", "#e01b24"),
                              (pos["target"], "Target", "#26a269")]:
        fig.add_hline(y=level, line=dict(color=color, width=1, dash="dot"),
                      annotation_text=txt, annotation_position="right", row=1, col=1)

fig.add_trace(go.Scatter(x=view.index, y=view.rsi, line=dict(color="#c061cb", width=1),
                         name="RSI", showlegend=False), row=2, col=1)
fig.add_hline(y=70, line=dict(color="#e01b24", width=1, dash="dot"), row=2, col=1)
fig.add_hline(y=30, line=dict(color="#26a269", width=1, dash="dot"), row=2, col=1)

hist_colors = ["#26a269" if v >= 0 else "#e01b24" for v in view.macd_hist]
fig.add_trace(go.Bar(x=view.index, y=view.macd_hist, marker_color=hist_colors,
                     name="Hist", showlegend=False), row=3, col=1)
fig.add_trace(go.Scatter(x=view.index, y=view.macd, line=dict(color="#62a0ea", width=1),
                         name="MACD", showlegend=False), row=3, col=1)
fig.add_trace(go.Scatter(x=view.index, y=view.macd_sig, line=dict(color="#f6c744", width=1),
                         name="Signal", showlegend=False), row=3, col=1)

fig.update_layout(height=900, template="plotly_dark", margin=dict(l=8, r=8, t=30, b=8),
                  legend=dict(orientation="h", y=1.02, x=0), dragmode="pan", hovermode="x")
# date axis + zoom crosshair on EVERY panel, weekends collapsed
fig.update_xaxes(showticklabels=True, rangeslider_visible=False, showspikes=True,
                 spikemode="across", spikethickness=1,
                 tickformat="%H:%M\n%d %b" if intraday else "%d %b\n%Y",
                 rangebreaks=[dict(bounds=["sat", "mon"])])
fig.update_yaxes(showspikes=True)
fig.update_xaxes(title_text="Date", row=3, col=1)
config = {"scrollZoom": True, "displayModeBar": True, "displaylogo": False,
          "modeBarButtonsToRemove": ["lasso2d", "select2d"]}
st.plotly_chart(fig, width="stretch", config=config)

# ---- Recent signals ----------------------------------------------------------
st.markdown("### Recent signals")
if events:
    st.dataframe(pd.DataFrame([{
        "Date": fmt_ts(e["date"]), "Signal": e["action"], "Price": round(e["price"], 2),
        "Result": f"{e['points']:+.1f} ({e['r_multiple']:+.2f}R)" if "r_multiple" in e else "—",
        "Reason": e["reason"],
    } for e in reversed(events[-15:])]), hide_index=True, width="stretch")
else:
    st.write("No signals in this window.")

# ---- Backtest ----------------------------------------------------------------
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
            "In": fmt_ts(t["entry_date"]), "Out": fmt_ts(t["exit_date"]),
            "Side": t["side"], "Entry": round(t["entry"], 2), "Exit": round(t["exit"], 2),
            "R": round(t["r_multiple"], 2), "Reason": t["reason"],
        } for t in reversed(trades)]), hide_index=True, width="stretch")
    else:
        st.write("No closed trades in this window.")

# ---- Glossary ----------------------------------------------------------------
with st.expander("📚 Setup school — how to spot these on any index"):
    for name, text in su.GLOSSARY:
        st.markdown(f"**{name}** — {text}")

st.caption("Data via Yahoo Finance. Educational tool, not investment advice. "
           "CFD trading is leveraged and can lose more than your stake.")
