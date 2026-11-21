"""Offline tests for nse.py on synthetic Yahoo-format data. Run: python3 -m unittest discover -s tests -v"""
import contextlib
import datetime as dt
import io
import json
import os
import random
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import nse  # noqa: E402

TODAY = dt.date(2026, 11, 23)  # a Monday; Nov 24 is an NSE holiday
IST = dt.timedelta(hours=5, minutes=30)


def epoch(ist_naive):
    return int(((ist_naive - IST) - dt.datetime(1970, 1, 1)).total_seconds())


def yahoo(bars, price=None, prev=None):
    return {"chart": {"result": [{
        "meta": {"regularMarketPrice": price, "chartPreviousClose": prev,
                 "regularMarketTime": bars[-1][0] if bars else None},
        "timestamp": [b[0] for b in bars],
        "indicators": {"quote": [{"open": [b[1] for b in bars], "high": [b[2] for b in bars],
                                  "low": [b[3] for b in bars], "close": [b[4] for b in bars],
                                  "volume": [b[5] for b in bars]}]}}], "error": None}}


def past_days(n):
    out, d = [], TODAY - dt.timedelta(days=1)
    while len(out) < n:
        if nse.trading_day(d)[0]:
            out.append(d)
        d -= dt.timedelta(days=1)
    return sorted(out)


def session_bars(day, start, rng, drift=0.0):
    bars, p = [], start
    t = dt.datetime.combine(day, nse.OPEN)
    for i in range(75):
        o = p
        c = max(1.0, o * (1 + drift + rng.gauss(0, 0.002)))
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.001)))
        l = min(o, c) * (1 - abs(rng.gauss(0, 0.001)))
        bars.append((epoch(t), round(o, 2), round(h, 2), round(l, 2), round(c, 2), 10000 + rng.randint(0, 5000)))
        p = c
        t += dt.timedelta(minutes=5)
    return bars


def today_bars(upto_min=None):
    """TEST today: range 99-101 in the first 15 min, the 9:35 bar closes 101.60 (entry at 9:40)."""
    t0 = dt.datetime.combine(TODAY, nse.OPEN)
    spec = [(100, 101, 99.5, 100.4), (100.4, 100.8, 99, 99.6), (99.6, 100.9, 99.4, 100.7),  # 9:15-9:30 range
            (100.7, 100.95, 100.2, 100.5),   # 9:30 bar: no break
            (100.5, 101.8, 100.4, 101.6)]    # 9:35 bar: closes above 101
    p = 101.6
    for i in range(20):
        spec.append((p, p + 0.4, p - 0.2, p + 0.15))
        p += 0.15
    bars = []
    for i, (o, h, l, c) in enumerate(spec):
        t = t0 + dt.timedelta(minutes=5 * i)
        if upto_min is not None and t >= t0 + dt.timedelta(minutes=upto_min):
            break
        bars.append((epoch(t), o, h, l, c, 20000 if i == 4 else 10000))
    return bars


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.work = os.path.join(self.tmp, "nse")
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(self.data)
        rng = random.Random(7)
        hist = []
        for d in past_days(40):
            hist += session_bars(d, 100, rng)
        self.hist = hist
        self.save("TEST.NS_5m.json", yahoo(hist + today_bars(), price=103.0, prev=100.0))
        # daily bars: a quiet base then a 20-day breakout on the last day
        drng = random.Random(3)
        d, daily = TODAY - dt.timedelta(days=120), []
        p = 200.0
        while d <= TODAY:
            if nse.trading_day(d)[0]:
                p = p * (1 + drng.gauss(0, 0.004))
                daily.append([epoch(dt.datetime.combine(d, nse.OPEN)), p, p * 1.005, p * 0.995, p, 500000])
            d += dt.timedelta(days=1)
        daily[-1][4] = max(b[4] for b in daily[-21:-1]) * 1.03
        daily[-1][2] = daily[-1][4] * 1.002
        self.save("TEST.NS_1d.json", yahoo([tuple(b) for b in daily]))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def save(self, name, doc):
        with open(os.path.join(self.data, name), "w") as f:
            json.dump(doc, f)

    def run_cli(self, *args, now="2026-11-23 10:00"):
        argv = list(args) + ["--dir", self.work, "--offline", self.data, "--now", now]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = nse.main(argv)
        return code, out.getvalue() + err.getvalue()

    def config(self, **kw):
        cfg = dict(nse.DEFAULT_CONFIG)
        cfg.update(kw)
        os.makedirs(self.work, exist_ok=True)
        with open(os.path.join(self.work, "config.json"), "w") as f:
            json.dump(cfg, f)

    def read(self, name):
        with open(os.path.join(self.work, name)) as f:
            return json.load(f)


class TestHelpers(unittest.TestCase):
    def test_inr_grouping(self):
        self.assertEqual(nse.inr(123456.5), "₹1,23,456.50")
        self.assertEqual(nse.inr(999), "₹999.00")
        self.assertEqual(nse.inr(-12345678), "-₹1,23,45,678.00")

    def test_symbols(self):
        self.assertEqual(nse.ysym("reliance"), "RELIANCE.NS")
        self.assertEqual(nse.ysym("TCS", "BSE"), "TCS.BO")
        self.assertEqual(nse.ysym("BSE:500325"), "500325.BO")
        self.assertEqual(nse.ysym("M&M.NS"), "M&M.NS")
        self.assertEqual(nse.equity_problem("RELIANCE", "NSE"), "")
        self.assertIn("crypto", nse.equity_problem("BTC-USD", "NSE"))
        self.assertIn("options", nse.equity_problem("NIFTY26NOV24000CE", "NSE"))
        self.assertIn("exchange", nse.equity_problem("RELIANCE", "MCX"))
        self.assertEqual(nse.equity_problem("3MINDIA", "NSE"), "")
        self.assertIn("options", nse.equity_problem("NIFTY2611324000CE", "NSE"))
        self.assertIn("futures", nse.equity_problem("RELIANCE26NOVFUT", "NSE"))

    def test_ticks(self):
        self.assertEqual(nse.floor_tick(100.07, 0.05), 100.05)
        self.assertEqual(nse.ceil_tick(100.01, 0.05), 100.05)
        self.assertEqual(nse.floor_tick(100.05, 0.05), 100.05)

    def test_clock(self):
        st = nse.market_status(dt.datetime(2026, 11, 23, 10, 0))
        self.assertEqual(st["state"], "open")
        st = nse.market_status(dt.datetime(2026, 11, 23, 16, 0))
        self.assertEqual(st["state"], "closed")
        self.assertEqual(st["next_open"], "Wed 2026-11-25 09:15")  # Tue 24th is Guru Nanak Jayanti
        self.assertEqual(nse.market_status(dt.datetime(2026, 11, 23, 9, 5))["state"], "preopen")
        self.assertFalse(nse.trading_day(dt.date(2026, 11, 21))[0])  # Saturday

    def test_parse_now_is_ist(self):
        self.assertEqual(nse.parse_now("2026-11-23 09:15"), dt.datetime(2026, 11, 23, 3, 45))
        self.assertEqual(nse.parse_now("2026-11-23T03:45Z"), dt.datetime(2026, 11, 23, 3, 45))

    def test_charges_intraday(self):
        ch = nse.charges(100000, 101000, nse.DEFAULT_CONFIG["charges"], "NSE", "MIS")
        self.assertEqual(ch["brokerage"], 40.0)          # 20 + 20 (0.03% would be 30)
        self.assertEqual(ch["stt"], 25.25)               # 0.025% of the sell side
        self.assertEqual(ch["stamp_duty"], 3.0)          # 0.003% of the buy side
        small = nse.charges(10000, 10000, nse.DEFAULT_CONFIG["charges"], "NSE", "MIS")
        self.assertEqual(small["brokerage"], 6.0)        # 0.03% = 3 each side, under 20

    def test_universe_csv(self):
        nifty = "Company Name,Industry,Symbol,Series,ISIN Code\nReliance Industries Ltd.,Oil,RELIANCE,EQ,INE002A01018\n"
        self.assertEqual(nse.parse_universe_csv(nifty)[0]["symbol"], "RELIANCE")
        full = ("SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING\nTCS,Tata Consultancy,EQ,25-AUG-2004\n"
                "XYZ,Xyz Ltd,BE,01-JAN-2020\n")
        self.assertEqual([x["symbol"] for x in nse.parse_universe_csv(full)], ["TCS"])

    def test_rss(self):
        xml = ("<rss><channel><item><title>Reliance Q2 results: net profit up 9%</title><link>u</link>"
               "<pubDate>Mon, 23 Nov 2026 03:30:00 GMT</pubDate><source>ET</source></item></channel></rss>")
        it = nse.parse_rss(xml)[0]
        self.assertEqual(it["utc"], dt.datetime(2026, 11, 23, 3, 30))
        self.assertIn("results", nse.tag(it["title"]))


class TestFlow(Base):
    def test_orb_states(self):
        _, bars = nse.chart("TEST.NS", "5m", "1d", self.data)
        at = lambda hm: dt.datetime.combine(TODAY, nse.hhmm(hm))
        self.assertEqual(nse.orb_state(bars, TODAY, at("09:25"))["status"], "forming")
        w = nse.orb_state(bars, TODAY, at("09:37"))
        self.assertEqual(w["status"], "waiting")
        self.assertEqual((w["or_high"], w["or_low"]), (101.0, 99.0))
        t = nse.orb_state(bars, TODAY, at("09:40"))
        self.assertEqual((t["status"], t["entry"], t["entry_time"]), ("triggered", 101.6, "09:40"))
        # a level above everything never triggers and expires after the last-entry time
        e = nse.orb_state(bars, TODAY, at("15:00"), level=500, last_entry=dt.time(14, 45))
        self.assertEqual(e["status"], "expired")
        # volume filter: the 9:35 bar has 2x volume, so 3x rejects it
        self.assertNotEqual(nse.orb_state(bars, TODAY, at("09:40"), vol_mult=3)["status"], "triggered")

    def test_daily_scan_breakout(self):
        code, out = self.run_cli("scan", "daily", "--symbols", "TEST")
        self.assertEqual(code, 0, out)
        hits = self.read("watchlist.json")["hits"]
        self.assertIn("breakout20", [h["setup"] for h in hits])

    def test_full_trade_flow(self):
        self.config(capital=100000, max_loss_per_trade=500, daily_loss_limit=1500, min_reward_risk=1.5)
        code, out = self.run_cli("entry", "check", "TEST", now="2026-11-23 09:41")
        self.assertEqual(code, 0, out)
        self.assertIn("TRIGGERED at 09:40", out)

        code, out = self.run_cli("plan", "--stop", "or-mid", "--target", "2R")
        self.assertEqual(code, 0, out)
        plan = self.read("plan.json")
        self.assertEqual(plan["stop"], 100.0)     # midpoint of 99-101
        self.assertEqual(plan["target"], 104.8)   # 101.6 + 2 x 1.6
        self.assertEqual(plan["risk_per_share"], 1.6)

        code, out = self.run_cli("sim", "--n", "2000", "--write")
        self.assertEqual(code, 0, out)
        sim = self.read("sim.json")["sim"]
        self.assertAlmostEqual(sim["p_target"] + sim["p_stop"] + sim["p_open"], 100.0, places=1)
        self.assertIn("Verdict", out)
        # same seed, same answer
        again = nse.simulate(plan, nse.chart("TEST.NS", "5m", "60d", self.data)[1], 2000, 19)
        self.assertEqual(again["p_target"], sim["p_target"])

        code, out = self.run_cli("size", now="2026-11-23 09:42")
        self.assertEqual(code, 0, out)
        o = self.read("orders.json")["orders"][0]
        self.assertEqual(o["qty"], 312)           # floor(500 / 1.6)
        self.assertIn("max loss per trade decided it", out)
        self.assertEqual(o["kite"][1]["order_type"], "SL-M")
        self.assertEqual(o["kite"][1]["trigger_price"], 100.0)
        self.assertEqual((o["dhan"]["targetPrice"], o["dhan"]["stopLossPrice"], o["dhan"]["productType"]), (104.8, 100.0, "INTRADAY"))

        code, out = self.run_cli("ticket", "show", now="2026-11-23 09:42")
        self.assertIn("NOT READY", out)

        code, out = self.run_cli("risk", now="2026-11-23 09:42")
        self.assertEqual(code, 0, out)
        self.assertIn("PASS", out)
        code, out = self.run_cli("ticket", "show", now="2026-11-23 09:43")
        self.assertIn("READY", out)
        self.assertNotIn("NOT READY", out)

        code, out = self.run_cli("ticket", "confirm", "--id", o["id"], "--said", "maybe later")
        self.assertEqual(code, 4)
        code, out = self.run_cli("ticket", "confirm", "--id", o["id"], "--said", "haan, place it")
        self.assertEqual(code, 0, out)

        code, out = self.run_cli("ticket", "fill", "--id", o["id"], "--price", "101.6")
        self.assertEqual(code, 0, out)
        # the same trade again would break the daily limit and trades count logic is applied
        code, out = self.run_cli("risk", now="2026-11-23 09:44")
        self.assertIn("PASS", out)  # 499.2 + 499.2 < 1500

        code, out = self.run_cli("ticket", "flatten", "--symbol", "TEST", "--qty", "312")
        self.assertIn("SELL 312 TEST", out)

    def test_risk_blocks(self):
        self.config(max_loss_per_trade=100000, max_order_value=10 ** 9, max_position_pct=100, cash_buffer_pct=0,
                    capital=10 ** 7, daily_loss_limit=10 ** 6)
        self.run_cli("entry", "check", "TEST", now="2026-11-23 09:41")
        self.run_cli("plan", "--target", "1R")
        self.run_cli("size", now="2026-11-23 09:42")
        code, out = self.run_cli("risk", now="2026-11-23 09:42")
        self.assertEqual(code, 2)
        self.assertIn("reward:risk 1.00 is under your minimum 1.50", out)
        code, out = self.run_cli("risk", now="2026-11-23 15:00")
        self.assertIn("no new entries after 14:45", out)
        code, out = self.run_cli("risk", now="2026-11-24 10:00")
        self.assertIn("Guru Nanak", out)

    def test_size_refuses_far_stop(self):
        self.config(max_loss_per_trade=1)
        self.run_cli("entry", "check", "TEST", now="2026-11-23 09:41")
        self.run_cli("plan")
        code, out = self.run_cli("size")
        self.assertEqual(code, 2)
        self.assertIn("No trade", out)

    def test_plan_rejects_bad_stop(self):
        code, out = self.run_cli("plan", "--symbol", "TEST", "--entry", "100", "--stop", "price:101")
        self.assertEqual(code, 4)
        self.assertIn("not below the entry", out)

    def test_alerts(self):
        self.run_cli("entry", "check", "TEST", now="2026-11-23 09:41")
        self.run_cli("plan")
        code, out = self.run_cli("alerts", "add", "--from-plan")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("alerts", "check", now="2026-11-23 11:00")
        self.assertIn("Nothing hit", out)  # price 103 is between 100 and 104.8
        self.run_cli("alerts", "add", "--symbol", "TEST", "--above", "102.5")
        code, out = self.run_cli("alerts", "check", now="2026-11-23 11:00")
        self.assertIn("ALERT TEST ABOVE 103.00", out)
        code, out = self.run_cli("alerts", "check", now="2026-11-23 11:05")
        self.assertNotIn("ABOVE", out)  # rings once
        code, out = self.run_cli("alerts", "check", now="2026-11-23 15:11")
        self.assertIn("Square off TEST", out)


    def test_review(self):
        self.config(max_loss_per_trade=500)
        code, out = self.run_cli("review", "--symbols", "TEST", "--days", "10", "--daily", now="2026-11-23 16:00")
        self.assertEqual(code, 0, out)
        self.assertIn("Latest day 2026-11-23", out)
        self.assertIn("TEST", out)
        self.assertIn("Last 10 trading days", out)
        # before the square-off time today is not counted yet
        code, out = self.run_cli("review", "--symbols", "TEST", "--days", "10", now="2026-11-23 11:00")
        self.assertNotIn("Latest day 2026-11-23", out)


if __name__ == "__main__":
    unittest.main()
