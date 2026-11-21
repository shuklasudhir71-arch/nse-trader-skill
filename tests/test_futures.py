"""Offline tests for futures.py."""
import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import futures  # noqa: E402
from test_crypto import bars_from  # noqa: E402


class TestFutures(unittest.TestCase):
    cfg = dict(futures.DEFAULT_CONFIG)

    def test_liquidation_2x(self):
        self.assertAlmostEqual(futures.liquidation(100, 1, 2, 1.5), 51.5)
        self.assertAlmostEqual(futures.liquidation(100, -1, 2, 1.5), 148.5)

    def test_signal_sides(self):
        up = bars_from([100 + i * 0.5 for i in range(60)] + [140])
        down = bars_from([100 - i * 0.5 for i in range(60)] + [60])
        self.assertEqual(futures.signal(up)[0], 1)
        self.assertEqual(futures.signal(down)[0], -1)

    def test_short_trade_profits_on_fall(self):
        closes = [100 - i * 0.5 for i in range(60)] + [60] + [60 - 2 * k for k in range(1, 25)]
        tr = futures.backtest_coin(bars_from(closes), self.cfg, 100.0, 60)
        self.assertTrue(tr)
        self.assertEqual(tr[0]["side"], "SHORT")
        self.assertGreater(tr[0]["gross"], 0)
        self.assertGreater(tr[0]["liq_pct"], tr[0]["stop_pct"])  # the stop always comes before liquidation at 2x

    def test_costs(self):
        tr = {"qty": 1.0, "entry": 100.0, "side": 1}
        c = futures.trade_costs(tr, 110.0, 2, self.cfg, 100.0)
        self.assertAlmostEqual(c["gross"], 1000.0)
        self.assertAlmostEqual(c["funding"], 0.0001 * 3 * 2 * 10000)


if __name__ == "__main__":
    unittest.main()
