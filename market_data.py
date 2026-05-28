"""Market data containers, Massive API fetch, and position-size utility.

If MASSIVE_API_KEY is set in .env, fetch_market_data() pulls full OHLCV +
ticker details automatically. CLI args passed from ThinkOrSwim override any
fetched field when provided.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
import requests

from utils import safe_div

_MASSIVE_BASE = "https://api.massive.com"
_MASSIVE_HEADERS = {"User-Agent": "microcapanalyzer/1.0"}


# ─── Data containers ──────────────────────────────────────────────────────────

@dataclass
class VolumeMetrics:
    ticker: str
    current_price: float | None = None
    volume_today: int | None = None
    volume_20d_mean: float | None = None
    volume_20d_std: float | None = None
    volume_zscore: float | None = None
    pct_change_1d: float | None = None
    pct_change_5d: float | None = None
    pct_change_20d: float | None = None
    market_cap: float | None = None
    avg_daily_dollar_volume: float | None = None
    week52_high: float | None = None
    week52_low: float | None = None
    pct_from_52w_high: float | None = None
    pct_from_52w_low: float | None = None
    float_shares: float | None = None
    shares_outstanding: float | None = None
    institutional_pct: float | None = None
    insider_pct: float | None = None
    short_pct_float: float | None = None
    short_ratio: float | None = None
    sector: str | None = None
    industry: str | None = None
    ohlcv_60d: pd.DataFrame = field(default_factory=pd.DataFrame)
    volume_pattern: str = "UNKNOWN"
    error: str | None = None


@dataclass
class PeerVolumeData:
    ticker: str
    volume_today: int | None
    volume_20d_mean: float | None
    zscore: float | None
    error: str | None = None


# ─── Massive API helpers ───────────────────────────────────────────────────────

def _massive_get(path: str, params: dict) -> dict:
    api_key = os.getenv("MASSIVE_API_KEY", "")
    params["apiKey"] = api_key
    r = requests.get(
        f"{_MASSIVE_BASE}{path}",
        params=params,
        headers=_MASSIVE_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _fetch_ohlcv(ticker: str) -> pd.DataFrame:
    end = date.today()
    start = end - timedelta(days=365)
    data = _massive_get(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}",
        {"adjusted": "true", "sort": "asc", "limit": 400},
    )
    results = data.get("results", [])
    if not results:
        return pd.DataFrame()
    rows = [
        {
            "Date": pd.to_datetime(r["t"], unit="ms"),
            "Open":   r.get("o"),
            "High":   r.get("h"),
            "Low":    r.get("l"),
            "Close":  r.get("c"),
            "Volume": r.get("v"),
        }
        for r in results
    ]
    return (
        pd.DataFrame(rows)
        .dropna(subset=["Close", "Volume"])
        .assign(Date=lambda d: pd.to_datetime(d["Date"]))
        .set_index("Date")
    )


def _fetch_ticker_details(ticker: str) -> dict:
    data = _massive_get(f"/v3/reference/tickers/{ticker}", {})
    return data.get("results", {})


def _fetch_short_interest(ticker: str) -> dict:
    data = _massive_get(
        "/stocks/v1/short-interest",
        {"ticker": ticker, "sort": "settlement_date.desc", "limit": 1},
    )
    results = data.get("results", [])
    return results[0] if results else {}


# ─── Volume pattern classification ────────────────────────────────────────────

def _linear_slope(series: pd.Series) -> float:
    n = len(series)
    if n < 2:
        return 0.0
    x = list(range(n))
    x_mean = sum(x) / n
    y_mean = series.mean()
    num = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, series))
    den = sum((xi - x_mean) ** 2 for xi in x)
    return num / den if den != 0 else 0.0


def _classify_volume_pattern(hist: pd.DataFrame) -> str:
    if len(hist) < 10:
        return "UNKNOWN"
    recent = hist.tail(20).copy()
    vol = recent["Volume"].astype(float)
    close = recent["Close"].astype(float)
    vol_trend = _linear_slope(vol)
    price_trend = _linear_slope(close)
    last_price = close.iloc[-1]
    last_vol = vol.iloc[-1]
    mean_vol = vol.mean()
    spike_ratio = last_vol / mean_vol if mean_vol > 0 else 1.0
    if spike_ratio > 3 and abs(vol_trend) < 0.05 * mean_vol:
        return "VOLATILITY_EVENT"
    if last_price >= close.max() * 0.98 and vol_trend > 0 and price_trend > 0:
        return "BREAKOUT_CONFIRMATION"
    if vol_trend > 0 and -0.5 <= price_trend / (last_price * 0.01 + 1e-9) <= 2.0:
        return "SILENT_ACCUMULATION"
    if vol_trend > 0 and price_trend <= 0:
        return "DISTRIBUTION"
    return "UNKNOWN"


# ─── Main fetch ───────────────────────────────────────────────────────────────

def fetch_market_data(ticker: str) -> VolumeMetrics:
    m = VolumeMetrics(ticker=ticker)
    try:
        df = _fetch_ohlcv(ticker)
        if df.empty:
            m.error = "No OHLCV data returned"
            return m

        hist_60d = df.tail(60).copy()
        m.ohlcv_60d = hist_60d

        vol = hist_60d["Volume"].astype(float)
        close = hist_60d["Close"].astype(float)

        rolling_mean = vol.rolling(20).mean()
        rolling_std = vol.rolling(20).std()

        m.volume_today = int(vol.iloc[-1])
        m.volume_20d_mean = float(rolling_mean.iloc[-1]) if not rolling_mean.empty else None
        m.volume_20d_std  = float(rolling_std.iloc[-1])  if not rolling_std.empty  else None
        m.volume_zscore   = safe_div(
            (m.volume_today - m.volume_20d_mean) if m.volume_20d_mean else None,
            m.volume_20d_std,
        )
        m.current_price = float(close.iloc[-1])

        if len(close) >= 2:
            m.pct_change_1d = float((close.iloc[-1] / close.iloc[-2] - 1) * 100)
        if len(close) >= 6:
            m.pct_change_5d = float((close.iloc[-1] / close.iloc[-6] - 1) * 100)
        if len(close) >= 20:
            m.pct_change_20d = float((close.iloc[-1] / close.iloc[-20] - 1) * 100)

        all_high = df["High"].astype(float)
        all_low  = df["Low"].astype(float)
        m.week52_high = float(all_high.max())
        m.week52_low  = float(all_low.min())
        if m.current_price and m.week52_high:
            m.pct_from_52w_high = (m.current_price / m.week52_high - 1) * 100
        if m.current_price and m.week52_low:
            m.pct_from_52w_low  = (m.current_price / m.week52_low  - 1) * 100

        dollar_vol = (hist_60d["Close"] * hist_60d["Volume"]).rolling(20).mean()
        m.avg_daily_dollar_volume = float(dollar_vol.iloc[-1]) if not dollar_vol.empty else None

        m.volume_pattern = _classify_volume_pattern(hist_60d)

        # Ticker details (best-effort — missing fields stay None)
        try:
            details = _fetch_ticker_details(ticker)
            m.market_cap         = details.get("market_cap")
            m.shares_outstanding = details.get("weighted_shares_outstanding")
            m.industry           = details.get("sic_description")
            m.sector             = details.get("sic_description")
        except Exception:
            pass

        # Short interest (best-effort)
        try:
            si = _fetch_short_interest(ticker)
            if si:
                short_shares = si.get("short_interest")
                m.short_ratio = si.get("days_to_cover")
                if short_shares and m.shares_outstanding:
                    m.short_pct_float = (short_shares / m.shares_outstanding) * 100
        except Exception:
            pass

    except Exception as exc:
        m.error = str(exc)

    return m


# ─── Utilities ────────────────────────────────────────────────────────────────

def position_size_reality_check(avg_dollar_volume: float | None) -> dict:
    intended = float(os.getenv("INTENDED_POSITION_SIZE", "25000"))

    if avg_dollar_volume is None or avg_dollar_volume <= 0:
        return {
            "intended": intended,
            "pct_of_adv": None,
            "rating": "UNKNOWN",
            "note": "ADV unavailable — liquidity cannot be assessed",
        }

    pct = (intended / avg_dollar_volume) * 100

    if pct < 1.0:
        rating, note = "EXECUTABLE",  f"${intended:,.0f} = {pct:.2f}% of 20D ADV — no market impact concern"
    elif pct < 3.0:
        rating, note = "CONSTRAINED", f"${intended:,.0f} = {pct:.2f}% of 20D ADV — expect slippage; use limit orders"
    else:
        rating, note = "ILLIQUID",    f"${intended:,.0f} = {pct:.2f}% of 20D ADV — cannot build position cleanly; reduce size"

    return {"intended": intended, "pct_of_adv": pct, "rating": rating, "note": note}


def is_sector_rotation(peers: list[PeerVolumeData], threshold: float = 1.5) -> bool:
    elevated = [p for p in peers if p.zscore is not None and p.zscore >= threshold]
    return len(elevated) >= 2
