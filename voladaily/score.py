"""Scoreboard: how good the forecasts were, against baselines, out of sample.

QLIKE is the standard loss for variance forecasts (robust to a noisy proxy, Patton 2011),
here against squared close-to-close returns. Brier scores the "chance of rain" numbers.
Calibration checks that 30% calls happen about 30% of the time.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from . import model

NAMES = {
    "naive20": "Naive (last 20 days)",
    "vix_only": "VIX alone",
    "vix1d": "VIX1D alone",
    "har": "HAR (price history only)",
    "har_vix": "HAR + VIX",
    "full": "Voladaily (HAR + VIX + events)",
}
THRESHOLDS = (0.01, 0.02)


def qlike(r2: pd.Series, var: pd.Series) -> pd.Series:
    r2 = r2.clip(lower=1e-10)
    x = r2 / var
    return x - np.log(x) - 1


def diebold_mariano(loss_a: pd.Series, loss_b: pd.Series, lags: int = 5) -> tuple[float, float]:
    """t-stat and two-sided p for mean(loss_a - loss_b) with Newey-West errors. Negative t = a is better."""
    d = (loss_a - loss_b).dropna().values
    n = len(d)
    dm = d - d.mean()
    lrv = dm @ dm / n
    for L in range(1, lags + 1):
        w = 1 - L / (lags + 1)
        lrv += 2 * w * (dm[L:] @ dm[:-L]) / n
    t = d.mean() / np.sqrt(lrv / n)
    return float(t), float(2 * stats.norm.sf(abs(t)))


def brier(p: pd.Series, hit: pd.Series) -> float:
    return float(((p - hit) ** 2).mean())


def calibration(p: pd.Series, hit: pd.Series, bins=(0, .05, .1, .2, .3, .4, .5, .6, .8, 1.0001)) -> pd.DataFrame:
    b = pd.cut(p, bins, right=False)
    g = pd.DataFrame({"p": p, "hit": hit, "b": b}).groupby("b", observed=True)
    return pd.DataFrame({"forecast": g.p.mean(), "actual": g.hit.mean(), "n": g.size()})


def add_vix1d(wf: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """VIX1D baseline (May 2022 onward), calibrated like the VIX baseline."""
    from . import data

    v = data.vol_indices()["VIX1D"].reindex(df.index).ffill().shift(1)
    raw = (v / 100) ** 2 / 252
    r2 = df["ret"] ** 2
    k = (r2.rolling(250, min_periods=120).mean() / raw.rolling(250, min_periods=120).mean()).shift(1)
    wf["var_vix1d"] = (raw * k).reindex(wf.index)
    wf["nu_vix1d"] = wf["nu_vix_only"]
    return wf


def scoreboard(wf: pd.DataFrame, specs=("naive20", "vix_only", "har", "har_vix", "full"), ref="full") -> dict:
    r2 = wf["ret"] ** 2
    ok = pd.concat([wf[f"var_{s}"] for s in specs], axis=1).notna().all(axis=1) & wf["ret"].notna()
    w = wf[ok]
    r2 = r2[ok]
    out = {"start": str(w.index[0].date()), "end": str(w.index[-1].date()), "n": int(len(w)), "models": {}}
    loss = {s: qlike(r2, w[f"var_{s}"]) for s in specs}
    for s in specs:
        sig = np.sqrt(w[f"var_{s}"])
        nu = w[f"nu_{s}"].fillna(6.0)
        row = {"name": NAMES.get(s, s), "qlike": float(loss[s].mean())}
        for x in THRESHOLDS:
            p = pd.Series(model.p_exceed(sig, x, nu), index=w.index)
            hit = (w["ret"].abs() >= x).astype(float)
            row[f"brier_{int(x*100)}"] = brier(p, hit)
            row[f"base_{int(x*100)}"] = brier(pd.Series(hit.expanding().mean().shift(1).fillna(hit.mean()), index=w.index), hit)
        for cov in (0.8, 0.95):
            lo = model.quantile(sig, (1 - cov) / 2, nu)
            row[f"cover_{int(cov*100)}"] = float(((w["ret"] >= lo) & (w["ret"] <= -lo)).mean())
        if s != ref:
            t, p = diebold_mariano(loss[ref], loss[s])
            row["dm_t_vs_ref"], row["dm_p_vs_ref"] = t, p
            row["ref_gain_pct"] = float(100 * (1 - loss[ref].mean() / loss[s].mean()))
        out["models"][s] = row
    # calibration of the reference model's 1% "chance of rain"
    sig = np.sqrt(w[f"var_{ref}"])
    nu = w[f"nu_{ref}"].fillna(6.0)
    p1 = pd.Series(model.p_exceed(sig, 0.01, nu), index=w.index)
    out["calibration_1pct"] = calibration(p1, (w["ret"].abs() >= 0.01).astype(float)).reset_index(drop=True).round(4).to_dict("records")
    # by year
    yr = {}
    for y, g in w.groupby(w.index.year):
        yr[int(y)] = {s: float(loss[s].loc[g.index].mean()) for s in specs}
    out["by_year"] = yr
    return out


def event_days(wf: pd.DataFrame, df: pd.DataFrame, specs=("vix_only", "har_vix", "full")) -> pd.DataFrame:
    """Average QLIKE on each kind of event day: where the event terms earn their keep."""
    r2 = wf["ret"] ** 2
    rows = []
    for e in model.EVENTS + ["post_fomc"]:
        mask = df.loc[wf.index, f"ev_{e}"] == 1
        if mask.sum() < 20:
            continue
        r = {"event": e, "n": int(mask.sum()), "avg_abs_move": float(wf.loc[mask, "ret"].abs().mean())}
        for s in specs:
            r[s] = float(qlike(r2[mask], wf.loc[mask, f"var_{s}"]).mean())
        rows.append(r)
    return pd.DataFrame(rows)
