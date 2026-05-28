# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

These rules apply to every task in this project unless explicitly overridden.
Bias: caution over speed on non-trivial work. Use judgment on trivial tasks.

## Rule 1 — Think Before Coding
State assumptions explicitly. If uncertain, ask rather than guess.
Present multiple interpretations when ambiguity exists.
Push back when a simpler approach exists.
Stop when confused. Name what's unclear.

## Rule 2 — Simplicity First
Minimum code that solves the problem. Nothing speculative.
No features beyond what was asked. No abstractions for single-use code.
Test: would a senior engineer say this is overcomplicated? If yes, simplify.

## Rule 3 — Surgical Changes
Touch only what you must. Clean up only your own mess.
Don't "improve" adjacent code, comments, or formatting.
Don't refactor what isn't broken. Match existing style.

## Rule 4 — Goal-Driven Execution
Define success criteria. Loop until verified.
Don't follow steps. Define success and iterate.
Strong success criteria let you loop independently.
For any code changes, success criteria must explicitly include:
- Script runs end-to-end without exceptions for a valid ticker
- SEC rate limiting sleeps are preserved (never remove `sec_sleep()` calls)
- Gemini API call is a single consolidated call (never split into multiple calls)
- All `Optional` fields have `None`-safe access before use

## Rule 5 — Prefer deterministic code over probabilistic reasoning
Prefer deterministic code over AI reasoning for routing, retries, and transforms.
Reserve judgment for: classification, ambiguity resolution, drafting, extraction.
If code can answer unambiguously, code answers. Don't reason where you can compute.

## Rule 6 — Token budgets are not advisory
Per-task: 4,000 tokens. Per-session: 30,000 tokens.
If approaching budget, summarize and start fresh.
Surface the breach. Do not silently overrun.
Exception: complex multi-file features may legitimately exceed session budget.
In that case, checkpoint explicitly and continue in a new session — never cut
fidelity or skip safety checks to stay under budget.

## Rule 7 — Surface conflicts, don't average them
If two patterns contradict, pick one (more recent / more tested).
Explain why. Flag the other for cleanup.
Don't blend conflicting patterns.

## Rule 8 — Read before you write
Before adding code, read exports, immediate callers, shared utilities.
"Looks orthogonal" is dangerous. If unsure why code is structured a way, ask.

## Rule 9 — Tests verify intent, not just behavior
Tests must encode WHY behavior matters, not just WHAT it does.
A test that can't fail when business logic changes is wrong.

## Rule 10 — Checkpoint after every significant step
Summarize what was done, what's verified, what's left.
Don't continue from a state you can't describe back.
If you lose track, stop and restate.

## Rule 11 — Match the codebase's conventions, even if you disagree
Conformance > taste inside the codebase.
If you genuinely think a convention is harmful, surface it. Don't fork silently.

## Rule 12 — Fail loud
"Completed" is wrong if anything was skipped silently.
"Tests pass" is wrong if any were skipped.
Default to surfacing uncertainty, not hiding it.

## Rule 13 — NO shortcuts. EVER. Best practices only, always.
This is non-negotiable. There are no exceptions.

**Prohibited at all times:**
- Removing or bypassing SEC rate limit sleeps (`sec_sleep()`) to speed up fetches
- Catching exceptions silently (`except: pass` or bare `except Exception`)
- Using `# type: ignore` to silence type errors instead of fixing them
- Shipping `TODO` or placeholder logic
- Making multiple Gemini API calls where one consolidated call was designed
- Adding `print()` debugging that gets committed
- Hardcoding API keys or credentials in source files (use `.env` only)

**When something is broken, the correct path is always:**
1. Identify the root cause precisely
2. Find the proper, officially-supported solution
3. If a library is unmaintained or broken — replace it with a maintained alternative
4. If no alternative exists — use the platform's built-in APIs or direct HTTP

**Shortcuts are always more expensive long-term.**

If the correct solution requires more time or research — surface that clearly and ask. Never silently take the easy path.

---

# Project Instructions

Guidance for Claude Code when working in this repository.

---

## Project Overview

**SEC Volume Spike Analyzer** — Python CLI research tool for discretionary traders who spot volume spikes on ThinkOrSwim and need an institutional-quality brief before acting.

- **Entrypoint:** `analyze.py`
- **Global command:** `analyzeVolumeSpike` (executable at `~/.local/bin/analyzeVolumeSpike`)
- **Python:** 3.11+, virtual env at `.venv/`
- **AI model:** Gemini Flash (`gemini-2.0-flash-lite` primary, fallback sequence in `nlp_analysis.py`)
- **Required env vars:** `GEMINI_API_KEY`, `INTENDED_POSITION_SIZE` (in `.env`)

---

## Dev Commands

```bash
# Setup
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run (two equivalent ways)
python analyze.py
analyzeVolumeSpike          # global command, no sourcing needed

# Dependencies
pip install -r requirements.txt
pip freeze > requirements.txt   # after adding a new package
```

---

## Architecture

```
analyze.py (orchestrator)
  ├── market_data.py    → Yahoo Finance direct chart API → VolumeMetrics, PeerVolumeData
  ├── edgar.py          → SEC EDGAR APIs → EdgarData, FilingRecord[]
  ├── nlp_analysis.py   → Gemini Flash API → structured 8-section brief text
  ├── display.py        → Rich terminal output (panels, tables, progress)
  └── utils.py          → SEC rate limiting, retry, formatting helpers
```

**Pipeline sequence (analyze.py):**
1. Macro regime check (VIX + SPY 5d return) → red warning if triggered
2. Ticker input
3. `fetch_market_data(ticker)` → `VolumeMetrics`
4. `fetch_edgar_data(ticker)` → `EdgarData` with all `FilingRecord[]`
5. `check_sec_enforcement(company_name, ticker)` → enforcement flag list
6. `position_size_reality_check(avg_dollar_volume)` → position size dict
7. `fetch_peer_volume(ticker, sector)` → `PeerVolumeData[]`
8. `run_analysis(ticker, metrics, edgar, peers)` → brief text (single Gemini call)
9. `print_research_brief(brief_text)` → 8-section Rich output

**Single consolidated Gemini call** — all data is assembled first, then one API call in `nlp_analysis.py:run_analysis()`. Never split this into multiple calls.

---

## Critical Code Rules

### SEC Rate Limiting — MANDATORY
```python
# Always call sec_sleep() between EDGAR requests
response = requests.get(url, headers=SEC_HEADERS)
sec_sleep()   # 0.15s minimum — SEC blocks IPs that exceed 10 req/s
```
Never remove `sec_sleep()` calls or reduce the sleep value. SEC IP blocks are silent (returns 403) and affect all subsequent fetches in the session.

### Gemini API — Model Fallback Pattern
```python
MODEL = "gemini-2.0-flash-lite"
FALLBACK_MODELS = ["gemini-flash-lite-latest", "gemini-2.0-flash", "gemini-pro-latest"]

# Only retry on quota/rate errors; re-raise on auth or not-found
except Exception as e:
    if "quota" in str(e).lower() or "429" in str(e):
        # try next model in fallback list
    else:
        raise
```

### Yahoo Finance — Direct Chart API Only
OHLCV data uses the direct chart endpoint, not `yfinance` download methods (which 429 on the crumb endpoint):
```python
url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
headers = {"User-Agent": "Mozilla/5.0 ..."}  # browser User-Agent required
```
`yfinance` is used only for `fast_info` (market cap, shares outstanding) as a best-effort enrichment with graceful fallback.

### Error Handling — Always Surface
```python
# ✅ CORRECT
except Exception as e:
    logger.warning(f"fetch failed for {ticker}: {e}")
    return None   # caller handles None gracefully

# ❌ WRONG
except Exception:
    pass
```

### Optional Fields — Always None-Safe
`VolumeMetrics` and `EdgarData` fields are all `Optional`. Access them with guards:
```python
zscore_str = f"{metrics.volume_zscore:.2f}σ" if metrics.volume_zscore is not None else "N/A"
```

### Circular Import Prevention
`display.py` imports from `nlp_analysis.py` (`extract_verdict`). Therefore `nlp_analysis.py` must **never** import from `display.py`. Use a local `Console()` instance in `nlp_analysis.py` for any output it needs.

### `.env` — Required Keys
```
GEMINI_API_KEY=AIza...
INTENDED_POSITION_SIZE=25000   # dollar amount for ADV liquidity check
```
API key is loaded via `python-dotenv`. Never read from `os.environ` directly without `load_dotenv()` first.

---

## 8-Section Brief Structure

The Gemini prompt instructs the model to produce exactly these sections (numbered, all-caps titles):

1. STATISTICAL CONTEXT
2. FILING INVENTORY ANALYSIS
3. INSTITUTIONAL ACCUMULATION EVIDENCE
4. TOXIC FINANCING SCREEN
5. SILENT WINDOW ANALYSIS
6. BEAR CASE
7. KEY RISKS
8. FINAL VERDICT

`display.py:_split_sections()` parses these with regex `r"^(?:#+\s*)?(\d+\.\s+[A-Z][A-Z .,/\-—()]+)"` — the `(?:#+\s*)?` prefix handles Gemini's occasional markdown heading output.

**TOXIC FINANCING SCREEN border logic** (qualitative, not numeric):
- `HIGH DILUTION RISK` in body → red border
- `ELEVATED` in body → yellow border
- Otherwise (CLEAN/ROUTINE) → green border

**Verdict types:** `STRONG BUY SIGNAL`, `MODERATE BUY SIGNAL`, `HOLD FOR MORE DATA`, `AVOID`, `INSUFFICIENT DATA`

---

## Debugging Quick Reference

| Issue | Where to look |
|-------|---------------|
| Yahoo Finance 429 | `market_data.py:_fetch_chart()` — confirm browser User-Agent header present |
| Gemini quota error | `nlp_analysis.py:MODEL` — try next model in `FALLBACK_MODELS` list |
| No sections in brief | `display.py:_split_sections()` regex — Gemini output format changed? |
| EDGAR 403 | Too many requests; `sec_sleep()` interval may need increasing in `utils.py` |
| Enforcement search empty | `edgar.py:check_sec_enforcement()` — check `efts.sec.gov` endpoint response |
| `GEMINI_API_KEY` not found | Confirm `.env` exists in project root; `load_dotenv()` called in `nlp_analysis.py` |
| Market data all None | yfinance `fast_info` 429 is separate from chart API — chart data may still work |

---

## Key File Locations

```
analyze.py              # Orchestrator — full pipeline, regime check, ticker input
market_data.py          # Yahoo Finance chart API, VolumeMetrics, peer volumes, regime warning
edgar.py                # EDGAR API, FilingRecord fetching, enforcement check, cash runway
nlp_analysis.py         # Gemini API call, Form 4 pre-parser, 8-section prompt, verdict extraction
display.py              # All Rich terminal output — panels, tables, progress, footer
utils.py                # SEC_RATE_LIMIT_SLEEP, sec_sleep(), retry_with_backoff(), fmt_* helpers
.env                    # GEMINI_API_KEY + INTENDED_POSITION_SIZE (never commit this file)
~/.local/bin/analyzeVolumeSpike  # Global executable (zsh wrapper around .venv python)
```

---

## graphify

This project has a graphify knowledge graph at `graphify-out/`.

**MANDATORY: Before answering any architecture question or making any code change, read `graphify-out/GRAPH_REPORT.md` for god nodes and community structure.** This graph encodes cross-module call relationships, data flows, and design rationale that are not obvious from reading individual files.

Rules:
- Before answering architecture or codebase questions, read `graphify-out/GRAPH_REPORT.md` first
- Before editing any file, check which community it belongs to and which god nodes connect through it
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
