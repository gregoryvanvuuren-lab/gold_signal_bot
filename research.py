#!/usr/bin/env python3
"""Commodity NEWS + EVENTS research scanner (WTI crude first).

Signal-only / educational. Pulls FREE, no-API-key sources and rolls them into a
plain-English *news bias* to sit ALONGSIDE the technical bot:

  1. Scheduled catalysts  -> a free economic-calendar feed (FairEconomy/ForexFactory
     weekly XML). Tells you WHEN a big move is likely (e.g. weekly EIA oil
     inventories, OPEC, FOMC/CPI/NFP). Times converted to UK local.
  2. Recent headlines     -> Google News RSS (queried per commodity) + source feeds
     (EIA, OilPrice). Each headline is keyword-tinted bull/bear.

It does NOT place, size, or execute trades. News moves price on the SURPRISE vs
what was already expected, so treat this as context + timing, not a prediction.
"""

import datetime as dt
import os
import sys
import tempfile
import time
import urllib.parse
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import feedparser
import requests

UK = ZoneInfo("Europe/London")
NY = ZoneInfo("America/New_York")          # the calendar feed publishes in US Eastern
UTC = dt.timezone.utc
UA = {"User-Agent": "Mozilla/5.0 (commodity-research-bot; news+events scan)"}
CAL_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"

DEFAULT_SYMBOL = "CL=F"

# ----------------------------------------------------------------------------
# Per-commodity profile: news query, extra source feeds, calendar keywords.
# (Only crude for now; add NG=F / GC=F / HG=F here later.)
# ----------------------------------------------------------------------------
PROFILES = {
    "CL=F": {
        "name": "WTI Crude Oil",
        "gnews": "crude oil OR WTI crude OR OPEC oil OR oil prices",
        "feeds": [
            ("EIA", "https://www.eia.gov/rss/todayinenergy.xml"),
            ("OilPrice", "https://oilprice.com/rss/main"),
        ],
        # calendar event-title fragments that specifically move oil
        "cal_keywords": ("crude oil inventories", "cushing", "gasoline inventories",
                         "distillate", "opec", "natural gas storage", "petroleum"),
    },
}

# Macro events that move ALL commodities via the US dollar / risk appetite.
MACRO_KEYWORDS = ("fomc", "federal funds", "cpi", "ppi", "non-farm", "nonfarm",
                  "unemployment rate", "gdp", "pce", "fed chair", "interest rate",
                  "jobless claims", "retail sales", "ism")

# Keyword lexicon (crude-oil tuned). Multi-word phrases are matched as substrings.
# Sentiment is a TINT, not truth — the headline is always shown so you can judge.
BULL_WORDS = (
    "supply cut", "output cut", "production cut", "opec cut", "cuts output", "cut output",
    "inventory draw", "stock draw", "crude draw", "drawdown", "stockpiles fall",
    "stocks fall", "supply disruption", "disruption", "outage", "shutdown", "force majeure",
    "sanction", "embargo", "attack", "strike on", "drone", "missile", "escalat",
    "tension", "conflict", "war", "hurricane", "cold snap", "shortage", "deficit",
    "tighten", "tight supply", "demand surge", "demand rises", "robust demand",
    "supply risk", "geopolit", "halts production", "blockade", "rally", "surge",
    "hormuz", "houthi", "refinery", "climb", "jump", "spike", "week high", "supply fear",
)
BEAR_WORDS = (
    "inventory build", "stock build", "crude build", "stockpiles rise", "stocks rise",
    "glut", "oversupply", "surplus", "output hike", "raise output", "raises output",
    "increase production", "boost output", "ramp up", "record output", "record production",
    "demand fear", "demand concern", "weak demand", "slowing demand", "demand slump",
    "recession", "slowdown", "economic weakness", "ceasefire", "truce", "peace deal",
    "deal reached", "resume", "restart", "reopen", "spr release", "reserve release",
    "release from reserves", "raises quota", "quota increase", "price war", "slump", "plunge",
)


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def now_uk() -> dt.datetime:
    return dt.datetime.now(UK)


def tone(text: str):
    """Return (label, score) where score = bull_hits - bear_hits for the headline."""
    t = (text or "").lower()
    b = sum(w in t for w in BULL_WORDS)
    s = sum(w in t for w in BEAR_WORDS)
    if b > s:
        return "bull", b - s
    if s > b:
        return "bear", b - s
    return "neutral", 0


def ago(when: dt.datetime, ref: dt.datetime) -> str:
    secs = (ref - when).total_seconds()
    if secs < 0:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def _entry_time(entry) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        tm = entry.get(key)
        if tm:
            return dt.datetime(*tm[:6], tzinfo=UTC).astimezone(UK)
    return None


def _get(url: str, retries: int = 3) -> bytes:
    """GET with small back-off on 429 / 5xx (feeds rate-limit shared cloud IPs)."""
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=UA, timeout=20)
            if r.status_code == 429 or r.status_code >= 500:
                last = requests.HTTPError(f"HTTP {r.status_code}")
                time.sleep(1.5 * (i + 1))
                continue
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            last = e
            time.sleep(1.0 * (i + 1))
    raise last if last else RuntimeError(f"failed to fetch {url}")


def _calendar_bytes() -> bytes:
    """Calendar feed with a last-good disk fallback — it's weekly data, so a cached
    copy stays valid all week and lets us ride out the feed's aggressive 429s."""
    path = os.path.join(tempfile.gettempdir(), "ff_calendar_thisweek.xml")
    try:
        data = _get(CAL_URL)
        try:
            with open(path, "wb") as fh:
                fh.write(data)
        except OSError:
            pass
        return data
    except Exception:
        if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < 86400:
            with open(path, "rb") as fh:                 # serve yesterday's copy
                return fh.read()
        raise


# ----------------------------------------------------------------------------
# Headlines
# ----------------------------------------------------------------------------
def _parse_feed(source: str, url: str, ref: dt.datetime, days: int):
    out = []
    try:
        feed = feedparser.parse(_get(url))
    except Exception:
        return out
    cutoff = ref - dt.timedelta(days=days)
    for e in feed.entries:
        title = (e.get("title") or "").strip()
        if not title:
            continue
        when = _entry_time(e)
        if when is not None and when < cutoff:
            continue
        label, score = tone(title)
        out.append({"source": source, "title": title, "url": e.get("link", ""),
                    "when": when, "tone": label, "score": score})
    return out


def fetch_news(symbol: str = DEFAULT_SYMBOL, days: int = 2, limit: int = 24):
    prof = PROFILES[symbol]
    ref = now_uk()
    q = urllib.parse.quote(f"{prof['gnews']} when:{days}d")
    gnews = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    items = _parse_feed("Google News", gnews, ref, days)
    for name, url in prof["feeds"]:
        items += _parse_feed(name, url, ref, days)

    # de-dupe by normalised title, newest first
    seen, uniq = set(), []
    for it in sorted(items, key=lambda x: x["when"] or ref, reverse=True):
        key = it["title"].lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq[:limit]


def news_bias(items):
    """Aggregate tilt from headline tones: (label, score)."""
    score = sum((it["tone"] == "bull") - (it["tone"] == "bear") for it in items)
    if score >= 3:
        return "Bullish", score
    if score <= -3:
        return "Bearish", score
    return "Mixed / neutral", score


# ----------------------------------------------------------------------------
# Scheduled catalysts (economic calendar)
# ----------------------------------------------------------------------------
def _cal_dt(date_s: str, time_s: str):
    """Parse the feed's MM-DD-YYYY + '10:30am' (US Eastern) -> (UK datetime, has_time)."""
    d = dt.datetime.strptime(date_s.strip(), "%m-%d-%Y").date()
    ts = (time_s or "").strip().lower()
    if not ts or ts in ("all day", "tentative") or "day" in ts:
        return dt.datetime.combine(d, dt.time(23, 59), tzinfo=NY).astimezone(UK), False
    clean = ts.upper().replace(" ", "")
    for fmt in ("%I:%M%p", "%I%p"):
        try:
            t = dt.datetime.strptime(clean, fmt).time()
            return dt.datetime.combine(d, t, tzinfo=NY).astimezone(UK), True
        except ValueError:
            continue
    return dt.datetime.combine(d, dt.time(23, 59), tzinfo=NY).astimezone(UK), False


def _relevant(title: str, country: str, impact: str, prof) -> bool:
    tl = title.lower()
    if any(k in tl for k in prof["cal_keywords"]):       # oil-specific reports
        return True
    if country == "USD" and impact == "High":            # big US macro (USD / demand)
        return True
    if country == "USD" and any(k in tl for k in MACRO_KEYWORDS) and impact in ("High", "Medium"):
        return True
    return False


def fetch_calendar(symbol: str = DEFAULT_SYMBOL, within_days: int = 3):
    prof = PROFILES[symbol]
    ref = now_uk()
    try:
        root = ET.fromstring(_calendar_bytes())
    except Exception:
        return []
    horizon = ref + dt.timedelta(days=within_days)
    out = []
    for ev in root.findall("event"):
        title = (ev.findtext("title") or "").strip()
        country = (ev.findtext("country") or "").strip()
        impact = (ev.findtext("impact") or "").strip()
        date_s = (ev.findtext("date") or "").strip()
        time_s = (ev.findtext("time") or "").strip()
        if not title or not date_s:
            continue
        if not _relevant(title, country, impact, prof):
            continue
        try:
            when, has_time = _cal_dt(date_s, time_s)
        except ValueError:
            continue
        if when < ref - dt.timedelta(hours=2) or when > horizon:
            continue
        is_oil = any(k in title.lower() for k in prof["cal_keywords"])
        out.append({"title": title, "country": country, "impact": impact or "—",
                    "when": when, "has_time": has_time, "is_oil": is_oil,
                    "forecast": (ev.findtext("forecast") or "").strip(),
                    "previous": (ev.findtext("previous") or "").strip()})
    return sorted(out, key=lambda x: x["when"])


# ----------------------------------------------------------------------------
# Educational glossary — how to read each catalyst (learn-to-spot, like the TA bot)
# ----------------------------------------------------------------------------
GLOSSARY = [
    ("EIA Crude Oil Inventories (Wed 15:30 UK)",
     "Weekly US crude stockpiles. A bigger-than-expected DRAW (stocks fall) means demand is "
     "outrunning supply — bullish; a BUILD (stocks rise) is bearish. It's the number vs the "
     "forecast that moves price, not the raw figure."),
    ("API Weekly Crude Stock (Tue evening)",
     "The industry's own estimate the night before the EIA. Often front-runs the EIA reaction, "
     "so a big API surprise can move oil on Tuesday evening."),
    ("OPEC / OPEC+ meetings",
     "The cartel sets production quotas. Output CUTS tighten supply (bullish); HIKES or a "
     "breakdown into a price war add supply (bearish). Watch the headline vs what was expected."),
    ("FOMC / rates, CPI, PCE",
     "Work through the US dollar and demand outlook. Hotter inflation / higher-for-longer rates "
     "lift the dollar and dent demand hopes — usually a headwind for oil, and vice versa."),
    ("Non-Farm Payrolls / GDP",
     "Growth proxies. Strong data = stronger expected fuel demand (supportive), but can also "
     "mean higher rates (a dollar headwind) — so read it together with the Fed picture."),
]


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def _fmt_when(ev):
    return ev["when"].strftime("%a %d %b %H:%M") if ev["has_time"] else \
        ev["when"].strftime("%a %d %b") + "  (all-day/tentative)"


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Commodity news + events research scan (signal-only).")
    p.add_argument("--symbol", default=DEFAULT_SYMBOL, help="commodity symbol (default CL=F)")
    p.add_argument("--days", type=int, default=2, help="headline look-back window (days)")
    p.add_argument("--within", type=int, default=3, help="calendar look-ahead window (days)")
    args = p.parse_args(argv)

    if args.symbol not in PROFILES:
        raise SystemExit(f"No research profile for {args.symbol!r}. Have: {', '.join(PROFILES)}")
    prof = PROFILES[args.symbol]
    ref = now_uk()

    print("=" * 68)
    print(f" {prof['name'].upper()}  ·  NEWS & EVENTS  ·  as of {ref:%a %d %b %Y %H:%M} UK")
    print("=" * 68)

    cal = fetch_calendar(args.symbol, args.within)
    print(f"\n UPCOMING CATALYSTS (next {args.within}d, UK time):")
    if cal:
        for ev in cal:
            star = "🛢" if ev["is_oil"] else "  "
            print(f"   {star} {_fmt_when(ev):<28} [{ev['impact']:<6}] {ev['title']} ({ev['country']})")
    else:
        print("   (none matched / feed unavailable)")

    news = fetch_news(args.symbol, args.days)
    label, score = news_bias(news)
    print(f"\n NEWS BIAS: {label} ({score:+d})  from {len(news)} headlines\n")
    print(" LATEST HEADLINES:")
    tag = {"bull": "BULL", "bear": "BEAR", "neutral": "  · "}
    for it in news[:15]:
        when = ago(it["when"], ref) if it["when"] else "—"
        print(f"   [{tag[it['tone']]}] {when:>7} · {it['source']:<12} {it['title'][:90]}")
    print("=" * 68)
    print(" News moves price on the SURPRISE vs expectations — context/timing, not a prediction.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
