"""Direction: the expected return for each session, signed.

Direction is much harder to forecast than size, so this stays small and honest:
a heavily shrunk ridge regression on effects with published evidence behind them,
fitted walk-forward, scored against the plain historical mean.

  - Pre-FOMC drift (Lucca & Moench 2015): stocks tend to rise into Fed decisions.
  - Turn of the month (Ariel 1987; Lakonishok & Smidt 1988): last day + first three.
  - Day of week, other event days.
  - Short-term reversal (yesterday, last week) and 12-1 momentum.
  - Risk premium: expected returns rise with implied volatility; the realized-vs-implied gap.

Every feature is known before the session opens.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import data

EVENTS = ["fomc", "post_fomc", "cpi", "nfp", "opex", "election", "post_election", "earn", "post_earn"]


def features(df: pd.DataFrame) -> pd.DataFrame:
    """df is model.features(ticker). Adds direction predictors (row t = known before t)."""
    x = pd.DataFrame(index=df.index)
    r = df["ret"].fillna(0)
    x["r1"] = r.shift(1)
    x["r5"] = r.rolling(5).sum().shift(1)
    x["r21"] = r.rolling(21).sum().shift(1)
    x["mom"] = r.rolling(231).sum().shift(22)  # 12-1 momentum
    x["vix"] = np.sqrt(np.exp(df["vix"]) * 252)  # VIX level as a decimal
    x["vix_slope"] = df["vix_slope"]
    x["gap"] = np.log(df["rv"].rolling(22).mean().shift(1) * 252) - np.log(x["vix"] ** 2)
    idx = df.index
    month = idx.to_period("M")
    pos = pd.Series(range(len(idx)), index=idx).groupby(month).rank(method="first")
    size = pd.Series(1, index=idx).groupby(month).transform("sum")
    x["tom"] = ((pos <= 3) | (pos == size)).astype(float)
    for d, name in enumerate(["mon", "tue", "wed", "thu"]):
        x[name] = (idx.dayofweek == d).astype(float)
    for e in EVENTS:
        c = f"ev_{e}"
        x[e] = df[c] if c in df else 0.0
    return x


def _fit(X: np.ndarray, y: np.ndarray, alpha: float):
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1
    Z = (X - mu) / sd
    ym = y.mean()
    beta = np.linalg.solve(Z.T @ Z + alpha * len(y) * np.eye(Z.shape[1]), Z.T @ (y - ym))
    return mu, sd, ym, beta


def _predict(m, X):
    mu, sd, ym, beta = m
    return ym + ((X - mu) / sd) @ beta


ALPHA = 2.0  # heavy shrinkage: direction signals are faint, so trust them little


class DirModel:
    def __init__(self, df: pd.DataFrame, alpha: float = ALPHA):
        x = features(df)
        cols = [c for c in x.columns if x[c].abs().sum() > 0]
        d = pd.concat([x[cols], df["ret"]], axis=1).dropna()
        self.cols = cols
        self.m = _fit(d[cols].values, d["ret"].values, alpha)
        self.coef = dict(zip(cols, self.m[3] / self.m[1]))  # per-unit effect

    def predict(self, rows: pd.DataFrame) -> np.ndarray:
        return _predict(self.m, rows[self.cols].fillna(0).values)

    def explain(self, rows: pd.DataFrame) -> list[dict]:
        """Per row: the average day, plus each feature's push away from it (in return units)."""
        mu, sd, ym, beta = self.m
        X = rows[self.cols].fillna(0).values
        parts = ((X - mu) / sd) * beta
        out = []
        for r in range(len(rows)):
            out.append({"base": float(ym), "parts": {c: float(parts[r, j]) for j, c in enumerate(self.cols)},
                        "values": {c: float(X[r, j]) for j, c in enumerate(self.cols)}})
        return out


def walk_forward(df: pd.DataFrame, start="2010-01-01", refit_every=21, alpha=ALPHA) -> pd.DataFrame:
    x = features(df)
    first = df["ret"].first_valid_index()
    start = max(pd.Timestamp(start), first + pd.DateOffset(years=3))
    test = df.index[df.index >= start]
    pred = pd.Series(np.nan, index=test)
    hist = pd.Series(np.nan, index=test)
    for i in range(0, len(test), refit_every):
        block = test[i : i + refit_every]
        tr = df.loc[: block[0] - pd.Timedelta(days=1)]
        dm = DirModel(tr, alpha)
        pred.loc[block] = dm.predict(x.loc[block])
        hist.loc[block] = tr["ret"].mean()
    return pd.DataFrame({"ret": df.loc[test, "ret"], "pred": pred, "hist": hist})


def score(wf: pd.DataFrame) -> dict:
    """Out-of-sample R^2 vs the historical mean, hit rates by horizon, and a long/flat test."""
    w = wf.dropna()
    r, p, h = w["ret"], w["pred"], w["hist"]
    r2 = 1 - ((r - p) ** 2).sum() / ((r - h) ** 2).sum()
    out = {"r2_oos": float(r2), "n": int(len(w)), "start": str(w.index[0].date()),
           "hit_day": float((np.sign(p) == np.sign(r)).mean()), "base_up_day": float((r > 0).mean())}
    for H, name in ((5, "week"), (21, "month")):
        fr = r[::-1].rolling(H).sum()[::-1]  # realized next H sessions incl. today
        fp = p[::-1].rolling(H).sum()[::-1]
        ok = fr.notna() & fp.notna()
        out[f"hit_{name}"] = float((np.sign(fp[ok]) == np.sign(fr[ok])).mean())
        out[f"base_up_{name}"] = float((fr[ok] > 0).mean())
    # does leaning on the sign help? long when pred > hist mean, else flat
    tilt = (p > h).astype(float)
    out["avg_ret_when_above"] = float(r[tilt == 1].mean())
    out["avg_ret_when_below"] = float(r[tilt == 0].mean())
    return out
