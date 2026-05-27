"""Market data: direct Yahoo Finance chart API + position size + regime warning."""

from __future__ import annotations

import os
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd
import requests

from utils import fmt_millions, fmt_pct, fmt_shares, safe_div

warnings.filterwarnings("ignore", category=FutureWarning)

_YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


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


# ─── Direct chart API ─────────────────────────────────────────────────────────

def _fetch_chart(ticker: str, range_: str = "90d") -> dict:
    url = _CHART_URL.format(ticker=ticker)
    r = requests.get(
        url,
        params={"range": range_, "interval": "1d", "includePrePost": "false"},
        headers=_YF_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    results = data.get("chart", {}).get("result")
    if not results:
        raise ValueError(f"No chart data returned for {ticker}")
    return results[0]


def _chart_to_ohlcv(result: dict) -> pd.DataFrame:
    timestamps = result.get("timestamp", [])
    quotes = result.get("indicators", {}).get("quote", [{}])[0]

    closes = quotes.get("close", [])
    volumes = quotes.get("volume", [])

    if not timestamps or not closes:
        return pd.DataFrame()

    rows = []
    for i, ts in enumerate(timestamps):
        rows.append({
            "Date": datetime.utcfromtimestamp(ts),
            "Open": quotes.get("open", [None])[i] if i < len(quotes.get("open", [])) else None,
            "High": quotes.get("high", [None])[i] if i < len(quotes.get("high", [])) else None,
            "Low": quotes.get("low", [None])[i] if i < len(quotes.get("low", [])) else None,
            "Close": closes[i] if i < len(closes) else None,
            "Volume": volumes[i] if i < len(volumes) else None,
        })

    df = pd.DataFrame(rows).dropna(subset=["Close", "Volume"])
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date")
    return df


# ─── UPDATE 1: Macro regime warning ───────────────────────────────────────────

def get_regime_warning() -> str | None:
    """Fetch VIX and SPY; return warning string if macro conditions are adverse."""
    try:
        vix_result = _fetch_chart("%5EVIX", range_="5d")
        vix_df = _chart_to_ohlcv(vix_result)
        vix_last = float(vix_df["Close"].iloc[-1]) if not vix_df.empty else None

        spy_result = _fetch_chart("SPY", range_="10d")
        spy_df = _chart_to_ohlcv(spy_result)
        spy_5d_change = None
        if len(spy_df) >= 6:
            spy_5d_change = float((spy_df["Close"].iloc[-1] / spy_df["Close"].iloc[-6] - 1) * 100)

        flags = []
        if vix_last is not None and vix_last > 28:
            flags.append(f"VIX at {vix_last:.1f} — elevated volatility regime (threshold: 28). Reduce size, widen stops.")
        if spy_5d_change is not None and spy_5d_change < -4.0:
            flags.append(f"SPY 5-day return: {spy_5d_change:.1f}% — broad market risk-off (threshold: -4%). Volume spikes may be forced selling.")

        return "\n".join(flags) if flags else None
    except Exception:
        return None


# ─── UPDATE 3: Position size reality check ────────────────────────────────────

def position_size_reality_check(avg_dollar_volume: float | None) -> dict:
    """Compare intended position size to avg daily dollar volume."""
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
        rating = "EXECUTABLE"
        note = f"${intended:,.0f} = {pct:.2f}% of 20D ADV — no market impact concern"
    elif pct < 3.0:
        rating = "CONSTRAINED"
        note = f"${intended:,.0f} = {pct:.2f}% of 20D ADV — expect slippage; use limit orders"
    else:
        rating = "ILLIQUID"
        note = f"${intended:,.0f} = {pct:.2f}% of 20D ADV — cannot build position cleanly; reduce size"

    return {"intended": intended, "pct_of_adv": pct, "rating": rating, "note": note}


# ─── Main market data fetch ───────────────────────────────────────────────────

def fetch_market_data(ticker: str) -> VolumeMetrics:
    m = VolumeMetrics(ticker=ticker)
    try:
        result = _fetch_chart(ticker, range_="90d")
        meta = result.get("meta", {})

        m.current_price = meta.get("regularMarketPrice")
        m.week52_high = meta.get("fiftyTwoWeekHigh")
        m.week52_low = meta.get("fiftyTwoWeekLow")

        df = _chart_to_ohlcv(result)
        if df.empty:
            m.error = "No OHLCV data returned"
            return m

        hist_60d = df.tail(60).copy()
        m.ohlcv_60d = hist_60d

        vol_series = hist_60d["Volume"].astype(float)
        close = hist_60d["Close"].astype(float)

        rolling_mean = vol_series.rolling(20).mean()
        rolling_std = vol_series.rolling(20).std()

        m.volume_today = int(vol_series.iloc[-1]) if not vol_series.empty else None
        m.volume_20d_mean = float(rolling_mean.iloc[-1]) if not rolling_mean.empty else None
        m.volume_20d_std = float(rolling_std.iloc[-1]) if not rolling_std.empty else None
        m.volume_zscore = safe_div(
            (m.volume_today - m.volume_20d_mean) if (m.volume_today and m.volume_20d_mean) else None,
            m.volume_20d_std,
        )

        m.current_price = float(close.iloc[-1]) if not close.empty else m.current_price

        if len(close) >= 2:
            m.pct_change_1d = float((close.iloc[-1] / close.iloc[-2] - 1) * 100)
        if len(close) >= 6:
            m.pct_change_5d = float((close.iloc[-1] / close.iloc[-6] - 1) * 100)
        if len(close) >= 20:
            m.pct_change_20d = float((close.iloc[-1] / close.iloc[-20] - 1) * 100)

        if m.current_price and m.week52_high:
            m.pct_from_52w_high = (m.current_price / m.week52_high - 1) * 100
        if m.current_price and m.week52_low:
            m.pct_from_52w_low = (m.current_price / m.week52_low - 1) * 100

        dollar_vol = (hist_60d["Close"] * hist_60d["Volume"]).rolling(20).mean()
        m.avg_daily_dollar_volume = float(dollar_vol.iloc[-1]) if not dollar_vol.empty else None

        m.volume_pattern = _classify_volume_pattern(hist_60d)
        _enrich_fundamentals(m, ticker)

    except Exception as exc:
        m.error = str(exc)

    return m


def _enrich_fundamentals(m: VolumeMetrics, ticker: str) -> None:
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).fast_info
        m.market_cap = getattr(info, "market_cap", None)
        m.shares_outstanding = getattr(info, "shares", None)
    except Exception:
        pass

    if not m.market_cap and m.current_price and m.shares_outstanding:
        m.market_cap = m.current_price * m.shares_outstanding


def _classify_volume_pattern(hist: pd.DataFrame) -> str:
    if len(hist) < 10:
        return "UNKNOWN"

    recent = hist.tail(20).copy()
    vol = recent["Volume"].astype(float)
    close = recent["Close"].astype(float)

    vol_trend = _linear_slope(vol)
    price_trend = _linear_slope(close)

    price_range_20d_high = close.max()
    last_price = close.iloc[-1]
    last_vol = vol.iloc[-1]
    mean_vol = vol.mean()
    spike_ratio = last_vol / mean_vol if mean_vol > 0 else 1.0

    if spike_ratio > 3 and abs(vol_trend) < 0.05 * mean_vol:
        return "VOLATILITY_EVENT"

    if last_price >= price_range_20d_high * 0.98 and vol_trend > 0 and price_trend > 0:
        return "BREAKOUT_CONFIRMATION"

    if vol_trend > 0 and -0.5 <= price_trend / (last_price * 0.01 + 1e-9) <= 2.0:
        return "SILENT_ACCUMULATION"

    if vol_trend > 0 and price_trend <= 0:
        return "DISTRIBUTION"

    return "UNKNOWN"


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


# ─── Peer comparison ──────────────────────────────────────────────────────────

_SECTOR_PEERS: dict[str, list[str]] = {
    "Technology": ["MSFT", "AAPL", "GOOGL", "META", "NVDA"],
    "Healthcare": ["JNJ", "UNH", "PFE", "ABBV", "MRK"],
    "Financials": ["JPM", "BAC", "WFC", "GS", "MS"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG"],
    "Consumer Discretionary": ["AMZN", "TSLA", "HD", "MCD", "NKE"],
    "Industrials": ["CAT", "HON", "UPS", "LMT", "RTX"],
    "Materials": ["LIN", "APD", "ECL", "DD", "NEM"],
    "Real Estate": ["AMT", "PLD", "CCI", "EQIX", "SPG"],
    "Utilities": ["NEE", "DUK", "SO", "D", "AEP"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS", "T"],
    "Consumer Staples": ["PG", "KO", "PEP", "WMT", "COST"],
}


def fetch_peer_volume(sector: str | None, target_ticker: str) -> list[PeerVolumeData]:
    if not sector or sector not in _SECTOR_PEERS:
        return []

    peers = [p for p in _SECTOR_PEERS.get(sector, []) if p != target_ticker][:5]
    results = []
    for p in peers:
        try:
            result = _fetch_chart(p, range_="30d")
            df = _chart_to_ohlcv(result)
            if df.empty:
                results.append(PeerVolumeData(ticker=p, volume_today=None, volume_20d_mean=None, zscore=None))
                continue
            vol = df["Volume"].astype(float)
            today_vol = int(vol.iloc[-1])
            mean_20d = float(vol.tail(20).mean())
            std_20d = float(vol.tail(20).std())
            zscore = safe_div(today_vol - mean_20d, std_20d)
            results.append(PeerVolumeData(ticker=p, volume_today=today_vol, volume_20d_mean=mean_20d, zscore=zscore))
            time.sleep(0.1)
        except Exception as exc:
            results.append(PeerVolumeData(ticker=p, volume_today=None, volume_20d_mean=None, zscore=None, error=str(exc)))
    return results


def is_sector_rotation(peers: list[PeerVolumeData], threshold: float = 1.5) -> bool:
    elevated = [p for p in peers if p.zscore is not None and p.zscore >= threshold]
    return len(elevated) >= 2
