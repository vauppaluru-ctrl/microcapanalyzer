"""Single consolidated Gemini Flash API call: prompt construction and response parsing."""

from __future__ import annotations

import os
import re
from datetime import date

import anthropic as _anthropic_sdk
import google.generativeai as genai
from rich.console import Console as _Console
from rich.panel import Panel as _Panel

from edgar import EdgarData, FilingRecord, InsiderSummary
from market_data import VolumeMetrics, PeerVolumeData, is_sector_rotation
from utils import fmt_millions, fmt_pct, fmt_shares

_console = _Console()

# ─── Model registry ───────────────────────────────────────────────────────────

_MODELS: dict[str, dict] = {
    "claude": {
        "display": "Claude Sonnet 4.6",
        "desc":    "best reasoning",
        "api":     "anthropic",
        "model_id":"claude-sonnet-4-6",
        "max_tokens": 8192,
        "fallbacks":  [],
    },
    "gemini-pro": {
        "display": "Gemini 2.5 Pro",
        "desc":    "strong, uncapped",
        "api":     "gemini",
        "model_id":"gemini-2.5-pro",
        "max_tokens": 65536,
        "fallbacks":  ["gemini-2.0-flash", "gemini-2.0-flash-lite"],
    },
    "gemini-flash": {
        "display": "Gemini 2.0 Flash",
        "desc":    "fast, cheap",
        "api":     "gemini",
        "model_id":"gemini-2.0-flash",
        "max_tokens": 4096,
        "fallbacks":  ["gemini-2.0-flash-lite", "gemini-flash-lite-latest"],
    },
}

# USD per 1M tokens (input, output). Approximate — verify at ai.google.dev/pricing and anthropic.com/pricing.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6":   (3.00,  15.00),
    "gemini-2.5-pro":      (1.25,  10.00),
    "gemini-2.0-flash":    (0.10,   0.40),
    "gemini-2.0-flash-lite":(0.075, 0.30),
    "gemini-flash-lite-latest":(0.075, 0.30),
}

# UPDATE 5A: thin filing guard appended to system prompt
SYSTEM_PROMPT = """\
You are an expert forensic financial analyst specializing in \
micro-cap and small-cap corporate finance ($10M–$2B market cap). \
You audit SEC filings to determine whether observed price and \
volume patterns represent genuine institutional accumulation or \
active corporate dilution and toxic financing.

You are rigorous, skeptical by default, and work strictly from \
the regulatory record — not from chart patterns or market rumors. \
When specific financial data should be present in filings but is \
missing, flag it explicitly rather than estimating. When evidence \
is thin, say so. Your job is to protect the trader from bad trades \
as much as to identify good ones.

Execute the requested 5-step analytical audit precisely. Follow \
the output format requirements exactly. Use actual numbers from \
the filings. Do not omit any of the 5 sections.\
"""


# ─── UPDATE 7: Form 4 pre-processing ─────────────────────────────────────────

def parse_form4_transactions(filings: list[FilingRecord]) -> dict:
    result: dict = {
        "high_conviction_buys": [],
        "low_conviction_buys": [],
        "option_exercises": [],
        "sales": [],
        "plan_based": [],
    }

    form4s = [f for f in filings if f.form_type == "4"]
    _SHARES_RE = re.compile(r"(\d[\d,]+)\s*(?:shares?|shs?)", re.IGNORECASE)
    _PRICE_RE = re.compile(r"\$\s*([\d.,]+)")
    _NAME_RE = re.compile(
        r"(?:reporting\s*owner|rptOwnerName)[^A-Za-z]*([A-Z][A-Za-z\s,.-]{4,50})",
        re.IGNORECASE,
    )
    _TITLE_RE = re.compile(
        r"(?:officerTitle|officer\s*title)[^A-Za-z]*([A-Za-z][A-Za-z\s,./]{3,40})",
        re.IGNORECASE,
    )

    for f in form4s:
        text = f.full_text
        if not text:
            continue

        has_10b51 = bool(re.search(r"10b5-?1\s*(?:plan|trading\s*plan|arrangement)", text, re.IGNORECASE))
        has_p = bool(re.search(r"\bP\b", text))
        has_s = bool(re.search(r"\bS\b", text))
        has_m = bool(re.search(r"\bM\b", text))

        shares = None
        price = None
        shares_m = _SHARES_RE.search(text)
        price_m = _PRICE_RE.search(text)
        if shares_m:
            try:
                shares = int(shares_m.group(1).replace(",", ""))
            except ValueError:
                pass
        if price_m:
            try:
                price = float(price_m.group(1).replace(",", ""))
            except ValueError:
                pass

        value = (shares * price) if shares and price else None

        name_m = _NAME_RE.search(text)
        title_m = _TITLE_RE.search(text)
        reporter = name_m.group(1).strip() if name_m else "Unknown"
        title = title_m.group(1).strip() if title_m else "Unknown"

        entry = {
            "name": reporter,
            "title": title,
            "shares": shares,
            "price": price,
            "value": value,
            "date": f.filed_date,
        }

        if has_10b51:
            result["plan_based"].append(entry)
        elif has_p:
            if value and value >= 50000:
                result["high_conviction_buys"].append(entry)
            else:
                result["low_conviction_buys"].append(entry)
        elif has_s:
            result["sales"].append(entry)
        elif has_m:
            result["option_exercises"].append(entry)

    return result


def _format_form4_summary(parsed: dict) -> str:
    lines = ["FORM 4 PRE-ANALYSIS SUMMARY:"]

    hcb = parsed["high_conviction_buys"]
    lines.append(f"High-conviction open market purchases (P-code, $50K+, no 10b5-1 plan): {len(hcb)}")
    for e in hcb:
        val = f"${e['value']:,.0f}" if e["value"] else "value unknown"
        shares = f"{e['shares']:,}" if e["shares"] else "?"
        lines.append(f"  {e['name']} | {e['title']} | {shares} shares | {val} | {e['date']}")

    lcb = parsed["low_conviction_buys"]
    lines.append(f"Low-conviction purchases (P-code, under $50K or with 10b5-1): {len(lcb)}")

    lines.append(f"Option exercises (M-code): {len(parsed['option_exercises'])}")
    lines.append(f"Sales (S-code): {len(parsed['sales'])}")
    lines.append(f"Plan-based transactions (10b5-1): {len(parsed['plan_based'])}")

    return "\n".join(lines)


# ─── Pre-computed structured summaries ───────────────────────────────────────

def _dilution_velocity_text(velocity: list[dict]) -> str:
    if not velocity:
        return "No historical 10-Q share count data available."
    lines = []
    for entry in velocity:
        if entry.get("qoq_pct") is not None:
            direction = "▲" if entry["qoq_pct"] > 0 else ("▼" if entry["qoq_pct"] < 0 else "—")
            lines.append(f"  {entry['date']}: {entry['shares']:,} shares  {direction} {entry['qoq_pct']:+.2f}% QoQ")
        else:
            lines.append(f"  {entry['date']}: {entry['shares']:,} shares  (baseline)")
    return "\n".join(lines)


def _warrant_overhang_text(warrants: list[dict]) -> str:
    if not warrants:
        return "No warrants with extractable terms found in 10-Q filings."
    lines = []
    for w in warrants[:12]:
        lines.append(f"  {w['shares']:,} shares @ ${w['strike']:.2f} strike — expires {w['expiry']}")
    if len(warrants) > 12:
        lines.append(f"  ... and {len(warrants) - 12} additional warrant tranches")
    return "\n".join(lines)


def _insider_cluster_text(clusters: list[dict]) -> str:
    if not clusters:
        return "No coordinated insider buying clusters detected in the filing window."
    lines = []
    for c in clusters:
        names_str = ", ".join(c["names"][:3])
        if len(c["names"]) > 3:
            names_str += f" +{len(c['names']) - 3} more"
        lines.append(
            f"  {c['window_start']} → {c['window_end']}: "
            f"{c['insider_count']} insiders, {c['transaction_count']} transactions "
            f"({names_str})"
        )
    return "\n".join(lines)


# ─── Insider summary text ─────────────────────────────────────────────────────

def _insider_summary_text(s: InsiderSummary) -> str:
    if s.total_buys == 0 and s.total_sells == 0 and s.total_exercises == 0:
        return "No Form 4 filings found in the analysis window."

    lines = []
    if s.total_buys:
        avg = f" @ avg ${s.avg_buy_price:.2f}" if s.avg_buy_price else ""
        lines.append(f"  Buys (P-code, open market): {s.total_buys} transactions, {s.buy_shares:,} shares{avg}")
    if s.total_sells:
        avg = f" @ avg ${s.avg_sell_price:.2f}" if s.avg_sell_price else ""
        lines.append(f"  Sells (S-code): {s.total_sells} transactions{avg}")
    if s.total_exercises:
        lines.append(f"  Option exercises (M-code): {s.total_exercises} transactions")
    lines.append(f"  Transaction code breakdown — P: {s.p_code_count}, S: {s.s_code_count}, M: {s.m_code_count}")
    if s.most_recent_date:
        lines.append(f"  Most recent transaction: {s.most_recent_date}")
    return "\n".join(lines)


# ─── Prompt construction ──────────────────────────────────────────────────────

def _build_user_message(
    ticker: str,
    metrics: VolumeMetrics,
    edgar: EdgarData,
    peers: list[PeerVolumeData],
    form4_parsed: dict,
) -> str:
    today = date.today().isoformat()
    price = f"{metrics.current_price:.4f}" if metrics.current_price else "N/A"
    zscore = f"{metrics.volume_zscore:.2f}" if metrics.volume_zscore is not None else "N/A"
    p1d = fmt_pct(metrics.pct_change_1d)
    p5d = fmt_pct(metrics.pct_change_5d)
    p20d = fmt_pct(metrics.pct_change_20d)
    mktcap = fmt_millions(metrics.market_cap)
    float_str = fmt_shares(metrics.float_shares) if metrics.float_shares else "N/A"
    short_pct = fmt_pct(metrics.short_pct_float) if metrics.short_pct_float else "N/A"
    inst_own = fmt_pct(metrics.institutional_pct) if metrics.institutional_pct else "N/A"
    ins_own = fmt_pct(metrics.insider_pct) if metrics.insider_pct else "N/A"
    runway_str = f"{edgar.runway_months:.1f}" if edgar.runway_months else "N/A"

    sector_rotation_note = ""
    if peers:
        if is_sector_rotation(peers):
            elevated = [p.ticker for p in peers if p.zscore is not None and p.zscore >= 1.5]
            sector_rotation_note = (
                f"\nSECTOR ROTATION FLAG: {len(elevated)} sector peers also showing elevated volume "
                f"({', '.join(elevated)}). This may be sector-wide rather than company-specific."
            )
        else:
            sector_rotation_note = "\nSector peer volumes: normal — spike appears company-specific."

    velocity_note = ""
    if edgar.filing_velocity_prior_60d > 0:
        ratio = edgar.filing_velocity_30d / edgar.filing_velocity_prior_60d
        if ratio > 1.5:
            velocity_note = (
                f"\nFILING VELOCITY ALERT: {edgar.filing_velocity_30d} relevant filings in last 30 days "
                f"vs {edgar.filing_velocity_prior_60d} in prior 60-day period "
                f"({ratio:.1f}x acceleration). Institutional activity clustering detected."
            )
    elif edgar.filing_velocity_30d > 0:
        velocity_note = f"\nFiling velocity: {edgar.filing_velocity_30d} relevant filings in last 30 days (no prior baseline)."

    catalyst_note = ""
    if edgar.catalyst_dates:
        lines = [f"  {c['type']}: {c['date']} (from 8-K filed {c['filing_date']})" for c in edgar.catalyst_dates]
        catalyst_note = "\nCATALYST CALENDAR (extracted from 8-K filings):\n" + "\n".join(lines)

    dilution_velocity_note = (
        "\nSHARE COUNT DILUTION VELOCITY (last 4 quarters — pre-computed from 10-Q cover pages):\n"
        + _dilution_velocity_text(edgar.dilution_velocity)
    )

    warrant_note = (
        "\nWARRANT OVERHANG MAP (extracted from 10-Q notes — sorted by strike price):\n"
        + _warrant_overhang_text(edgar.warrant_overhang)
    )

    cluster_note = (
        "\nINSIDER BUYING CLUSTER ANALYSIS (coordinated P-code purchases within 14-day windows):\n"
        + _insider_cluster_text(edgar.insider_clusters)
    )

    filing_blocks = []
    form_type_set: set[str] = set()
    for f in edgar.filings:
        form_type_set.add(f.form_type)
        block = (
            f"{'=' * 40}\n"
            f"FILING TYPE: {f.form_type}\n"
            f"DATE: {f.filed_date}\n"
            f"PRIORITY: {f.priority}\n"
            f"{'-' * 40}\n"
            f"{f.full_text if f.full_text else '[No text retrieved]'}\n"
        )
        filing_blocks.append(block)

    filings_section = "\n".join(filing_blocks) if filing_blocks else "No filings found in the 90-day window."
    form_types_str = ", ".join(sorted(form_type_set)) if form_type_set else "none"

    return f"""FORENSIC SEC FILING AUDIT REQUEST
===================================
Ticker: {ticker}
Analysis date: {today}
Current price: ${price}
Volume Z-score today: {zscore} (standard deviations above 20-day mean)
Volume pattern classification: {metrics.volume_pattern}
Price change 1D / 5D / 20D: {p1d} / {p5d} / {p20d}
Market cap: {mktcap}
Float: {float_str} shares
Short interest as % of float: {short_pct}
Institutional ownership: {inst_own}
Insider ownership: {ins_own}
Estimated cash runway: {runway_str} months (pre-computed from most recent 10-Q/K)
{sector_rotation_note}{velocity_note}{catalyst_note}
{dilution_velocity_note}
{warrant_note}
{cluster_note}

INSIDER TRANSACTION SUMMARY (last 90 days):
{_insider_summary_text(edgar.insider_summary)}

{_format_form4_summary(form4_parsed)}

SEC FILINGS FOUND (last 90 days):
{len(edgar.filings)} filings across {form_types_str}

{filings_section}

---

Execute the following 5-step analytical audit on the filing data above:

STEP 1: CALCULATE THE CASH RUNWAY
Extract from the most recent 10-Q or 10-K:
- Cash and cash equivalents
- Total current assets vs total current liabilities
- Net cash used in operating activities (Statement of Cash Flows)
Compute: Quarterly Burn Rate = most recent 6-month or 9-month operating cash burn annualized / 4
Compute: Cash Runway (Quarters) = Current Cash / Quarterly Burn Rate
CRITICAL THRESHOLD: Cash Runway < 4 quarters = severe structural pressure to dilute.

STEP 2: AUDIT ATM OFFERING EXPOSURE
Search all S-3, S-3/A, and 424B filings for "At-the-Market", "Sales Agreement", or "Equity Distribution Agreement".
- Identify the sales agents by name
- Extract total dollar capacity of the active ATM facility
- Extract remaining balance available to be sold

STEP 3: TRACK REAL-TIME SHARE COUNT DILUTION
The pre-computed dilution velocity table above shows QoQ share count changes across up to 4 quarters.
Use it to identify the trend. Also cross-reference with any 8-K or prospectus supplements for more recent share counts.
- Cumulative dilution over 4 quarters > 10% is a structural red flag regardless of ATM status
- QoQ increase > 2% during sideways price compression signals active ATM or equity line dilution
- Accelerating QoQ rate (each quarter worse than the last) is highest severity

STEP 4: SCREEN FOR TOXIC FINANCING AND CONVERTIBLE DEBT
The warrant overhang map above lists known warrants with strike prices and expiry.
Also search all 8-K and 10-Q/K filings for "Convertible Debentures", "Senior Secured Notes", "PIPE", or "Warrants".
- Is there a Variable Conversion Price or Floating Discount to VWAP?
- Is there a floor price? If floorless, this is a death-spiral structure.
- Quote the exact variable pricing clause if found
- For warrants: note if any strikes are near or below current price (in-the-money overhang)

STEP 5: VALIDATE INSIDER AND INSTITUTIONAL BLOCKS
The insider buying cluster analysis above shows whether multiple insiders bought within the same 14-day windows.
Coordinated cluster buying (2+ insiders, same window) is the highest-conviction insider signal.
Form 4 audit: differentiate Code P (open market purchase) from Code M or A (options). Only count Code P.
13D/G audit: is the reporting person an active small-cap fund building a stake, or a toxic funding entity (Yorkville, Lincoln Park, etc.) crossing 5% via conversion?

---

OUTPUT FORMAT — use exactly this structure:

## 1. Executive Verdict
- **Classification:** [Active Toxic Dilution / Active ATM Distribution / Dead Money / Genuine Accumulation]
- **Confidence Score:** [0–100%]
- **Core Catalyst:** [1-sentence explanation]

## 2. Quantitative Health Metrics
- **Current Cash:** $_________
- **Quarterly Burn Rate:** $_________
- **Estimated Cash Runway:** _________ Quarters
- **90-Day Share Dilution Delta:** _________% increase in shares outstanding

## 3. Dilution Mechanics Inventory
- **Active ATM Facility?** [Yes/No — name agent and remaining balance]
- **Toxic Convertible Covenants Found?** [Yes/No — quote the variable pricing clause if present]

## 4. True Demand Footprint
- **Legitimate Open-Market Insider Buying (Code P) Past 90 Days:** [Yes/No — state total shares and dollar amount]
- **Active Institutional Block Building (13D/G):** [Yes/No — name the funds and stake sizes]

## 5. Skeptical Conclusion
Explain how the technical chart patterns (sideways compression, high-volume nodes, Z-score of {zscore}) match or contradict the fundamental regulatory profile found above. Be direct. A trader is reading this in real time.
"""


# ─── API call functions ───────────────────────────────────────────────────────

def _call_claude(model_id: str, max_tokens: int, user_message: str) -> tuple[str, dict]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set. Add it to your .env file.")
    client = _anthropic_sdk.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model_id,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    in_tok = response.usage.input_tokens
    out_tok = response.usage.output_tokens
    in_price, out_price = _MODEL_PRICING.get(model_id, (0.0, 0.0))
    cost = (in_tok / 1_000_000) * in_price + (out_tok / 1_000_000) * out_price
    return response.content[0].text, {
        "model": model_id,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": cost,
    }


def _call_gemini(model_id: str, max_tokens: int, fallbacks: list[str], user_message: str) -> tuple[str, dict]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set. Check your .env file.")
    genai.configure(api_key=api_key)
    generation_config = genai.types.GenerationConfig(max_output_tokens=max_tokens, temperature=0.2)
    last_exc: Exception | None = None
    for candidate in [model_id] + fallbacks:
        try:
            model = genai.GenerativeModel(
                model_name=candidate,
                system_instruction=SYSTEM_PROMPT,
                generation_config=generation_config,
            )
            response = model.generate_content(user_message)
            usage = response.usage_metadata
            in_tok = getattr(usage, "prompt_token_count", 0) or 0
            out_tok = getattr(usage, "candidates_token_count", 0) or 0
            in_price, out_price = _MODEL_PRICING.get(candidate, (0.0, 0.0))
            cost = (in_tok / 1_000_000) * in_price + (out_tok / 1_000_000) * out_price
            return response.text, {
                "model": candidate,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cost_usd": cost,
            }
        except Exception as exc:
            last_exc = exc
            if "429" in str(exc) or "quota" in str(exc).lower() or "rate" in str(exc).lower():
                continue
            raise
    raise last_exc or RuntimeError("All Gemini candidates exhausted")


# ─── Main analysis entry point ────────────────────────────────────────────────

def run_analysis(
    ticker: str,
    metrics: VolumeMetrics,
    edgar: EdgarData,
    peers: list[PeerVolumeData],
    model_key: str = "claude",
) -> tuple[str, dict]:
    substantive = [f for f in edgar.filings if len(f.full_text.split()) > 500]
    if len(substantive) < 2:
        _console.print(_Panel(
            f"[yellow]Only {len(substantive)} filing(s) exceed 500 words. "
            f"Analysis will have limited depth — treat verdict as preliminary.[/yellow]",
            title="[bold yellow]THIN DATA WARNING[/bold yellow]",
            border_style="yellow",
        ))

    form4_parsed = parse_form4_transactions(edgar.filings)
    user_message = _build_user_message(ticker, metrics, edgar, peers, form4_parsed)

    cfg = _MODELS[model_key]
    if cfg["api"] == "anthropic":
        return _call_claude(cfg["model_id"], cfg["max_tokens"], user_message)
    return _call_gemini(cfg["model_id"], cfg["max_tokens"], cfg["fallbacks"], user_message)


def extract_verdict(brief_text: str) -> str:
    verdicts = [
        "ACTIVE TOXIC DILUTION",
        "ACTIVE ATM DISTRIBUTION",
        "GENUINE ACCUMULATION",
        "DEAD MONEY",
    ]
    upper = brief_text.upper()
    for v in verdicts:
        if v in upper:
            return v
    return "UNKNOWN"
