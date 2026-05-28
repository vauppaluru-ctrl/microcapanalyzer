"""Entry point: SEC Volume Spike Analyzer.

If MASSIVE_API_KEY is set in .env, market data is fetched automatically.
TOS CLI args override any auto-fetched field when provided.

Example (auto market data):
  analyzeVolumeSpike CBRS

Example (TOS override):
  analyzeVolumeSpike CBRS --zscore 4.2 --price 3.15
"""

from __future__ import annotations

import argparse
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from display import (
    console,
    make_filing_progress,
    print_enforcement_flags,
    print_error,
    print_filing_summary,
    print_footer,
    print_header,
    print_research_brief,
    print_volume_metrics,
    print_warning,
)
from edgar import EdgarData, FilingRecord, check_sec_enforcement, fetch_edgar_data
from market_data import VolumeMetrics, fetch_market_data, position_size_reality_check
from nlp_analysis import run_analysis, _MODELS


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="analyzeVolumeSpike",
        description=(
            "SEC filing intelligence brief for a volume spike spotted in ThinkOrSwim. "
            "All market data flags are optional — pass what you can read off TOS."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "ThinkOrSwim parameters (paste the values you see in TOS):\n"
            "  --price        Current price shown in chart header\n"
            "  --zscore       Volume z-score from the ThinkScript study\n"
            "  --volume       Today's volume bar\n"
            "  --avg-volume   20-day average volume (shown in scan columns)\n"
            "  --change-1d    Today's %% price change\n"
            "  --change-5d    5-day %% price change\n"
            "  --change-20d   20-day %% price change\n"
            "  --market-cap   Market cap in dollars (TOS: Fundamentals tab)\n"
            "  --adv          Avg daily dollar volume; derived from price × avg-volume if omitted\n"
        ),
    )
    p.add_argument("ticker", nargs="?", help="Ticker symbol (prompted interactively if omitted)")
    p.add_argument("--price", type=float, metavar="FLOAT", help="Current price")
    p.add_argument("--zscore", type=float, metavar="FLOAT", help="Volume z-score")
    p.add_argument("--volume", type=int, metavar="INT", help="Today's volume")
    p.add_argument("--avg-volume", type=float, metavar="FLOAT", help="20-day avg volume")
    p.add_argument("--change-1d", type=float, metavar="FLOAT", help="1-day price change %%")
    p.add_argument("--change-5d", type=float, metavar="FLOAT", help="5-day price change %%")
    p.add_argument("--change-20d", type=float, metavar="FLOAT", help="20-day price change %%")
    p.add_argument("--market-cap", type=float, metavar="FLOAT", help="Market cap in dollars")
    p.add_argument("--adv", type=float, metavar="FLOAT", help="Avg daily dollar volume (for position sizing)")
    return p.parse_args()


def _choose_model() -> str:
    keys = list(_MODELS.keys())
    console.print("\n[bold]Choose analysis model:[/bold]")
    for i, key in enumerate(keys, 1):
        cfg = _MODELS[key]
        console.print(f"  [cyan]{i}[/cyan].  {cfg['display']:<24} {cfg['desc']}")
    while True:
        try:
            raw = input("Model [1]: ").strip()
        except (KeyboardInterrupt, EOFError):
            return keys[0]
        if not raw:
            return keys[0]
        if raw.isdigit() and 1 <= int(raw) <= len(keys):
            return keys[int(raw) - 1]
        console.print(f"[yellow]Enter 1–{len(keys)}[/yellow]")


def _build_metrics(ticker: str, args: argparse.Namespace) -> VolumeMetrics:
    import os
    if os.getenv("MASSIVE_API_KEY"):
        console.print(f"\n[bold cyan]Fetching market data for [white]{ticker}[/white]…[/bold cyan]")
        m = fetch_market_data(ticker)
        if m.error:
            print_warning(f"Massive API: {m.error} — proceeding with CLI args / N/A fields.")
            m = VolumeMetrics(ticker=ticker)
    else:
        m = VolumeMetrics(ticker=ticker)

    # CLI args override auto-fetched values when explicitly provided
    if args.price is not None:      m.current_price = args.price
    if args.zscore is not None:     m.volume_zscore = args.zscore
    if args.volume is not None:     m.volume_today  = args.volume
    if args.avg_volume is not None: m.volume_20d_mean = args.avg_volume
    if args.change_1d is not None:  m.pct_change_1d = args.change_1d
    if args.change_5d is not None:  m.pct_change_5d = args.change_5d
    if args.change_20d is not None: m.pct_change_20d = args.change_20d
    if args.market_cap is not None: m.market_cap = args.market_cap
    if args.adv is not None:        m.avg_daily_dollar_volume = args.adv

    if m.avg_daily_dollar_volume is None and m.current_price and m.volume_20d_mean:
        m.avg_daily_dollar_volume = m.current_price * m.volume_20d_mean
    return m


def main() -> None:
    args = _parse_args()

    if args.ticker:
        ticker = args.ticker.upper()
    else:
        try:
            ticker_raw = input("Enter ticker symbol: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(0)
        if not ticker_raw:
            print_error("No ticker entered.")
            sys.exit(1)
        ticker = ticker_raw.upper()

    model_key = _choose_model()
    t_start = time.time()
    metrics = _build_metrics(ticker, args)

    print_header(ticker, metrics)

    # ── Step 1: EDGAR filings ─────────────────────────────────────────────────
    console.print(f"\n[bold cyan]Fetching SEC EDGAR filings…[/bold cyan]")

    progress = make_filing_progress()
    filing_task = None

    with progress:
        def on_progress(record: FilingRecord, idx: int, total: int) -> None:
            nonlocal filing_task
            if filing_task is None:
                filing_task = progress.add_task("Fetching filings…", total=total)
            progress.update(
                filing_task,
                advance=1,
                description=f"[cyan]{record.form_type}[/cyan] {record.filed_date}",
            )

        edgar_data = fetch_edgar_data(ticker, progress_callback=on_progress)

    if edgar_data.error:
        print_error(edgar_data.error)
        sys.exit(1)

    if not edgar_data.filings:
        print_warning(
            "No relevant filings found in the last 90 days. "
            "This is itself a finding — no institutional disclosure obligations triggered."
        )

    # ── Step 2: Enforcement check + metrics display ───────────────────────────
    enforcement_flags = check_sec_enforcement(edgar_data.company_name, ticker)
    print_enforcement_flags(enforcement_flags)

    pos_check = position_size_reality_check(metrics.avg_daily_dollar_volume)

    console.print()
    print_volume_metrics(metrics, position_size_check=pos_check)
    print_filing_summary(edgar_data)

    # ── Step 3: Gemini analysis ───────────────────────────────────────────────
    model_display = _MODELS[model_key]["display"]
    console.print(f"\n[bold cyan]Sending to {model_display} for analysis…[/bold cyan]")
    try:
        brief, usage = run_analysis(ticker, metrics, edgar_data, [], model_key=model_key)
    except ValueError as exc:
        print_error(str(exc))
        sys.exit(1)
    except Exception as exc:
        print_error(f"Gemini API error: {exc}\nCheck your GEMINI_API_KEY in .env and try again.")
        sys.exit(1)

    # ── Step 4: Output ────────────────────────────────────────────────────────
    print_research_brief(brief)
    print_footer(edgar_data, time.time() - t_start, usage=usage)


if __name__ == "__main__":
    main()
