"""Prices, volatility indices and the scheduled-event calendar.

Everything is cached under cache/. Event dates are the dates releases actually came out
(ALFRED vintages, Fed meeting pages), which were all published well before each event, so
using them in a walk-forward is not lookahead.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import subprocess

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
CACHE.mkdir(exist_ok=True)
UA = {"User-Agent": "curl/8.7.1"}
BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

START = "2004-01-01"
VOL_INDICES = ["^VIX", "^VIX9D", "^VIX3M", "^VVIX"]

# FRED/ALFRED release ids -> event name
RELEASES = {
    "cpi": 10,
    "nfp": 50,
    "gdp": 53,
    "pce": 54,
    "ppi": 46,
    "retail": 9,
}
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def _fresh(path: Path, hours: float) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < hours * 3600


def _get(url: str, headers=UA, tries: int = 3) -> str:
    """GET through curl: the St. Louis Fed's edge stalls Python's TLS client but serves curl."""
    for i in range(tries):
        try:
            out = subprocess.run(
                ["curl", "-sSfL", "--http1.1", "--max-time", "60", "-A", headers["User-Agent"], url],
                capture_output=True, check=True, timeout=90,
            )
            return out.stdout.decode("utf-8", "replace")
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(url)


# ---------------------------------------------------------------- prices

def bars(ticker: str, start: str = START, max_age_h: float = 12) -> pd.DataFrame:
    """Daily OHLC, split- and dividend-adjusted consistently across all four prices."""
    path = CACHE / f"bars_{re.sub(r'[^A-Za-z0-9]', '_', ticker)}.pkl"
    if _fresh(path, max_age_h):
        return pd.read_pickle(path)
    import yfinance as yf

    df = yf.download(ticker, start=start, auto_adjust=True, progress=False, threads=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")]
    if len(df):
        df.to_pickle(path)
    elif path.exists():
        return pd.read_pickle(path)
    return df


def vol_indices(max_age_h: float = 12) -> pd.DataFrame:
    """Closing levels of VIX, VIX9D, VIX3M, VVIX, plus VIX1D from CBOE (2022 onward)."""
    cols = {}
    for t in VOL_INDICES:
        b = bars(t, max_age_h=max_age_h)
        if len(b):
            cols[t.strip("^")] = b["close"]
    path = CACHE / "cboe_VIX1D.csv"
    if not _fresh(path, max_age_h):
        try:
            path.write_text(_get("https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX1D_History.csv", BROWSER))
        except Exception:
            pass
    if path.exists():
        v = pd.read_csv(path)
        v.index = pd.to_datetime(v["DATE"], format="%m/%d/%Y")
        cols["VIX1D"] = v["CLOSE"]
    return pd.DataFrame(cols).sort_index()


# ---------------------------------------------------------------- trading calendar

_NYSE = None


def nyse():
    global _NYSE
    if _NYSE is None:
        _NYSE = xcals.get_calendar("XNYS", start="2000-01-01", end=f"{dt.date.today().year + 2}-12-31")
    return _NYSE


def sessions(start, end) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(nyse().sessions_in_range(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize())).tz_localize(None)


def next_sessions(after, n: int) -> pd.DatetimeIndex:
    s = sessions(pd.Timestamp(after) + pd.Timedelta(days=1), pd.Timestamp(after) + pd.Timedelta(days=int(n * 1.6) + 10))
    return s[:n]


def early_closes() -> set:
    return {pd.Timestamp(d).tz_localize(None).normalize() for d in nyse().early_closes}


# ---------------------------------------------------------------- event calendar

def _alfred_dates(rid: int) -> list[pd.Timestamp]:
    txt = _get(f"https://alfred.stlouisfed.org/release/downloaddates?rid={rid}&ff=txt", UA)
    return sorted({pd.Timestamp(d) for d in re.findall(r"^(\d{4}-\d{2}-\d{2})\s*$", txt, flags=re.M)})


def _fred_upcoming(rid: int, years) -> list[pd.Timestamp]:
    out = set()
    for y in years:
        try:
            html = _get(f"https://fred.stlouisfed.org/releases/calendar?rid={rid}&y={y}", UA)
        except Exception:
            continue
        for m in re.findall(rf"((?:{MONTHS}) \d{{1,2}}, \d{{4}})", html):
            out.add(pd.Timestamp(dt.datetime.strptime(m, "%B %d, %Y")))
    return sorted(out)


def _fomc_days() -> list[pd.Timestamp]:
    """Scheduled FOMC decision days (last day of each scheduled meeting), 2004 onward.

    Unscheduled calls (March 2020 and the like) are left out on purpose: nobody knew about
    them in advance, so a forecaster can't use them.
    """
    days = set()
    month_n = {m: i + 1 for i, m in enumerate(MONTHS.split("|"))}
    month_n.update({m[:3]: i + 1 for i, m in enumerate(MONTHS.split("|"))})

    def add(year: int, month_txt: str, day_txt: str):
        months = [m.strip() for m in re.split(r"[/-]", month_txt) if m.strip()]
        d = re.findall(r"\d+", day_txt)
        if not months or not d:
            return
        last_month = months[-1]
        mn = month_n.get(last_month) or month_n.get(last_month[:3])
        if mn:
            days.add(pd.Timestamp(year=year, month=mn, day=int(d[-1])))

    cur = _get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", BROWSER)
    for block in re.split(r'<h4><a id="\d+">', cur)[1:]:
        y = re.match(r"(\d{4}) FOMC Meetings", block)
        if not y:
            continue
        year = int(y.group(1))
        for mo, dd in re.findall(r'fomc-meeting__month[^>]*><strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<', block, flags=re.S):
            if "notation" in dd.lower() or "unscheduled" in dd.lower():
                continue
            add(year, mo, dd)
    first_current = min(d.year for d in days) if days else 2021
    for year in range(2004, first_current):
        try:
            html = _get(f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm", BROWSER)
        except Exception:
            continue
        for mo, dd in re.findall(rf"<h5[^>]*>\s*((?:{MONTHS})(?:/(?:{MONTHS}))?)\s+([\d\-]+)\s+Meeting", html):
            add(year, mo, dd)
    return sorted(days)


def _third_friday(y: int, m: int) -> pd.Timestamp:
    d = pd.Timestamp(year=y, month=m, day=15)
    return d + pd.Timedelta(days=(4 - d.weekday()) % 7)


def _opex_days(start: int, end: int) -> tuple[list, list]:
    """Monthly options expiry (3rd Friday, or the session before if it's a holiday); quad witching in Mar/Jun/Sep/Dec."""
    sess = set(sessions(f"{start}-01-01", f"{end}-12-31"))
    monthly, quad = [], []
    for y in range(start, end + 1):
        for m in range(1, 13):
            d = _third_friday(y, m)
            while d not in sess and d.year == y and d.month == m and d.day > 10:
                d -= pd.Timedelta(days=1)
            monthly.append(d)
            if m in (3, 6, 9, 12):
                quad.append(d)
    return monthly, quad


def events(max_age_h: float = 24 * 7) -> pd.DataFrame:
    """Long table: date, event. Scheduled US events from 2004 through next year."""
    path = CACHE / "events.json"
    if _fresh(path, max_age_h):
        rows = json.loads(path.read_text())
    else:
        today = dt.date.today()
        rows = []
        for name, rid in RELEASES.items():
            dates = set(_alfred_dates(rid)) | set(_fred_upcoming(rid, [today.year, today.year + 1]))
            rows += [(d.strftime("%Y-%m-%d"), name) for d in dates if d.year >= 2004]
        rows += [(d.strftime("%Y-%m-%d"), "fomc") for d in _fomc_days()]
        monthly, quad = _opex_days(2004, today.year + 1)
        rows += [(d.strftime("%Y-%m-%d"), "opex") for d in monthly]
        rows += [(d.strftime("%Y-%m-%d"), "quad") for d in quad]
        for y in range(2004, today.year + 2, 2):  # US federal elections: first Tuesday after the first Monday in November
            nov1 = pd.Timestamp(year=y, month=11, day=1)
            first_mon = nov1 + pd.Timedelta(days=(0 - nov1.weekday()) % 7)
            rows.append(((first_mon + pd.Timedelta(days=1)).strftime("%Y-%m-%d"), "election"))
        rows = sorted(set(map(tuple, rows)))
        path.write_text(json.dumps(rows))
    df = pd.DataFrame(rows, columns=["date", "event"])
    df["date"] = pd.to_datetime(df["date"])
    return df


EVENT_LABELS = {
    "cpi": "CPI", "nfp": "Jobs report", "gdp": "GDP", "pce": "PCE inflation", "ppi": "PPI",
    "retail": "Retail sales", "fomc": "Fed decision", "opex": "Options expiry", "quad": "Quad witching",
    "election": "US election",
}
EVENT_TIMES = {
    "cpi": "8:30", "nfp": "8:30", "gdp": "8:30", "pce": "8:30", "ppi": "8:30", "retail": "8:30",
    "fomc": "14:00", "opex": "close", "quad": "close", "election": "polls close evening",
}


def event_matrix(index: pd.DatetimeIndex) -> pd.DataFrame:
    ev = events()
    m = pd.DataFrame(0.0, index=index, columns=list(EVENT_LABELS))
    for name, g in ev.groupby("event"):
        hit = index.isin(pd.DatetimeIndex(g["date"]))
        m.loc[hit, name] = 1.0
    return m
