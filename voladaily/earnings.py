"""Per-stock earnings: exact past dates, the moves that followed, and the options-implied move.

Past dates come from SEC EDGAR: every 8-K with Item 2.02 ("Results of Operations"), with
its acceptance time. Filed after the 16:00 close means the reaction is the next session;
filed before 09:30 (or during the day) means the reaction is that session. These are the
dates as filed, so no revised-date leakage.

SEC asks for a contact address in the User-Agent. It is read from SEC_USER_AGENT and only
ever sent to sec.gov.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import data

SEC_UA = os.environ.get("SEC_USER_AGENT", "voladaily-research (set SEC_USER_AGENT)")
_last = [0.0]


def _sec(url: str, path: Path, max_age_h: float) -> dict:
    if data._fresh(path, max_age_h):
        return json.loads(path.read_text())
    wait = 0.25 - (time.time() - _last[0])  # stay well under SEC's 10 requests/second
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    try:
        txt = data._get(url, {"User-Agent": SEC_UA})
    except Exception:
        if path.exists():  # SEC unreachable or no contact address set: fall back to the last copy
            return json.loads(path.read_text())
        raise
    path.write_text(txt)
    return json.loads(txt)


def cik(ticker: str) -> int:
    m = _sec("https://www.sec.gov/files/company_tickers.json", data.CACHE / "sec_tickers.json", 24 * 30)
    t = ticker.upper().replace(".", "-")
    for v in m.values():
        if v["ticker"].upper() == t:
            return int(v["cik_str"])
    raise KeyError(f"{ticker}: no SEC CIK (ETFs and foreign listings have no earnings 8-Ks)")


# Companies that moved to a new SEC registrant (holding-company reorganisations, mergers):
# their older earnings releases live under the predecessor's CIK.
PREDECESSORS = {
    "XOM": [34088],     # Exxon Mobil Corp, before the 2026 holding-company reorganisation
    "DIS": [1001039],   # TWDC Enterprises 18 (old Walt Disney Co), before the 2019 Fox deal
    "GOOGL": [1288776], "GOOG": [1288776],  # Google Inc, before Alphabet (2015)
    "META": [],
}


def _filings_all(ticker: str) -> pd.DataFrame:
    frames = [_filings(cik(ticker))] + [_filings(c) for c in PREDECESSORS.get(ticker.upper(), [])]
    return pd.concat(frames, ignore_index=True)


def _filings(c: int) -> pd.DataFrame:
    base = _sec(f"https://data.sec.gov/submissions/CIK{c:010d}.json", data.CACHE / f"sec_sub_{c}.json", 24)
    frames = [pd.DataFrame(base["filings"]["recent"])]
    for f in base["filings"].get("files", []):
        if f.get("filingTo", "") >= "2005-01-01":
            frames.append(pd.DataFrame(_sec(f"https://data.sec.gov/submissions/{f['name']}", data.CACHE / f"sec_{f['name']}", 24 * 30)))
    return pd.concat(frames, ignore_index=True)


def history(ticker: str) -> pd.DataFrame:
    """One row per earnings release: accepted (US/Eastern), timing, reaction session."""
    allf = _filings_all(ticker)
    f = allf[(allf["form"] == "8-K") & allf["items"].fillna("").str.contains("2.02")].copy()
    f["accepted"] = pd.to_datetime(f["acceptanceDateTime"], utc=True).dt.tz_convert("America/New_York")
    f = f.sort_values("accepted")
    # Item 2.02 also covers other results updates (Tesla files its delivery numbers under it).
    # The earnings release is the last 2.02 filing in the 75 days before each 10-Q/10-K.
    reports = pd.to_datetime(allf.loc[allf["form"].isin(["10-Q", "10-K", "10-K405", "10-KT"]), "filingDate"]).sort_values()
    if len(reports) >= 4:
        pick = set()
        for rd in reports:
            win = f[(f["accepted"].dt.tz_localize(None) <= rd + pd.Timedelta(days=1)) & (f["accepted"].dt.tz_localize(None) >= rd - pd.Timedelta(days=75))]
            if len(win):
                pick.add(win.index[-1])
        last2 = f[f["accepted"].dt.tz_localize(None) > reports.iloc[-1] + pd.Timedelta(days=1)]  # released, report not filed yet
        if len(last2):
            pick.add(last2.index[-1])
        f = f.loc[sorted(pick, key=lambda i: f.loc[i, "accepted"])]
    # a corrected or duplicate release: keep the first within 25 days
    keep, last = [], None
    for ts in f["accepted"]:
        keep.append(last is None or (ts - last).days > 25)
        if keep[-1]:
            last = ts
    f = f[keep]
    sess = data.sessions("2001-01-01", pd.Timestamp.today() + pd.Timedelta(days=10))
    rows = []
    for ts in f["accepted"]:
        day = ts.tz_localize(None).normalize()
        mins = ts.hour * 60 + ts.minute
        timing = "after close" if mins >= 16 * 60 else ("before open" if mins < 9 * 60 + 30 else "during market")
        i = sess.searchsorted(day)
        if i >= len(sess):
            continue
        if sess[i] != day or timing == "after close":  # non-session day or after the close -> next session
            i = i + 1 if sess[i] == day else i
        if i >= len(sess):
            continue
        rows.append({"accepted": ts.strftime("%Y-%m-%d %H:%M"), "timing": timing, "session": sess[i]})
    return pd.DataFrame(rows)


def moves(ticker: str) -> pd.DataFrame:
    """The reaction move (prior close to reaction close) for each release, and how it compared
    with a normal day for that stock at the time."""
    h = history(ticker)
    b = data.bars(ticker)
    lr = np.log(b["close"]).diff()
    normal = lr.abs().rolling(60).median().shift(1)  # typical non-event day, known beforehand
    h = h[h["session"].isin(b.index)].copy()
    h["move"] = [float(np.exp(lr.loc[s]) - 1) for s in h["session"]]
    h["abs_move"] = h["move"].abs()
    h["normal_day"] = [float(normal.loc[s]) for s in h["session"]]
    h["x_normal"] = h["abs_move"] / h["normal_day"]
    gap = np.log(b["open"] / b["close"].shift(1))
    h["gap"] = [float(np.exp(gap.loc[s]) - 1) for s in h["session"]]
    return h.reset_index(drop=True)


def upcoming(ticker: str) -> dict | None:
    """Next earnings date from Yahoo (it moves; flagged as estimated unless confirmed)."""
    import yfinance as yf

    try:
        cal = yf.Ticker(ticker).calendar or {}
    except Exception:
        cal = {}
    dates = cal.get("Earnings Date") or []
    if not dates:
        return None
    d = pd.Timestamp(dates[0])
    timing = None
    try:
        ed = yf.Ticker(ticker).get_earnings_dates(limit=6)
        if ed is not None and len(ed):
            idx = ed.index.tz_convert("America/New_York")
            fut = [t for t in idx if t.tz_localize(None).normalize() >= pd.Timestamp.today().normalize() - pd.Timedelta(days=1)]
            if fut:
                t = min(fut)
                d = t.tz_localize(None).normalize()
                m = t.hour * 60 + t.minute
                timing = "after close" if m >= 16 * 60 else ("before open" if m < 9 * 60 + 30 and m > 0 else None)
    except Exception:
        pass
    if timing is None:  # fall back to the company's habit
        try:
            timing = history(ticker)["timing"].iloc[-8:].mode().iloc[0]
        except Exception:
            timing = "after close"
    sess = data.sessions(d - pd.Timedelta(days=5), d + pd.Timedelta(days=10))
    react = sess[sess > d][0] if timing == "after close" else sess[sess >= d][0]
    return {"date": d.strftime("%Y-%m-%d"), "timing": timing, "session": react, "estimated": len(dates) > 1}


def implied_move(ticker: str, session: pd.Timestamp, normal_daily_sigma: float) -> dict | None:
    """Options-implied earnings move from the first expiry on or after the reaction session.

    The ATM straddle prices the whole stretch to expiry. Normal days' variance (from the
    Voladaily forecast without the earnings bump) is subtracted so what's left is the
    earnings day itself. E|move| = sqrt(2/pi) * sigma_event.
    """
    import yfinance as yf

    tk = yf.Ticker(ticker)
    try:
        exps = [pd.Timestamp(e) for e in tk.options]
    except Exception:
        return None
    today = pd.Timestamp.today().normalize()
    after = [e for e in exps if e >= session]
    before = [e for e in exps if today < e < session]
    if not after:
        return None
    spot = float(data.bars(ticker, max_age_h=1)["close"].iloc[-1])

    def straddle_var(exp):
        """Total variance to expiry implied by the ATM straddle: straddle ~ sqrt(2/pi) * sigma_total * S."""
        ch = tk.option_chain(exp.strftime("%Y-%m-%d"))
        k = ch.calls.iloc[(ch.calls["strike"] - spot).abs().argsort()[:1]]["strike"].iloc[0]

        def mid(df):
            r = df[df["strike"] == k]
            if not len(r):
                return 0.0
            r = r.iloc[0]
            bid, ask, last = float(r.get("bid", 0) or 0), float(r.get("ask", 0) or 0), float(r.get("lastPrice", 0) or 0)
            return (bid + ask) / 2 if bid > 0 and ask > 0 else last

        st = mid(ch.calls) + mid(ch.puts)
        n = len(data.sessions(today + pd.Timedelta(days=1), exp))
        return st, float(k), (st / spot / np.sqrt(2 / np.pi)) ** 2, n

    st2, k2, v2, n2 = straddle_var(after[0])
    if st2 <= 0:
        return None
    method = "model normal days"
    normal_var = normal_daily_sigma**2
    if before:
        st1, _, v1, n1 = straddle_var(before[-1])
        if st1 > 0 and n1 > 0:
            normal_var = v1 / n1  # the market's own price for a normal day, from the pre-earnings expiry
            method = f"expiry spread ({before[-1]:%b %d} vs {after[0]:%b %d})"
    event_var = v2 - max(n2 - 1, 0) * normal_var
    event_sigma = float(np.sqrt(max(event_var, 0)))
    return {"expiry": after[0].strftime("%Y-%m-%d"), "strike": k2, "spot": spot, "straddle": round(st2, 2),
            "straddle_pct": st2 / spot, "sessions_to_expiry": n2, "implied_move": float(np.sqrt(2 / np.pi) * event_sigma),
            "implied_sigma": event_sigma, "normal_day_implied_sigma": float(np.sqrt(normal_var)), "method": method}
