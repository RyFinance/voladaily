"""Today, the 10-day forecast and the month outlook for one ticker."""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import data, model

NAMES = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "Russell 2000", "TLT": "20+ Year Treasuries", "GLD": "Gold",
         "DIA": "Dow Jones", "AAPL": "Apple", "NVDA": "Nvidia", "MSFT": "Microsoft", "AMZN": "Amazon", "TSLA": "Tesla",
         "META": "Meta", "GOOGL": "Alphabet", "NFLX": "Netflix", "JPM": "JPMorgan", "PLTR": "Palantir", "AMD": "AMD"}

CONDITIONS = [(0.35, "calm"), (0.70, "normal"), (0.90, "choppy"), (1.01, "storm")]
VIX1D_WEIGHT = 0.5  # blend with VIX1D's own forecast for tomorrow, when it exists (see scoreboard)


def _implied_daily_var(v: pd.Series, h: int) -> float:
    """Market-implied daily variance for the session h days ahead, from the VIX term structure.

    VIX9D, VIX and VIX3M give total variance to 9, 21 and 63 sessions; the forward variance
    between maturities is the implied variance for days in that stretch.
    """
    pts = [(1, v.get("VIX1D")), (6, v.get("VIX9D")), (21, v.get("VIX")), (63, v.get("VIX3M"))]
    pts = [(t, (x / 100) ** 2 / 252) for t, x in pts if x is not None and np.isfinite(x)]
    if not pts:
        return np.nan
    tot = [(t, d * t) for t, d in pts]  # cumulative variance to maturity t
    prev_t, prev_tot = 0, 0.0
    for t, cum in tot:
        if h <= t:
            return max((cum - prev_tot) / (t - prev_t), 1e-8)
        prev_t, prev_tot = t, cum
    return pts[-1][1]


def _condition(sig: float, hist_sigma: pd.Series) -> str:
    pct = float((hist_sigma < sig).mean())
    for cut, name in CONDITIONS:
        if pct < cut:
            return name
    return "storm"


def _future_rows(df: pd.DataFrame, f: model.Fit, dates: pd.DatetimeIndex, rv_path: np.ndarray | None = None, v: pd.Series | None = None,
                 earn_session: pd.Timestamp | None = None) -> pd.DataFrame:
    """Predictor rows for future sessions. Lagged realized variance comes from history plus
    rv_path (simulated) or the expected path (deterministic forecast)."""
    if v is None:
        v = data.vol_indices().ffill().iloc[-1]
    ev = data.event_matrix(dates)
    early = data.early_closes()
    hist_rv = list(df["rv"].dropna().values[-22:])
    last_date = df.index[-1]
    rows = []
    slope0 = float(np.log(v["VIX"] / v["VIX3M"]))
    for i, d in enumerate(dates):
        lag = np.array(hist_rv[-22:])
        r = {
            "har_d": np.log(lag[-1]), "har_w": np.log(lag[-5:].mean()), "har_m": np.log(lag.mean()),
            "neg": 0.5 * lag[-1] / lag.mean(),  # expected value of the leverage term
            "monday": float(d.dayofweek == 0),
            "long_gap": float((d - (dates[i - 1] if i else last_date)).days > 3),
            "half_day": float(d in early),
            "vix": np.log(_implied_daily_var(v, i + 1)),
            "vix_slope": slope0 * np.exp(-i / 10),
        }
        for e in model.EVENTS:
            r[f"ev_{e}"] = ev.loc[d, e]
        r["ev_post_fomc"] = float(ev["fomc"].iloc[i - 1]) if i else float(df["ev_fomc"].iloc[-1])
        r["ev_post_election"] = float(ev["election"].iloc[i - 1]) if i else float(df["ev_election"].iloc[-1])
        r["ev_earn"] = float(earn_session is not None and d == earn_session)
        r["ev_post_earn"] = float(earn_session is not None and i > 0 and dates[i - 1] == earn_session)
        rows.append(r)
        X = np.array([1.0] + [r[c] for c in f.cols])
        exp_rv = float(np.exp(X @ f.beta + f.resid_sd**2 / 2)) if rv_path is None else float(rv_path[i])
        hist_rv.append(exp_rv)
    return pd.DataFrame(rows, index=dates)


def explain(f: model.Fit, row: pd.Series) -> list[dict]:
    """Event bumps on a given day, as % added to expected variance."""
    out = []
    for c, b in zip(f.cols, f.beta[1:]):
        if c.startswith("ev_") and row[c] == 1:
            out.append({"event": c[3:], "label": data.EVENT_LABELS.get(c[3:], {"post_fomc": "Day after Fed", "post_election": "Election results",
                                                                 "earn": "Earnings reaction", "post_earn": "Day after earnings"}.get(c[3:], c[3:])),
                        "time": data.EVENT_TIMES.get(c[3:], ""), "variance_bump_pct": round(float(100 * (np.exp(b) - 1)), 1)})
    return out


def simulate_month(df: pd.DataFrame, f: model.Fit, dates: pd.DatetimeIndex, n_paths: int = 4000, seed: int = 7, drift: float = 0.0003, v: pd.Series | None = None,
                   earn_session: pd.Timestamp | None = None):
    """Monte Carlo paths with volatility that keeps evolving: each day's log realized variance is
    drawn around the HAR forecast given that path's own history, then the return is drawn from it."""
    rng = np.random.default_rng(seed)
    base = _future_rows(df, f, dates, v=v, earn_session=earn_session)
    X0 = base[f.cols].values
    hist = df["rv"].dropna().values[-22:]
    H = len(dates)
    rv_hist = np.tile(hist, (n_paths, 1))
    rets = np.zeros((n_paths, H))
    idx = {c: j for j, c in enumerate(f.cols)}
    for h in range(H):
        X = np.repeat(X0[h : h + 1], n_paths, axis=0).copy()
        lag = rv_hist[:, -22:]
        X[:, idx["har_d"]] = np.log(lag[:, -1])
        X[:, idx["har_w"]] = np.log(lag[:, -5:].mean(1))
        X[:, idx["har_m"]] = np.log(lag.mean(1))
        mu = f.beta[0] + X @ f.beta[1:]
        rv = np.exp(mu + f.resid_sd * rng.standard_normal(n_paths))
        dr = drift[h] if np.ndim(drift) else drift
        r = dr + np.sqrt(f.k * rv) * rng.standard_normal(n_paths)
        rets[:, h] = r
        rv_hist = np.column_stack([rv_hist, rv])
    path = np.exp(np.cumsum(rets, axis=1))
    total = path[:, -1] - 1
    peak = np.maximum.accumulate(np.column_stack([np.ones(n_paths), path]), axis=1)[:, 1:]
    dd = (path / peak - 1).min(axis=1)
    rng_ = path.max(1) - np.minimum(path.min(1), 1.0)
    big = (np.abs(rets) >= 0.01).sum(1)
    return {
        "sessions": H,
        "return_q": {str(q): float(np.quantile(total, q)) for q in (0.05, 0.25, 0.5, 0.75, 0.95)},
        "p_up": float((total > 0).mean()),
        "p_drawdown_5": float((dd <= -0.05).mean()),
        "p_drawdown_10": float((dd <= -0.10).mean()),
        "range_median": float(np.median(rng_)),
        "expected_big_days": float(big.mean()),
        "fan": {str(q): np.quantile(path, q, axis=0).round(5).tolist() for q in (0.05, 0.25, 0.5, 0.75, 0.95)},
        "_rets": rets,
    }


def _base_rates(close: pd.Series, h: int) -> dict:
    """Share of all past h-session windows with a 5%/10% drawdown, and with a gain."""
    c = close.dropna().values
    n = len(c) - h
    dd5 = dd10 = up = 0
    for i in range(0, n, 5):  # every 5th start keeps it quick; overlapping windows anyway
        w = c[i + 1 : i + h + 1] / c[i]
        peak = np.maximum.accumulate(np.concatenate([[1.0], w]))[1:]
        d = (w / peak - 1).min()
        dd5 += d <= -0.05
        dd10 += d <= -0.10
        up += w[-1] > 1
    m = len(range(0, n, 5))
    return {"dd5": dd5 / m, "dd10": dd10 / m, "up": up / m}


def readings(df: pd.DataFrame) -> dict:
    v = data.vol_indices().ffill()
    last = v.iloc[-1]
    yr = v.iloc[-252:]
    rv10 = float(np.sqrt(df["ret"].iloc[-10:].pow(2).mean() * 252) * 100)
    return {
        "temperature": {"vix": round(float(last["VIX"]), 2), "pct_1y": round(float((yr["VIX"] < last["VIX"]).mean()), 2)},
        "feels_like": {"realized_10d": round(rv10, 1), "vix": round(float(last["VIX"]), 2),
                       "gap": round(rv10 - float(last["VIX"]), 1)},
        "pressure": {"vix_over_vix3m": round(float(last["VIX"] / last["VIX3M"]), 3),
                     "state": "backwardation (stress)" if last["VIX"] > last["VIX3M"] else "contango (normal)"},
        "tail": {"vvix": round(float(last["VVIX"]), 1), "pct_1y": round(float((yr["VVIX"] < last["VVIX"]).mean()), 2)},
    }


def forecast(ticker: str = "SPY", days: int = 10, month: int = 21) -> dict:
    df = model.features(ticker)
    f = model.fit(df, model.SPECS["full"])
    asof = df.index[-1]
    dates = data.next_sessions(asof, max(days, month))
    earn = None
    if df["ev_earn"].sum() >= 3:
        from . import earnings
        try:
            earn = earnings.upcoming(ticker)
        except Exception:
            earn = None
    earn_session = earn["session"] if earn else None
    rows = _future_rows(df, f, dates, earn_session=earn_session)
    var = model.predict_var(f, rows)
    # tomorrow: blend with VIX1D's forecast, which the scoreboard shows ties the model since 2022.
    # Index ETFs only: VIX1D prices the S&P 500, not single stocks.
    v1 = data.vol_indices()["VIX1D"].dropna()
    blended = False
    if ticker in ("SPY", "VOO", "IVV", "QQQ", "IWM", "DIA") and len(v1) and v1.index[-1] >= asof - pd.Timedelta(days=4):
        r2 = df["ret"] ** 2
        raw = (v1 / 100) ** 2 / 252
        k1 = float(r2.reindex(raw.index).iloc[-250:].mean() / raw.shift(1).iloc[-250:].mean())
        var[0] = float(np.exp(VIX1D_WEIGHT * np.log(var[0]) + (1 - VIX1D_WEIGHT) * np.log(k1 * raw.iloc[-1])))
        blended = True
    sig = np.sqrt(var)

    # history of this model's own forecast sigma for "calm/normal/choppy/storm" cut-offs
    try:
        wf = pd.read_pickle(data.CACHE / f"wf_{ticker}.pkl")
        hist_sigma = np.sqrt(wf["var_full"].dropna().iloc[-1260:])
    except Exception:
        hist_sigma = np.sqrt(df["ret"].pow(2).rolling(20).mean().dropna().iloc[-1260:])

    out_days = []
    nus = model.nu_for(f, rows)
    from . import direction as _dir
    _dm = _dir.DirModel(df)
    _drows = _direction_rows(df, dates[:days], rows.iloc[:days])
    _mu = _dm.predict(_drows)
    _why = [_why_text(x) for x in _dm.explain(_drows)]
    for i, d in enumerate(dates[:days]):
        s = float(sig[i])
        nu = float(nus[i])
        out_days.append({
            "date": d.strftime("%Y-%m-%d"), "weekday": d.strftime("%a"),
            "expected_move": round(s * 0.7979, 5),  # E|r| for a normal; the plain-English "±"
            "sigma": round(s, 5),
            "p_move_1pct": round(float(model.p_exceed(s, 0.01, nu)), 3),
            "p_move_2pct": round(float(model.p_exceed(s, 0.02, nu)), 3),
            "p_move_5pct": round(float(model.p_exceed(s, 0.05, nu)), 3),
            "range_80": [round(float(_mu[i] + model.quantile(s, 0.1, nu)), 5), round(float(_mu[i] + model.quantile(s, 0.9, nu)), 5)],
            "exp_return": round(float(_mu[i]), 6),
            "why": _why[i],
            "p_up": round(float(1 - __import__("scipy").stats.t.cdf(-_mu[i] / (s * np.sqrt((nu - 2) / nu)), nu)), 4),
            "condition": _condition(s, hist_sigma),
            "events": explain(f, rows.iloc[i]),
        })
    from . import direction

    dmod = direction.DirModel(df)
    drows = _direction_rows(df, dates[:month], rows.iloc[:month])
    mu = dmod.predict(drows)
    month_out = simulate_month(df, f, dates[:month], earn_session=earn_session, drift=mu)
    rets = month_out.pop("_rets")
    weeks = []
    wk = pd.Series(range(month), index=dates[:month]).groupby(dates[:month].to_period("W-FRI"))
    for per, g in wk:
        ix = list(g.values)
        w = _period(rets, mu, ix)
        w.update({"start": dates[ix[0]].strftime("%Y-%m-%d"), "end": dates[ix[-1]].strftime("%Y-%m-%d"), "sessions": len(ix),
                  "events": [e["label"] for i in ix for e in explain(f, rows.iloc[i])],
                  "high_risk": bool(any(_condition(float(sig[i]), hist_sigma) == "storm" for i in ix)),
                  "sigma": float(np.sqrt(np.sum(sig[ix] ** 2)))})
        weeks.append(w)
    month_out.update(_period(rets, mu, list(range(month))))
    # The month backtest (2010-26) showed raw path odds run hot in stormy months, and that the
    # simulated direction is worse than the plain base rate. So pullback odds are shrunk halfway
    # to the historical base rate (that blend scored best walk-forward), and direction is shown
    # only as the base rate.
    base = _base_rates(data.bars(ticker)["close"], month)
    month_out["raw_p_drawdown_5"], month_out["raw_p_drawdown_10"] = month_out["p_drawdown_5"], month_out["p_drawdown_10"]
    month_out["p_drawdown_5"] = 0.5 * month_out["p_drawdown_5"] + 0.5 * base["dd5"]
    month_out["p_drawdown_10"] = 0.5 * month_out["p_drawdown_10"] + 0.5 * base["dd10"]
    month_out["p_up_base_rate"] = base["up"]
    month_out.pop("raw_p_up", None)
    month_out["calendar"] = [{"date": d.strftime("%Y-%m-%d"), "sigma": round(float(sig[i]), 5),
                              "condition": _condition(float(sig[i]), hist_sigma),
                              "events": [e["label"] for e in explain(f, rows.iloc[i])]}
                             for i, d in enumerate(dates[:month])]
    # what tomorrow would look like with no scheduled events: the "base" weather
    calm_row = rows.iloc[[0]].copy()
    for c in calm_row.columns:
        if c.startswith("ev_"):
            calm_row[c] = 0.0
    base_sigma = float(np.sqrt(model.predict_var(f, calm_row))[0])
    b = data.bars(ticker)
    spark = b["close"].iloc[-120:]
    earn_out = None
    if earn:
        from . import earnings
        earn_out = earnings_card(ticker, df, f, rows, earn)
    return {
        "name": NAMES.get(ticker, ticker),
        "spot": float(b["close"].iloc[-1]),
        "change_1d": float(b["close"].iloc[-1] / b["close"].iloc[-2] - 1),
        "spark": {"dates": [d.strftime("%Y-%m-%d") for d in spark.index], "close": [round(float(x), 4) for x in spark.values]},
        "recent_moves": [round(float(x), 5) for x in df["ret"].iloc[-60:].values],
        "realized_20d_annual": float(np.sqrt(df["ret"].iloc[-20:].pow(2).mean() * 252)),
        "base_sigma": base_sigma,
        "typical_sigma": float(np.median(hist_sigma)),
        "earnings": earn_out,
        "ticker": ticker, "as_of_close": asof.strftime("%Y-%m-%d"), "model": "HAR + VIX term structure + events",
        "tomorrow_blended_with_vix1d": blended, "t_dof": f.nu,
        "event_multipliers": {k: round(v, 3) for k, v in f.event_mult.items()},
        "readings": readings(df), "days": out_days, "month": month_out, "weeks": weeks,
        "direction_coef_bp": {k: round(float(v) * 1e4, 3) for k, v in dmod.coef.items()},
        "direction_score": direction.score(direction.walk_forward(df)),
    }


def backtest_month(ticker: str = "SPY", start: str = "2010-01-01", n_paths: int = 1500) -> pd.DataFrame:
    """Walk-forward check of the month outlook: at each month's first session, forecast the next
    21 sessions with only prior data, then compare with what happened."""
    df = model.features(ticker)
    vols = data.vol_indices().ffill()
    close = data.bars(ticker)["close"]
    firsts = df.index[(df.index >= pd.Timestamp(start))].to_series().groupby(df.index[(df.index >= pd.Timestamp(start))].to_period("M")).first()
    rows = []
    for t0 in firsts:
        i0 = df.index.get_loc(t0)
        if i0 + 21 > len(df):
            break
        hist = df.iloc[:i0]
        f = model.fit(hist, model.SPECS["full"])
        dates = df.index[i0 : i0 + 21]
        v = vols.loc[: hist.index[-1]].iloc[-1]
        sim = simulate_month(hist, f, dates, n_paths=n_paths, v=v)
        path = close.loc[dates] / close.loc[hist.index[-1]]
        peak = np.maximum.accumulate(np.concatenate([[1.0], path.values]))[1:]
        dd = float((path.values / peak - 1).min())
        rows.append({
            "start": t0, "p_dd5": sim["p_drawdown_5"], "p_dd10": sim["p_drawdown_10"], "p_up": sim["p_up"],
            "q05": sim["return_q"]["0.05"], "q25": sim["return_q"]["0.25"], "q75": sim["return_q"]["0.75"], "q95": sim["return_q"]["0.95"],
            "big_days": sim["expected_big_days"],
            "ret": float(path.iloc[-1] - 1), "dd": dd, "act_big": int((np.log(path).diff().fillna(np.log(path.iloc[0])).abs() >= 0.01).sum()),
        })
    return pd.DataFrame(rows).set_index("start")


def _direction_rows(df: pd.DataFrame, dates: pd.DatetimeIndex, rows: pd.DataFrame) -> pd.DataFrame:
    """Direction predictors for future sessions, using only what is known today. Recent-return
    terms only include returns that have happened; calendar and event terms are exact."""
    from . import direction

    hx = direction.features(df)
    today = hx.iloc[-1]
    r = df["ret"].fillna(0).values
    sess = data.sessions(dates[0] - pd.Timedelta(days=40), dates[-1] + pd.Timedelta(days=40))
    month = sess.to_period("M")
    pos = pd.Series(range(len(sess)), index=sess).groupby(month).rank(method="first")
    size = pd.Series(1, index=sess).groupby(month).transform("sum")
    tom = ((pos <= 3) | (pos == size)).astype(float)
    out = []
    for h, d in enumerate(dates):
        known = r[-(5 - h):] if h < 5 else r[:0]
        known21 = r[-(21 - h):] if h < 21 else r[:0]
        row = {
            "r1": r[-1] if h == 0 else 0.0, "r5": known.sum(), "r21": known21.sum(),
            "mom": today["mom"], "vix": today["vix"], "vix_slope": today["vix_slope"], "gap": today["gap"],
            "tom": float(tom.get(d, 0.0)),
            "mon": float(d.dayofweek == 0), "tue": float(d.dayofweek == 1), "wed": float(d.dayofweek == 2), "thu": float(d.dayofweek == 3),
        }
        for e in direction.EVENTS:
            row[e] = float(rows.iloc[h].get(f"ev_{e}", 0.0))
        out.append(row)
    return pd.DataFrame(out, index=dates)


WHY_LABEL = {
    "fomc": "Fed decision day: stocks have tended to drift up into Fed announcements",
    "post_fomc": "Day after a Fed decision: some of the pre-Fed gain usually gives back",
    "cpi": "CPI release", "nfp": "Jobs report", "opex": "Options expiry",
    "election": "Election day: historically a firm session (11 elections, small sample)",
    "post_election": "Election results", "earn": "Earnings reaction day", "post_earn": "Day after earnings",
    "tom": "Turn of the month: month-end and early-month inflows",
}


def _why_text(x: dict) -> dict:
    """Turn the model's pushes into short, plain reasons, biggest first."""
    v, p = x["values"], x["parts"]
    out = []
    for k, c in p.items():
        if abs(c) < 2e-5:
            continue
        if k == "r1":
            t = f"Last session moved {np.expm1(v[k]) * 100:+.1f}%: short-term moves tend to partly reverse"
        elif k == "r5":
            t = f"The past week is {np.expm1(v[k]) * 100:+.1f}%: one-week swings tend to fade"
        elif k == "r21":
            t = f"The past month is {np.expm1(v[k]) * 100:+.1f}%"
        elif k == "mom":
            t = f"12-month trend {np.expm1(v[k]) * 100:+.0f}%: trends tend to persist"
        elif k == "vix":
            t = f"VIX at {v[k] * 100:.0f}: higher fear has paid a higher expected return"
        elif k == "vix_slope":
            t = "VIX curve shape" + (" (inverted: stress)" if v[k] > 0 else " (normal contango)")
        elif k == "gap":
            t = "Realized vs implied volatility gap"
        elif k in ("wed", "thu"):
            continue  # weekday terms are tiny and noisy; fold them into "other"
        elif k in ("mon", "tue"):
            day = "Monday" if k == "mon" else "Tuesday"
            t = f"{day}s have historically been {'firmer' if c > 0 else 'weaker'} for this ticker"
        else:
            t = WHY_LABEL.get(k, k)
        out.append({"key": k, "text": t, "effect": round(c, 6)})
    out.sort(key=lambda r: -abs(r["effect"]))
    top = out[:5]
    other = sum(r["effect"] for r in out[5:]) + sum(c for k, c in p.items() if k in ("wed", "thu"))
    return {"base": round(x["base"], 6), "reasons": top, "other": round(other, 6)}


def _period(rets: np.ndarray, mu: np.ndarray, idx: list[int]) -> dict:
    """Distribution of the compounded return over sessions idx, from the simulated paths."""
    tot = np.exp(rets[:, idx].sum(1)) - 1
    return {"exp_return": float(np.expm1(mu[idx].sum())), "median": float(np.median(tot)), "p_up": float((tot > 0).mean()),
            "q10": float(np.quantile(tot, 0.1)), "q90": float(np.quantile(tot, 0.9))}


def earnings_card(ticker: str, df: pd.DataFrame, f: model.Fit, rows: pd.DataFrame, earn: dict) -> dict:
    """Next earnings: Voladaily's expected move, the options-implied move, and the stock's own record."""
    from . import earnings

    m = earnings.moves(ticker)
    last12 = m.tail(12)
    sess = earn["session"]
    out = {"date": earn["date"], "timing": earn["timing"], "reaction_session": sess.strftime("%Y-%m-%d"),
           "past": last12[["accepted", "timing", "move", "gap"]].assign(session=last12["session"].dt.strftime("%Y-%m-%d")).round(5).to_dict("records"),
           "past_avg_abs_move": float(last12["abs_move"].mean()), "past_median_abs_move": float(last12["abs_move"].median()),
           "past_up_share": float((last12["move"] > 0).mean()), "n_history": int(len(m))}
    if sess in rows.index:
        i = rows.index.get_loc(sess)
        sig = float(np.sqrt(model.predict_var(f, rows.iloc[[i]]))[0])
        no_earn = rows.iloc[[i]].copy()
        no_earn["ev_earn"] = 0.0
        normal = float(np.sqrt(model.predict_var(f, no_earn))[0])
        out["model_expected_move"] = sig * np.sqrt(2 / np.pi)
        out["model_sigma"] = sig
        out["model_normal_day_sigma"] = normal
    else:
        normal = float(np.sqrt(model.predict_var(f, rows.iloc[[0]]))[0])
        out["model_normal_day_sigma"] = normal
    try:
        imp = earnings.implied_move(ticker, sess, normal)
    except Exception:
        imp = None
    out["implied"] = imp
    if imp and imp["implied_move"] > 0:
        # the 2023 JRFM signal: past realized move relative to what options price now
        out["past_over_implied"] = out["past_avg_abs_move"] / imp["implied_move"]
        r = out["past_over_implied"]
        out["verdict"] = ("options look rich" if r < 0.85 else "options look cheap" if r > 1.15 else "options look fairly priced")
    return out
