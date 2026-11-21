#!/usr/bin/env python3
"""futures.py - CoinDCX USDT-margined perpetual futures: swing scan and backtest, paper only.

Long AND short on daily candles, low leverage (2x by default), a stop-loss on every trade
and the liquidation price shown next to it. Reads CoinDCX's free public data; never places
an order and needs no API key.

    python3 futures.py scan                 # today's long/short signals with stop, target, liquidation
    python3 futures.py backtest --days 365  # the rule on a year of daily candles, after fees, GST and funding
    python3 futures.py backtest --trades    # every trade

Rule: on a closed daily candle, LONG when the close breaks the prior 20 closes' high above the
50-day average; SHORT when it breaks the prior 20 closes' low below the 50-day average. Enter at
the next day's open. Stop 2 x ATR(14); target 3R; close after 20 days otherwise.

Global options: --dir DIR (default ./futures), --offline FOLDER.
Education and tooling, not investment or tax advice. Leverage can lose more than you expect.
"""
import argparse
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nse  # noqa: E402
import crypto  # noqa: E402
from nse import Bar, inr, r2, atr, sma, _read, _write, NSEError  # noqa: E402

INSTRUMENTS_URL = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments?margin_currency_short_name[]=USDT"
INSTRUMENT_URL = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/instrument?pair=%s&margin_currency_short_name=USDT"
CANDLES_URL = "https://public.coindcx.com/market_data/candlesticks?pair=%s&from=%d&to=%d&resolution=1D&pcode=f"
DEFAULT_COINS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "LINK", "AVAX", "LTC", "SUI", "TRX"]

DEFAULT_CONFIG = {
    "_note": "Rupees. Paper only. Fees from CoinDCX's instrument data; check funding and GST on CoinDCX. Tax: ask a CA.",
    "capital": 100000,
    "max_loss_per_trade": 1000,
    "leverage": 2,
    "max_margin_pct": 25,
    "max_open_positions": 4,
    "coins": DEFAULT_COINS,
    "atr_mult": 2.0,
    "target_r": 3.0,
    "time_stop_days": 20,
    "taker_fee_pct": 0.059,
    "gst_pct": 18.0,
    "funding_pct_per_8h": 0.01,
    "maintenance_pct": 1.5,
    "usdt_inr": None,
    "tax_pct_vda": 31.2,
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


def pair_of(coin):
    return "B-%s_USDT" % coin.strip().upper()


def daily(pair, days, offline=None, now_utc=None):
    end = int(((now_utc or nse.utcnow()) - dt.datetime(1970, 1, 1)).total_seconds())
    doc = crypto._json(CANDLES_URL % (urllib.parse.quote(pair), end - 86400 * days, end), offline, "fut_%s.json" % pair)
    rows = doc.get("data", []) if isinstance(doc, dict) else doc
    bars = []
    for c in rows:
        try:
            bars.append(Bar(nse.to_ist(nse.from_epoch(int(c["time"]) // 1000)), float(c["open"]), float(c["high"]),
                            float(c["low"]), float(c["close"]), float(c.get("volume") or 0)))
        except (KeyError, ValueError, TypeError):
            continue
    bars.sort()
    return bars


def usdt_inr(cfg, offline=None):
    if cfg.get("usdt_inr"):
        return float(cfg["usdt_inr"])
    t = crypto.tickers(offline).get("USDTINR")
    if not t:
        raise NSEError("no USDT/INR price: set usdt_inr in futures/config.json")
    return t["last"]


def signal(bars):
    """+1 long, -1 short, 0 none, on the last closed candle."""
    if len(bars) < 51:
        return 0, ""
    cl = [b.c for b in bars]
    hi, lo, s50 = max(cl[-21:-1]), min(cl[-21:-1]), sma(cl[-50:])
    if cl[-1] > hi and cl[-1] > s50:
        return 1, "close %s broke the 20-day high %s, above the 50-day average %s" % (crypto.fmt(cl[-1]), crypto.fmt(hi), crypto.fmt(s50))
    if cl[-1] < lo and cl[-1] < s50:
        return -1, "close %s broke the 20-day low %s, below the 50-day average %s" % (crypto.fmt(cl[-1]), crypto.fmt(lo), crypto.fmt(s50))
    return 0, ""


def liquidation(entry, side, lev, mm_pct):
    """Isolated-margin liquidation estimate: margin 1/lev, less the maintenance margin."""
    return entry * (1 - side * (1 / lev - mm_pct / 100))


def make_trade(bars_upto, entry, side, cfg, rate):
    a = atr(bars_upto[-30:])
    if not a:
        return None
    stop = entry - side * cfg["atr_mult"] * a
    risk_usdt = abs(entry - stop)
    qty = cfg["max_loss_per_trade"] / (risk_usdt * rate)              # coins, sized to the rupee loss at the stop
    margin_cap_inr = cfg["capital"] * cfg["max_margin_pct"] / 100
    qty = min(qty, margin_cap_inr * cfg["leverage"] / (entry * rate))
    liq = liquidation(entry, side, cfg["leverage"], cfg["maintenance_pct"])
    return {"side": side, "entry": entry, "stop": stop, "target": entry + side * cfg["target_r"] * risk_usdt, "qty": qty,
            "atr": a, "liq": liq, "notional_inr": qty * entry * rate, "margin_inr": qty * entry * rate / cfg["leverage"],
            "stop_pct": risk_usdt / entry * 100, "liq_pct": abs(entry - liq) / entry * 100}


def trade_costs(tr, exit_px, days, cfg, rate):
    notional_in = tr["qty"] * tr["entry"] * rate
    notional_out = tr["qty"] * exit_px * rate
    fee = cfg["taker_fee_pct"] / 100 * (notional_in + notional_out)
    gst = cfg["gst_pct"] / 100 * fee
    funding = cfg["funding_pct_per_8h"] / 100 * 3 * max(1, days) * notional_in
    gross = tr["side"] * tr["qty"] * (exit_px - tr["entry"]) * rate
    return {"gross": gross, "fee_gst": fee + gst, "funding": funding, "pre_tax": gross - fee - gst - funding}


def backtest_coin(bars, cfg, rate, days):
    out = []
    i = max(51, len(bars) - days)
    while i < len(bars) - 1:
        side, why = signal(bars[: i + 1])
        if not side:
            i += 1
            continue
        nxt = bars[i + 1]
        tr = make_trade(bars[: i + 1], nxt.o, side, cfg, rate)
        if not tr:
            i += 1
            continue
        exit_px, how, j = None, "time", i + 1
        while j < len(bars):
            b = bars[j]
            adverse = b.l if side > 0 else b.h
            favour = b.h if side > 0 else b.l
            if (adverse - tr["liq"]) * side <= 0:
                exit_px, how = tr["liq"], "LIQUIDATED"
                break
            if (adverse - tr["stop"]) * side <= 0:
                gap = b.o if (b.o - tr["stop"]) * side < 0 else tr["stop"]
                exit_px, how = gap, "stop"
                break
            if (favour - tr["target"]) * side >= 0:
                exit_px, how = tr["target"], "target"
                break
            if j - (i + 1) >= cfg["time_stop_days"]:
                exit_px, how = b.c, "time"
                break
            j += 1
        if exit_px is None:
            exit_px, how, j = bars[-1].c, "open", len(bars) - 1
        held = max(1, j - (i + 1))
        c = trade_costs(tr, exit_px, held, cfg, rate)
        out.append({"entry_date": nxt.t.date().isoformat(), "exit_date": bars[j].t.date().isoformat(),
                    "side": "LONG" if side > 0 else "SHORT", "entry": tr["entry"], "exit": exit_px, "how": how,
                    "days": held, "margin_inr": r2(tr["margin_inr"]), "liq_pct": r2(tr["liq_pct"]), "stop_pct": r2(tr["stop_pct"]),
                    "gross": r2(c["gross"]), "fee_gst": r2(c["fee_gst"]), "funding": r2(c["funding"]), "pre_tax": r2(c["pre_tax"])})
        i = j + 1
    return out


def cmd_backtest(a, ctx):
    cfg = ctx.config()
    rate = usdt_inr(cfg, ctx.offline)
    trades = []
    for i, coin in enumerate(cfg["coins"]):
        try:
            bars = [b for b in daily(pair_of(coin), a.days + 90, ctx.offline) if b.t.date() < ctx.now.date()]
        except NSEError:
            print("  %s: no futures candles" % coin)
            continue
        for tr in backtest_coin(bars, cfg, rate, a.days):
            tr["coin"] = coin
            trades.append(tr)
        if not ctx.offline and i + 1 < len(cfg["coins"]):
            time.sleep(nse.PAUSE)
    # Coins move together: keep at most max_open_positions open at once (first come, first kept).
    trades.sort(key=lambda x: (x["entry_date"], cfg["coins"].index(x["coin"])))
    kept, skipped = [], 0
    for t in trades:
        live = [k for k in kept if k["entry_date"] <= t["entry_date"] < k["exit_date"]]
        if len(live) >= cfg["max_open_positions"]:
            skipped += 1
            continue
        kept.append(t)
    trades = sorted(kept, key=lambda x: x["exit_date"])
    n = len(trades)
    pre = sum(t["pre_tax"] for t in trades)
    won = sum(1 for t in trades if t["pre_tax"] > 0)
    tax_vda = sum(cfg["tax_pct_vda"] / 100 * max(0.0, t["gross"]) for t in trades)
    eq = peak = dd = 0.0
    for t in trades:
        eq += t["pre_tax"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    longs = [t for t in trades if t["side"] == "LONG"]
    shorts = [t for t in trades if t["side"] == "SHORT"]
    liqd = sum(1 for t in trades if t["how"] == "LIQUIDATED")
    print("Futures backtest: %d days, %d CoinDCX USDT perpetuals, %sx leverage, risk %s a trade, USDT at %s."
          % (a.days, len(cfg["coins"]), cfg["leverage"], inr(cfg["max_loss_per_trade"]), inr(rate)))
    print("  %d trades (%d long, %d short), %d won (%s). Liquidated: %d. Skipped %d signals while %d positions were open."
          % (n, len(longs), len(shorts), won, ("%.0f%%" % (won / n * 100)) if n else "-", liqd, skipped,
             cfg["max_open_positions"]))
    print("  Gross %s - fees+GST %s - funding %s = before tax %s. Worst drop from a high %s."
          % (inr(sum(t["gross"] for t in trades)), inr(sum(t["fee_gst"] for t in trades)), inr(sum(t["funding"] for t in trades)),
             inr(pre), inr(dd)))
    print("  Longs %s, shorts %s (before tax)." % (inr(sum(t["pre_tax"] for t in longs)), inr(sum(t["pre_tax"] for t in shorts))))
    print("  Tax, two readings (ask a CA which applies to you):")
    print("    a) as crypto (VDA, 30%% + cess on each winning trade, no loss set-off): about %s tax -> net %s"
          % (inr(tax_vda), inr(pre - tax_vda)))
    print("    b) as business income: %s before tax, taxed at your slab, losses can be set off" % inr(pre))
    if a.trades:
        for t in trades:
            print("    %s -> %s %-5s %-5s %-10s %3dd  stop %.1f%%  liq %.0f%% away  %s"
                  % (t["entry_date"], t["exit_date"], t["coin"], t["side"], t["how"], t["days"], t["stop_pct"], t["liq_pct"],
                     inr(t["pre_tax"])))
    _write(ctx.p("backtest.json"), {"as_of": ctx.now.strftime("%Y-%m-%d %H:%M IST"), "days": a.days, "usdt_inr": rate,
                                    "trades": trades, "pre_tax": r2(pre), "tax_vda": r2(tax_vda), "max_drop": r2(dd)})
    print("Funding is assumed at %.3f%% per 8 hours against you; real funding varies and is sometimes paid to you. "
          "Converting rupees to USDT has its own spread (USDT/INR). Past candles, not a prediction." % cfg["funding_pct_per_8h"])
    return 0


def cmd_scan(a, ctx):
    cfg = ctx.config()
    rate = usdt_inr(cfg, ctx.offline)
    found = []
    for i, coin in enumerate(cfg["coins"]):
        try:
            bars = [b for b in daily(pair_of(coin), 200, ctx.offline) if b.t.date() < ctx.now.date()]
        except NSEError:
            continue
        side, why = signal(bars)
        if side:
            tr = make_trade(bars, bars[-1].c, side, cfg, rate)
            found.append((coin, why, tr))
        if not ctx.offline and i + 1 < len(cfg["coins"]):
            time.sleep(nse.PAUSE)
    print("CoinDCX USDT perpetuals, last closed daily candle: %d signal(s) among %d coins (%sx leverage, risk %s)."
          % (len(found), len(cfg["coins"]), cfg["leverage"], inr(cfg["max_loss_per_trade"])))
    if len(found) > cfg["max_open_positions"]:
        print("  Coins move together: only the first %d (in your coins order) are planned; %d signals in the same "
              "direction are one bet, not %d." % (cfg["max_open_positions"], len(found), len(found)))
        found = found[: cfg["max_open_positions"]]
    tickets = []
    for coin, why, tr in found:
        side = "LONG (buy)" if tr["side"] > 0 else "SHORT (sell)"
        qty = math.floor(tr["qty"] * 1000) / 1000 if tr["entry"] > 1000 else round(tr["qty"], 2)
        print("  %s %s: %s" % (coin, side, why))
        print("    entry ~%s USDT, stop %s (%.1f%%), target %s, qty %s, margin about %s, liquidation about %s (%.0f%% away)"
              % (crypto.fmt(tr["entry"]), crypto.fmt(tr["stop"]), tr["stop_pct"], crypto.fmt(tr["target"]), qty,
                 inr(tr["margin_inr"]), crypto.fmt(tr["liq"]), tr["liq_pct"]))
        tickets.append({"coin": coin, "pair": pair_of(coin), "side": "buy" if tr["side"] > 0 else "sell", "quantity": qty,
                        "entry": tr["entry"], "stop": tr["stop"], "target": tr["target"], "leverage": cfg["leverage"],
                        "liquidation": tr["liq"]})
    _write(ctx.p("signals.json"), {"as_of": ctx.now.strftime("%Y-%m-%d %H:%M IST"), "signals": tickets})
    if not found:
        print("  No breakout today. No trade is a fine result.")
    print("Paper only. Place a stop-market order with every entry; never let the liquidation price be closer than the stop.")
    return 0


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", default="futures")
    common.add_argument("--offline")
    ap = argparse.ArgumentParser(prog="futures.py", description="CoinDCX USDT perpetuals: paper swing scan and backtest.")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", parents=[common])
    p = sub.add_parser("backtest", parents=[common])
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--trades", action="store_true")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        return {"scan": cmd_scan, "backtest": cmd_backtest}[a.command](a, Ctx(a))
    except NSEError as e:
        print("error: %s" % e, file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
