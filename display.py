"""All rich terminal formatting: panels, tables, progress, final brief."""

from __future__ import annotations

from datetime import datetime

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from edgar import EdgarData, FilingRecord
from market_data import PeerVolumeData, VolumeMetrics
from nlp_analysis import extract_verdict
from utils import fmt_millions, fmt_pct, fmt_shares

console = Console()


# ─── Header ───────────────────────────────────────────────────────────────────

def print_header(ticker: str, metrics: VolumeMetrics) -> None:
    price_str = f"${metrics.current_price:.4f}" if metrics.current_price else "N/A"
    mktcap_str = fmt_millions(metrics.market_cap)
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    header_text = Text()
    header_text.append(f"  {ticker.upper()}  ", style="bold white on navy_blue")
    header_text.append(f"  {price_str}  ", style="bold green")
    header_text.append(f"  Cap: {mktcap_str}  ", style="dim")
    header_text.append(f"  {date_str}", style="dim")

    console.print()
    console.print(Panel(header_text, title="[bold cyan]SEC Volume Spike Analyzer[/bold cyan]", border_style="cyan"))


# ─── Volume metrics table (UPDATE 3: position size row; UPDATE 6: no dilution score) ──

def print_volume_metrics(metrics: VolumeMetrics, position_size_check: dict | None = None) -> None:
    table = Table(
        title="Volume & Price Metrics",
        box=box.ROUNDED,
        border_style="blue",
        show_header=True,
        header_style="bold blue",
        expand=False,
    )
    table.add_column("Metric", style="dim", width=30)
    table.add_column("Value", justify="right", width=22)

    def row(label: str, value: str, style: str = "") -> None:
        table.add_row(label, Text(value, style=style))

    vol_today = f"{metrics.volume_today:,}" if metrics.volume_today else "N/A"
    mean_20d = f"{int(metrics.volume_20d_mean):,}" if metrics.volume_20d_mean else "N/A"
    zscore = metrics.volume_zscore
    zscore_str = f"{zscore:.2f}σ" if zscore is not None else "N/A"
    zscore_style = "bold red" if zscore and zscore > 3 else ("yellow" if zscore and zscore > 1.5 else "green")

    row("Volume Today", vol_today)
    row("20-Day Avg Volume", mean_20d)
    row("Volume Z-Score", zscore_str, zscore_style)
    row("Volume Pattern", metrics.volume_pattern, "bold cyan")
    table.add_section()

    row("Price Change 1D", fmt_pct(metrics.pct_change_1d), _pct_style(metrics.pct_change_1d))
    row("Price Change 5D", fmt_pct(metrics.pct_change_5d), _pct_style(metrics.pct_change_5d))
    row("Price Change 20D", fmt_pct(metrics.pct_change_20d), _pct_style(metrics.pct_change_20d))
    table.add_section()

    hi = f"${metrics.week52_high:.4f}" if metrics.week52_high else "N/A"
    lo = f"${metrics.week52_low:.4f}" if metrics.week52_low else "N/A"
    row("52-Week High", hi)
    row("52-Week Low", lo)
    row("% From 52W High", fmt_pct(metrics.pct_from_52w_high), "red" if metrics.pct_from_52w_high and metrics.pct_from_52w_high > -5 else "")
    row("% From 52W Low", fmt_pct(metrics.pct_from_52w_low))
    table.add_section()

    row("Float", fmt_shares(metrics.float_shares))
    row("Shares Outstanding", fmt_shares(metrics.shares_outstanding))
    row("Avg Daily Dollar Volume", fmt_millions(metrics.avg_daily_dollar_volume))
    row("Short % of Float", fmt_pct(metrics.short_pct_float))
    row("Short Ratio", f"{metrics.short_ratio:.1f}d" if metrics.short_ratio else "N/A")
    row("Institutional Ownership", fmt_pct(metrics.institutional_pct))
    row("Insider Ownership", fmt_pct(metrics.insider_pct))

    # UPDATE 3: position size check row
    if position_size_check:
        table.add_section()
        rating = position_size_check.get("rating", "UNKNOWN")
        pct = position_size_check.get("pct_of_adv")
        intended = position_size_check.get("intended", 25000)
        r_style = (
            "bold green" if rating == "EXECUTABLE"
            else "bold yellow" if rating == "CONSTRAINED"
            else "bold red"
        )
        pct_str = f"{pct:.1f}% of ADV" if pct is not None else "ADV N/A"
        row(f"Position Size (${intended:,.0f})", f"{rating} — {pct_str}", r_style)

    console.print(table)


def _pct_style(val: float | None) -> str:
    if val is None:
        return ""
    return "green" if val >= 0 else "red"


# ─── UPDATE 2: Enforcement history panel ─────────────────────────────────────

def print_enforcement_flags(flags: list[str]) -> None:
    if not flags:
        return
    body = "\n".join(f"• {f}" for f in flags)
    console.print(Panel(
        f"[bold red]{body}[/bold red]",
        title="[bold]SEC ENFORCEMENT HISTORY[/bold]",
        border_style="red",
    ))


# ─── Progress bar ─────────────────────────────────────────────────────────────

def make_filing_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )


def print_filing_summary(edgar: EdgarData) -> None:
    console.print()
    console.print(Rule("[bold]Filing Inventory[/bold]"))

    if not edgar.filings:
        console.print(Panel(
            "[yellow]No relevant filings found in the last 90 days.[/yellow]\n"
            "Absence of EDGAR activity is itself a data point — no institutional disclosure obligations triggered.",
            border_style="yellow",
        ))
        return

    tbl = Table(box=box.SIMPLE_HEAVY, border_style="dim", show_header=True, header_style="bold")
    tbl.add_column("Form", width=12)
    tbl.add_column("Filed", width=12)
    tbl.add_column("Priority", width=10)
    tbl.add_column("Status", width=12)

    for f in edgar.filings:
        p_style = "bold red" if f.priority == "HIGH" else ("yellow" if f.priority == "MEDIUM" else "dim")
        err = "[red]ERR[/red]" if f.fetch_error else "[green]OK[/green]"
        tbl.add_row(f.form_type, f.filed_date, Text(f.priority, style=p_style), err)

    console.print(tbl)

    if edgar.filing_velocity_30d > edgar.filing_velocity_prior_60d * 1.5 and edgar.filing_velocity_prior_60d > 0:
        console.print(
            f"[bold yellow]⚡ Filing velocity accelerated: {edgar.filing_velocity_30d} filings in last 30d "
            f"vs {edgar.filing_velocity_prior_60d} in prior 60d[/bold yellow]"
        )

    if edgar.catalyst_dates:
        console.print("[bold cyan]Catalyst Dates Detected:[/bold cyan]")
        for c in edgar.catalyst_dates:
            console.print(f"  • {c['type']}: [cyan]{c['date']}[/cyan] (8-K filed {c['filing_date']})")


def print_peer_volume(peers: list[PeerVolumeData], sector: str | None) -> None:
    if not peers:
        return
    console.print()
    console.print(Rule(f"[bold]Sector Peer Volume ({sector or 'Unknown'})[/bold]"))
    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
    tbl.add_column("Ticker", width=10)
    tbl.add_column("Volume Today", justify="right", width=16)
    tbl.add_column("20D Avg", justify="right", width=16)
    tbl.add_column("Z-Score", justify="right", width=12)

    for p in peers:
        zscore_str = f"{p.zscore:.2f}σ" if p.zscore is not None else "N/A"
        z_style = "bold red" if p.zscore and p.zscore > 2 else ("yellow" if p.zscore and p.zscore > 1.0 else "")
        tbl.add_row(
            p.ticker,
            f"{p.volume_today:,}" if p.volume_today else "N/A",
            f"{int(p.volume_20d_mean):,}" if p.volume_20d_mean else "N/A",
            Text(zscore_str, style=z_style),
        )
    console.print(tbl)


# ─── Research brief ───────────────────────────────────────────────────────────

def print_research_brief(brief_text: str) -> None:
    console.print()
    console.print(Rule("[bold white]RESEARCH BRIEF[/bold white]"))

    sections = _split_sections(brief_text)
    verdict = extract_verdict(brief_text)

    for title, body in sections:
        border = _border_for_section(title, body, verdict)
        console.print(Panel(
            body.strip(),
            title=f"[bold]{title}[/bold]",
            border_style=border,
            title_align="left",
        ))

    if not sections:
        console.print(Panel(brief_text, title="Analysis", border_style="blue"))


def _split_sections(text: str) -> list[tuple[str, str]]:
    import re
    pattern = re.compile(r"^(?:#+\s*)?(\d+\.\s+[A-Z][A-Z .,/\-—()]+)", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if not matches:
        return []

    sections = []
    for i, match in enumerate(matches):
        title = match.group(1).strip().rstrip(".")
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].lstrip("\n")
        sections.append((title, body))
    return sections


def _border_for_section(title: str, body: str, verdict: str) -> str:
    upper_title = title.upper()

    # UPDATE 6: qualitative dilution assessment drives border color
    if "TOXIC" in upper_title:
        body_upper = body.upper()
        if "HIGH DILUTION RISK" in body_upper:
            return "bold red"
        if "ELEVATED" in body_upper:
            return "bold yellow"
        return "bold green"

    if "FINAL VERDICT" in upper_title:
        if "BUY" in verdict:
            return "bold green"
        if "HOLD" in verdict:
            return "bold yellow"
        return "bold red"

    if "BEAR" in upper_title or "RISKS" in upper_title:
        return "yellow"
    if "ACCUMULATION" in upper_title or "INSTITUTIONAL" in upper_title:
        return "cyan"
    return "blue"


# ─── Footer ───────────────────────────────────────────────────────────────────

def print_footer(edgar: EdgarData, elapsed_seconds: float) -> None:
    console.print()
    console.print(Rule())
    console.print(
        f"[dim]Analyzed {len(edgar.filings)} filings  •  "
        f"Runtime: {elapsed_seconds:.1f}s  •  "
        f"Model: Gemini Flash  •  "
        f"Not financial advice[/dim]"
    )
    console.print()


def print_error(message: str) -> None:
    console.print(Panel(f"[bold red]{message}[/bold red]", title="Error", border_style="red"))


def print_warning(message: str) -> None:
    console.print(f"[yellow]⚠  {message}[/yellow]")
