#!/usr/bin/env python3
"""crypto.py - spot crypto swing trading tools for CoinDCX INR markets (paper by default).

Standard-library Python 3.8+. Reads CoinDCX's free public market data
(api.coindcx.com, public.coindcx.com). It never places an order and needs no API key.

Why swing, not intraday: CoinDCX INR books are thin. A BTC/INR spread of 0.3-0.6% plus
fees and GST costs 1.5-2% a round trip, which eats any intraday edge. Daily candles and
multi-day holds give moves big enough to cover that.

    python3 crypto.py init                      # crypto/config.json with safe defaults
    python3 crypto.py markets                   # top INR coins by 24h value, with spread
    python3 crypto.py quote BTC ETH             # price, bid/ask, spread
    python3 crypto.py scan                      # daily setups on the top coins
    python3 crypto.py plan BTC                  # entry, ATR stop, target, time stop
    python3 crypto.py size                      # quantity, fees, GST, TDS, 30% tax
    python3 crypto.py risk                      # PASS / BLOCK
    python3 crypto.py ticket                    # CoinDCX order ticket (never placed)
    python3 crypto.py backtest --days 365       # the swing rule on the top coins, after costs and tax
    python3 crypto.py news --coins BTC,ETH

Global options: --dir DIR (default ./crypto), --offline FOLDER.
Education and tooling, not investment advice. Crypto is unregulated in India.
"""
import argparse
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nse  # noqa: E402  (shared helpers: rupees, indicators, http, files)
from nse import Bar, inr, r2, rsi, atr, sma, mean, _read, _write, http_get, NSEError  # noqa: E402

VERSION = "1.0.0"
TICKER_URL = "https://api.coindcx.com/exchange/ticker"
MARKETS_URL = "https://api.coindcx.com/exchange/v1/markets_details"
CANDLES_URL = "https://public.coindcx.com/market_data/candles?pair=%s&interval=%s&limit=%d"
# Stablecoins, gold tokens and wrapped coins move like the rupee, gold or another coin.
NOT_TRADED = {"USDT", "USDC", "DAI", "TUSD", "FDUSD", "BUSD", "PAXG", "XAUT", "WBTC", "STETH", "WETH"}

DEFAULT_CONFIG = {
    "_note": "Rupees. paper: true = practice only. Check fees on CoinDCX's fee page; tax rules on incometax.gov.in.",
    "paper": True,
    "capital": 100000,
    "max_loss_per_trade": 1000,
    "max_open_positions": 4,
    "max_position_pct": 25,
    "top_coins": 15,
    "min_24h_value_cr": 0.5,
    "max_spread_pct": 0.8,
    "atr_mult": 2.0,
    "target_r": 3.0,
    "time_stop_days": 20,
    "fee_pct": 0.5,
    "gst_pct": 18.0,
    "tds_pct": 1.0,
    "tax_pct": 31.2,
    "blocked": [],
}


class Ctx:
    def __init__(self, a):
        self.dir = a.dir
        self.offline = getattr(a, "offline", None)
        self.now = nse.to_ist(nse.utcnow())

    def p(self, *parts):
        return os.path.join(self.dir, *parts)

    def config(self):
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(_read(self.p("config.json"), {}) or {})
        return cfg


# ------------------------------------------------------------------ data

def _json(url, offline, name):
    if offline:
        doc = _read(os.path.join(offline, name), None)
        if doc is None:
            raise NSEError("offline: no file %s" % name)
        return doc
    try:
        return json.loads(http_get(url).decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
        raise NSEError("could not read %s: %s" % (url, e))


def coin_of(market):
    return market[:-3] if market.endswith("INR") else market


def market_of(coin):
    c = coin.strip().upper()
    return c if c.endswith("INR") else c + "INR"


def tickers(offline=None):
    rows = _json(TICKER_URL, offline, "ticker.json")
    out = {}
    for t in rows:
        m = t.get("market", "")
        if not m.endswith("INR"):
            continue
        try:
            last, bid, ask = float(t["last_price"]), float(t.get("bid") or 0), float(t.get("ask") or 0)
            vol = float(t.get("volume") or 0)
        except (KeyError, ValueError):
            continue
        mid = (bid + ask) / 2 if bid and ask else last
        out[m] = {"market": m, "coin": coin_of(m), "last": last, "bid": bid, "ask": ask,
                  "spread_pct": r2((ask - bid) / mid * 100) if bid and ask and mid else None,
                  "value_cr": r2(vol / 1e7), "change_24h": r2(float(t.get("change_24_hour") or 0)),
                  "time_ist": nse.to_ist(nse.from_epoch(int(t.get("timestamp") or 0))).strftime("%Y-%m-%d %H:%M")}
    return out


def market_info(offline=None):
    rows = _json(MARKETS_URL, offline, "markets.json")
    return {r["coindcx_name"]: r for r in rows if r.get("coindcx_name", "").endswith("INR")}


def candles(pair, interval="1d", limit=400, offline=None):
    name = "candles_%s_%s.json" % (pair, interval)
    rows = _json(CANDLES_URL % (urllib.parse.quote(pair), interval, limit), offline, name)
    bars = []
    for c in rows:
        try:
            t = nse.to_ist(nse.from_epoch(int(c["time"]) // 1000))
            bars.append(Bar(t, float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]), float(c["volume"])))
        except (KeyError, ValueError, TypeError):
            continue
    bars.sort()
    return bars


def top_markets(tk, cfg, n=None):
    rows = [t for t in tk.values() if t["coin"] not in NOT_TRADED and t["coin"] not in cfg.get("blocked", [])
            and t["value_cr"] >= cfg["min_24h_value_cr"]]
    rows.sort(key=lambda t: -t["value_cr"])
    return rows[: (n or cfg["top_coins"])]


def round_qty(q, info):
    step = float((info or {}).get("step") or 0) or 10 ** -int((info or {}).get("target_currency_precision") or 6)
    return math.floor(q / step + 1e-9) * step if step else q


def round_price(p, info):
    prec = int((info or {}).get("base_currency_precision") or 2)
    return round(p, prec)


# ------------------------------------------------------------------ costs and tax

def costs(buy_val, sell_val, cfg):
    """Fees (with GST) on both sides, TDS on the sell, and income tax on a profit (no loss set-off)."""
    fee = cfg["fee_pct"] / 100 * (buy_val + sell_val)
    gst = cfg["gst_pct"] / 100 * fee
    pre_tax = sell_val - buy_val - fee - gst
    # Section 115BBH: only the cost of acquisition is deductible, not fees; losses are not set off.
    tax = cfg["tax_pct"] / 100 * max(0.0, sell_val - buy_val)
    tds = cfg["tds_pct"] / 100 * sell_val
    return {"fee": r2(fee), "gst": r2(gst), "tax": r2(tax), "tds_held": r2(tds),
            "net": r2(pre_tax - tax)}


# ------------------------------------------------------------------ the swing rule

def signal(bars):
    """Daily breakout in an uptrend: today's close above the prior 20 closes and above the 50-day average."""
    if len(bars) < 51:
        return None
    cl = [b.c for b in bars]
    hi20 = max(cl[-21:-1])
    s50 = sma(cl[-50:])
    if cl[-1] > hi20 and cl[-1] > s50:
        return {"why": "close %s above the 20-day closing high %s, and above the 50-day average %s"
                       % (fmt(cl[-1]), fmt(hi20), fmt(s50)), "hi20": hi20, "sma50": s50}
    return None


def fmt(x):
    return ("%.0f" % x) if x >= 1000 else ("%.2f" % x) if x >= 1 else ("%.6f" % x)


def swing_plan(bars, cfg, entry=None):
    a = atr(bars[-30:])
    if not a:
        raise NSEError("not enough daily candles for an ATR")
    e = entry or bars[-1].c
    stop = e - cfg["atr_mult"] * a
    if stop <= 0:
        raise NSEError("the ATR stop is below zero: this coin is too volatile for the rule")
    risk = e - stop
    return {"entry": e, "stop": stop, "target": e + cfg["target_r"] * risk, "atr": a, "risk": risk,
            "risk_pct": risk / e * 100}


def backtest_coin(bars, cfg, half_spread_pct, days):
    """Replay the rule on one coin's daily candles over the last `days` days."""
    trades = []
    start = len(bars) - days
    i = max(51, start)
    hs = half_spread_pct / 100
    while i < len(bars) - 1:
        sig = signal(bars[: i + 1])
        if not sig:
            i += 1
            continue
        pl = swing_plan(bars[: i + 1], cfg)
        e = pl["entry"] * (1 + hs)
        stop, tgt = pl["stop"], pl["target"]
        qty = cfg["max_loss_per_trade"] / (e - stop)
        qty = min(qty, cfg["capital"] * cfg["max_position_pct"] / 100 / e)
        exit_px, how, j = None, "time", i + 1
        while j < len(bars):
            b = bars[j]
            if b.l <= stop:
                exit_px, how = min(b.o, stop), "stop"
                break
            if b.h >= tgt:
                exit_px, how = max(b.o, tgt), "target"
                break
            if j - i >= cfg["time_stop_days"]:
                exit_px, how = b.c, "time"
                break
            j += 1
        if exit_px is None:
            exit_px, how, j = bars[-1].c, "open", len(bars) - 1
        exit_px *= (1 - hs)
        c = costs(qty * e, qty * exit_px, cfg)
        trades.append({"entry_date": bars[i].t.date().isoformat(), "exit_date": bars[j].t.date().isoformat(),
                       "entry": e, "exit": exit_px, "qty": qty, "how": how, "days": j - i,
                       "gross": r2(qty * (exit_px - e)), "fee_gst": r2(c["fee"] + c["gst"]), "tax": c["tax"],
                       "net": c["net"]})
        i = j + 1
    return trades


# ------------------------------------------------------------------ commands

def cmd_init(a, ctx):
    path = ctx.p("config.json")
    if os.path.exists(path) and not a.force:
        print("%s already exists (use --force to overwrite)." % path)
        return 0
    _write(path, DEFAULT_CONFIG)
    print("Wrote %s. Set capital and max_loss_per_trade to your own numbers; check fee_pct on CoinDCX's fee page." % path)
    return 0


def cmd_markets(a, ctx):
    cfg = ctx.config()
    tk = tickers(ctx.offline)
    rows = top_markets(tk, cfg, a.top)
    print("CoinDCX INR spot: top %d coins by 24h traded value (stablecoins, gold and wrapped tokens left out)." % len(rows))
    print("  %-8s %14s %8s %9s %10s" % ("coin", "price", "24h", "spread", "24h value"))
    for t in rows:
        warn = "  wide" if t["spread_pct"] is not None and t["spread_pct"] > cfg["max_spread_pct"] else ""
        print("  %-8s %14s %+7.2f%% %8s%% %8.2f cr%s" % (t["coin"], inr(t["last"]), t["change_24h"],
                                                       t["spread_pct"] if t["spread_pct"] is not None else "-", t["value_cr"], warn))
    _write(ctx.p("markets.json"), {"as_of": ctx.now.strftime("%Y-%m-%d %H:%M IST"), "markets": rows})
    print("Spread = gap between the best buy and sell price; you pay about half of it on each side.")
    return 0


def cmd_quote(a, ctx):
    tk = tickers(ctx.offline)
    for c in a.coins:
        t = tk.get(market_of(c))
        if not t:
            print("%s: no INR market on CoinDCX" % c.upper())
            continue
        print("%-6s last %s  bid %s  ask %s  spread %s%%  24h %+.2f%%  (%s IST)"
              % (t["coin"], inr(t["last"]), inr(t["bid"]), inr(t["ask"]), t["spread_pct"], t["change_24h"], t["time_ist"]))
    return 0


def cmd_scan(a, ctx):
    cfg = ctx.config()
    tk = tickers(ctx.offline)
    info = market_info(ctx.offline)
    rows = top_markets(tk, cfg, a.top)
    hits = []
    for i, t in enumerate(rows):
        mi = info.get(t["market"])
        if not mi:
            continue
        try:
            bars = candles(mi["pair"], "1d", 200, ctx.offline)
        except NSEError:
            continue
        bars = [b for b in bars if b.t.date() < ctx.now.date()]  # closed daily candles only
        sig = signal(bars)
        others = nse.daily_setups(bars)
        r = rsi([b.c for b in bars])
        if sig or others:
            hits.append({"coin": t["coin"], "price": t["last"], "spread_pct": t["spread_pct"], "value_cr": t["value_cr"],
                         "swing_signal": bool(sig), "why": sig["why"] if sig else "",
                         "setups": [k for k, _ in others], "rsi": r2(r) if r is not None else None})
        if not ctx.offline and i + 1 < len(rows):
            time.sleep(nse.PAUSE)
    hits.sort(key=lambda h: (not h["swing_signal"], -h["value_cr"]))
    _write(ctx.p("watchlist.json"), {"as_of": ctx.now.strftime("%Y-%m-%d %H:%M IST"), "hits": hits})
    sw = [h for h in hits if h["swing_signal"]]
    print("Scanned the top %d CoinDCX INR coins on closed daily candles. %d swing signal(s) (20-day breakout in an "
          "uptrend); %d with other setups." % (len(rows), len(sw), len(hits) - len(sw)))
    for h in hits:
        print("  %-7s %s %14s  spread %s%%  %s%s" % (h["coin"], "SIGNAL" if h["swing_signal"] else "      ", inr(h["price"]),
                                                     h["spread_pct"], h["why"] or ", ".join(h["setups"]),
                                                     ("  RSI %.0f" % h["rsi"]) if h["rsi"] is not None else ""))
    if sw:
        print("Next: plan %s (stop, target, time stop), then size and risk." % sw[0]["coin"])
    return 0


def cmd_plan(a, ctx):
    cfg = ctx.config()
    m = market_of(a.coin)
    tk = tickers(ctx.offline)
    info = market_info(ctx.offline)
    t, mi = tk.get(m), info.get(m)
    if not t or not mi:
        raise NSEError("%s has no active INR market on CoinDCX" % a.coin.upper())
    bars = [b for b in candles(mi["pair"], "1d", 200, ctx.offline) if b.t.date() < ctx.now.date()]
    entry = float(a.entry) if a.entry else (t["ask"] or t["last"])
    pl = swing_plan(bars, cfg, entry)
    sig = signal(bars)
    plan = {"coin": t["coin"], "market": m, "pair": mi["pair"], "date": ctx.now.date().isoformat(),
            "entry": round_price(pl["entry"], mi), "stop": round_price(pl["stop"], mi), "target": round_price(pl["target"], mi),
            "atr": r2(pl["atr"]), "risk_pct": r2(pl["risk_pct"]), "target_r": cfg["target_r"],
            "time_stop_days": cfg["time_stop_days"], "signal": bool(sig), "why": sig["why"] if sig else "",
            "spread_pct": t["spread_pct"], "made_at": ctx.now.strftime("%Y-%m-%d %H:%M IST")}
    _write(ctx.p("plan.json"), plan)
    print("Plan for %s: buy near %s (the ask), stop %s (%.1f%% below: %.1f x the 14-day ATR of %s), target %s (%.0fR), "
          "or sell after %d days whatever happens."
          % (t["coin"], inr(plan["entry"]), inr(plan["stop"]), plan["risk_pct"], cfg["atr_mult"], inr(plan["atr"]),
             inr(plan["target"]), cfg["target_r"], cfg["time_stop_days"]))
    if not sig:
        print("Note: %s has no swing signal on the last closed candle. This plan is for a trade the rule did not pick." % t["coin"])
    print("-> %s. Next: size." % ctx.p("plan.json"))
    return 0


def cmd_size(a, ctx):
    cfg = ctx.config()
    plan = _read(ctx.p("plan.json"), None)
    if not plan:
        raise NSEError("no plan yet: run plan COIN first")
    info = market_info(ctx.offline).get(plan["market"], {})
    e, s, tg = plan["entry"], plan["stop"], plan["target"]
    risk = e - s
    by = {"max loss per trade": cfg["max_loss_per_trade"] / risk,
          "position cap": cfg["capital"] * cfg["max_position_pct"] / 100 / e}
    qty = round_qty(min(by.values()), info)
    decided = min(by, key=by.get)
    val = qty * e
    if qty <= 0 or val < float(info.get("min_notional") or 100):
        print("No trade: the order would be %s, under CoinDCX's minimum of %s." % (inr(val), inr(float(info.get("min_notional") or 100))))
        _write(ctx.p("orders.json"), {"orders": []})
        return 2
    at_stop = costs(val, qty * s, cfg)
    at_tgt = costs(val, qty * tg, cfg)
    order = {"id": "%s-%s" % (plan["coin"], plan["date"]), "coin": plan["coin"], "market": plan["market"], "side": "buy",
             "qty": qty, "entry": e, "stop": s, "target": tg, "value": r2(val), "date": plan["date"],
             "loss_at_stop": r2(-at_stop["net"]), "net_at_target": at_tgt["net"], "tds_at_target": at_tgt["tds_held"],
             "paper": bool(cfg["paper"]),
             "coindcx": {"side": "buy", "order_type": "limit_order", "market": plan["market"],
                         "price_per_unit": e, "total_quantity": qty}}
    _write(ctx.p("orders.json"), {"as_of": ctx.now.strftime("%Y-%m-%d %H:%M IST"), "orders": [order]})
    print("%s: buy %s %s at %s = %s (%s decided it)." % (plan["coin"], ("%.8f" % qty).rstrip("0").rstrip("."), plan["coin"],
                                                      inr(e), inr(val), decided))
    print("  At the stop %s: you lose %s, fees and GST included (no tax on a loss, and it cannot be set off)."
          % (inr(s), inr(-at_stop["net"])))
    print("  At the target %s: you keep %s after %s fees+GST and %s income tax (%.1f%%). CoinDCX also holds %s as 1%% TDS, "
          "which you get back against your tax when you file." % (inr(tg), inr(at_tgt["net"]), inr(at_tgt["fee"] + at_tgt["gst"]),
                                                                  inr(at_tgt["tax"]), cfg["tax_pct"], inr(at_tgt["tds_held"])))
    print("-> %s (%s). Nothing placed. Next: risk." % (ctx.p("orders.json"), "PAPER" if cfg["paper"] else "LIVE"))
    return 0


def cmd_risk(a, ctx):
    cfg = ctx.config()
    orders = (_read(ctx.p("orders.json"), {}) or {}).get("orders", [])
    if not orders:
        print("No orders: run size first.")
        return 1
    tk = tickers(ctx.offline)
    open_pos = [p for p in (_read(ctx.p("positions.json"), []) or []) if p.get("status") == "open"]
    npass = 0
    log = _read(ctx.p("risk.json"), {}) or {}
    for o in orders:
        bad = []
        t = tk.get(o["market"])
        if o["coin"] in NOT_TRADED:
            bad.append("%s is a stablecoin, gold or wrapped token" % o["coin"])
        if o["coin"] in cfg.get("blocked", []):
            bad.append("%s is on your blocked list" % o["coin"])
        if o.get("side") != "buy":
            bad.append("spot buys only: no short selling, no futures, no leverage")
        if not (o["stop"] < o["entry"] < o["target"]):
            bad.append("the stop must be below the entry and the target above it")
        if o["loss_at_stop"] > cfg["max_loss_per_trade"] * 1.05:
            bad.append("loss at the stop %s is over your %s" % (inr(o["loss_at_stop"]), inr(cfg["max_loss_per_trade"])))
        if o["value"] > cfg["capital"] * cfg["max_position_pct"] / 100 + 1:
            bad.append("position %s is over %s%% of capital" % (inr(o["value"]), cfg["max_position_pct"]))
        if len(open_pos) >= cfg["max_open_positions"]:
            bad.append("already %d open positions (your limit is %d)" % (len(open_pos), cfg["max_open_positions"]))
        if any(p["coin"] == o["coin"] for p in open_pos):
            bad.append("you already hold %s" % o["coin"])
        if t:
            if t["spread_pct"] is None or t["spread_pct"] > cfg["max_spread_pct"]:
                bad.append("spread %s%% is wider than your %s%%: the book is too thin right now" % (t["spread_pct"], cfg["max_spread_pct"]))
            if t["value_cr"] < cfg["min_24h_value_cr"]:
                bad.append("only %.2f crore traded in 24h (your minimum is %s)" % (t["value_cr"], cfg["min_24h_value_cr"]))
            if t["ask"] and t["ask"] > o["entry"] * 1.01:
                bad.append("the ask %s has run more than 1%% above the plan's entry: plan again" % inr(t["ask"]))
        else:
            bad.append("no live CoinDCX price for %s" % o["market"])
        v = "BLOCK" if bad else "PASS"
        log[o["id"]] = {"verdict": v, "reasons": bad, "at": ctx.now.strftime("%Y-%m-%d %H:%M IST")}
        if bad:
            print("BLOCK %s:" % o["id"])
            for r in bad:
                print("  - " + r)
        else:
            npass += 1
            print("PASS %s: buy %s %s at %s, stop %s, target %s, loss at stop %s."
                  % (o["id"], ("%.8f" % o["qty"]).rstrip("0").rstrip("."), o["coin"], inr(o["entry"]), inr(o["stop"]),
                     inr(o["target"]), inr(o["loss_at_stop"])))
    _write(ctx.p("risk.json"), log)
    if not cfg["paper"]:
        print("note: paper is false: this would be real money")
    return 0 if npass == len(orders) else 2


def cmd_ticket(a, ctx):
    orders = (_read(ctx.p("orders.json"), {}) or {}).get("orders", [])
    log = _read(ctx.p("risk.json"), {}) or {}
    if not orders:
        print("No orders: run size, then risk.")
        return 1
    for o in orders:
        ready = (log.get(o["id"]) or {}).get("verdict") == "PASS"
        print("=" * 60)
        print("%s  %s  [%s]" % ("READY" if ready else "NOT READY", o["id"], "PAPER" if o.get("paper", True) else "LIVE"))
        print("  CoinDCX app: Spot > %s/INR > Buy > Limit. Price %s, quantity %s (about %s)."
              % (o["coin"], inr(o["entry"]), ("%.8f" % o["qty"]).rstrip("0").rstrip("."), inr(o["value"])))
        print("  Then set the exit: a stop-loss sell at %s (use CoinDCX's stop-limit order where offered, or a price alert)"
              % inr(o["stop"]))
        print("  and a limit sell at the target %s. Sell after %d days if neither hit." % (inr(o["target"]), DEFAULT_CONFIG["time_stop_days"]))
        print("  Loss at the stop about %s; at the target you keep about %s after fees, GST and tax."
              % (inr(o["loss_at_stop"]), inr(o["net_at_target"])))
        print("  CoinDCX API body (POST /exchange/v1/orders/create, needs your own key and secret):")
        print("    " + json.dumps(o["coindcx"]))
    print("=" * 60)
    print("Nothing is placed by this tool. An order goes out only after you type yes for that exact ticket.")
    return 0


def cmd_backtest(a, ctx):
    cfg = ctx.config()
    tk = tickers(ctx.offline)
    info = market_info(ctx.offline)
    rows = top_markets(tk, cfg, a.top)
    all_trades = []
    for i, t in enumerate(rows):
        mi = info.get(t["market"])
        if not mi:
            continue
        try:
            bars = [b for b in candles(mi["pair"], "1d", a.days + 80, ctx.offline) if b.t.date() < ctx.now.date()]
        except NSEError:
            continue
        hs = (t["spread_pct"] or 0.5) / 2
        for tr in backtest_coin(bars, cfg, hs, a.days):
            tr["coin"] = t["coin"]
            all_trades.append(tr)
        if not ctx.offline and i + 1 < len(rows):
            time.sleep(nse.PAUSE)
    all_trades.sort(key=lambda x: x["exit_date"])
    n = len(all_trades)
    won = sum(1 for x in all_trades if x["net"] > 0)
    net = sum(x["net"] for x in all_trades)
    gross = sum(x["gross"] for x in all_trades)
    fees = sum(x["fee_gst"] for x in all_trades)
    tax = sum(x["tax"] for x in all_trades)
    eq = peak = dd = 0.0
    for x in all_trades:
        eq += x["net"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    by = {}
    for x in all_trades:
        b = by.setdefault(x["coin"], [0, 0.0])
        b[0] += 1
        b[1] += x["net"]
    print("Backtest: %d days, top %d CoinDCX INR coins. Rule: buy when a daily close breaks the 20-day closing high above the "
          "50-day average; stop %.1f x ATR below; target %.0fR; sell after %d days otherwise. Risk %s a trade, half the "
          "current spread paid each side." % (a.days, len(rows), cfg["atr_mult"], cfg["target_r"], cfg["time_stop_days"],
                                               inr(cfg["max_loss_per_trade"])))
    print("  %d trades, %d won (%s), gross %s, fees+GST %s, income tax %s, net %s, worst drop from a high %s."
          % (n, won, ("%.0f%%" % (won / n * 100)) if n else "-", inr(gross), inr(fees), inr(tax), inr(net), inr(dd)))
    for c, (k, v) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        print("    %-7s %2d trades  %s" % (c, k, inr(v)))
    if a.trades:
        for x in all_trades:
            print("    %s -> %s  %-6s %-6s %3dd  net %s" % (x["entry_date"], x["exit_date"], x["coin"], x["how"], x["days"], inr(x["net"])))
    _write(ctx.p("backtest.json"), {"as_of": ctx.now.strftime("%Y-%m-%d %H:%M IST"), "days": a.days, "trades": all_trades,
                                    "net": r2(net), "won": won, "n": n, "max_drop": r2(dd)})
    print("Tax is 30% + 4% cess on each winning trade with no loss set-off, which is why net trails gross by so much. "
          "Past candles, not a prediction; coins delisted or crashed to zero are not in today's top list (survivor bias).")
    return 0


def cmd_news(a, ctx):
    coins = [c.strip().upper() for c in (a.coins or "BTC,ETH").split(",") if c.strip()]
    names = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "BNB": "BNB crypto", "XRP": "XRP Ripple",
             "DOGE": "Dogecoin", "ADA": "Cardano", "TRX": "Tron crypto"}
    since = nse.utcnow() - dt.timedelta(hours=a.hours)
    for c in coins:
        q = '"%s" when:1d' % names.get(c, c + " crypto")
        url = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en" % urllib.parse.quote(q)
        try:
            items = nse.parse_rss(http_get(url, ua=nse.BROWSER_UA).decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            print("%s: news unavailable (%s)" % (c, e))
            continue
        items = [x for x in items if not x["utc"] or x["utc"] >= since]
        items.sort(key=lambda x: x["utc"] or dt.datetime.min, reverse=True)
        print("%s: %d in the last %d hours" % (c, len(items), a.hours))
        for x in items[: a.top]:
            print("  %s IST  %s" % (nse.to_ist(x["utc"]).strftime("%d %b %H:%M") if x["utc"] else "--", x["title"]))
        time.sleep(nse.PAUSE)
    return 0


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", default="crypto")
    common.add_argument("--offline")
    ap = argparse.ArgumentParser(prog="crypto.py", description="CoinDCX INR spot swing tools. Never places orders.")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init", parents=[common]); p.add_argument("--force", action="store_true")
    p = sub.add_parser("markets", parents=[common]); p.add_argument("--top", type=int)
    p = sub.add_parser("quote", parents=[common]); p.add_argument("coins", nargs="+")
    p = sub.add_parser("scan", parents=[common]); p.add_argument("--top", type=int)
    p = sub.add_parser("plan", parents=[common]); p.add_argument("coin"); p.add_argument("--entry")
    sub.add_parser("size", parents=[common])
    sub.add_parser("risk", parents=[common])
    sub.add_parser("ticket", parents=[common])
    p = sub.add_parser("backtest", parents=[common])
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--top", type=int)
    p.add_argument("--trades", action="store_true", help="list every trade")
    p = sub.add_parser("news", parents=[common])
    p.add_argument("--coins"); p.add_argument("--hours", type=int, default=24); p.add_argument("--top", type=int, default=5)
    return ap


COMMANDS = {"init": cmd_init, "markets": cmd_markets, "quote": cmd_quote, "scan": cmd_scan, "plan": cmd_plan,
            "size": cmd_size, "risk": cmd_risk, "ticket": cmd_ticket, "backtest": cmd_backtest, "news": cmd_news}


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        return COMMANDS[a.command](a, Ctx(a))
    except NSEError as e:
        print("error: %s" % e, file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
