"""Volatility forecast: HAR on log realized variance, plus implied vol and scheduled events.

A forecast for session t uses only what was known before t's open: bars through t-1's
close, vol indices through t-1's close, and the event calendar (published well ahead).

Realized variance per day = overnight gap squared + Garman-Klass intraday range variance.
That is a far less noisy measure than the squared close-to-close return, so the model learns
faster. Close-to-close returns are what people experience, so the forecast is then
calibrated to them: a scale factor k (realized variance -> close-to-close variance) and a
Student-t shape, both fitted walk-forward.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

from . import data

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*inplace.*")

EVENTS = ["cpi", "nfp", "fomc", "gdp", "pce", "ppi", "retail", "opex", "quad", "election"]
FLOOR = 1e-8


def realized_variance(b: pd.DataFrame) -> pd.Series:
    o, h, l, c = (np.log(b[x]) for x in ("open", "high", "low", "close"))
    overnight = (o - c.shift(1)) ** 2
    gk = 0.5 * (h - l) ** 2 - (2 * np.log(2) - 1) * (c - o) ** 2
    rv = overnight.fillna(0) + gk.clip(lower=0)
    return rv.clip(lower=FLOOR)


def features(ticker: str = "SPY", sets=("base", "vix", "events")) -> pd.DataFrame:
    """One row per session. Row t holds the target for t and predictors known before t."""
    b = data.bars(ticker)
    rv = realized_variance(b)
    ret = np.log(b["close"]).diff()
    df = pd.DataFrame(index=b.index)
    df["ret"] = ret
    df["rv"] = rv
    df["y"] = np.log(rv)
    lag = rv.shift(1)
    df["har_d"] = np.log(lag)
    df["har_w"] = np.log(lag.rolling(5).mean())
    df["har_m"] = np.log(lag.rolling(22).mean())
    df["neg"] = np.minimum(ret.shift(1), 0) ** 2 / lag.rolling(22).mean()  # leverage effect, scaled
    gap = df.index.to_series().diff().dt.days.fillna(1)
    df["long_gap"] = (gap > 3).astype(float)  # holiday weekends
    df["monday"] = (df.index.dayofweek == 0).astype(float)
    early = data.early_closes()
    df["half_day"] = df.index.isin(early).astype(float)

    v = data.vol_indices().reindex(df.index).ffill().shift(1)
    df["vix"] = np.log((v["VIX"] / 100) ** 2 / 252)
    df["vix_slope"] = np.log(v["VIX"] / v["VIX3M"]).fillna(0.0)
    df["vix_level"] = v["VIX"]

    ev = data.event_matrix(df.index)
    for e in EVENTS:
        df[f"ev_{e}"] = ev[e]
    df["ev_post_fomc"] = ev["fomc"].shift(1).fillna(0)
    df["ev_post_election"] = ev["election"].shift(1).fillna(0)  # results land overnight
    df["ev_earn"] = 0.0
    df["ev_post_earn"] = 0.0
    sess = earnings_sessions(ticker)
    if sess is not None:
        df["ev_earn"] = df.index.isin(sess).astype(float)
        df["ev_post_earn"] = df["ev_earn"].shift(1).fillna(0)
    return df


def earnings_sessions(ticker: str):
    """Reaction sessions for every past earnings release (None for ETFs and indices)."""
    try:
        from . import earnings

        return pd.DatetimeIndex(earnings.history(ticker)["session"])
    except Exception:
        return None


SPECS = {
    "naive20": None,  # trailing 20-day close-to-close variance
    "vix_only": None,  # VIX^2/252, calibrated
    "har": ["har_d", "har_w", "har_m", "neg", "monday", "long_gap", "half_day"],
    "har_vix": ["har_d", "har_w", "har_m", "neg", "monday", "long_gap", "half_day", "vix", "vix_slope"],
    "full": ["har_d", "har_w", "har_m", "neg", "monday", "long_gap", "half_day", "vix", "vix_slope"]
    + [f"ev_{e}" for e in EVENTS] + ["ev_post_fomc", "ev_post_election", "ev_earn", "ev_post_earn"],
}


@dataclass
class Fit:
    cols: list
    beta: np.ndarray
    resid_sd: float
    k: float  # close-to-close variance / E[rv]
    nu: float  # Student-t degrees of freedom of standardized returns
    as_of: pd.Timestamp = None
    event_mult: dict = field(default_factory=dict)
    earn_k: float = 1.0  # extra variance scale on earnings days (fitted on past earnings days)
    earn_nu: float = 6.0  # fatter tails on earnings days


def _ols(X: np.ndarray, y: np.ndarray):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    return beta, resid


def _t_nu(z: np.ndarray) -> float:
    z = z[np.isfinite(z)]
    if len(z) < 200:
        return 6.0
    best, best_ll = 6.0, -np.inf
    for nu in (3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10.0, 15.0, 30.0):
        s = np.sqrt((nu - 2) / nu)
        ll = stats.t.logpdf(z / s, nu).sum() - len(z) * np.log(s)
        if ll > best_ll:
            best, best_ll = nu, ll
    return best


def fit(df: pd.DataFrame, cols, calib_days: int = 750) -> Fit:
    cols = [c for c in cols if not c.startswith("ev_") or df[c].sum() >= 3]  # drop events with too few cases (e.g. earnings on ETFs)
    d = df.dropna(subset=["y"] + cols)
    X = np.column_stack([np.ones(len(d))] + [d[c].values for c in cols])
    beta, resid = _ols(X, d["y"].values)
    sd = float(resid.std(ddof=len(cols) + 1))
    erv = np.exp(X @ beta + sd**2 / 2)
    tail = slice(-calib_days, None)
    r2 = d["ret"].values[tail] ** 2
    k = float(np.nanmean(r2) / np.nanmean(erv[tail]))
    z = d["ret"].values[tail] / np.sqrt(k * erv[tail])
    mult = {c[3:]: float(np.exp(b)) for c, b in zip(cols, beta[1:]) if c.startswith("ev_")}
    out = Fit(list(cols), beta, sd, k, _t_nu(z[np.asarray(d["ev_earn"].values[tail] == 0) if "ev_earn" in d else slice(None)]), d.index[-1], mult)
    if "ev_earn" in cols:
        # earnings days get their own scale and tail shape, from every past earnings day
        e = d["ev_earn"].values == 1
        ze = d["ret"].values[e] / np.sqrt(k * erv[e])
        ze = ze[np.isfinite(ze)]
        if len(ze) >= 12:
            out.earn_k = float(np.clip(np.mean(ze**2), 0.5, 3.0))
            ze = ze / np.sqrt(out.earn_k)
            out.earn_nu = _t_nu_small(ze)
    return out


def _t_nu_small(z: np.ndarray) -> float:
    best, best_ll = 6.0, -np.inf
    for nu in (2.5, 3.0, 4.0, 6.0, 10.0, 30.0):
        s = np.sqrt((nu - 2) / nu)
        ll = stats.t.logpdf(z / s, nu).sum() - len(z) * np.log(s)
        if ll > best_ll:
            best, best_ll = nu, ll
    return best


def predict_var(f: Fit, row: pd.DataFrame) -> np.ndarray:
    """Expected close-to-close variance."""
    X = np.column_stack([np.ones(len(row))] + [row[c].values for c in f.cols])
    v = f.k * np.exp(X @ f.beta + f.resid_sd**2 / 2)
    if "ev_earn" in f.cols:
        v = v * np.where(row["ev_earn"].values == 1, f.earn_k, 1.0)
    return v


def nu_for(f: Fit, row: pd.DataFrame) -> np.ndarray:
    if "ev_earn" in f.cols:
        return np.where(row["ev_earn"].values == 1, f.earn_nu, f.nu)
    return np.full(len(row), f.nu)


def p_exceed(sigma, x, nu):
    """P(|return| >= x) for a standardized Student-t scaled to std sigma."""
    s = np.asarray(sigma) * np.sqrt((nu - 2) / nu)
    return 2 * stats.t.sf(x / s, nu)


def quantile(sigma, q, nu):
    return np.asarray(sigma) * np.sqrt((nu - 2) / nu) * stats.t.ppf(q, nu)


# ---------------------------------------------------------------- walk-forward

def walk_forward(df: pd.DataFrame, start="2010-01-01", refit_every: int = 21, train_years: int | None = None) -> pd.DataFrame:
    """Out-of-sample forecasts for every spec. Each refit sees only data before the forecast block."""
    first = df["y"].first_valid_index()
    start = max(pd.Timestamp(start), first + pd.DateOffset(years=3))  # need some history to fit on
    test = df.index[df.index >= start]
    out = pd.DataFrame(index=test)
    out["ret"] = df.loc[test, "ret"]
    out["rv"] = df.loc[test, "rv"]

    # naive and VIX baselines, calibrated on a trailing window (known before t)
    r2 = df["ret"] ** 2
    naive_raw = r2.rolling(20).mean().shift(1)
    vix_raw = np.exp(df["vix"])
    for name, raw in (("naive20", naive_raw), ("vix_only", vix_raw)):
        k = (r2.rolling(750, min_periods=250).mean() / raw.rolling(750, min_periods=250).mean()).shift(1)
        var = raw * k
        z = df["ret"] / np.sqrt(var)
        out[f"var_{name}"] = var.loc[test]
        nu = pd.Series(np.nan, index=df.index)
        for i in range(0, len(test), 252):
            t0 = test[i]
            past = z.loc[:t0].iloc[-1000:-1].values
            nu.loc[t0:] = _t_nu(past)
        out[f"nu_{name}"] = nu.loc[test]

    for name in ("har", "har_vix", "full"):
        cols = SPECS[name]
        var = pd.Series(np.nan, index=test)
        nus = pd.Series(np.nan, index=test)
        for i in range(0, len(test), refit_every):
            block = test[i : i + refit_every]
            train = df.loc[: block[0] - pd.Timedelta(days=1)]
            if train_years:
                train = train.loc[block[0] - pd.DateOffset(years=train_years) :]
            f = fit(train, cols)
            rows = df.loc[block, cols + (["ev_earn"] if "ev_earn" not in cols else [])].ffill()
            var.loc[block] = predict_var(f, rows)
            nus.loc[block] = nu_for(f, df.loc[block])
        out[f"var_{name}"] = var
        out[f"nu_{name}"] = nus
    return out
