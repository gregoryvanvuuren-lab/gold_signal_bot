"""Mobile-friendly NEWS & EVENTS radar for commodity day-trading (Streamlit).

Companion to the technical Gold Signal Bot: instead of chart signals, this scans
FREE news + a scheduled-events calendar and rolls them into a plain-English news
bias. Signal-only, educational — not financial advice, and it never trades.

Run locally:   streamlit run research_app.py
Deploy free:   same GitHub repo as the gold app -> new Streamlit app, main file
               research_app.py (add `feedparser` to requirements.txt).
"""

import streamlit as st

import research as rb

st.set_page_config(page_title="Commodity News Radar", page_icon="🛢️", layout="centered")

BIAS_COLOR = {"Bullish": "#26a269", "Bearish": "#e01b24", "Mixed / neutral": "#9a9996"}
TONE_COLOR = {"bull": "#26a269", "bear": "#e01b24", "neutral": "#9a9996"}
TONE_TAG = {"bull": "🟢 bullish", "bear": "🔴 bearish", "neutral": "⚪ neutral"}
IMPACT_COLOR = {"High": "#e01b24", "Medium": "#f6c744", "Low": "#9a9996"}


@st.cache_data(ttl=1200, show_spinner="Scanning the news…")
def load_news(symbol: str, days: int):
    return rb.fetch_news(symbol, days)


@st.cache_data(ttl=900, show_spinner="Loading the events calendar…")
def load_cal(symbol: str, within: int):
    return rb.fetch_calendar(symbol, within)


symbol = rb.DEFAULT_SYMBOL
prof = rb.PROFILES[symbol]

st.title("🛢️ Commodity News Radar")
st.caption(f"{prof['name']} · scheduled catalysts + live headlines, rolled into a news bias. "
           "Signal-only — news moves price on the *surprise* vs expectations, not on the raw number.")

with st.expander("⚙️ Settings", expanded=False):
    days = st.slider("Headline look-back (days)", 1, 5, 2)
    within = st.slider("Calendar look-ahead (days)", 1, 7, 3)
    st.caption(f"Instrument: {prof['name']} ({symbol}). More commodities coming — "
               "the engine is profile-driven.")

ref = rb.now_uk()
st.caption(f"As of {ref:%a %d %b %Y · %H:%M} (UK)")

# ============================================================================
# HEADLINE — news bias
# ============================================================================
news = load_news(symbol, days)
bias_label, bias_score = rb.news_bias(news)
n_bull = sum(it["tone"] == "bull" for it in news)
n_bear = sum(it["tone"] == "bear" for it in news)

bc = BIAS_COLOR[bias_label]
st.markdown(
    f"<div style='background:{bc};padding:12px 16px;border-radius:8px;color:#fff;margin-bottom:8px'>"
    f"<span style='font-size:1.4em;font-weight:700'>News bias: {bias_label}</span>"
    f"<span style='opacity:0.85'> &nbsp;({bias_score:+d} · {n_bull} bullish / {n_bear} bearish "
    f"of {len(news)} headlines)</span></div>",
    unsafe_allow_html=True,
)
st.caption("A keyword read of the last few days' headlines — a tint to weigh, not gospel. "
           "Always skim the stories yourself (tap a headline).")

st.divider()

# ============================================================================
# Upcoming catalysts (scheduled events)
# ============================================================================
st.markdown("### 📅 Upcoming catalysts")
st.caption(f"Scheduled, high-impact events in the next {within} days · UK time · "
           "🛢 = oil-specific. This is your 'know when the move is coming' list.")

cal = load_cal(symbol, within)
if cal:
    for ev in cal:
        icol = IMPACT_COLOR.get(ev["impact"], "#9a9996")
        when = ev["when"].strftime("%a %d %b · %H:%M") if ev["has_time"] \
            else ev["when"].strftime("%a %d %b") + " · all-day/tentative"
        oil = "🛢 " if ev["is_oil"] else ""
        extra = ""
        if ev["forecast"] or ev["previous"]:
            extra = (f"<br><span style='font-size:0.82em;opacity:0.7'>"
                     f"forecast {ev['forecast'] or '—'} · previous {ev['previous'] or '—'}</span>")
        st.markdown(
            f"<div style='border-left:4px solid {icol};padding:8px 12px;margin:6px 0;"
            f"background:rgba(127,127,127,0.08);border-radius:4px'>"
            f"<span style='font-size:0.82em;opacity:0.7'>{when}</span><br>"
            f"<b>{oil}{ev['title']}</b> "
            f"<span style='background:{icol};color:#111;font-size:0.72em;padding:1px 6px;"
            f"border-radius:10px;font-weight:700'>{ev['impact']}</span>"
            f"<span style='opacity:0.6;font-size:0.82em'> · {ev['country']}</span>{extra}"
            f"</div>",
            unsafe_allow_html=True,
        )
else:
    st.info("No scheduled catalysts matched in this window (or the calendar feed is briefly "
            "unavailable). Quiet diary — moves are more likely to come from breaking headlines.")

st.divider()

# ============================================================================
# Latest headlines
# ============================================================================
st.markdown("### 📰 Latest headlines")
st.caption(f"Last {days} days · Google News + source feeds · tagged by a keyword sentiment read.")

if news:
    for it in news:
        c = TONE_COLOR[it["tone"]]
        when = rb.ago(it["when"], ref) if it["when"] else "—"
        title = it["title"]
        link = f"<a href='{it['url']}' target='_blank' style='color:inherit;text-decoration:none'>{title}</a>" \
            if it["url"] else title
        st.markdown(
            f"<div style='border-left:4px solid {c};padding:6px 12px;margin:5px 0;"
            f"background:rgba(127,127,127,0.06);border-radius:4px'>"
            f"<span style='font-size:0.95em'>{link}</span><br>"
            f"<span style='font-size:0.78em;opacity:0.7'>{TONE_TAG[it['tone']]} · "
            f"{it['source']} · {when}</span></div>",
            unsafe_allow_html=True,
        )
else:
    st.write("No headlines pulled just now — try again shortly, or widen the look-back in Settings.")

# ============================================================================
# Catalyst school
# ============================================================================
with st.expander("📚 Catalyst school — how to read each event"):
    for name, text in rb.GLOSSARY:
        st.markdown(f"**{name}** — {text}")

st.caption("Free sources: Google News, EIA, OilPrice, and a public economic-events calendar. "
           "Educational tool, not investment advice. CFDs are leveraged and can lose more than "
           "your stake — news can gap price straight through a stop.")
