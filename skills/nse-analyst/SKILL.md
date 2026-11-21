---
name: nse-analyst
description: >-
  NSE Earnings Analyst. Shows an Indian company's last quarterly results from free Yahoo data: revenue,
  operating margin, net profit, net margin and diluted EPS in crore rupees, labelled by Indian fiscal
  quarter (Q2 FY27 = July-September 2026), with year-on-year and quarter-on-quarter change, and says
  plainly when a quarter is missing. Use when the user says "RELIANCE ke results kaise the", "quarterly
  results", "munafa badha ya ghata", "EPS kya hai", "compare TCS and INFY results", "results analyze karo".
argument-hint: "[SYMBOL,SYMBOL]"
---

# nse-analyst

Reply in the user's language. `${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/nse.py" results RELIANCE
python3 "${CLAUDE_SKILL_DIR}/nse.py" results TCS,INFY,WIPRO,HCLTECH
```

## Steps

1. Run `results`. Sum up in 3-4 lines: is revenue growing, are margins rising or falling, how profit
   and EPS changed against the same quarter last year (or the previous quarter if that is missing).
2. If the user pastes a results press release or a concall transcript, read it and add what changed
   and what management said about the next quarters.
3. For upcoming results dates, use `nse-news`; results days gap: warn before an intraday trade.

## Rules

- Free, unofficial data: quarters can be missing and a number can differ from the filing.
  The official source is the company's announcement on nseindia.com or bseindia.com.
- Never fill in a missing quarter with a guess. Education and tooling, not investment advice.
