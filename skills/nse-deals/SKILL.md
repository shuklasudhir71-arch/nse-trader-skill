---
name: nse-deals
description: >-
  NSE Big-Money Tracker (the Indian answer to a "Pelosi tracker"). Reads NSE's public bulk and block deal
  files for the latest trading day and shows which stocks real investors (mutual funds, foreign funds,
  insurers, companies, individuals) bought or sold in size, net in crore rupees, with prop/HFT desks and
  same-day round trips left out. Writes nse/deals.json. Use when the user says "bade investor kya kharid
  rahe hain", "bulk deals", "block deals", "FII ne kya kharida", "mutual fund buying", "smart money",
  "kaun bech raha hai", or in the morning briefing.
argument-hint: "[--universe] [--min-cr 5]"
---

# nse-deals

Reply in the user's language. `${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/nse.py" deals                       # all stocks, net >= 1 crore
python3 "${CLAUDE_SKILL_DIR}/nse.py" deals --min-cr 5 --top 10
python3 "${CLAUDE_SKILL_DIR}/nse.py" universe --index nifty500
python3 "${CLAUDE_SKILL_DIR}/nse.py" deals --universe            # only Nifty 500 stocks
python3 "${CLAUDE_SKILL_DIR}/nse.py" deals --symbols SULA,GALLANTT
```

- Bulk deal: one client trades more than 0.5% of a company's shares in a day. Block deal: a single
  trade of 10 crore or more in the block-deal window. NSE publishes both after the close, for the
  latest trading day only (archives.nseindia.com/content/equities/bulk.csv and block.csv).
- The client type (mutual fund, foreign fund, insurance, company, individual, prop/HFT) is guessed
  from the name. Say "looks like" when it matters.
- Prop/HFT desks (AlphaGrep, QE, Jump, Graviton...) buy and sell the same day: they are counted as
  skipped, not as a signal.

## Steps

1. Run `deals`. Summarize the 3-5 biggest net buys and sells in plain words: who, how much, at what price.
2. Point out a promoter or promoter-family seller, a mutual fund or foreign fund buyer, or a stock that
   is also on today's watchlist.
3. Most bulk deals are in small, illiquid stocks: warn that those move wildly and are poor intraday picks.

## Rules

- A big buyer is information, not a reason to buy. Education and tooling, not investment advice.
