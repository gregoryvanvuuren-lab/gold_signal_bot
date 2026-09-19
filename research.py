#!/usr/bin/env python3
"""Commodity NEWS + EVENTS research scanner (10 markets: energy, metals, ags).

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

try:                                   # feed deps are optional at import so the
    import feedparser                  # commodity catalog (PROFILES) is always
    import requests                    # importable; fetch fns guard on HAVE_DEPS.
except ModuleNotFoundError:
    feedparser = None
    requests = None

HAVE_DEPS = feedparser is not None and requests is not None

UK = ZoneInfo("Europe/London")
NY = ZoneInfo("America/New_York")          # the calendar feed publishes in US Eastern
UTC = dt.timezone.utc
UA = {"User-Agent": "Mozilla/5.0 (commodity-research-bot; news+events scan)"}
CAL_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"

DEFAULT_SYMBOL = "CL=F"

# Macro events that move ALL commodities via the US dollar / risk appetite.
MACRO_KEYWORDS = ("fomc", "federal funds", "cpi", "ppi", "non-farm", "nonfarm",
                  "unemployment rate", "gdp", "pce", "fed chair", "interest rate",
                  "jobless claims", "retail sales", "ism")

# ----------------------------------------------------------------------------
# Per-commodity sentiment lexicons. Multi-word phrases match as substrings.
# Sentiment is a TINT, not truth — the headline is always shown so you can judge.
# Drivers differ by market, so each group carries its own bull/bear word list.
# ----------------------------------------------------------------------------
_BULL_OIL = (
    "supply cut", "output cut", "production cut", "opec cut", "cuts output", "cut output",
    "inventory draw", "stock draw", "crude draw", "drawdown", "stockpiles fall",
    "stocks fall", "supply disruption", "disruption", "outage", "shutdown", "force majeure",
    "sanction", "embargo", "attack", "strike on", "drone", "missile", "escalat",
    "tension", "conflict", "war", "hurricane", "cold snap", "shortage", "deficit",
    "tighten", "tight supply", "demand surge", "demand rises", "robust demand",
    "supply risk", "geopolit", "halts production", "blockade", "rally", "surge",
    "hormuz", "houthi", "refinery", "climb", "jump", "spike", "week high", "supply fear",
)
_BEAR_OIL = (
    "inventory build", "stock build", "crude build", "stockpiles rise", "stocks rise",
    "glut", "oversupply", "surplus", "output hike", "raise output", "raises output",
    "increase production", "boost output", "ramp up", "record output", "record production",
    "demand fear", "demand concern", "weak demand", "slowing demand", "demand slump",
    "recession", "slowdown", "economic weakness", "ceasefire", "truce", "peace deal",
    "deal reached", "resume", "restart", "reopen", "spr release", "reserve release",
    "release from reserves", "raises quota", "quota increase", "price war", "slump", "plunge",
)

_BULL_GAS = (
    "cold snap", "cold front", "cold blast", "polar vortex", "arctic", "freeze", "freeze-off",
    "freeze off", "heating demand", "colder", "below normal", "heatwave", "heat wave",
    "cooling demand", "record demand", "storage draw", "bigger draw", "larger draw",
    "supply outage", "outage", "shut-in", "pipeline", "lng demand", "record lng",
    "export demand", "feedgas", "production drop", "output falls", "supply risk",
    "shortage", "deficit", "tight", "rally", "surge", "spike", "jump", "climb",
)
_BEAR_GAS = (
    "mild", "milder", "warm", "warmer", "above normal", "mild winter", "warm weather",
    "storage build", "bigger build", "larger build", "injection", "record production",
    "record output", "oversupply", "glut", "surplus", "weak demand", "demand drops",
    "lng outage", "terminal outage", "export halt", "maintenance", "slump", "plunge",
    "tumble", "record storage", "ample supply", "well-supplied",
)

_BULL_METAL = (   # precious metals: gold & silver
    "rate cut", "rate cuts", "cuts rates", "dovish", "pivot", "easing", "weaker dollar",
    "dollar falls", "dollar weakens", "falling yields", "lower yields", "real yields fall",
    "safe haven", "safe-haven", "haven demand", "flight to safety", "inflation hedge",
    "geopolit", "tension", "conflict", "war", "banking stress", "recession",
    "central bank buying", "central banks buy", "record buying", "reserve buying",
    "etf inflows", "etf inflow", "fund buying", "record high", "all-time high", "record",
    "rally", "surge", "jump", "climb", "spike", "shortage", "supply deficit",
)
_BEAR_METAL = (
    "rate hike", "rate hikes", "hikes rates", "hawkish", "higher for longer", "stronger dollar",
    "dollar rises", "dollar strengthens", "rising yields", "higher yields", "real yields rise",
    "risk-on", "risk appetite", "etf outflows", "etf outflow", "fund selling", "profit-taking",
    "profit taking", "sell-off", "selloff", "slump", "plunge", "tumble", "slide", "eases",
    "retreat", "pullback", "de-escalat", "ceasefire", "truce",
)

_BULL_INDU = (   # industrial metals: copper & platinum
    "supply deficit", "deficit", "mine strike", "strike", "smelter outage", "smelter cut",
    "supply disruption", "disruption", "outage", "shutdown", "force majeure", "load-shedding",
    "load shedding", "power cut", "china stimulus", "stimulus", "infrastructure",
    "strong demand", "demand surge", "robust demand", "ev demand", "energy transition",
    "inventory draw", "stock draw", "lme drawdown", "falling stocks", "tight supply",
    "tighten", "shortage", "rally", "surge", "jump", "climb", "spike", "record high",
)
_BEAR_INDU = (
    "surplus", "oversupply", "glut", "demand slump", "weak demand", "slowing demand",
    "china slowdown", "property slump", "property crisis", "weak pmi", "contraction",
    "recession", "slowdown", "inventory build", "stock build", "rising stocks",
    "lme build", "new supply", "ramp up", "record output", "record production",
    "slump", "plunge", "tumble", "slide", "profit-taking", "sell-off", "selloff",
)

_BULL_AG = (   # grains & oilseeds
    "drought", "dry", "dryness", "heatwave", "heat wave", "frost", "freeze", "flood",
    "crop damage", "poor harvest", "poor crop", "low yield", "yield cut", "yields fall",
    "planting delay", "planting delays", "planting slow", "supply concern", "tight supply",
    "tight stocks", "lower stocks", "stocks fall", "export demand", "strong demand",
    "china buying", "china buys", "large purchase", "export ban", "export tax", "black sea",
    "shortage", "deficit", "rally", "surge", "jump", "climb", "spike",
)
_BEAR_AG = (
    "bumper", "record harvest", "record crop", "big crop", "large crop", "good crop",
    "favorable weather", "favourable weather", "good rains", "beneficial rain", "ideal weather",
    "high yield", "yields rise", "record yield", "ample supply", "big stocks", "rising stocks",
    "stocks rise", "weak demand", "demand slump", "cancellation", "cancellations",
    "surplus", "oversupply", "glut", "slump", "plunge", "tumble", "slide", "harvest pressure",
)

# ----------------------------------------------------------------------------
# Educational glossaries — how to read each catalyst, composed per group.
# ----------------------------------------------------------------------------
_GLO_OIL = [
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
_GLO_GAS = [
    ("EIA Natural Gas Storage (Thu 15:30 UK)",
     "The weekly injection/withdrawal report. A bigger WITHDRAWAL (draw) than expected means "
     "demand is eating into supply — bullish; a bigger INJECTION (build) is bearish. Surprise "
     "vs the forecast is what moves it."),
    ("Weather & degree days",
     "The #1 gas driver. Cold snaps spike heating demand and summer heatwaves spike power-burn "
     "for cooling (both bullish); mild winters or warm shoulder seasons crush demand (bearish)."),
    ("LNG export feedgas",
     "Gas leaving the US as LNG tightens the domestic balance (bullish). A big export-terminal "
     "outage backs that gas up at home (bearish)."),
    ("Production & freeze-offs",
     "Record output builds storage (bearish). In deep cold, wellhead 'freeze-offs' can abruptly "
     "cut supply (bullish)."),
    ("FOMC / rates",
     "Indirect — works through industrial demand and the dollar. Far less dominant for gas than "
     "weather and the weekly storage number."),
]
_GLO_PRECIOUS = [
    ("Fed rates & real yields",
     "Gold pays no interest, so it shines when real (inflation-adjusted) yields fall — a dovish "
     "Fed or rate cuts (bullish) — and struggles when yields rise (hawkish / higher-for-longer "
     "is bearish)."),
    ("US Dollar (DXY)",
     "Priced in dollars, so a weaker dollar makes it cheaper abroad (bullish) and a stronger "
     "dollar is a headwind."),
    ("CPI / PCE inflation",
     "Hot inflation can lift gold as a hedge — but only if the Fed isn't expected to out-hike "
     "it. Always read inflation alongside the rates picture."),
    ("Safe-haven & geopolitics",
     "War, banking stress or market panic drive haven buying (bullish). Calm, risk-on markets "
     "sap that demand."),
    ("Central-bank & ETF flows",
     "Sustained official buying and ETF inflows are a structural tailwind; heavy outflows are "
     "a drag."),
]
_GLO_SILVER_EXTRA = (
    "Industrial demand (silver)",
    "Silver is roughly half industrial — solar panels, electronics — so on top of gold's macro "
    "it tracks the growth cycle. Strong industrial demand adds a tailwind gold doesn't have.")
_GLO_INDUSTRIAL = [
    ("China data (PMI, GDP, stimulus)",
     "China is ~half of global metals demand. Strong PMIs or fresh stimulus = bullish; a "
     "property slump or weak PMI = bearish. Copper is nicknamed 'Dr Copper' for reading the "
     "economy."),
    ("LME / exchange inventories",
     "Falling warehouse stocks signal tight physical supply (bullish); rising stocks signal a "
     "building surplus (bearish)."),
    ("Mine supply",
     "Strikes, outages or ore-grade declines tighten supply (bullish). New capacity or a "
     "surplus forecast is bearish."),
    ("FOMC / US Dollar",
     "Priced in dollars and sensitive to growth via rates — a strong dollar and high rates are "
     "headwinds."),
]
_GLO_PLATINUM_EXTRA = (
    "Autos & South Africa (platinum)",
    "Platinum-group metals go into catalytic converters, so auto demand matters — and South "
    "African power cuts (load-shedding) that hit mines can abruptly tighten supply (bullish).")
_GLO_AGS = [
    ("USDA WASDE & crop reports",
     "Monthly world supply/demand estimates plus quarterly stocks/acreage are the big scheduled "
     "movers. Lower yields or tighter ending stocks = bullish; bumper crops = bearish."),
    ("Weather",
     "Drought, frost, heat or floods during the growing season cut yields (bullish); ideal rains "
     "and calm weather are bearish. The single biggest day-to-day driver."),
    ("Export sales & demand",
     "Strong weekly export sales — especially soybeans to China — are bullish; cancellations or "
     "new tariffs are bearish."),
    ("US Dollar",
     "A weaker dollar makes US grain cheaper on the world market, supporting exports and price; "
     "a strong dollar does the opposite."),
]
_GLO_WHEAT_EXTRA = (
    "Black Sea & geopolitics (wheat)",
    "Russia and Ukraine are top wheat exporters; conflict, drone strikes on their ports or "
    "export bans there can spike wheat fast.")

# ----------------------------------------------------------------------------
# Per-commodity profiles: display name/icon, news query, source feeds, calendar
# keywords, sentiment lexicon and glossary. Ordered by group for the dropdown.
# ----------------------------------------------------------------------------
PROFILES = {
    # ---- Energy -------------------------------------------------------------
    "CL=F": {
        "name": "WTI Crude Oil", "short": "WTI", "icon": "🛢️", "group": "Energy",
        "gnews": "crude oil OR WTI crude OR OPEC oil OR oil prices",
        "feeds": [("EIA", "https://www.eia.gov/rss/todayinenergy.xml"),
                  ("OilPrice", "https://oilprice.com/rss/main")],
        "cal_keywords": ("crude oil inventories", "cushing", "gasoline inventories",
                         "distillate", "opec", "petroleum"),
        "bull": _BULL_OIL, "bear": _BEAR_OIL, "glossary": _GLO_OIL,
    },
    "BZ=F": {
        "name": "Brent Crude Oil", "short": "Brent", "icon": "🛢️", "group": "Energy",
        "gnews": "Brent crude OR crude oil OR OPEC oil OR oil prices",
        "feeds": [("EIA", "https://www.eia.gov/rss/todayinenergy.xml"),
                  ("OilPrice", "https://oilprice.com/rss/main")],
        "cal_keywords": ("crude oil inventories", "cushing", "gasoline inventories",
                         "distillate", "opec", "petroleum"),
        "bull": _BULL_OIL, "bear": _BEAR_OIL, "glossary": _GLO_OIL,
    },
    "NG=F": {
        "name": "Natural Gas", "short": "NatGas", "icon": "🔥", "group": "Energy",
        "gnews": "natural gas price OR Henry Hub OR LNG exports OR gas storage",
        "feeds": [("EIA", "https://www.eia.gov/rss/todayinenergy.xml"),
                  ("OilPrice", "https://oilprice.com/rss/main")],
        "cal_keywords": ("natural gas storage", "natural gas"),
        "bull": _BULL_GAS, "bear": _BEAR_GAS, "glossary": _GLO_GAS,
    },
    # ---- Precious metals ----------------------------------------------------
    "GC=F": {
        "name": "Gold", "short": "Gold", "icon": "🥇", "group": "Precious metals",
        "gnews": "gold price OR spot gold OR gold bullion OR XAU",
        "feeds": [],
        "cal_keywords": ("gold",),
        "bull": _BULL_METAL, "bear": _BEAR_METAL, "glossary": _GLO_PRECIOUS,
    },
    "SI=F": {
        "name": "Silver", "short": "Silver", "icon": "🥈", "group": "Precious metals",
        "gnews": "silver price OR spot silver OR silver bullion OR XAG",
        "feeds": [],
        "cal_keywords": ("silver",),
        "bull": _BULL_METAL, "bear": _BEAR_METAL, "glossary": _GLO_PRECIOUS + [_GLO_SILVER_EXTRA],
    },
    # ---- Industrial metals --------------------------------------------------
    "HG=F": {
        "name": "Copper", "short": "Copper", "icon": "🔶", "group": "Industrial metals",
        "gnews": "copper price OR LME copper OR copper demand China",
        "feeds": [],
        "cal_keywords": ("copper",),
        "cal_countries": ("USD", "CNY"),
        "bull": _BULL_INDU, "bear": _BEAR_INDU, "glossary": _GLO_INDUSTRIAL,
    },
    "PL=F": {
        "name": "Platinum", "short": "Platinum", "icon": "⚪", "group": "Industrial metals",
        "gnews": "platinum price OR platinum demand OR PGM platinum",
        "feeds": [],
        "cal_keywords": ("platinum",),
        "cal_countries": ("USD", "CNY"),
        "bull": _BULL_INDU, "bear": _BEAR_INDU, "glossary": _GLO_INDUSTRIAL + [_GLO_PLATINUM_EXTRA],
    },
    # ---- Agriculture --------------------------------------------------------
    "ZC=F": {
        "name": "Corn", "short": "Corn", "icon": "🌽", "group": "Agriculture",
        "gnews": "corn price OR corn futures OR CBOT corn OR USDA corn",
        "feeds": [],
        "cal_keywords": ("corn",),
        "bull": _BULL_AG, "bear": _BEAR_AG, "glossary": _GLO_AGS,
    },
    "ZW=F": {
        "name": "Wheat", "short": "Wheat", "icon": "🌾", "group": "Agriculture",
        "gnews": "wheat price OR wheat futures OR CBOT wheat OR Black Sea wheat",
        "feeds": [],
        "cal_keywords": ("wheat",),
        "bull": _BULL_AG, "bear": _BEAR_AG, "glossary": _GLO_AGS + [_GLO_WHEAT_EXTRA],
    },
    "ZS=F": {
        "name": "Soybeans", "short": "Soybeans", "icon": "🫘", "group": "Agriculture",
        "gnews": "soybean price OR soybean futures OR CBOT soybeans OR soybean exports",
        "feeds": [],
        "cal_keywords": ("soybean", "soybeans"),
        "bull": _BULL_AG, "bear": _BEAR_AG, "glossary": _GLO_AGS,
    },
}

# Backward-compatible module-level defaults (used by the standalone crude news app).
BULL_WORDS = _BULL_OIL
BEAR_WORDS = _BEAR_OIL
GLOSSARY = _GLO_OIL


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def now_uk() -> dt.datetime:
    return dt.datetime.now(UK)


def tone(text: str, bull=BULL_WORDS, bear=BEAR_WORDS):
    """Return (label, score) where score = bull_hits - bear_hits for the headline.

    ``bull``/``bear`` default to the crude lexicon but are passed per-commodity by
    the fetch functions, since what's bullish for oil differs from gold or wheat."""
    t = (text or "").lower()
    b = sum(w in t for w in bull)
    s = sum(w in t for w in bear)
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
    if requests is None:
        raise RuntimeError("requests not installed — add it to requirements.txt")
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
def _parse_feed(source: str, url: str, ref: dt.datetime, days: int,
                bull=BULL_WORDS, bear=BEAR_WORDS):
    out = []
    if feedparser is None:
        return out
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
        label, score = tone(title, bull, bear)
        out.append({"source": source, "title": title, "url": e.get("link", ""),
                    "when": when, "tone": label, "score": score})
    return out


def fetch_news(symbol: str = DEFAULT_SYMBOL, days: int = 2, limit: int = 24):
    prof = PROFILES[symbol]
    bull, bear = prof["bull"], prof["bear"]
    ref = now_uk()
    q = urllib.parse.quote(f"{prof['gnews']} when:{days}d")
    gnews = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    items = _parse_feed("Google News", gnews, ref, days, bull, bear)
    for name, url in prof["feeds"]:
        items += _parse_feed(name, url, ref, days, bull, bear)

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
    if any(k in tl for k in prof["cal_keywords"]):       # commodity-specific reports
        return True
    countries = prof.get("cal_countries", ("USD",))      # metals also track China (CNY)
    if country in countries and impact == "High":        # big macro (dollar / demand)
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
            star = prof.get("icon", "•") if ev["is_oil"] else "  "
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
