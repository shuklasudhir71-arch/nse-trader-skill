#!/usr/bin/env python3
"""nse.py - day-trading tools for the Indian stock market (NSE and BSE).

Standard-library Python 3.8+. Reads free, unofficial public data:
Yahoo Finance charts (RELIANCE.NS, TCS.BO), NSE / Nifty Indices symbol lists
and Google News RSS. It never places an order.

Every time is India Standard Time (IST, UTC+5:30, no daylight saving).
Files are written to ./nse/ (change with --dir).

    python3 nse.py init                                # nse/config.json with safe defaults
    python3 nse.py clock                               # is NSE open? next open
    python3 nse.py universe --index nifty500           # symbol list -> nse/universe.json
    python3 nse.py scan daily                          # end-of-day setups -> nse/watchlist.json
    python3 nse.py scan orb --at 09:45                 # opening-range breakouts so far today
    python3 nse.py news --symbols RELIANCE,TCS         # new headlines since last check
    python3 nse.py entry check RELIANCE                # waiting / triggered / expired
    python3 nse.py entry watch RELIANCE --every 60     # until it triggers
    python3 nse.py plan --stop or-mid --target 2R      # stop and target -> nse/plan.json
    python3 nse.py sim                                 # 10,000 replays + history
    python3 nse.py size                                # shares, charges -> nse/orders.json
    python3 nse.py risk                                # PASS / BLOCK per order
    python3 nse.py ticket show                         # the order ticket, READY or not
    python3 nse.py alerts add --from-plan              # stop, target, square-off time
    python3 nse.py alerts check                        # rings what hit, once
    python3 nse.py quote RELIANCE TCS --write          # -> nse/prices.json

Global options (after the command): --dir DIR, --now "2026-11-23 09:45" (IST),
--offline FOLDER (read saved Yahoo JSON files named SYMBOL.NS_5m.json / _1d.json).

Education and tooling, not investment advice.
"""
import argparse
import collections
import csv
import datetime as dt
import email.utils
import hashlib
import io
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))
IST = dt.timedelta(hours=5, minutes=30)
OPEN = dt.time(9, 15)
CLOSE = dt.time(15, 30)
PREOPEN = dt.time(9, 0)
SESSION_MIN = 375  # 9:15 to 15:30
UA = "Mozilla/5.0 (compatible; nse-trader-skill/%s)" % VERSION
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
YAHOO_HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")
PAUSE = 0.4  # seconds between requests: be polite to free endpoints
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

Bar = collections.namedtuple("Bar", "t o h l c v")  # t = naive IST start of the bar

DEFAULT_CONFIG = {
    "_note": "Amounts in rupees. paper: true means you practise without real orders. "
             "Check the charges against your own broker's current rate card.",
    "paper": True,
    "capital": 100000,
    "max_loss_per_trade": 1000,
    "daily_loss_limit": 3000,
    "max_trades_per_day": 3,
    "max_position_pct": 50,
    "max_order_value": 100000,
    "cash_buffer_pct": 10,
    "mis_leverage": 1,
    "broker": "dhan",
    "product": "MIS",
    "exchange": "NSE",
    "min_reward_risk": 1.5,
    "no_entries_before": "09:30",
    "no_entries_after": "14:45",
    "flat_by": "15:10",
    "tick_size": 0.05,
    "range_min": 15,
    "blocked": [],
    "min_price": 50,
    "min_avg_turnover_cr": 5,
    "charges": {
        "brokerage_flat": 20.0,
        "brokerage_pct_intraday": 0.03,
        "brokerage_pct_delivery": 0.0,
        "stt_pct_intraday_sell": 0.025,
        "stt_pct_delivery": 0.1,
        "txn_pct_nse": 0.00297,
        "txn_pct_bse": 0.00375,
        "sebi_per_crore": 10.0,
        "stamp_pct_intraday_buy": 0.003,
        "stamp_pct_delivery_buy": 0.015,
        "gst_pct": 18.0
    }
}


class NSEError(Exception):
    pass


# ------------------------------------------------------------------ small helpers

def inr(x):
    """Rupees with Indian digit grouping: 123456.5 -> Rs 1,23,456.50."""
    neg = x < 0
    s = "%.2f" % abs(x)
    whole, frac = s.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return ("-" if neg else "") + "₹" + whole + "." + frac


def r2(x):
    return round(x + 0.0, 2)


def floor_tick(x, tick):
    return r2(math.floor(x / tick + 1e-9) * tick)


def ceil_tick(x, tick):
    return r2(math.ceil(x / tick - 1e-9) * tick)


def hhmm(text, what="time"):
    m = HHMM.match(str(text).strip())
    if not m:
        raise NSEError("%s wants an IST time like 09:45, not %r" % (what, text))
    return dt.time(int(m.group(1)), int(m.group(2)))


def add_min(t, minutes):
    return t + dt.timedelta(minutes=minutes)


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except ValueError as e:
        raise NSEError("%s is not valid JSON: %s" % (path, e))


def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


# ------------------------------------------------------------------ time (IST, no tz files)

def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def to_ist(utc):
    return utc + IST


def from_epoch(sec):
    return dt.datetime(1970, 1, 1) + dt.timedelta(seconds=sec)


def parse_now(text):
    """--now -> naive UTC. Without an offset the time is IST; Z or +hh:mm makes it absolute."""
    if not text:
        return utcnow()
    s = text.strip().replace("T", " ")
    try:
        if s.endswith("Z"):
            return dt.datetime.fromisoformat(s[:-1])
        x = dt.datetime.fromisoformat(s)
    except ValueError:
        raise NSEError('--now wants an IST time like "2026-11-23 09:45" (or ...Z for UTC)')
    if x.tzinfo is not None:
        return x.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return x - IST


_HOLIDAYS = None


def holidays(workdir=None):
    """{date: name}, {year: verified}. The pack's list, overridden by nse/holidays.json."""
    global _HOLIDAYS
    if _HOLIDAYS is None:
        days, years = {}, {}
        for path in (os.path.join(HERE, "holidays.json"),
                     os.path.join(workdir, "holidays.json") if workdir else None):
            if not path:
                continue
            doc = _read(path, {})
            for y, block in doc.items():
                if not y.isdigit() or not isinstance(block, dict):
                    continue
                years[int(y)] = bool(block.get("verified"))
                for d, name in (block.get("dates") or {}).items():
                    days[dt.date.fromisoformat(d)] = name
        _HOLIDAYS = (days, years)
    return _HOLIDAYS


def trading_day(d):
    """(True, '') or (False, reason)."""
    if d.weekday() >= 5:
        return False, "weekend"
    days, _ = holidays()
    if d in days:
        return False, "NSE holiday: " + days[d]
    return True, ""


def calendar_warning(d):
    _, years = holidays()
    if d.year not in years:
        return "No NSE holiday list for %d: weekdays are treated as open. Add the year to holidays.json." % d.year
    if not years[d.year]:
        return ("The %d holiday list is unverified: check it once at nseindia.com "
                "(Resources > Exchange communication > Holidays)." % d.year)
    return ""


def next_open(now_ist):
    d = now_ist.date()
    if now_ist.time() >= OPEN:
        d += dt.timedelta(days=1)
    for _ in range(30):
        if trading_day(d)[0]:
            return dt.datetime.combine(d, OPEN)
        d += dt.timedelta(days=1)
    return None


def market_status(now_ist):
    d, t = now_ist.date(), now_ist.time()
    ok, why = trading_day(d)
    st = {"now": now_ist.strftime("%Y-%m-%d %H:%M"), "date": d.isoformat()}
    if not ok:
        st.update(state="closed", reason=why)
    elif t < PREOPEN:
        st.update(state="closed", reason="before the pre-open (9:00)")
    elif t < OPEN:
        st.update(state="preopen", reason="pre-open call auction 9:00 to 9:15")
    elif t < CLOSE:
        st.update(state="open", reason="regular session 9:15 to 15:30")
    else:
        st.update(state="closed", reason="after the close (15:30)")
    if st["state"] != "open":
        nx = next_open(now_ist)
        st["next_open"] = nx.strftime("%a %Y-%m-%d %H:%M") if nx else None
    st["warning"] = calendar_warning(d)
    return st


# ------------------------------------------------------------------ symbols

def ysym(sym, exchange="NSE"):
    """RELIANCE -> RELIANCE.NS ; NSE:TCS -> TCS.NS ; BSE:500325 -> 500325.BO ; TCS.BO stays."""
    s = str(sym).strip().upper()
    if ":" in s:
        ex, s = s.split(":", 1)
        exchange = ex
    if s.endswith(".NS") or s.endswith(".BO"):
        return s
    if exchange.upper() in ("BSE", "BO"):
        return s + ".BO"
    return s + ".NS"


def split_sym(y):
    """RELIANCE.NS -> ('RELIANCE', 'NSE')."""
    if y.endswith(".BO"):
        return y[:-3], "BSE"
    if y.endswith(".NS"):
        return y[:-3], "NSE"
    return y, "NSE"


FNO = re.compile(r"(\d{2}[A-Z]{3}(\d+(\.\d+)?(CE|PE)|FUT)|\d{5,}(\.\d+)?(CE|PE))$")
CRYPTO = re.compile(r"(BTC|ETH|USDT|DOGE|SOL|XRP)|-USD$|-INR$")
EQ_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")


def equity_problem(symbol, exchange):
    """Why a symbol is not a plain NSE/BSE cash-equity symbol, or ''."""
    if exchange not in ("NSE", "BSE"):
        return "exchange %r: only NSE or BSE cash equity" % exchange
    if CRYPTO.search(symbol):
        return "%s looks like crypto: this pack trades NSE/BSE shares only" % symbol
    if FNO.search(symbol):
        return "%s looks like a futures or options contract: F&O is not allowed" % symbol
    if not EQ_SYMBOL.match(symbol):
        return "%s is not an NSE/BSE equity symbol" % symbol
    return ""


# ------------------------------------------------------------------ network

def http_get(url, ua=UA, timeout=20, accept=None):
    headers = {"User-Agent": ua, "Accept-Language": "en-IN,en;q=0.9"}
    if accept:
        headers["Accept"] = accept
    last = None
    for attempt in range(2):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429 and attempt == 0:
                time.sleep(2.0)
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            if attempt == 0:
                time.sleep(1.0)
                continue
            raise
    raise last


def yahoo_url(y, interval, rng, host=YAHOO_HOSTS[0]):
    return ("https://%s/v8/finance/chart/%s?interval=%s&range=%s&includePrePost=false"
            % (host, urllib.parse.quote(y, safe=""), interval, rng))


def parse_chart(doc, y):
    try:
        res = doc["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        err = (doc.get("chart") or {}).get("error") if isinstance(doc, dict) else None
        raise NSEError("Yahoo has no chart for %s%s" % (y, (": %s" % err.get("description")) if err else ""))
    meta = res.get("meta") or {}
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    cols = [q.get(k) or [] for k in ("open", "high", "low", "close", "volume")]
    bars = []
    for i, t in enumerate(ts):
        vals = [c[i] if i < len(c) else None for c in cols]
        if any(v is None for v in vals[:4]):
            continue
        bars.append(Bar(to_ist(from_epoch(t)), *[float(v) for v in vals[:4]], float(vals[4] or 0)))
    return meta, bars


def chart(y, interval="5m", rng="1d", offline=None):
    """(meta, bars) from Yahoo's free chart JSON, or from FOLDER/<SYM.NS>_<interval>.json."""
    if offline:
        path = os.path.join(offline, "%s_%s.json" % (y, interval))
        doc = _read(path, None)
        if doc is None:
            raise NSEError("offline: no file %s" % path)
        return parse_chart(doc, y)
    err = None
    for host in YAHOO_HOSTS:
        try:
            raw = http_get(yahoo_url(y, interval, rng, host))
            return parse_chart(json.loads(raw.decode("utf-8")), y)
        except urllib.error.HTTPError as e:
            err = "HTTP %d" % e.code
            if e.code == 404:
                break
        except urllib.error.URLError as e:
            err = str(getattr(e, "reason", e))
        except ValueError as e:
            err = "bad JSON (%s)" % e
    raise NSEError("could not read Yahoo chart for %s: %s" % (y, err))


def quote(y, offline=None):
    meta, bars = chart(y, "5m" if offline else "1m", "1d", offline=offline)
    price = meta.get("regularMarketPrice")
    t = meta.get("regularMarketTime")
    if price is None and bars:
        price, t = bars[-1].c, None
    if price is None:
        raise NSEError("no price for %s" % y)
    when = to_ist(from_epoch(t)) if t else (bars[-1].t if bars else None)
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    return {"symbol": y, "price": r2(float(price)), "time_ist": when.strftime("%Y-%m-%d %H:%M") if when else None,
            "prev_close": r2(float(prev)) if prev else None}


# ------------------------------------------------------------------ indicators

def sma(xs):
    return sum(xs) / len(xs)


def rsi(cl, n=14):
    if len(cl) < n + 1:
        return None
    g = l = 0.0
    for i in range(1, n + 1):
        d = cl[i] - cl[i - 1]
        g += max(d, 0)
        l += max(-d, 0)
    ag, al = g / n, l / n
    for i in range(n + 1, len(cl)):
        d = cl[i] - cl[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def atr(bars, n=14):
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        b, p = bars[i], bars[i - 1]
        trs.append(max(b.h - b.l, abs(b.h - p.c), abs(b.l - p.c)))
    a = sum(trs[:n]) / n
    for x in trs[n:]:
        a = (a * (n - 1) + x) / n
    return a


def daily_setups(bars):
    """Setups on daily bars (oldest first, last bar = latest close)."""
    cl = [b.c for b in bars]
    if len(cl) < 30:
        return []
    out = []
    c, p = cl[-1], cl[-2]
    hi20 = max(cl[-21:-1])
    if c > hi20:
        out.append(("breakout20", "close %.2f above the 20-day closing high %.2f" % (c, hi20)))
    s20, s20p = sma(cl[-20:]), sma(cl[-21:-1])
    if p < s20p and c > s20:
        out.append(("reclaim20", "closed back above the 20-day average %.2f" % s20))
    r, rp = rsi(cl), rsi(cl[:-1])
    if r is not None and r < 30:
        out.append(("oversold", "RSI(14) %.1f" % r))
    if r is not None and rp is not None and rp < 30 <= r:
        out.append(("bounce", "RSI(14) back above 30 (%.1f -> %.1f)" % (rp, r)))
    pole = cl[-6] / cl[-16] - 1
    if pole >= 0.10:
        top = max(b.h for b in bars[-16:-5])
        flag = bars[-5:]
        fhi, flo = max(b.h for b in flag), min(b.l for b in flag)
        size = cl[-6] - cl[-16]
        if fhi <= top * 1.01 and (top - flo) <= 0.5 * size and c >= (fhi + flo) / 2:
            out.append(("bull_flag", "%.0f%% run in 10 days, tight 5-day flag %.2f-%.2f" % (pole * 100, flo, fhi)))
    return out


# ------------------------------------------------------------------ opening range breakout

def day_bars(bars, day):
    return [b for b in bars if b.t.date() == day and OPEN <= b.t.time() < CLOSE]


def orb_state(bars, day, cutoff, range_min=15, step=5, level=None, vol_mult=None, last_entry=None):
    """Status of an opening-range breakout on `day`, using only bars complete at `cutoff` (naive IST).

    The entry is the first completed bar after the range that CLOSES above the range high
    (or above `level`). With vol_mult, that bar's volume must also be vol_mult x the
    average volume of the bars before it."""
    sb = day_bars(bars, day)
    start = dt.datetime.combine(day, OPEN)
    or_end = add_min(start, range_min)
    done = [b for b in sb if add_min(b.t, step) <= cutoff]
    res = {"date": day.isoformat(), "range_min": range_min, "step": step}
    if cutoff < or_end:
        res.update(status="forming", note="the opening range ends at %s" % or_end.strftime("%H:%M"))
        return res
    orb = [b for b in done if b.t < or_end]
    need = max(1, range_min // step)
    if len(orb) < need:
        res.update(status="no_data", note="only %d of %d opening-range bars" % (len(orb), need))
        return res
    hi, lo = max(b.h for b in orb), min(b.l for b in orb)
    trig = level if level is not None else hi
    res.update(or_high=r2(hi), or_low=r2(lo), trigger=r2(trig))
    prior = list(orb)
    last = None
    for b in done:
        if b.t < or_end:
            continue
        end = add_min(b.t, step)
        if last_entry and end.time() > last_entry:
            break
        last = b
        if b.c > trig:
            if vol_mult:
                avg = mean(x.v for x in prior)
                if avg > 0 and b.v < vol_mult * avg:
                    prior.append(b)
                    continue
            res.update(status="triggered", entry=r2(b.c), entry_time=end.strftime("%H:%M"),
                       over_pct=r2((b.c / trig - 1) * 100), volume=int(b.v), value_cr=r2(b.c * b.v / 1e7))
            return res
        prior.append(b)
    ref = last or (done[-1] if done else None)
    if last_entry and cutoff.time() >= last_entry:
        res["status"] = "expired"
        res["note"] = "no close above %.2f before %s" % (trig, last_entry.strftime("%H:%M"))
    else:
        res["status"] = "waiting"
    if ref:
        res.update(last_close=r2(ref.c), last_time=add_min(ref.t, step).strftime("%H:%M"),
                   away_pct=r2((trig / ref.c - 1) * 100))
    return res


# ------------------------------------------------------------------ config / paths

class Ctx:
    def __init__(self, a):
        self.dir = a.dir
        self.now_utc = parse_now(getattr(a, "now", None))
        self.now = to_ist(self.now_utc)
        self.today = self.now.date()
        self.offline = getattr(a, "offline", None)
        holidays(self.dir)

    def p(self, *parts):
        return os.path.join(self.dir, *parts)

    def config(self):
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        user = _read(self.p("config.json"), {})
        for k, v in user.items():
            if k == "charges" and isinstance(v, dict):
                cfg["charges"].update(v)
            else:
                cfg[k] = v
        return cfg


def need_plan(ctx):
    plan = _read(ctx.p("plan.json"), None)
    if not plan:
        raise NSEError("no %s yet: run `plan` first (after `entry check` triggers)" % ctx.p("plan.json"))
    return plan


# ------------------------------------------------------------------ commands: init / clock / universe

def cmd_init(a, ctx):
    path = ctx.p("config.json")
    if os.path.exists(path) and not a.force:
        print("%s already exists (use --force to overwrite)." % path)
        return 0
    _write(path, DEFAULT_CONFIG)
    print("Wrote %s. Edit capital, max_loss_per_trade and daily_loss_limit to your own numbers." % path)
    print("paper is true: practise first. Charges are Zerodha-style defaults; check your broker's rate card.")
    return 0


def cmd_clock(a, ctx):
    st = market_status(ctx.now)
    if a.json:
        print(json.dumps(st, indent=2))
        return 0
    line = "NSE %s (%s IST, %s)." % (st["state"].upper(), st["now"], st["reason"])
    if st.get("next_open"):
        line += " Next open: %s IST." % st["next_open"]
    print(line)
    if st["warning"]:
        print("Note: " + st["warning"])
    return 0


UNIVERSE_URLS = {
    "nifty50": "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "nifty100": "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv",
    "nifty200": "https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv",
    "nifty500": "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
    "niftybank": "https://www.niftyindices.com/IndexConstituent/ind_niftybanklist.csv",
    "midcap150": "https://www.niftyindices.com/IndexConstituent/ind_niftymidcap150list.csv",
    "smallcap250": "https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap250list.csv",
    "all": "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
}


def parse_universe_csv(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    out = []
    for r in rows:
        r = {(k or "").strip().upper(): (v or "").strip() for k, v in r.items()}
        sym = r.get("SYMBOL")
        series = r.get("SERIES", "EQ")
        if not sym or series not in ("EQ", ""):
            continue  # EQ series only: BE/BZ are trade-for-trade, no intraday
        out.append({"symbol": sym, "name": r.get("COMPANY NAME") or r.get("NAME OF COMPANY") or "",
                    "industry": r.get("INDUSTRY", "")})
    return out


def cmd_universe(a, ctx):
    if a.file:
        with open(a.file, encoding="utf-8-sig") as f:
            text = f.read()
        src = a.file
        syms = parse_universe_csv(text)
    else:
        names = [x.strip().lower() for x in a.index.split(",") if x.strip()]
        bad = [x for x in names if x not in UNIVERSE_URLS]
        if bad:
            raise NSEError("unknown index %s: choose from %s (comma list allowed)" % (", ".join(bad), ", ".join(sorted(UNIVERSE_URLS))))
        syms, seen = [], set()
        for name in names:
            url = UNIVERSE_URLS[name]
            try:
                text = http_get(url, ua=BROWSER_UA, accept="text/csv,*/*").decode("utf-8-sig")
            except (urllib.error.URLError, urllib.error.HTTPError) as e:
                raise NSEError("could not download %s (%s). NSE sites sometimes refuse scripts: open the URL "
                               "in a browser, save the CSV and run `universe --file the.csv`." % (url, e))
            for x in parse_universe_csv(text):
                if x["symbol"] not in seen:
                    seen.add(x["symbol"])
                    syms.append(x)
            if not ctx.offline:
                time.sleep(PAUSE)
        src = " + ".join(names)
    if not syms:
        raise NSEError("no symbols found in %s (expected a SYMBOL column)" % src)
    doc = {"as_of": ctx.now.strftime("%Y-%m-%d %H:%M IST"), "source": src, "exchange": "NSE", "symbols": syms}
    _write(ctx.p("universe.json"), doc)
    print("%d NSE symbols from %s -> %s" % (len(syms), src, ctx.p("universe.json")))
    return 0


def universe_symbols(ctx, a):
    if getattr(a, "symbols", None):
        return [s.strip() for s in a.symbols.split(",") if s.strip()]
    doc = _read(ctx.p("universe.json"), None)
    if not doc:
        raise NSEError("no universe yet: run `universe --index nifty500` (or pass --symbols A,B,C)")
    syms = [x["symbol"] for x in doc["symbols"]]
    return syms[: a.limit] if getattr(a, "limit", None) else syms


def names_map(ctx):
    doc = _read(ctx.p("universe.json"), {}) or {}
    return {x["symbol"]: x.get("name") or x["symbol"] for x in doc.get("symbols", [])}


# ------------------------------------------------------------------ scan

def cmd_scan(a, ctx):
    cfg = ctx.config()
    ex = a.exchange or cfg["exchange"]
    if a.mode == "orb":
        return scan_orb(a, ctx, cfg, ex)
    syms = universe_symbols(ctx, a)
    hits, errors = [], 0
    for i, s in enumerate(syms):
        y = ysym(s, ex)
        try:
            _, bars = chart(y, "1d", "6mo", ctx.offline)
        except NSEError:
            errors += 1
            continue
        bars = [b for b in bars if b.t.date() <= ctx.today]
        if len(bars) < 30:
            continue
        price = bars[-1].c
        turnover = mean(b.c * b.v for b in bars[-20:]) / 1e7
        if price < cfg["min_price"] or turnover < cfg["min_avg_turnover_cr"]:
            continue
        for kind, why in daily_setups(bars):
            if a.setup and kind not in a.setup.split(","):
                continue
            hits.append({"symbol": split_sym(y)[0], "exchange": ex, "setup": kind, "why": why,
                         "close": r2(price), "date": bars[-1].t.date().isoformat(),
                         "turnover_cr": r2(turnover)})
        if not ctx.offline and i + 1 < len(syms):
            time.sleep(PAUSE)
    hits.sort(key=lambda h: (h["setup"], -h["turnover_cr"]))
    _write(ctx.p("watchlist.json"), {"as_of": ctx.now.strftime("%Y-%m-%d %H:%M IST"), "mode": "daily",
                                     "scanned": len(syms), "hits": hits})
    print("Scanned %d symbols (%d had no data). %d setups -> %s" % (len(syms), errors, len(hits),
                                                                    ctx.p("watchlist.json")))
    for h in hits[: a.top]:
        print("  %-12s %-11s %10s  %s  (avg turnover %s cr/day)" % (h["symbol"], h["setup"], inr(h["close"]),
                                                                     h["why"], h["turnover_cr"]))
    if len(hits) > a.top:
        print("  ... %d more in the file" % (len(hits) - a.top))
    return 0


def scan_orb(a, ctx, cfg, ex):
    cutoff = dt.datetime.combine(ctx.today, hhmm(a.at, "--at")) if a.at else ctx.now
    ok, why = trading_day(ctx.today)
    if not ok:
        print("NSE is closed on %s (%s). Nothing to scan." % (ctx.today, why))
        return 1
    syms = universe_symbols(ctx, a)
    last_entry = hhmm(cfg["no_entries_after"])
    hits, waiting = [], 0
    for i, s in enumerate(syms):
        y = ysym(s, ex)
        try:
            _, bars = chart(y, "5m", "1d", ctx.offline)
        except NSEError:
            continue
        st = orb_state(bars, ctx.today, cutoff, a.range_min or cfg["range_min"], 5, None, a.volume_mult, last_entry)
        if st["status"] == "triggered":
            hits.append(dict(st, symbol=split_sym(y)[0], exchange=ex, setup="orb"))
        elif st["status"] == "waiting":
            waiting += 1
        if not ctx.offline and i + 1 < len(syms):
            time.sleep(PAUSE)
    hits.sort(key=lambda h: (h["entry_time"], -h.get("value_cr", 0)))  # earliest first; ties: most rupees traded
    _write(ctx.p("watchlist.json"), {"as_of": cutoff.strftime("%Y-%m-%d %H:%M IST"), "mode": "orb",
                                     "scanned": len(syms), "hits": hits})
    print("Opening-range breakouts at %s IST: %d of %d charts closed above their %d-minute range "
          "(%d still waiting)." % (cutoff.strftime("%H:%M"), len(hits), len(syms),
                                   a.range_min or cfg["range_min"], waiting))
    for h in hits[: a.top]:
        print("  %-12s range %.2f-%.2f, the %s bar closed at %.2f (%.2f%% over, %.1f cr traded in that bar)"
              % (h["symbol"], h["or_low"], h["or_high"], h["entry_time"], h["entry"], h["over_pct"], h.get("value_cr", 0)))
    return 0


# ------------------------------------------------------------------ news

TAGS = [
    ("results", ["results", "q1 ", "q2 ", "q3 ", "q4 ", "quarterly", "net profit", "earnings", "revenue", "ebitda"]),
    ("order", ["order win", "bags order", "order worth", "wins order", "contract worth", "secures order"]),
    ("deal", ["acquire", "acquisition", "merger", "demerger", "stake sale", "block deal", "bulk deal", "ofs"]),
    ("corporate", ["dividend", "bonus", "stock split", "buyback", "record date", "rights issue", "qip"]),
    ("broker", ["target price", "upgrade", "downgrade", "buy rating", "sell rating", "brokerage", "initiates"]),
    ("regulatory", ["sebi", "rbi ", "penalty", "probe", "raid", "nclt", "insolvency", "gst notice", "income tax"]),
    ("management", ["ceo", "md ", "resigns", "appoint", "chairman", "cfo"]),
    ("macro", ["nifty", "sensex", "rbi policy", "repo rate", "inflation", "fii", "budget"]),
]


def tag(title):
    t = " " + title.lower() + " "
    return [name for name, words in TAGS if any(w in t for w in words)] or ["general"]


def parse_rss(text):
    items = []
    root = ET.fromstring(text)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        src = it.find("source")
        when = None
        if pub:
            try:
                x = email.utils.parsedate_to_datetime(pub)
                when = x.astimezone(dt.timezone.utc).replace(tzinfo=None) if x.tzinfo else x
            except (TypeError, ValueError):
                pass
        items.append({"title": title, "link": link, "utc": when,
                      "source": (src.text or "").strip() if src is not None else ""})
    return items


def news_sources(symbol, name):
    q = '"%s" when:2d' % name if name and name != symbol else '"%s" NSE share when:2d' % symbol
    return [
        "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en" % urllib.parse.quote(q),
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=IN&lang=en-IN"
        % urllib.parse.quote(symbol + ".NS"),
    ]


def cmd_news(a, ctx):
    if a.symbols:
        syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    else:
        plan = _read(ctx.p("plan.json"), None)
        wl = _read(ctx.p("watchlist.json"), {}) or {}
        syms = list(dict.fromkeys(([plan["symbol"]] if plan else []) + [h["symbol"] for h in wl.get("hits", [])]))
        syms = syms[: a.limit]
    if not syms:
        raise NSEError("which stocks? pass --symbols RELIANCE,TCS or run a scan first")
    names = names_map(ctx)
    seen_path = ctx.p("state", "news-seen.json")
    seen = _read(seen_path, {}) or {}
    since = ctx.now_utc - dt.timedelta(hours=a.hours)
    total = 0
    for s in syms:
        items = []
        if ctx.offline:
            path = os.path.join(ctx.offline, "news_%s.xml" % s)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    items = parse_rss(f.read())
        else:
            for url in news_sources(s, names.get(s)):
                try:
                    items += parse_rss(http_get(url, ua=BROWSER_UA).decode("utf-8", "replace"))
                except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError):
                    pass
                time.sleep(PAUSE)
        fresh, keys = [], set()
        for it in items:
            if it["utc"] and it["utc"] < since:
                continue
            key = hashlib.sha1((it["title"].lower()[:90]).encode()).hexdigest()[:16]
            if key in keys or (key in seen and not a.all):
                continue
            keys.add(key)
            fresh.append(it)
            seen[key] = ctx.now_utc.strftime("%Y-%m-%dT%H:%MZ")
        fresh.sort(key=lambda x: x["utc"] or dt.datetime.min, reverse=True)
        print("%s%s: %d new" % (s, (" (%s)" % names[s]) if s in names else "", len(fresh)))
        for it in fresh[: a.top]:
            when = to_ist(it["utc"]).strftime("%d %b %H:%M") if it["utc"] else "--"
            print("  %s IST  [%s]  %s%s" % (when, ",".join(tag(it["title"])), it["title"],
                                           (" - " + it["source"]) if it["source"] and it["source"] not in it["title"] else ""))
        total += len(fresh)
    if len(seen) > 5000:
        seen = dict(sorted(seen.items(), key=lambda kv: kv[1])[-3000:])
    _write(seen_path, seen)
    if not total:
        print("Nothing new in the last %d hours." % a.hours)
    return 0


# ------------------------------------------------------------------ entry

def cmd_entry(a, ctx):
    cfg = ctx.config()
    ex = a.exchange or cfg["exchange"]
    y = ysym(a.symbol, ex)
    last_entry = hhmm(cfg["no_entries_after"])
    rng_min = a.range_min or cfg["range_min"]
    while True:
        now = to_ist(parse_now(a.now)) if a.now else to_ist(utcnow())
        ok, why = trading_day(now.date())
        if not ok or now.time() < OPEN:
            nx = next_open(now)
            print("%s: NSE not open (%s). Next open %s IST." % (split_sym(y)[0], why or "before 9:15",
                                                                nx.strftime("%a %d %b %H:%M") if nx else "?"))
            return 1
        _, bars = chart(y, "5m", "1d", ctx.offline)
        st = orb_state(bars, now.date(), now, rng_min, 5, a.level, a.volume_mult, last_entry)
        sym = split_sym(y)[0]
        if st["status"] == "triggered":
            print("%s TRIGGERED at %s IST: the 5-minute bar closed at %.2f, above %.2f (%s). Range %.2f-%.2f."
                  % (sym, st["entry_time"], st["entry"], st["trigger"],
                     "your level" if a.level else "the %d-minute range high" % rng_min, st["or_low"], st["or_high"]))
            doc = dict(st, symbol=sym, exchange=split_sym(y)[1], setup="level" if a.level else "orb",
                       checked_at=now.strftime("%Y-%m-%d %H:%M IST"))
            _write(ctx.p("entry.json"), doc)
            print("-> %s. Next: plan (stop and target) before you buy." % ctx.p("entry.json"))
            return 0
        if st["status"] == "waiting":
            print("%s waiting (%s IST): last close %.2f, needs a close above %.2f (%.2f%% away)."
                  % (sym, st.get("last_time", now.strftime("%H:%M")), st.get("last_close", 0), st["trigger"],
                     st.get("away_pct", 0)))
        elif st["status"] == "expired":
            print("%s expired: %s. No entry today." % (sym, st["note"]))
            return 2
        else:
            print("%s %s: %s" % (sym, st["status"], st.get("note", "")))
        if a.cmd != "watch":
            return 3
        if a.until and now.time() >= hhmm(a.until, "--until"):
            print("Stopped at %s as asked." % a.until)
            return 3
        time.sleep(max(15, a.every))


# ------------------------------------------------------------------ plan (exit)

def cmd_plan(a, ctx):
    cfg = ctx.config()
    tick = float(a.tick or cfg["tick_size"])
    ent = _read(ctx.p("entry.json"), {}) or {}
    sym = (a.symbol or ent.get("symbol") or "").upper()
    if not sym:
        raise NSEError("no entry: run `entry check SYMBOL` first, or pass --symbol and --entry")
    if a.symbol and ent.get("symbol") != sym:
        ent = {"symbol": sym}
    entry = float(a.entry) if a.entry else ent.get("entry")
    if entry is None:
        raise NSEError("no entry price for %s: pass --entry" % sym)
    exchange = ent.get("exchange") or a.exchange or cfg["exchange"]
    hi = float(a.or_high) if a.or_high else ent.get("or_high")
    lo = float(a.or_low) if a.or_low else ent.get("or_low")
    method = a.stop
    if method in ("or-mid", "or-low") and (hi is None or lo is None):
        raise NSEError("--stop %s needs the opening range: from entry.json, or --or-high/--or-low" % method)
    if method == "or-mid":
        raw = (hi + lo) / 2
    elif method == "or-low":
        raw = lo
    elif method.startswith("pct:"):
        raw = entry * (1 - float(method[4:]) / 100)
    elif method.startswith("price:"):
        raw = float(method[6:])
    elif method.startswith("atr:"):
        _, bars = chart(ysym(sym, exchange), "5m", "5d", ctx.offline)
        x = atr([b for b in bars if b.t.date() <= ctx.today][-40:])
        if not x:
            raise NSEError("not enough 5-minute bars for an ATR")
        raw = entry - float(method[4:]) * x
    else:
        raise NSEError("--stop must be or-mid, or-low, pct:1.5, price:2410 or atr:2")
    stop = floor_tick(raw, tick)
    risk = r2(entry - stop)
    if stop >= entry:
        raise NSEError("the stop %.2f is not below the entry %.2f: a long trade needs its stop underneath" % (stop, entry))
    if risk < 3 * tick:
        raise NSEError("the stop is only %.2f below the entry: too tight to survive normal ticks. Widen it." % risk)
    t = a.target
    if t.upper().endswith("R"):
        rm = float(t[:-1])
        target = ceil_tick(entry + rm * risk, tick)
    elif t.startswith("price:"):
        target = ceil_tick(float(t[6:]), tick)
    else:
        raise NSEError("--target must be like 2R, 1.5R or price:2500")
    if target <= entry:
        raise NSEError("the target %.2f is not above the entry %.2f" % (target, entry))
    rm = r2((target - entry) / risk)
    flat_by = a.flat_by or cfg["flat_by"]
    hhmm(flat_by, "--flat-by")
    plan = {"symbol": sym, "exchange": exchange, "date": ent.get("date") or ctx.today.isoformat(),
            "setup": ent.get("setup", "manual"), "entry": r2(entry), "entry_time": ent.get("entry_time"),
            "or_high": hi, "or_low": lo, "stop": stop, "stop_method": method, "target": target,
            "risk_per_share": risk, "r_multiple": rm, "flat_by": flat_by, "tick_size": tick,
            "made_at": ctx.now.strftime("%Y-%m-%d %H:%M IST")}
    _write(ctx.p("plan.json"), plan)
    print("Plan for %s (%s): entry %.2f, stop %.2f (%s, %.2f a share at risk, %.2f%%), target %.2f (%.1fR). "
          "Square off by %s IST either way." % (sym, exchange, entry, stop, method, risk, risk / entry * 100,
                                                 target, rm, flat_by))
    print("-> %s. Next: sim (the odds), then size." % ctx.p("plan.json"))
    return 0


# ------------------------------------------------------------------ sim (time machine)

def sessions(bars, step=5, exclude=None):
    by = collections.defaultdict(list)
    for b in bars:
        if OPEN <= b.t.time() < CLOSE and b.t.date() != exclude:
            by[b.t.date()].append(b)
    need = int(SESSION_MIN / step * 0.9)
    return {d: sorted(bs) for d, bs in by.items() if len(bs) >= need}


def slot_of(t, step=5):
    return int(((t.hour * 60 + t.minute) - (OPEN.hour * 60 + OPEN.minute)) // step)


def stop_for(method, entry, hi, lo, plan):
    if method == "or-mid":
        return (hi + lo) / 2
    if method == "or-low":
        return lo
    return entry * (plan["stop"] / plan["entry"])  # same percentage distance as the plan


def simulate(plan, bars, n=10000, seed=19, step=5):
    day = dt.date.fromisoformat(plan["date"])
    sess = sessions(bars, step, exclude=day)
    if len(sess) < 10:
        raise NSEError("only %d full past sessions of 5-minute bars: need at least 10" % len(sess))
    et = hhmm(plan.get("entry_time") or "09:30")
    s0 = slot_of(et, step)
    s1 = slot_of(hhmm(plan["flat_by"]), step)
    pool = collections.defaultdict(list)
    for d, bs in sess.items():
        for i in range(1, len(bs)):
            pc = bs[i - 1].c
            k = slot_of(bs[i].t.time(), step)
            if s0 <= k < s1 and pc > 0:
                b = bs[i]
                pool[k].append((b.o / pc, b.h / pc, b.l / pc, b.c / pc))
    slots = [k for k in range(s0, s1) if pool[k]]
    if not slots:
        raise NSEError("no bars between the entry %s and the square-off %s" % (et, plan["flat_by"]))
    rng = random.Random(seed)
    e, s, tg = plan["entry"], plan["stop"], plan["target"]
    risk = e - s
    out = {"target": 0, "stop": 0, "open": 0}
    rs, closes = [], []
    for _ in range(n):
        p = e
        res = None
        for k in slots:
            o, h, l, c = rng.choice(pool[k])
            bo, bh, bl, bc = p * o, p * h, p * l, p * c
            if bl <= s:
                res = ("stop", (min(bo, s) - e) / risk)
                break
            if bh >= tg:
                res = ("target", (tg - e) / risk)
                break
            p = bc
        if res is None:
            res = ("open", (p - e) / risk)
            closes.append(p)
        out[res[0]] += 1
        rs.append(res[1])
    closes.sort()
    pct = lambda q: r2(closes[int(q * (len(closes) - 1))]) if closes else None
    return {"n": n, "seed": seed, "sessions": len(sess), "bars_left": len(slots),
            "p_target": r2(out["target"] / n * 100), "p_stop": r2(out["stop"] / n * 100),
            "p_open": r2(out["open"] / n * 100), "avg_r": round(mean(rs), 3),
            "open_close_p10_p50_p90": [pct(0.1), pct(0.5), pct(0.9)]}


def history(plan, bars, cfg, step=5):
    day = dt.date.fromisoformat(plan["date"])
    sess = sessions(bars, step, exclude=day)
    last_entry = hhmm(cfg["no_entries_after"])
    flat = hhmm(plan["flat_by"])
    rng_min = int(cfg.get("range_min", 15))
    res = {"days": len(sess), "fired": 0, "target": 0, "stop": 0, "neither": 0, "r": []}
    for d in sorted(sess):
        bs = sess[d]
        st = orb_state(bs, d, dt.datetime.combine(d, CLOSE), rng_min, step, None, None, last_entry)
        if st["status"] != "triggered":
            continue
        e = st["entry"]
        s = stop_for(plan["stop_method"], e, st["or_high"], st["or_low"], plan)
        if s >= e:
            continue
        risk = e - s
        tg = e + plan["r_multiple"] * risk
        et = hhmm(st["entry_time"])
        res["fired"] += 1
        outcome, r = "neither", None
        lastc = e
        for b in bs:
            if b.t.time() < et:
                continue
            if add_min(b.t, step).time() > flat:
                break
            if b.l <= s:
                outcome, r = "stop", (min(b.o, s) - e) / risk
                break
            if b.h >= tg:
                outcome, r = "target", plan["r_multiple"]
                break
            lastc = b.c
        if r is None:
            r = (lastc - e) / risk
        res[outcome] += 1
        res["r"].append(r)
    res["avg_r"] = round(mean(res["r"]), 3) if res["r"] else None
    del res["r"]
    return res


def verdict(sim, hist):
    a = sim["avg_r"]
    h = hist.get("avg_r") if hist else None
    if a > 0.15 and (h is None or h > 0.1):
        return "Leans your way: the replays and the history both average above zero. Still no promise."
    if a < -0.15 or (h is not None and h < -0.15):
        return "Leans against you: on average this trade has lost money. Think twice."
    return "Close to a coin flip: no clear edge either way. That is the point of looking first."


def cmd_sim(a, ctx):
    cfg = ctx.config()
    plan = need_plan(ctx)
    _, bars = chart(ysym(plan["symbol"], plan["exchange"]), "5m", "60d", ctx.offline)
    s = simulate(plan, bars, a.n, a.seed)
    hist = history(plan, bars, cfg) if plan.get("setup") == "orb" else None
    v = verdict(s, hist)
    doc = {"plan": plan, "sim": s, "history": hist, "verdict": v, "as_of": ctx.now.strftime("%Y-%m-%d %H:%M IST")}
    if a.write:
        _write(ctx.p("sim.json"), doc)
    if a.json:
        print(json.dumps(doc, indent=2))
        return 0
    print("Time machine, %s: entry %.2f at %s, stop %.2f, target %.2f, square off %s."
          % (plan["symbol"], plan["entry"], plan.get("entry_time") or "?", plan["stop"], plan["target"], plan["flat_by"]))
    print("%d replays (seed %d) of the rest of the day, each 5-minute bar drawn from the same time of day "
          "in the last %d sessions:" % (s["n"], s["seed"], s["sessions"]))
    print("  target first %.1f%%   stop first %.1f%%   still open at square-off %.1f%%   average %.2fR"
          % (s["p_target"], s["p_stop"], s["p_open"], s["avg_r"]))
    if s["open_close_p10_p50_p90"][0] is not None:
        print("  where the open ones finish: %.2f / %.2f / %.2f (10th / 50th / 90th percentile)"
              % tuple(s["open_close_p10_p50_p90"]))
    if hist:
        print("History: the same opening-range rule fired on %d of %d past sessions: %d target, %d stop, %d neither "
              "(average %sR)." % (hist["fired"], hist["days"], hist["target"], hist["stop"], hist["neither"],
                                  "%.2f" % hist["avg_r"] if hist["avg_r"] is not None else "-"))
    print("Verdict: " + v)
    print("A simulation from the last 60 days, not a prediction: results, news or a circuit limit can do what those days never did.")
    return 0


# ------------------------------------------------------------------ size (budget) + charges

def charges(buy_val, sell_val, ch, exchange="NSE", product="MIS"):
    intraday = product == "MIS"
    pct = ch["brokerage_pct_intraday"] if intraday else ch["brokerage_pct_delivery"]
    br = sum(min(ch["brokerage_flat"], pct / 100 * v) for v in (buy_val, sell_val) if v > 0)
    stt = (ch["stt_pct_intraday_sell"] / 100 * sell_val) if intraday else (ch["stt_pct_delivery"] / 100 * (buy_val + sell_val))
    txn = (ch["txn_pct_bse"] if exchange == "BSE" else ch["txn_pct_nse"]) / 100 * (buy_val + sell_val)
    sebi = ch["sebi_per_crore"] * (buy_val + sell_val) / 1e7
    stamp = (ch["stamp_pct_intraday_buy"] if intraday else ch["stamp_pct_delivery_buy"]) / 100 * buy_val
    gst = ch["gst_pct"] / 100 * (br + txn + sebi)
    parts = {"brokerage": br, "stt": stt, "exchange_txn": txn, "sebi": sebi, "stamp_duty": stamp, "gst": gst}
    parts = {k: r2(v) for k, v in parts.items()}
    parts["total"] = r2(sum(parts.values()))
    return parts


def fresh_price(ctx, sym, minutes=15):
    doc = _read(ctx.p("prices.json"), {}) or {}
    x = (doc.get("prices") or {}).get(sym)
    if not x or not x.get("time_ist"):
        return None
    t = dt.datetime.strptime(x["time_ist"], "%Y-%m-%d %H:%M")
    if abs((ctx.now - t).total_seconds()) > minutes * 60:
        return None
    return x["price"]


def size_order(plan, cfg, price_now=None):
    ch = cfg["charges"]
    e, s, tg = plan["entry"], plan["stop"], plan["target"]
    worst = max(e, price_now or 0)
    risk = worst - s
    lev = max(1.0, float(cfg.get("mis_leverage", 1))) if cfg["product"] == "MIS" else 1.0
    buying_power = cfg["capital"] * (1 - cfg["cash_buffer_pct"] / 100) * lev
    limits = {
        "max loss per trade": math.floor(cfg["max_loss_per_trade"] / risk) if risk > 0 else 0,
        "position cap": math.floor(cfg["capital"] * lev * cfg["max_position_pct"] / 100 / worst),
        "max order value": math.floor(cfg["max_order_value"] / worst),
        "cash after buffer": math.floor(buying_power / worst),
    }
    qty = min(limits.values())
    decided = [k for k, v in limits.items() if v == qty][0]
    return qty, decided, limits, risk, worst, lev


def dhan_super_order(o):
    """DhanHQ v2 Super Order: entry + target + stop-loss in one; when one exit fills, the other is cancelled."""
    return {"dhanClientId": "<your Dhan client id>", "correlationId": o["id"][:25],
            "transactionType": "BUY", "exchangeSegment": "BSE_EQ" if o["exchange"] == "BSE" else "NSE_EQ",
            "productType": "INTRADAY" if o["product"] == "MIS" else "CNC", "orderType": "LIMIT",
            "securityId": "<%s security id from Dhan's scrip master>" % o["symbol"],
            "quantity": o["qty"], "price": o["entry"], "targetPrice": o["target"],
            "stopLossPrice": o["stop"], "trailingJump": 0}


def kite_legs(o):
    base = {"variety": "regular", "exchange": o["exchange"], "tradingsymbol": o["symbol"],
            "quantity": o["qty"], "product": o["product"], "validity": "DAY"}
    return [
        dict(base, transaction_type="BUY", order_type="LIMIT", price=o["entry"], tag="entry"),
        dict(base, transaction_type="SELL", order_type="SL-M", trigger_price=o["stop"], tag="stop"),
    ]


def cmd_size(a, ctx):
    cfg = ctx.config()
    plan = need_plan(ctx)
    if plan["date"] != ctx.today.isoformat() and not a.any_day:
        raise NSEError("the plan is for %s and today is %s (IST): make a new plan" % (plan["date"], ctx.today))
    pnow = fresh_price(ctx, plan["symbol"])
    qty, decided, limits, risk, worst, lev = size_order(plan, cfg, pnow)
    sym = plan["symbol"]
    if qty < 1:
        print("No trade: even 1 share of %s breaks a limit (%s). Risk per share %.2f vs max loss %s. "
              "A smaller order would not fix a stop that is too far away." % (sym, decided, risk, inr(cfg["max_loss_per_trade"])))
        _write(ctx.p("orders.json"), {"as_of": ctx.now.strftime("%Y-%m-%d %H:%M IST"), "orders": []})
        return 2
    val = qty * plan["entry"]
    ch_stop = charges(val, qty * plan["stop"], cfg["charges"], plan["exchange"], cfg["product"])
    ch_tgt = charges(val, qty * plan["target"], cfg["charges"], plan["exchange"], cfg["product"])
    loss = qty * (plan["entry"] - plan["stop"])
    gain = qty * (plan["target"] - plan["entry"])
    oid = "%s-%s-%s" % (sym, plan["date"], (plan.get("entry_time") or "0000").replace(":", ""))
    order = {"id": oid, "symbol": sym, "exchange": plan["exchange"], "side": "BUY", "qty": qty,
             "product": cfg["product"], "entry": plan["entry"], "stop": plan["stop"], "target": plan["target"],
             "flat_by": plan["flat_by"], "date": plan["date"], "value": r2(val),
             "max_loss_at_stop": r2(loss), "gain_at_target": r2(gain),
             "charges_if_stop": ch_stop, "charges_if_target": ch_tgt, "paper": bool(cfg["paper"])}
    order["kite"] = kite_legs(order)
    order["dhan"] = dhan_super_order(order)
    _write(ctx.p("orders.json"), {"as_of": ctx.now.strftime("%Y-%m-%d %H:%M IST"), "orders": [order]})
    print("%s: %d shares at %.2f = %s (%s decided it; limits %s)."
          % (sym, qty, plan["entry"], inr(val), decided, ", ".join("%s %d" % kv for kv in limits.items())))
    if pnow and pnow > plan["entry"]:
        print("  The last price %.2f is above the plan's entry, so risk was measured from %.2f." % (pnow, worst))
    print("  At the stop %.2f: loss %s + charges %s = %s." % (plan["stop"], inr(loss), inr(ch_stop["total"]),
                                                              inr(loss + ch_stop["total"])))
    print("  At the target %.2f: gain %s - charges %s = %s." % (plan["target"], inr(gain), inr(ch_tgt["total"]),
                                                                inr(gain - ch_tgt["total"])))
    if lev > 1:
        print("  Using %.0fx MIS leverage: losses are on the full position, not on your margin." % lev)
    print("-> %s (%s, %s). Nothing placed. Next: risk." % (ctx.p("orders.json"), cfg["product"],
                                                           "PAPER" if cfg["paper"] else "LIVE"))
    return 0


# ------------------------------------------------------------------ risk

def order_hash(o):
    keys = ("id", "symbol", "exchange", "side", "qty", "product", "entry", "stop", "target", "date")
    return hashlib.sha256(json.dumps({k: o.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()[:16]


def risk_check(o, cfg, ctx, trades_today, passed_today):
    bad, warn = [], []
    sym, ex = o.get("symbol", ""), o.get("exchange", "")
    p = equity_problem(sym, ex)
    if p:
        bad.append("asset: " + p)
    if sym in [b.upper() for b in cfg.get("blocked", [])]:
        bad.append("blocked: %s is on your blocked list" % sym)
    if o.get("side") != "BUY":
        bad.append("side: entries are long only (BUY); short selling is not allowed")
    if o.get("product") not in ("MIS", "CNC"):
        bad.append("product: %r (only MIS intraday or CNC delivery)" % o.get("product"))
    qty, e, s, tg = o.get("qty", 0), o.get("entry"), o.get("stop"), o.get("target")
    if not isinstance(qty, int) or qty < 1:
        bad.append("qty: %r is not a whole number of shares" % qty)
        qty = 0
    if s is None:
        bad.append("stop: no stop-loss: every day trade needs one")
    elif e is None or s >= e:
        bad.append("stop: %s is not below the entry %s" % (s, e))
    if tg is None or e is None or tg <= e:
        bad.append("target: %s is not above the entry %s" % (tg, e))
    if bad:
        return bad, warn
    worst = max(e, fresh_price(ctx, sym) or 0)
    loss = qty * (worst - s)
    if loss > cfg["max_loss_per_trade"] + 1e-6:
        bad.append("max loss: %s at the stop (from %.2f) is over your %s per trade"
                   % (inr(loss), worst, inr(cfg["max_loss_per_trade"])))
    rr = (tg - e) / (e - s)
    if rr + 1e-9 < cfg["min_reward_risk"]:
        bad.append("reward:risk %.2f is under your minimum %.2f" % (rr, cfg["min_reward_risk"]))
    day_risk = sum(t.get("max_loss", 0) for t in trades_today) + sum(x["loss"] for x in passed_today
                                                                      if x["hash"] != order_hash(o))
    if day_risk + loss > cfg["daily_loss_limit"] + 1e-6:
        bad.append("daily limit: %s already at risk today + %s is over your %s"
                   % (inr(day_risk), inr(loss), inr(cfg["daily_loss_limit"])))
    n = len(trades_today) + len([x for x in passed_today if x["hash"] != order_hash(o)])
    if n + 1 > cfg["max_trades_per_day"]:
        bad.append("trades per day: this would be trade %d, your limit is %d" % (n + 1, cfg["max_trades_per_day"]))
    lev = max(1.0, float(cfg.get("mis_leverage", 1))) if o["product"] == "MIS" else 1.0
    val = qty * e
    if val > cfg["max_order_value"] + 1e-6:
        bad.append("order size: %s is over your max order %s" % (inr(val), inr(cfg["max_order_value"])))
    if val > cfg["capital"] * lev * cfg["max_position_pct"] / 100 + 1e-6:
        bad.append("position cap: %s is over %s%% of your capital" % (inr(val), cfg["max_position_pct"]))
    if val > cfg["capital"] * (1 - cfg["cash_buffer_pct"] / 100) * lev + 1e-6:
        bad.append("cash: %s leaves less than your %s%% cash buffer" % (inr(val), cfg["cash_buffer_pct"]))
    if o.get("date") != ctx.today.isoformat():
        bad.append("date: the order is for %s, today is %s" % (o.get("date"), ctx.today))
    ok, why = trading_day(ctx.today)
    t = ctx.now.time()
    if not ok:
        bad.append("window: NSE is closed today (%s)" % why)
    elif not (OPEN <= t < CLOSE):
        bad.append("window: the market is closed (%s IST; the session is 09:15 to 15:30)" % ctx.now.strftime("%H:%M"))
    else:
        if t < hhmm(cfg["no_entries_before"]):
            bad.append("window: no new entries before %s IST (it is %s)" % (cfg["no_entries_before"], ctx.now.strftime("%H:%M")))
        if t > hhmm(cfg["no_entries_after"]):
            bad.append("window: no new entries after %s IST (it is %s)" % (cfg["no_entries_after"], ctx.now.strftime("%H:%M")))
    if lev > 1:
        warn.append("MIS leverage %.0fx is on: a loss is on the full %s position" % (lev, inr(val)))
    if not cfg["paper"]:
        warn.append("paper is false: this would be a LIVE order with real money")
    w = calendar_warning(ctx.today)
    if w:
        warn.append(w)
    return bad, warn


def today_state(ctx):
    d = ctx.today.isoformat()
    trades = [t for t in (_read(ctx.p("state", "trades.json"), []) or []) if t.get("date") == d]
    log = _read(ctx.p("state", "risk.json"), {}) or {}
    passed = [dict(v, hash=k) for k, v in (log.get(d) or {}).items() if v.get("verdict") == "PASS"]
    return trades, passed, log


def cmd_risk(a, ctx):
    cfg = ctx.config()
    orders = (_read(ctx.p("orders.json"), {}) or {}).get("orders", [])
    if not orders:
        print("No orders in %s: run size first." % ctx.p("orders.json"))
        return 1
    trades, passed, log = today_state(ctx)
    d = ctx.today.isoformat()
    day = log.setdefault(d, {})
    npass = 0
    for o in orders:
        bad, warn = risk_check(o, cfg, ctx, trades, passed)
        h = order_hash(o)
        v = "BLOCK" if bad else "PASS"
        loss = o.get("qty", 0) * (o.get("entry", 0) - o.get("stop", 0)) if o.get("stop") else 0
        day[h] = {"id": o.get("id"), "verdict": v, "loss": r2(loss), "reasons": bad,
                  "at": ctx.now.strftime("%H:%M IST")}
        if v == "PASS":
            npass += 1
            passed.append(dict(day[h], hash=h))
            print("PASS %s: buy %d %s at %.2f, stop %.2f, target %.2f, max loss %s."
                  % (o["id"], o["qty"], o["symbol"], o["entry"], o["stop"], o["target"], inr(loss)))
        else:
            print("BLOCK %s:" % o.get("id"))
            for r in bad:
                print("  - " + r)
        for w in warn:
            print("  note: " + w)
    _write(ctx.p("state", "risk.json"), {k: v for k, v in log.items() if k >= (ctx.today - dt.timedelta(days=10)).isoformat()})
    print("%d passed, %d blocked at %s IST." % (npass, len(orders) - npass, ctx.now.strftime("%H:%M")))
    return 0 if npass == len(orders) else 2


# ------------------------------------------------------------------ ticket (broker)

def cmd_ticket(a, ctx):
    cfg = ctx.config()
    orders = (_read(ctx.p("orders.json"), {}) or {}).get("orders", [])
    _, _, log = today_state(ctx)
    day = log.get(ctx.today.isoformat(), {})
    if a.cmd == "flatten":
        qty = a.qty
        ex = a.exchange or cfg["exchange"]
        leg = {"variety": "regular", "exchange": ex, "tradingsymbol": a.symbol.upper(), "transaction_type": "SELL",
               "quantity": qty, "product": a.product or cfg["product"], "order_type": "MARKET", "validity": "DAY",
               "tag": "squareoff"}
        print("SQUARE-OFF ticket: SELL %d %s at market (%s). Getting out is never blocked by a day limit." %
              (qty, a.symbol.upper(), leg["product"]))
        if cfg.get("broker", "dhan") == "dhan":
            print("In the Dhan app: Positions > %s > Exit. A Super Order's open target and stop-loss legs are cancelled "
                  "with it; check that no leg is left open." % a.symbol.upper())
        else:
            print("Cancel the open SL-M stop order for %s first, or it may sell again and leave you short." % a.symbol.upper())
        print("Kite place_order parameters:\n" + json.dumps(leg, indent=2))
        print("Needs your explicit yes in the chat before anyone places it.")
        return 0
    if a.cmd == "fill":
        trades = _read(ctx.p("state", "trades.json"), []) or []
        o = next((x for x in orders if x["id"] == a.id), None)
        if not o:
            raise NSEError("no order %s in orders.json" % a.id)
        price = a.price or o["entry"]
        trades.append({"id": o["id"], "date": ctx.today.isoformat(), "symbol": o["symbol"], "qty": o["qty"],
                       "fill": price, "stop": o["stop"], "max_loss": r2(o["qty"] * (price - o["stop"])),
                       "status": a.status, "at": ctx.now.strftime("%H:%M IST")})
        _write(ctx.p("state", "trades.json"), trades)
        print("Recorded %s %s (%d at %.2f). It now counts toward today's trades and daily loss limit."
              % (a.status, o["id"], o["qty"], price))
        return 0
    if a.cmd == "confirm":
        o = next((x for x in orders if x["id"] == a.id), None)
        if not o:
            raise NSEError("no order %s in orders.json" % a.id)
        v = day.get(order_hash(o), {})
        if v.get("verdict") != "PASS":
            raise NSEError("%s has not passed the risk check today (or changed since): run risk again" % a.id)
        if not re.search(r"\b(yes|haan|ha|han|haa|ji haan|place it)\b|हाँ|हां", a.said.lower()):
            raise NSEError("that is not an explicit yes: %r" % a.said)
        appr = _read(ctx.p("state", "approvals.json"), []) or []
        appr.append({"id": a.id, "hash": order_hash(o), "said": a.said, "at": ctx.now.strftime("%Y-%m-%d %H:%M IST"),
                     "paper": bool(cfg["paper"])})
        _write(ctx.p("state", "approvals.json"), appr)
        print("Recorded the yes for %s word for word: %r." % (a.id, a.said))
        return 0
    # show
    if not orders:
        print("No orders: run size, then risk.")
        return 1
    for o in orders:
        v = day.get(order_hash(o), {})
        ready = v.get("verdict") == "PASS"
        mode = "PAPER" if o.get("paper", True) else "LIVE (real money)"
        print("=" * 64)
        print("%s  %s  [%s]" % ("READY" if ready else "NOT READY", o["id"], mode))
        if not ready:
            print("  " + ("blocked: " + "; ".join(v.get("reasons", [])) if v else "not risk-checked today (or changed since): run risk"))
        print("  BUY %d %s on %s, %s, limit %.2f  (about %s)" % (o["qty"], o["symbol"], o["exchange"], o["product"],
                                                                o["entry"], inr(o["value"])))
        print("  Stop-loss: SL-M sell at trigger %.2f -> max loss %s + about %s charges"
              % (o["stop"], inr(o["max_loss_at_stop"]), inr(o["charges_if_stop"]["total"])))
        print("  Target: %.2f -> gain %s - about %s charges (price alert, then you sell)"
              % (o["target"], inr(o["gain_at_target"]), inr(o["charges_if_target"]["total"])))
        print("  Square off by %s IST whatever happens." % o["flat_by"])
        if ready and cfg.get("broker", "dhan") == "dhan":
            print("  In the Dhan app: %s > Buy > Super Order. Product %s, Limit %.2f, Qty %d, Target %.2f, "
                  "Stop Loss %.2f. When one exit fills, Dhan cancels the other."
                  % (o["symbol"], "Intraday" if o["product"] == "MIS" else "Delivery", o["entry"], o["qty"],
                     o["target"], o["stop"]))
            print("  DhanHQ API (POST /v2/super/orders):\n    " + json.dumps(o.get("dhan") or dhan_super_order(o)))
        elif ready:
            print("  Kite place_order parameters, in order (entry first; the stop only after the entry fills):")
            for leg in o["kite"]:
                print("    " + json.dumps(leg))
    print("=" * 64)
    print("Nothing is placed by this tool. An order goes out only after you type yes for that exact ticket.")
    return 0


# ------------------------------------------------------------------ deals (bulk and block deals)

DEALS_URLS = {"bulk": "https://archives.nseindia.com/content/equities/bulk.csv",
              "block": "https://archives.nseindia.com/content/equities/block.csv"}
PROP_WORDS = ["ALPHAGREP", "QE SECURITIES", "JUMP TRADING", "GRAVITON", "IRAGE", "NK SECURITIES", "HRTI",
              "TOWER RESEARCH", "MICROCURVES", "JUNOMONETA", "SILVERLEAF", "PACE STOCK", "SUNRISE GILTS",
              "ELIXIR WEALTH", "MANSI SHARE", "QUADEYE", "DOLAT", "XTX", "CITADEL", "VIRTU", "OPTIVER",
              "NUVAMA WEALTH", "SHARE INDIA", "ESTEE ADVISORS", "NEXTBILLION", "BEACON STOCK"]
KIND_WORDS = [
    ("mutual fund", ["MUTUAL FUND", " MF ", " MF-", "AMC ", "ASSET MANAGEMENT"]),
    ("insurance", ["INSURANCE", "LIC OF INDIA", "LIFE INSURANCE"]),
    ("foreign fund", ["FPI", "FUND", "PTE", "LLC", "PLC", "SICAV", "MAURITIUS", "SINGAPORE", "CAYMAN",
                      "LP ", "L.P", "INC", "MASTER", "TRUST", "CAPITAL"]),
    ("company", ["PRIVATE LIMITED", "PVT LTD", "LIMITED", "LTD", "LLP"]),
]


def deal_kind(client):
    c = " " + client.upper() + " "
    if any(w in c for w in PROP_WORDS):
        return "prop/HFT"
    for name, words in KIND_WORDS:
        if any(w in c for w in words):
            return name
    return "individual"


def parse_deals(text, kind):
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        try:
            qty = int(float(r.get("Quantity Traded", "0").replace(",", "")))
            px = float(r.get("Trade Price / Wght. Avg. Price", "0").replace(",", ""))
            d = dt.datetime.strptime(r["Date"], "%d-%b-%Y").date()
        except (ValueError, KeyError):
            continue
        side = r.get("Buy/Sell", "").upper()
        if side not in ("BUY", "SELL"):
            continue
        out.append({"date": d.isoformat(), "symbol": r.get("Symbol", "").upper(), "name": r.get("Security Name", ""),
                    "client": r.get("Client Name", ""), "side": side, "qty": qty, "price": px,
                    "value_cr": r2(qty * px / 1e7), "type": kind, "who": deal_kind(r.get("Client Name", ""))})
    return out


def summarize_deals(deals):
    """Per stock: net rupee value by real investors (prop/HFT and same-day round trips left out)."""
    by_client = collections.defaultdict(lambda: {"buy": 0, "sell": 0, "bv": 0.0, "sv": 0.0})
    for d in deals:
        k = (d["symbol"], d["client"])
        x = by_client[k]
        if d["side"] == "BUY":
            x["buy"] += d["qty"]
            x["bv"] += d["qty"] * d["price"]
        else:
            x["sell"] += d["qty"]
            x["sv"] += d["qty"] * d["price"]
    stocks = collections.defaultdict(lambda: {"net_cr": 0.0, "buyers": [], "sellers": [], "skipped": 0, "name": ""})
    names = {d["symbol"]: d["name"] for d in deals}
    kinds = {(d["symbol"], d["client"]): (d["who"], d["type"]) for d in deals}
    for (sym, client), x in by_client.items():
        st = stocks[sym]
        st["name"] = names.get(sym, "")
        who, typ = kinds[(sym, client)]
        round_trip = x["buy"] and x["sell"] and min(x["buy"], x["sell"]) >= 0.8 * max(x["buy"], x["sell"])
        if who == "prop/HFT" or round_trip:
            st["skipped"] += 1
            continue
        net = (x["bv"] - x["sv"]) / 1e7
        if abs(net) < 0.01:
            continue
        st["net_cr"] += net
        e = {"client": client, "who": who, "type": typ, "net_cr": r2(net),
             "qty": x["buy"] - x["sell"], "avg_price": r2((x["bv"] / x["buy"]) if net > 0 and x["buy"] else
                                                          (x["sv"] / x["sell"]) if x["sell"] else 0)}
        (st["buyers"] if net > 0 else st["sellers"]).append(e)
    out = []
    for sym, st in stocks.items():
        if not st["buyers"] and not st["sellers"]:
            continue
        st["buyers"].sort(key=lambda e: -e["net_cr"])
        st["sellers"].sort(key=lambda e: e["net_cr"])
        out.append(dict(st, symbol=sym, net_cr=r2(st["net_cr"])))
    out.sort(key=lambda s: -abs(s["net_cr"]))
    return out


def cmd_deals(a, ctx):
    deals = []
    for kind, url in DEALS_URLS.items():
        if ctx.offline:
            path = os.path.join(ctx.offline, "%s.csv" % kind)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8-sig") as f:
                text = f.read()
        else:
            try:
                text = http_get(url, ua=BROWSER_UA, accept="text/csv,*/*").decode("utf-8-sig", "replace")
            except (urllib.error.URLError, urllib.error.HTTPError) as e:
                print("could not download %s deals (%s)" % (kind, e))
                continue
        deals += parse_deals(text, kind)
    if not deals:
        raise NSEError("no bulk or block deals downloaded")
    day = max(d["date"] for d in deals)
    uni = set()
    if a.universe:
        doc = _read(ctx.p("universe.json"), None)
        if not doc:
            raise NSEError("--universe needs nse/universe.json: run `universe --index nifty500` first")
        uni = {x["symbol"] for x in doc["symbols"]}
    stocks = summarize_deals(deals)
    if uni:
        stocks = [s for s in stocks if s["symbol"] in uni]
    if a.symbols:
        want = {x.strip().upper() for x in a.symbols.split(",")}
        stocks = [s for s in stocks if s["symbol"] in want]
    stocks = [s for s in stocks if abs(s["net_cr"]) >= a.min_cr]
    _write(ctx.p("deals.json"), {"date": day, "as_of": ctx.now.strftime("%Y-%m-%d %H:%M IST"),
                                 "raw": len(deals), "stocks": stocks})
    print("NSE bulk + block deals of %s: %d deals; %d stocks with real-investor buying or selling of at least "
          "%s crore (prop/HFT desks and same-day round trips left out)." % (day, len(deals), len(stocks), a.min_cr))
    for s in stocks[: a.top]:
        print("  %-12s net %s %.2f cr  %s" % (s["symbol"], "BUY " if s["net_cr"] > 0 else "SELL", abs(s["net_cr"]), s["name"]))
        for e in (s["buyers"] + s["sellers"])[:3]:
            print("      %-4s %s cr  %s (%s, %s deal) at %.2f" % ("buy" if e["net_cr"] > 0 else "sell", "%.2f" % abs(e["net_cr"]),
                                                                e["client"], e["who"], e["type"], e["avg_price"]))
    if len(stocks) > a.top:
        print("  ... %d more in %s" % (len(stocks) - a.top, ctx.p("deals.json")))
    print("Bulk = over 0.5% of a company's shares in a day; block = a single trade of 10 crore+ in the block window. "
          "Public data from NSE, end of day. A big buyer is information, not a reason to buy.")
    return 0


# ------------------------------------------------------------------ results (quarterly earnings)

FUND_TYPES = ["quarterlyTotalRevenue", "quarterlyOperatingIncome", "quarterlyNetIncome", "quarterlyDilutedEPS"]


def fiscal_label(d):
    """Indian fiscal quarter: 2026-09-30 -> Q2 FY27."""
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(d.month)
    fy = d.year + 1 if d.month >= 4 else d.year
    return ("Q%d FY%02d" % (q, fy % 100)) if q else d.isoformat()


def parse_fundamentals(doc):
    """{date: {revenue, operating, net, eps}} from Yahoo's fundamentals-timeseries JSON (rupees)."""
    keys = {"quarterlyTotalRevenue": "revenue", "quarterlyOperatingIncome": "operating",
            "quarterlyNetIncome": "net", "quarterlyDilutedEPS": "eps"}
    out = collections.defaultdict(dict)
    for r in ((doc.get("timeseries") or {}).get("result") or []):
        t = ((r.get("meta") or {}).get("type") or [None])[0]
        if t not in keys:
            continue
        for x in r.get(t) or []:
            if x and x.get("asOfDate") and (x.get("reportedValue") or {}).get("raw") is not None:
                out[dt.date.fromisoformat(x["asOfDate"])][keys[t]] = float(x["reportedValue"]["raw"])
    return dict(out)


def fundamentals(y, offline=None, now_utc=None):
    if offline:
        doc = _read(os.path.join(offline, "%s_fund.json" % y), None)
        if doc is None:
            raise NSEError("offline: no file %s_fund.json" % y)
        return parse_fundamentals(doc)
    p2 = int(((now_utc or utcnow()) - dt.datetime(1970, 1, 1)).total_seconds())
    p1 = p2 - 86400 * 900
    err = None
    for host in YAHOO_HOSTS:
        url = ("https://%s/ws/fundamentals-timeseries/v1/finance/timeseries/%s?type=%s&period1=%d&period2=%d"
               % (host, urllib.parse.quote(y, safe=""), ",".join(FUND_TYPES), p1, p2))
        try:
            return parse_fundamentals(json.loads(http_get(url).decode("utf-8")))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
            err = e
    raise NSEError("could not read quarterly results for %s: %s" % (y, err))


def pct_change(new, old):
    if new is None or old in (None, 0):
        return None
    return (new - old) / abs(old) * 100


def results_summary(q):
    dates = sorted(q)
    if not dates:
        return None
    last = dates[-1]
    yago = next((d for d in dates if abs((d - last.replace(year=last.year - 1)).days) <= 5), None)
    prev = dates[-2] if len(dates) > 1 else None
    L = q[last]
    out = {"quarter": fiscal_label(last), "date": last.isoformat(), "rows": [], "notes": []}
    for d in dates[-6:]:
        x = q[d]
        rev = x.get("revenue")
        out["rows"].append({"quarter": fiscal_label(d), "revenue_cr": r2(rev / 1e7) if rev else None,
                            "op_margin": r2(x["operating"] / rev * 100) if rev and x.get("operating") is not None else None,
                            "net_cr": r2(x["net"] / 1e7) if x.get("net") is not None else None,
                            "net_margin": r2(x["net"] / rev * 100) if rev and x.get("net") is not None else None,
                            "eps": x.get("eps")})
    for key in ("revenue", "net", "eps"):
        if yago:
            out["yoy_" + key] = r2(pct_change(L.get(key), q[yago].get(key))) if pct_change(L.get(key), q[yago].get(key)) is not None else None
        if prev:
            out["qoq_" + key] = r2(pct_change(L.get(key), q[prev].get(key))) if pct_change(L.get(key), q[prev].get(key)) is not None else None
    shown = dates[-6:]
    missing, d = [], shown[0]
    while d < last:
        m = d.month + 3
        d = dt.date(d.year + (m - 1) // 12, (m - 1) % 12 + 1, 1)
        d = dt.date(d.year, d.month, [31, 29 if d.year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][d.month - 1])
        if d < last and not any(abs((x - d).days) <= 5 for x in shown):
            missing.append(fiscal_label(d))
    if missing:
        out["notes"].append("missing in the free data: " + ", ".join(missing))
    if not yago:
        out["notes"].append("the same quarter a year earlier is missing, so no year-on-year change")
    return out


def cmd_results(a, ctx):
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    names = names_map(ctx)
    docs = {}
    for i, s in enumerate(syms):
        y = ysym(s, a.exchange or "NSE")
        try:
            q = fundamentals(y, ctx.offline, ctx.now_utc)
        except NSEError as e:
            print("%s: %s" % (s, e))
            continue
        sm = results_summary(q)
        if not sm:
            print("%s: no quarterly results in the free data." % s)
            continue
        docs[s] = sm
        print("%s%s: latest quarter %s (ended %s). Amounts in crore rupees." % (s, (" (%s)" % names[s]) if s in names else "",
                                                                            sm["quarter"], sm["date"]))
        print("  %-9s %12s %9s %12s %9s %8s" % ("quarter", "revenue", "op marg", "net profit", "net marg", "EPS"))
        f = lambda v, fmt: (fmt % v) if v is not None else "-"
        for r in sm["rows"]:
            print("  %-9s %12s %9s %12s %9s %8s" % (r["quarter"], f(r["revenue_cr"], "%.0f"), f(r["op_margin"], "%.1f%%"),
                                                     f(r["net_cr"], "%.0f"), f(r["net_margin"], "%.1f%%"), f(r["eps"], "%.2f")))
        bits = []
        for key, label in (("revenue", "revenue"), ("net", "net profit"), ("eps", "EPS")):
            y_ = sm.get("yoy_" + key)
            q_ = sm.get("qoq_" + key)
            if y_ is not None or q_ is not None:
                bits.append("%s %s" % (label, ", ".join(x for x in (("%+.1f%% YoY" % y_) if y_ is not None else "",
                                                                     ("%+.1f%% QoQ" % q_) if q_ is not None else "") if x)))
        if bits:
            print("  Change: " + "; ".join(bits) + ".")
        for n in sm["notes"]:
            print("  Note: " + n + ".")
        if not ctx.offline and i + 1 < len(syms):
            time.sleep(PAUSE)
    if docs:
        _write(ctx.p("results.json"), {"as_of": ctx.now.strftime("%Y-%m-%d %H:%M IST"), "stocks": docs})
    print("Free, unofficial Yahoo data (consolidated as Yahoo reports it). Official numbers: the company's filing on nseindia.com.")
    return 0 if docs else 1


# ------------------------------------------------------------------ review (paper results of the daily routine)

def replay_day(sym_bars, day, cfg, at, picks, step=5):
    """What the 9:50 routine would have suggested on `day`, and how each pick ended by flat_by.

    Rule: opening-range breakout at `at`, earliest triggers first (ties: the most rupees traded in
    the breakout bar), stop at the range midpoint,
    target 2R, size with the config, square off at flat_by. A bar touching both = stop."""
    tick = float(cfg["tick_size"])
    last_entry = hhmm(cfg["no_entries_after"])
    flat = hhmm(cfg["flat_by"])
    cutoff = dt.datetime.combine(day, at)
    cands = []
    for sym, bars in sym_bars.items():
        st = orb_state(bars, day, cutoff, int(cfg["range_min"]), step, None, None, last_entry)
        if st["status"] == "triggered":
            cands.append((st["entry_time"], -st.get("value_cr", 0), sym, st))
    cands.sort(key=lambda c: c[:3])  # earliest first; ties: most rupees traded in the breakout bar
    trades = []
    for et, _, sym, st in cands:
        if len(trades) >= picks:
            break
        e = st["entry"]
        stop = floor_tick((st["or_high"] + st["or_low"]) / 2, tick)
        if stop >= e or e - stop < 3 * tick:
            continue
        tgt = ceil_tick(e + 2 * (e - stop), tick)
        qty = size_order({"entry": e, "stop": stop, "target": tgt}, cfg)[0]
        if qty < 1:
            continue
        exit_px, how = None, "square-off"
        lastc = e
        for b in day_bars(sym_bars[sym], day):
            if b.t.time() < hhmm(et):
                continue
            if add_min(b.t, step).time() > flat:
                break
            if b.l <= stop:
                exit_px, how = min(b.o, stop), "stop"
                break
            if b.h >= tgt:
                exit_px, how = tgt, "target"
                break
            lastc = b.c
        if exit_px is None:
            exit_px = lastc
        ch = charges(qty * e, qty * exit_px, cfg["charges"], "NSE", cfg["product"])["total"]
        pnl = qty * (exit_px - e) - ch
        trades.append({"symbol": sym, "entry_time": et, "entry": e, "stop": stop, "target": tgt, "qty": qty,
                       "exit": r2(exit_px), "how": how, "pnl": r2(pnl)})
    return trades


def cmd_review(a, ctx):
    cfg = ctx.config()
    syms = universe_symbols(ctx, a)
    at = hhmm(a.at, "--at")
    sym_bars = {}
    for i, s in enumerate(syms):
        try:
            sym_bars[s] = chart(ysym(s, "NSE"), "5m", "60d", ctx.offline)[1]
        except NSEError:
            pass
        if not ctx.offline and i + 1 < len(syms):
            time.sleep(PAUSE)
    days = sorted({b.t.date() for bars in sym_bars.values() for b in bars})
    done_today = ctx.now.time() >= hhmm(cfg["flat_by"])
    days = [d for d in days if trading_day(d)[0] and (d < ctx.today or (d == ctx.today and done_today))]
    days = days[-a.days:]
    if not days:
        print("No finished trading days in the data yet.")
        return 1
    total, wins, losses, n, peak, dd, eq = 0.0, 0, 0, 0, 0.0, 0.0, 0.0
    rows = []
    for d in days:
        tr = replay_day(sym_bars, d, cfg, at, a.picks)
        day_pnl = sum(t["pnl"] for t in tr)
        rows.append((d, tr, day_pnl))
        for t in tr:
            n += 1
            wins += t["pnl"] > 0
            losses += t["pnl"] <= 0
        eq += day_pnl
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        total += day_pnl
    last = rows[-1]
    print("Paper review: the %s routine on %d stocks (breakout by %s, stop at range middle, target 2R, "
          "max loss %s a trade, square off %s, charges included)."
          % (at.strftime("%H:%M"), len(sym_bars), at.strftime("%H:%M"), inr(cfg["max_loss_per_trade"]), cfg["flat_by"]))
    print("Latest day %s: %s" % (last[0], "no trades" if not last[1] else inr(last[2])))
    for t in last[1]:
        print("  %-11s buy %d at %.2f (%s), stop %.2f, target %.2f -> %s at %.2f: %s"
              % (t["symbol"], t["qty"], t["entry"], t["entry_time"], t["stop"], t["target"], t["how"], t["exit"], inr(t["pnl"])))
    if a.daily:
        for d, tr, p in rows:
            print("  %s  %d trade(s)  %s" % (d, len(tr), inr(p)))
    print("Last %d trading days: %d trades, %d won, %d lost (%s win rate), net %s, worst drop from a high %s."
          % (len(rows), n, wins, losses, ("%.0f%%" % (wins / n * 100)) if n else "-", inr(total), inr(dd)))
    print("Hypothetical: real fills, slippage and delayed data would make it worse. Not a prediction.")
    if a.write:
        _write(ctx.p("review.json"), {"as_of": ctx.now.strftime("%Y-%m-%d %H:%M IST"), "days": [
            {"date": d.isoformat(), "pnl": r2(p), "trades": tr} for d, tr, p in rows],
            "total": r2(total), "trades": n, "wins": wins, "max_drawdown": r2(dd)})
    return 0


# ------------------------------------------------------------------ quotes / alerts

def cmd_quote(a, ctx):
    cfg = ctx.config()
    syms = list(a.symbols)
    if a.holdings:
        plan = _read(ctx.p("plan.json"), None)
        if plan:
            syms.append(plan["symbol"])
    if not syms:
        raise NSEError("which symbols?")
    doc = _read(ctx.p("prices.json"), {}) or {}
    prices = doc.get("prices", {})
    for i, s in enumerate(dict.fromkeys(syms)):
        y = ysym(s, a.exchange or cfg["exchange"])
        try:
            q = quote(y, ctx.offline)
        except NSEError as e:
            print("%s: %s" % (s, e))
            continue
        chg = ("  %+.2f%% vs prev close %.2f" % ((q["price"] / q["prev_close"] - 1) * 100, q["prev_close"])
               if q["prev_close"] else "")
        print("%-12s %10s  at %s IST%s  (free, delayed)" % (split_sym(y)[0], inr(q["price"]), q["time_ist"], chg))
        prices[split_sym(y)[0]] = q
        if not ctx.offline and i + 1 < len(syms):
            time.sleep(PAUSE)
    if a.write:
        _write(ctx.p("prices.json"), {"as_of": ctx.now.strftime("%Y-%m-%d %H:%M IST"), "prices": prices})
        print("-> " + ctx.p("prices.json"))
    return 0


def rules_from_plan(plan):
    s, d = plan["symbol"], plan["date"]
    k = s.lower()
    return [
        {"id": "%s-plan-stop" % k, "type": "stop", "symbol": s, "price": plan["stop"], "date": d},
        {"id": "%s-plan-target" % k, "type": "target", "symbol": s, "price": plan["target"], "date": d},
        {"id": "%s-squareoff" % k, "type": "time", "at": plan["flat_by"], "date": d,
         "label": "square off %s" % s, "symbol": s},
    ]


def cmd_alerts(a, ctx):
    cfg = ctx.config()
    path, spath = ctx.p("alerts.json"), ctx.p("state", "alerts.json")
    rules = (_read(path, {}) or {}).get("rules", [])
    state = _read(spath, {}) or {}
    today = ctx.today.isoformat()
    if a.cmd == "add":
        new = []
        if a.from_plan:
            new += rules_from_plan(need_plan(ctx))
        if a.opening_bell:
            new.append({"id": "opening-bell", "type": "opening_bell"})
        if a.symbol:
            s = a.symbol.upper()
            for typ, val in (("above", a.above), ("below", a.below), ("stop", a.stop), ("target", a.target)):
                if val is not None:
                    new.append({"id": "%s-%s-%s" % (s.lower(), typ, val), "type": typ, "symbol": s, "price": val})
            if a.pct is not None:
                new.append({"id": "%s-pct-%s" % (s.lower(), a.pct), "type": "pct", "symbol": s, "pct": a.pct})
        if a.at:
            hhmm(a.at, "--at")
            new.append({"id": "time-%s" % a.at.replace(":", ""), "type": "time", "at": a.at,
                        "label": a.label or "reminder"})
        if not new:
            raise NSEError("nothing to add: --from-plan, --symbol X --above/--below/--stop/--target/--pct, --at, --opening-bell")
        ids = {r["id"] for r in new}
        rules = [r for r in rules if r["id"] not in ids] + new
        _write(path, {"rules": rules})
        for r in new:
            state.pop(r["id"], None)
            print("added %s" % r["id"])
        _write(spath, state)
        return 0
    if a.cmd == "list":
        for r in rules:
            print("%-28s %-12s %s%s" % (r["id"], r["type"], r.get("symbol", ""),
                                        "  rang %s" % state[r["id"]] if r["id"] in state else ""))
        return 0
    if a.cmd == "reset":
        state.pop(a.id, None)
        _write(spath, state)
        print("reset %s" % a.id)
        return 0
    # check
    st = market_status(ctx.now)
    active = [r for r in rules if not r.get("date") or r["date"] == today]
    syms = sorted({r["symbol"] for r in active if r["type"] not in ("time", "opening_bell")})
    quotes = {}
    for s in syms:
        try:
            quotes[s] = quote(ysym(s, cfg["exchange"]), ctx.offline)
        except NSEError as e:
            print("no quote for %s: %s" % (s, e))
    rang = []
    for r in active:
        last = state.get(r["id"])
        typ = r["type"]
        if typ in ("time", "opening_bell", "pct"):
            if last and last[:10] == today:
                continue
        elif last:
            continue
        msg = None
        q = quotes.get(r.get("symbol"))
        if typ == "opening_bell" and st["state"] == "open":
            msg = "Opening bell: NSE is open (%s IST)." % ctx.now.strftime("%H:%M")
        elif typ == "time" and trading_day(ctx.today)[0] and ctx.now.time() >= hhmm(r["at"]):
            msg = "%s: it is %s IST (set for %s). NSE closes at 15:30; brokers auto square-off MIS around 15:20." % (
                (lambda x: x[:1].upper() + x[1:])(r.get("label", "reminder")), ctx.now.strftime("%H:%M"), r["at"])
            if r.get("symbol"):
                msg += " If %s is still open, close it: ticket flatten --symbol %s --qty N, then your yes." % (r["symbol"], r["symbol"])
        elif q and typ in ("above", "target") and q["price"] >= r["price"]:
            msg = "%s %s %.2f: at or above %.2f (as of %s IST)." % (r["symbol"], typ.upper(), q["price"], r["price"], q["time_ist"])
        elif q and typ in ("below", "stop") and q["price"] <= r["price"]:
            msg = "%s %s %.2f: at or below %.2f (as of %s IST)." % (r["symbol"], typ.upper(), q["price"], r["price"], q["time_ist"])
        elif q and typ == "pct" and q.get("prev_close"):
            mv = (q["price"] / q["prev_close"] - 1) * 100
            if abs(mv) >= abs(r["pct"]):
                msg = "%s moved %+.2f%% from the previous close %.2f (now %.2f)." % (r["symbol"], mv, q["prev_close"], q["price"])
        if msg:
            rang.append(msg)
            state[r["id"]] = ctx.now.strftime("%Y-%m-%d %H:%M")
    _write(spath, state)
    print("NSE %s (%s IST). " % (st["state"].upper(), ctx.now.strftime("%Y-%m-%d %H:%M")) +
          ("%d alert(s):" % len(rang) if rang else "Nothing hit. %d rule(s) watched today%s." % (
              len(active), (", " + ", ".join("%s %s" % (s, inr(q["price"])) for s, q in quotes.items())) if quotes else "")))
    for m in rang:
        print("ALERT " + m)
    return 0 if rang else 1


# ------------------------------------------------------------------ CLI

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", default="nse", help="working folder (default ./nse)")
    common.add_argument("--now", help='pretend it is this IST time, e.g. "2026-11-23 09:45"')
    common.add_argument("--offline", help="read saved Yahoo JSON from this folder instead of the network")
    ap = argparse.ArgumentParser(prog="nse.py", description="NSE/BSE day-trading tools (IST). Never places orders.")
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", parents=[common], help="write nse/config.json")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("clock", parents=[common], help="is NSE open?")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("universe", parents=[common], help="symbol list -> nse/universe.json")
    p.add_argument("--index", default="nifty500",
                   help="one or more, comma separated: " + ", ".join(sorted(UNIVERSE_URLS)) + " (e.g. nifty500,niftybank)")
    p.add_argument("--file", help="a CSV you saved yourself (needs a Symbol column)")

    p = sub.add_parser("scan", parents=[common], help="daily setups or opening-range breakouts")
    p.add_argument("mode", choices=["daily", "orb"])
    p.add_argument("--symbols", help="comma list instead of the universe")
    p.add_argument("--limit", type=int, help="first N symbols of the universe")
    p.add_argument("--setup", help="daily: only these (breakout20,reclaim20,oversold,bounce,bull_flag)")
    p.add_argument("--at", help="orb: cut the clock at this IST time, HH:MM")
    p.add_argument("--range-min", type=int)
    p.add_argument("--volume-mult", type=float)
    p.add_argument("--exchange", choices=["NSE", "BSE"])
    p.add_argument("--top", type=int, default=25)

    p = sub.add_parser("news", parents=[common], help="new headlines per stock")
    p.add_argument("--symbols")
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--top", type=int, default=8)
    p.add_argument("--all", action="store_true", help="show items already seen too")

    p = sub.add_parser("entry", parents=[common], help="check / watch one setup")
    p.add_argument("cmd", choices=["check", "watch"])
    p.add_argument("symbol")
    p.add_argument("--level", type=float, help="enter on a close above this price instead of the range high")
    p.add_argument("--range-min", type=int)
    p.add_argument("--volume-mult", type=float)
    p.add_argument("--every", type=int, default=60)
    p.add_argument("--until")
    p.add_argument("--exchange", choices=["NSE", "BSE"])

    p = sub.add_parser("plan", parents=[common], help="stop and target -> nse/plan.json")
    p.add_argument("--symbol")
    p.add_argument("--entry")
    p.add_argument("--or-high")
    p.add_argument("--or-low")
    p.add_argument("--stop", default="or-mid")
    p.add_argument("--target", default="2R")
    p.add_argument("--flat-by")
    p.add_argument("--tick")
    p.add_argument("--exchange", choices=["NSE", "BSE"])

    p = sub.add_parser("sim", parents=[common], help="time machine")
    p.add_argument("--n", type=int, default=10000)
    p.add_argument("--seed", type=int, default=19)
    p.add_argument("--write", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("size", parents=[common], help="shares and charges -> nse/orders.json")
    p.add_argument("--any-day", action="store_true", help="allow a plan from another day (practice)")

    sub.add_parser("risk", parents=[common], help="PASS / BLOCK per order")

    p = sub.add_parser("ticket", help="order tickets (never places anything)")
    tsub = p.add_subparsers(dest="cmd", required=True)
    tsub.add_parser("show", parents=[common])
    t = tsub.add_parser("confirm", parents=[common])
    t.add_argument("--id", required=True)
    t.add_argument("--said", required=True, help="the user's words, verbatim")
    t = tsub.add_parser("fill", parents=[common])
    t.add_argument("--id", required=True)
    t.add_argument("--price", type=float)
    t.add_argument("--status", default="filled", choices=["filled", "denied", "failed", "closed"])
    t = tsub.add_parser("flatten", parents=[common])
    t.add_argument("--symbol", required=True)
    t.add_argument("--qty", type=int, required=True)
    t.add_argument("--product", choices=["MIS", "CNC"])
    t.add_argument("--exchange", choices=["NSE", "BSE"])

    p = sub.add_parser("deals", parents=[common], help="NSE bulk and block deals: who bought, who sold")
    p.add_argument("--universe", action="store_true", help="only stocks in nse/universe.json")
    p.add_argument("--symbols")
    p.add_argument("--min-cr", type=float, default=1.0, help="minimum net value in crore (default 1)")
    p.add_argument("--top", type=int, default=15)

    p = sub.add_parser("results", parents=[common], help="quarterly results: revenue, margins, profit, EPS")
    p.add_argument("symbols", help="comma list, e.g. RELIANCE,TCS")
    p.add_argument("--exchange", choices=["NSE", "BSE"])

    p = sub.add_parser("review", parents=[common], help="paper results of the daily breakout routine")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--at", default="09:50")
    p.add_argument("--picks", type=int, default=3)
    p.add_argument("--symbols")
    p.add_argument("--limit", type=int)
    p.add_argument("--daily", action="store_true", help="one line per day")
    p.add_argument("--write", action="store_true")

    p = sub.add_parser("quote", parents=[common], help="free delayed prices")
    p.add_argument("symbols", nargs="*")
    p.add_argument("--holdings", action="store_true", help="add the plan's symbol")
    p.add_argument("--write", action="store_true")
    p.add_argument("--exchange", choices=["NSE", "BSE"])

    p = sub.add_parser("alerts", help="price and time alerts")
    asub = p.add_subparsers(dest="cmd", required=True)
    t = asub.add_parser("add", parents=[common])
    t.add_argument("--from-plan", action="store_true")
    t.add_argument("--opening-bell", action="store_true")
    t.add_argument("--symbol")
    for k in ("above", "below", "stop", "target", "pct"):
        t.add_argument("--" + k, type=float)
    t.add_argument("--at")
    t.add_argument("--label")
    asub.add_parser("check", parents=[common])
    asub.add_parser("list", parents=[common])
    t = asub.add_parser("reset", parents=[common])
    t.add_argument("id")
    return ap


COMMANDS = {"init": cmd_init, "clock": cmd_clock, "universe": cmd_universe, "scan": cmd_scan, "news": cmd_news,
            "entry": cmd_entry, "plan": cmd_plan, "sim": cmd_sim, "size": cmd_size, "risk": cmd_risk,
            "ticket": cmd_ticket, "deals": cmd_deals, "results": cmd_results, "review": cmd_review, "quote": cmd_quote, "alerts": cmd_alerts}


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        ctx = Ctx(a)
        return COMMANDS[a.command](a, ctx)
    except NSEError as e:
        print("error: %s" % e, file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
