# SEC Volume Spike Analyzer

Research automation tool for discretionary traders who identify volume spikes on ThinkOrSwim and need a fast institutional-quality brief before acting.

## Setup

```bash
cd sec_spike_analyzer
pip install -r requirements.txt
```

Add your Gemini API key to `.env`:
```
GEMINI_API_KEY=AIza...
```
Get a key at: https://aistudio.google.com/apikey

## Run

```bash
python analyze.py
Enter ticker symbol: XXXX
```

Or use the global shell command (after sourcing ~/.zshrc or opening a new terminal):

```bash
analyzeVolumeSpike
Enter ticker symbol: XXXX
```

The tool runs fully autonomously and prints the complete brief. No further input required.

## Pipeline Summary

1. **Volume Context** — pulls 60 days of OHLCV, computes 20-day rolling mean/std, Z-score, price change across 1/5/20 days, float, short interest, ownership structure
2. **EDGAR Fetch** — resolves CIK, pulls all SC 13D/G, Form 4, 8-K, S-3, 10-Q, DEF 14A, and 424B filings from last 90 days
3. **Supplementary Intelligence** — insider transaction code parsing (P/S/M), cash runway from 10-Q, catalyst date extraction from 8-Ks, sector peer volume comparison, filing velocity acceleration detection
4. **Gemini Analysis** — single consolidated API call to `gemini-2.0-flash` producing an 8-section structured brief
5. **Rich Output** — color-coded panels, tables, progress tracking

## Verdict Types

| Verdict | Meaning |
|---|---|
| **STRONG BUY SIGNAL** | Institutional anchor filing (13D/13G), clean capital structure, volume pattern consistent with accumulation, meaningful silent window remaining |
| **MODERATE BUY SIGNAL** | Some institutional evidence but incomplete — partial position sizing appropriate |
| **HOLD FOR MORE DATA** | Filing evidence is thin or ambiguous — wait for next EDGAR cycle (check back in 3-5 days) |
| **AVOID** | Toxic financing structure detected (variable-rate convertibles, ATM, warrant resets) OR volume pattern indicates distribution |
| **INSUFFICIENT DATA** | Too few filings in the window to form a view — common for very small issuers or very recent volume events |

## Key Signals Explained

**Volume Z-Score**: How many standard deviations today's volume is above the 20-day mean. Z > 2 is statistically notable. Z > 3 is the threshold where institutional orders become the most likely explanation on a microcap.

**Volume Patterns**:
- `SILENT_ACCUMULATION` — sustained volume build with flat/slightly positive price action; classic institutional accumulation signature before a catalyst
- `BREAKOUT_CONFIRMATION` — volume spike coinciding with a price breakout above the 20-day range; momentum entry signal
- `DISTRIBUTION` — volume rising into declining or near-highs price action; professional selling into retail demand
- `VOLATILITY_EVENT` — one-day spike with no sustained pattern; could be news, algorithm trigger, or noise
- `UNKNOWN` — insufficient data to classify

**P-Code Form 4 Transactions**: Open-market insider purchases are the highest-conviction signal in microcap trading. Insiders buying on the open market at market prices, disclosed on Form 4 with transaction code P, have superior information and are bearing real risk. A cluster of P-code transactions in the last 30 days alongside elevated institutional activity is a high-quality combined signal.

**Filing Velocity Acceleration**: If the number of relevant EDGAR filings in the last 30 days is significantly higher than the prior 60-day period, institutional activity is clustering — consistent with pre-catalyst positioning.

## Recent Updates

1. **Macro Regime Alert** — On startup, fetches VIX and SPY 5-day return. If VIX > 28 or SPY 5d < -4%, prints a red warning panel before asking for a ticker. Does not block execution.

2. **SEC Enforcement History** — After resolving the company's CIK, searches EDGAR for Litigation Releases, AAERs, and Administrative Proceedings involving the ticker or company name. Any hits print as a red panel before analysis.

3. **Position Size Liquidity Check** — Set `INTENDED_POSITION_SIZE` in `.env` (default $25,000). The metrics table now shows whether your intended size is EXECUTABLE (< 1% of ADV), CONSTRAINED (1–3%), or ILLIQUID (> 3%).

4. **High-Conviction Form 4 Filter** — Claude now only counts insider Form 4s as high-conviction if they are: open-market purchase (P-code), by CEO/CFO/Director/10%+ owner, value > $50K, with no 10b5-1 plan. Everything else is explicitly excluded from the accumulation assessment.

5. **Thin Data Guard** — If fewer than 2 filings exceed 500 words, a yellow warning panel prints before the brief, and Claude's system prompt instructs it to flag uncertainty throughout all sections.

6. **Qualitative Dilution Assessment** — The numeric dilution score (0–10) is replaced by a three-factor qualitative check: Factor A (S-3/424B + sub-9-month runway = HIGH DILUTION RISK), Factor B (convertible with variable rate or MFN = HIGH DILUTION RISK), Factor C (S-3 + >18-month runway, no converts = ROUTINE). The Toxic Financing panel border is green (CLEAN), yellow (ELEVATED), or red (HIGH DILUTION RISK).

7. **Form 4 Pre-Analysis Summary** — Before sending to Gemini, all Form 4 filings are pre-parsed to extract high-conviction buys, low-conviction buys, option exercises, sales, and plan-based transactions. This structured summary is injected into the prompt ahead of the raw filing texts.

## Disclaimer

This tool is research automation, not financial advice. All output is for informational purposes only. Past filing patterns do not guarantee future price movement. Microcap securities carry substantial liquidity risk. Always size positions in accordance with your own risk management rules.
