---
name: crypto-coindcx
description: >-
  Crypto Desk for CoinDCX (India, INR spot). Lists the most traded INR coins with their bid-ask spread,
  scans daily candles for a swing signal (20-day closing-high breakout above the 50-day average) and
  oversold coins, plans an ATR stop, 3R target and 20-day time stop, sizes the buy, and shows the full
  Indian cost: fees, 18% GST, 1% TDS and 30% + cess tax with no loss set-off. Backtests the rule on a
  year of CoinDCX candles after all costs. Spot only, paper by default, never places an order. Use when
  the user says "crypto", "bitcoin", "CoinDCX", "BTC kharidu", "crypto scan", "crypto backtest",
  "crypto me kitna tax", or names a coin.
argument-hint: "[markets|scan|plan COIN|backtest]"
---

# crypto-coindcx

Reply in the user's language. `${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/crypto.py" init                    # crypto/config.json
python3 "${CLAUDE_SKILL_DIR}/crypto.py" markets                 # top INR coins, spread, 24h value
python3 "${CLAUDE_SKILL_DIR}/crypto.py" quote BTC ETH
python3 "${CLAUDE_SKILL_DIR}/crypto.py" scan                    # swing signals + oversold on closed daily candles
python3 "${CLAUDE_SKILL_DIR}/crypto.py" plan BTC                # ATR stop, 3R target, 20-day time stop
python3 "${CLAUDE_SKILL_DIR}/crypto.py" size                    # quantity; loss at stop; net at target after tax
python3 "${CLAUDE_SKILL_DIR}/crypto.py" risk                    # PASS / BLOCK
python3 "${CLAUDE_SKILL_DIR}/crypto.py" ticket                  # CoinDCX app steps + API body (never sent)
python3 "${CLAUDE_SKILL_DIR}/crypto.py" backtest --days 365 --trades
python3 "${CLAUDE_SKILL_DIR}/crypto.py" news --coins BTC,ETH
```

Needs network access to api.coindcx.com and public.coindcx.com (public data, no key).

## What to tell the user, every time it matters

- CoinDCX INR books are thin: spreads of 0.3-0.9% and a few crore a day even for BTC. Intraday
  trading there loses to costs, so this is a swing tool (days to weeks), on daily candles.
- India taxes each crypto gain at 30% + 4% cess, fees are not deductible, and losses cannot be set
  off against gains (Section 115BBH). 1% TDS is held on every sale and comes back when filing.
  On a rule that wins a third of its trades, that tax can turn a gross profit into a net loss: the
  November 2026 one-year backtest did exactly that. Show the net, not the gross.
- Fees: `fee_pct` in crypto/config.json (default 0.5%) is a placeholder. Ask the user to check
  CoinDCX's fee page and set it.

## Rules

- Spot buys only. No futures, margin, leverage or short selling, even if asked: say why.
- Stablecoins, gold tokens and wrapped coins are not traded.
- Nothing is placed. An order needs the user's explicit yes for that exact ticket; with no connector,
  the user places it in the CoinDCX app. API keys never go in the chat.
- Crypto is unregulated in India. Education and tooling, not investment or tax advice.
