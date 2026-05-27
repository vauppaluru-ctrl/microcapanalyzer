"""All SEC EDGAR API logic: CIK resolution, filing fetch, supplementary data."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import requests

from utils import retry_with_backoff, sec_sleep, truncate

SEC_HEADERS = {"User-Agent": "sec-spike-analyzer contact@research.com"}
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{filename}"
ENFORCEMENT_URL = "https://efts.sec.gov/LATEST/search-index"

PRIORITY_FORMS: dict[str, int] = {
    "SC 13D": 1,
    "SC 13G": 1,
    "SC 13G/A": 1,
    "SC 13D/A": 1,
    "4": 2,
    "8-K": 3,
    "S-3": 4,
    "S-3/A": 4,
    "10-Q": 5,
    "DEF 14A": 6,
    "424B1": 7,
    "424B2": 7,
    "424B3": 7,
    "424B4": 7,
    "424B5": 7,
}

PRIORITY_LABELS = {1: "HIGH", 2: "HIGH", 3: "MEDIUM", 4: "HIGH", 5: "MEDIUM", 6: "LOW", 7: "HIGH"}


@dataclass
class FilingRecord:
    form_type: str
    filed_date: str
    accession_no: str
    primary_doc: str
    cik: str
    priority: str = "LOW"
    full_text: str = ""
    fetch_error: str | None = None


@dataclass
class InsiderSummary:
    total_buys: int = 0
    total_sells: int = 0
    total_exercises: int = 0
    buy_shares: int = 0
    sell_shares: int = 0
    avg_buy_price: float | None = None
    avg_sell_price: float | None = None
    most_recent_date: str | None = None
    p_code_count: int = 0
    s_code_count: int = 0
    m_code_count: int = 0
    raw_lines: list[str] = field(default_factory=list)


@dataclass
class EdgarData:
    ticker: str
    cik: str | None = None
    company_name: str | None = None
    filings: list[FilingRecord] = field(default_factory=list)
    insider_summary: InsiderSummary = field(default_factory=InsiderSummary)
    cash_and_equivalents: float | None = None
    quarterly_burn: float | None = None
    runway_months: float | None = None
    catalyst_dates: list[dict[str, str]] = field(default_factory=list)
    filing_velocity_30d: int = 0
    filing_velocity_prior_60d: int = 0
    error: str | None = None


# ─── CIK resolution ───────────────────────────────────────────────────────────

def resolve_cik(ticker: str) -> str | None:
    try:
        def _fetch():
            r = requests.get(COMPANY_TICKERS_URL, headers=SEC_HEADERS, timeout=15)
            r.raise_for_status()
            return r.json()

        data = retry_with_backoff(_fetch, label="CIK lookup")
        ticker_upper = ticker.upper()
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker_upper:
                return str(entry["cik_str"]).zfill(10)
    except Exception:
        pass
    return None


# ─── Submissions JSON ──────────────────────────────────────────────────────────

def fetch_submissions(cik: str) -> dict[str, Any]:
    def _fetch():
        r = requests.get(SUBMISSIONS_URL.format(cik=cik), headers=SEC_HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()

    return retry_with_backoff(_fetch, label="submissions JSON")


# ─── UPDATE 2: SEC enforcement history check ──────────────────────────────────

def check_sec_enforcement(company_name: str | None, ticker: str) -> list[str]:
    """Search EDGAR full-text for enforcement actions involving this company."""
    flags: list[str] = []

    def _search(query: str, forms: str) -> int:
        sec_sleep()
        try:
            r = requests.get(
                ENFORCEMENT_URL,
                params={"q": f'"{query}"', "forms": forms},
                headers=SEC_HEADERS,
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                return data.get("hits", {}).get("total", {}).get("value", 0)
        except Exception:
            pass
        return 0

    ticker_hits = _search(ticker, "LR,AAER,AP")
    if ticker_hits > 0:
        flags.append(
            f"{ticker_hits} SEC enforcement action(s) found referencing '{ticker}' "
            f"(Litigation Release / AAER / Admin Proceeding)"
        )

    if company_name:
        name_hits = _search(company_name, "LR")
        if name_hits > 0:
            flags.append(
                f"{name_hits} Litigation Release(s) found referencing company name '{company_name}'"
            )

    return flags


# ─── Filing list filtering ─────────────────────────────────────────────────────

def _parse_filings_from_submissions(submissions: dict, cik: str, lookback_days: int = 90) -> list[FilingRecord]:
    cutoff = datetime.now() - timedelta(days=lookback_days)
    recent = submissions.get("filings", {}).get("recent", {})

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    records: list[FilingRecord] = []
    form_counts: dict[str, int] = {}

    for form, date_str, acc, pdoc in zip(forms, dates, accessions, primary_docs):
        try:
            filed_dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue

        if filed_dt < cutoff:
            continue

        base_form = form.strip()
        priority_num = PRIORITY_FORMS.get(base_form)
        if priority_num is None:
            if base_form.startswith("424B"):
                priority_num = 7
            else:
                continue

        form_counts[base_form] = form_counts.get(base_form, 0) + 1
        if base_form == "10-Q" and form_counts[base_form] > 1:
            continue
        if base_form == "DEF 14A":
            thirty_ago = datetime.now() - timedelta(days=30)
            if filed_dt < thirty_ago:
                continue

        records.append(
            FilingRecord(
                form_type=base_form,
                filed_date=date_str,
                accession_no=acc,
                primary_doc=pdoc,
                cik=cik,
                priority=PRIORITY_LABELS.get(priority_num, "LOW"),
            )
        )

    return records


# ─── Document fetch ────────────────────────────────────────────────────────────

def _build_doc_url(cik: str, accession_no: str, filename: str) -> str:
    acc_dashes = accession_no.replace("-", "")
    return ARCHIVES_BASE.format(cik=cik.lstrip("0"), accession_no_dashes=acc_dashes, filename=filename)


def fetch_filing_text(record: FilingRecord) -> str:
    url = _build_doc_url(record.cik, record.accession_no, record.primary_doc)
    try:
        def _fetch():
            r = requests.get(url, headers=SEC_HEADERS, timeout=20)
            r.raise_for_status()
            return r.text

        text = retry_with_backoff(_fetch, label=f"{record.form_type} doc fetch")
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"\s{3,}", " ", text)
        return truncate(text.strip())
    except Exception as exc:
        return f"[FETCH ERROR: {exc}]"


# ─── Insider summary ──────────────────────────────────────────────────────────

_P_CODE_RE = re.compile(r"\bP\b")
_S_CODE_RE = re.compile(r"\bS\b")
_M_CODE_RE = re.compile(r"\bM\b")
_SHARES_RE = re.compile(r"(\d[\d,]+)\s*(?:shares?|shs?)", re.IGNORECASE)
_PRICE_RE = re.compile(r"\$\s*([\d.,]+)")


def _parse_insider_summary(form4_filings: list[FilingRecord]) -> InsiderSummary:
    summary = InsiderSummary()
    buy_prices: list[float] = []
    sell_prices: list[float] = []

    for f in form4_filings:
        text = f.full_text
        if not text:
            continue

        summary.most_recent_date = f.filed_date if (
            summary.most_recent_date is None or f.filed_date > summary.most_recent_date
        ) else summary.most_recent_date

        p_matches = _P_CODE_RE.findall(text)
        s_matches = _S_CODE_RE.findall(text)
        m_matches = _M_CODE_RE.findall(text)

        summary.p_code_count += len(p_matches)
        summary.s_code_count += len(s_matches)
        summary.m_code_count += len(m_matches)

        if p_matches:
            summary.total_buys += 1
            share_m = _SHARES_RE.search(text)
            if share_m:
                try:
                    summary.buy_shares += int(share_m.group(1).replace(",", ""))
                except ValueError:
                    pass
            price_m = _PRICE_RE.search(text)
            if price_m:
                try:
                    buy_prices.append(float(price_m.group(1).replace(",", "")))
                except ValueError:
                    pass

        if s_matches:
            summary.total_sells += 1
            price_m = _PRICE_RE.search(text)
            if price_m:
                try:
                    sell_prices.append(float(price_m.group(1).replace(",", "")))
                except ValueError:
                    pass

        if m_matches:
            summary.total_exercises += 1

    if buy_prices:
        summary.avg_buy_price = sum(buy_prices) / len(buy_prices)
    if sell_prices:
        summary.avg_sell_price = sum(sell_prices) / len(sell_prices)

    return summary


# ─── Cash runway from 10-Q ────────────────────────────────────────────────────

_CASH_RE = re.compile(
    r"cash\s+and\s+cash\s+equivalents[^$\d]*\$?\s*([\d,]+(?:\.\d+)?)\s*(million|thousand|billion)?",
    re.IGNORECASE,
)
_BURN_RE = re.compile(
    r"(?:net\s+cash\s+used|operating\s+activities)[^$\d]*\$?\s*([\d,]+(?:\.\d+)?)\s*(million|thousand|billion)?",
    re.IGNORECASE,
)


def _multiplier(unit: str | None) -> float:
    if not unit:
        return 1.0
    u = unit.lower()
    if u == "million":
        return 1e6
    if u == "billion":
        return 1e9
    if u == "thousand":
        return 1e3
    return 1.0


def _parse_cash_runway(tenq_text: str) -> tuple[float | None, float | None, float | None]:
    cash_m = _CASH_RE.search(tenq_text)
    burn_m = _BURN_RE.search(tenq_text)

    cash = None
    burn = None

    if cash_m:
        try:
            cash = float(cash_m.group(1).replace(",", "")) * _multiplier(cash_m.group(2))
        except ValueError:
            pass

    if burn_m:
        try:
            burn = float(burn_m.group(1).replace(",", "")) * _multiplier(burn_m.group(2))
        except ValueError:
            pass

    runway = None
    if cash and burn and burn > 0:
        runway = (cash / burn) * 3

    return cash, burn, runway


# ─── Catalyst date extraction ─────────────────────────────────────────────────

_CATALYST_PATTERNS = [
    (re.compile(r"earnings\s+(?:release|date|call)[^.]*?(\w+ \d{1,2},? \d{4})", re.IGNORECASE), "Earnings"),
    (re.compile(r"FDA\s+(?:action|approval|decision|PDUFA)[^.]*?(\w+ \d{1,2},? \d{4})", re.IGNORECASE), "FDA Event"),
    (re.compile(r"contract\s+award[^.]*?(\w+ \d{1,2},? \d{4})", re.IGNORECASE), "Contract Award"),
    (re.compile(r"partnership\s+announcement[^.]*?(\w+ \d{1,2},? \d{4})", re.IGNORECASE), "Partnership"),
]


def _extract_catalyst_dates(eightk_filings: list[FilingRecord]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for f in eightk_filings:
        for pattern, label in _CATALYST_PATTERNS:
            m = pattern.search(f.full_text)
            if m:
                results.append({"type": label, "date": m.group(1), "filing_date": f.filed_date})
    return results


# ─── Filing velocity ──────────────────────────────────────────────────────────

def _compute_velocity(submissions: dict) -> tuple[int, int]:
    recent = submissions.get("filings", {}).get("recent", {})
    dates = recent.get("filingDate", [])
    forms = recent.get("form", [])

    now = datetime.now()
    cutoff_30 = now - timedelta(days=30)
    cutoff_90 = now - timedelta(days=90)

    count_30 = 0
    count_prior = 0

    for date_str, form in zip(dates, forms):
        if form not in PRIORITY_FORMS and not form.startswith("424B"):
            continue
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if dt >= cutoff_30:
            count_30 += 1
        elif dt >= cutoff_90:
            count_prior += 1

    return count_30, count_prior


# ─── Main entry point ─────────────────────────────────────────────────────────

def fetch_edgar_data(ticker: str, progress_callback=None) -> EdgarData:
    data = EdgarData(ticker=ticker)

    cik = resolve_cik(ticker)
    if not cik:
        data.error = f"Ticker '{ticker}' not found in SEC EDGAR company tickers database."
        return data
    data.cik = cik

    try:
        submissions = fetch_submissions(cik)
    except Exception as exc:
        data.error = f"Failed to fetch submissions JSON: {exc}"
        return data

    data.company_name = submissions.get("name")
    sec_sleep()

    filings = _parse_filings_from_submissions(submissions, cik)
    data.filing_velocity_30d, data.filing_velocity_prior_60d = _compute_velocity(submissions)

    for i, record in enumerate(filings):
        if progress_callback:
            progress_callback(record, i, len(filings))
        sec_sleep()
        text = fetch_filing_text(record)
        record.full_text = text

    data.filings = filings

    form4s = [f for f in filings if f.form_type == "4"]
    data.insider_summary = _parse_insider_summary(form4s)

    tenqs = [f for f in filings if f.form_type == "10-Q"]
    if tenqs:
        cash, burn, runway = _parse_cash_runway(tenqs[0].full_text)
        data.cash_and_equivalents = cash
        data.quarterly_burn = burn
        data.runway_months = runway

    eightks = [f for f in filings if f.form_type == "8-K"]
    data.catalyst_dates = _extract_catalyst_dates(eightks)

    return data
