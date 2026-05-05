from __future__ import annotations

import datetime as dt

import pandas as pd
import yfinance as yf


REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    return df


def ensure_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    df = _flatten_columns(df).dropna(how="all").copy()
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing OHLCV columns: {missing}")
    return df


def _parse_asof(asof_date: str | None) -> dt.date | None:
    if not asof_date:
        return None
    try:
        return dt.date.fromisoformat(asof_date)
    except ValueError:
        return None


def _period_to_start(asof: dt.date, period: str) -> dt.date:
    p = period.strip().lower()
    if p.endswith("mo"):
        months = int(p[:-2])
        return asof - dt.timedelta(days=months * 31)
    if p.endswith("y"):
        years = int(p[:-1])
        return asof - dt.timedelta(days=years * 366)
    if p.endswith("d"):
        days = int(p[:-1])
        return asof - dt.timedelta(days=days + 10)
    return asof - dt.timedelta(days=366)


def download_ohlcv(
    ticker: str,
    period: str,
    interval: str = "1d",
    asof_date: str | None = None,
) -> pd.DataFrame:
    asof = _parse_asof(asof_date)
    if asof is None:
        raw = yf.download(
            ticker,
            period=period,
            interval=interval,
            auto_adjust=False,
            progress=False,
        )
        return ensure_ohlcv(raw).dropna().copy()

    start = _period_to_start(asof, period)
    end = asof + dt.timedelta(days=1)
    raw = yf.download(
        ticker,
        start=start.isoformat(),
        end=end.isoformat(),
        interval=interval,
        auto_adjust=False,
        progress=False,
    )
    df = ensure_ohlcv(raw).dropna().copy()
    return df
