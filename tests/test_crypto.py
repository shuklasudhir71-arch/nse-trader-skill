"""Offline tests for crypto.py. Run: python3 -m unittest discover -s tests -v"""
import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import crypto  # noqa: E402
from nse import Bar  # noqa: E402


def bars_from(closes, start=dt.datetime(2026, 1, 1, 5, 30)):
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        out.append(Bar(start + dt.timedelta(days=i), prev, max(prev, c) * 1.01, min(prev, c) * 0.99, c, 100))
        prev = c
    return out


class TestCrypto(unittest.TestCase):
    cfg = dict(crypto.DEFAULT_CONFIG)

    def test_tax_has_no_loss_set_off_and_fees_not_deductible(self):
        win = crypto.costs(100000, 110000, dict(self.cfg, fee_pct=0.5))
        self.assertEqual(win["fee"], 1050.0)
        self.assertEqual(win["gst"], 189.0)
        self.assertEqual(win["tax"], 3120.0)          # 31.2% of the 10,000 gain, fees not deducted
        self.assertEqual(win["tds_held"], 1100.0)     # 1% of the sale, held, not a cost
        self.assertEqual(win["net"], 10000 - 1050 - 189 - 3120)
        loss = crypto.costs(100000, 95000, dict(self.cfg, fee_pct=0.5))
        self.assertEqual(loss["tax"], 0.0)

    def test_signal_and_plan(self):
        closes = [100 + i * 0.5 for i in range(60)] + [140]   # steady uptrend, then a new high
        bars = bars_from(closes)
        self.assertIsNotNone(crypto.signal(bars))
        self.assertIsNone(crypto.signal(bars_from([100 - i * 0.5 for i in range(61)])))
        pl = crypto.swing_plan(bars, self.cfg)
        self.assertLess(pl["stop"], pl["entry"])
        self.assertAlmostEqual(pl["target"] - pl["entry"], 3 * (pl["entry"] - pl["stop"]))

    def test_backtest_hits_target(self):
        closes = [100 + i * 0.5 for i in range(60)] + [140] + [140 + 5 * k for k in range(1, 30)]
        tr = crypto.backtest_coin(bars_from(closes), self.cfg, 0.25, 60)
        self.assertTrue(tr)
        self.assertEqual(tr[0]["how"], "target")
        self.assertGreater(tr[0]["tax"], 0)

    def test_markets(self):
        self.assertEqual(crypto.market_of("btc"), "BTCINR")
        self.assertEqual(crypto.coin_of("SOLINR"), "SOL")
        self.assertIn("USDT", crypto.NOT_TRADED)


if __name__ == "__main__":
    unittest.main()
