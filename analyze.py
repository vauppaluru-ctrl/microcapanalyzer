"""Entry point: orchestrates the full SEC Volume Spike Analyzer pipeline."""

from __future__ import annotations

import sys
import time

from dotenv import load_dotenv
from rich.panel import Panel

load_dotenv()

from display import (
    console,
    make_filing_progress,
    print_enforcement_flags,
    print_error,
    print_filing_summary,
    print_footer,
    print_header,
    print_peer_volume,
    print_research_brief,
    print_volume_metrics,
    print_warning,
)
from edgar import EdgarData, FilingRecord, check_sec_enforcement, fetch_edgar_data
from market_data import (
    VolumeMetrics,
    fetch_market_data,
    fetch_peer_volume,
    get_regime_warning,
    is_sector_rotation,
    position_size_reality_check,
)
from nlp_analysis import run_analysis


def main() -> None:
    # UPDATE 1: macro regime warning before ticker prompt
    console.print("\n[dim]Checking macro regime…[/dim]")
    regime_warning = get_regime_warning()
    if regime_warning:
        console.print(Panel(
            f"[bold red]{regime_warning}[/bold red]",
            title="[bold]MACRO REGIME ALERT[/bold]",
            border_style="red",
        ))

    try:
        ticker_raw = input("Enter ticker symbol: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)

    if not ticker_raw:
        print_error("No ticker entered.")
        sys.exit(1)

    ticker = ticker_raw.upper()
    t_start = time.time()

    # ── Step 1: Market data ──────────────────────────────────────────────────
    console.print(f"\n[bold cyan]Fetching market data for [white]{ticker}[/white]…[/bold cyan]")
    metrics = fetch_market_data(ticker)

    if metrics.error:
        print_warning(f"Market data unavailable: {metrics.error}. Proceeding with filing analysis only.")
        metrics = VolumeMetrics(ticker=ticker)

    print_header(ticker, metrics)

    # ── Step 2: EDGAR filings ────────────────────────────────────────────────
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

    # UPDATE 2: SEC enforcement check (warn and continue, never exit)
    enforcement_flags = check_sec_enforcement(edgar_data.company_name, ticker)
    print_enforcement_flags(enforcement_flags)

    # UPDATE 3: position size check
    pos_check = position_size_reality_check(metrics.avg_daily_dollar_volume)

    console.print()
    print_volume_metrics(metrics, position_size_check=pos_check)
    print_filing_summary(edgar_data)

    # ── Step 3: Supplementary intelligence ──────────────────────────────────
    peers: list = []
    if metrics.sector:
        console.print(f"\n[dim]Fetching sector peer volumes ({metrics.sector})…[/dim]")
        peers = fetch_peer_volume(metrics.sector, ticker)
        print_peer_volume(peers, metrics.sector)

        if is_sector_rotation(peers):
            console.print(
                "[bold yellow]⚡ SECTOR ROTATION DETECTED — multiple peers showing elevated volume. "
                "Treat as sector event, not company-specific signal.[/bold yellow]"
            )

    # ── Step 4: Gemini analysis ──────────────────────────────────────────────
    console.print("\n[bold cyan]Sending to Gemini for analysis…[/bold cyan]")
    try:
        # UPDATE 6: dilution_score removed from signature
        brief = run_analysis(ticker, metrics, edgar_data, peers)
    except ValueError as exc:
        print_error(str(exc))
        sys.exit(1)
    except Exception as exc:
        print_error(f"Gemini API error: {exc}\nCheck your GEMINI_API_KEY in .env and try again.")
        sys.exit(1)

    # ── Step 5: Output ───────────────────────────────────────────────────────
    print_research_brief(brief)
    print_footer(edgar_data, time.time() - t_start)


if __name__ == "__main__":
    main()
