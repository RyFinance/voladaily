"""Scoreboard page. Deliberately plain: the visual design is still to be chosen."""

from __future__ import annotations

import datetime as dt
import html

import numpy as np
import pandas as pd

from . import score

CSS = """
body{font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;background:#fafafa;color:#111}
main{max-width:1000px;margin:0 auto;padding:40px 20px 80px}
h1{font-size:30px;margin:0 0 4px}h2{font-size:19px;margin:44px 0 8px;border-top:1px solid #ddd;padding-top:20px}
p.lead{color:#555;margin:0 0 20px}.note{color:#666;font-size:13px}
table{border-collapse:collapse;width:100%;margin:8px 0 6px;font-size:13.5px;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:6px 8px;border-bottom:1px solid #e6e6e6}th:first-child,td:first-child{text-align:left}
th{font-weight:600;color:#444;background:#f0f0f0}
tr.ref td{font-weight:600;background:#fff}
.good{color:#0a7a3a}.bad{color:#b3261e}.verdict{padding:10px 14px;background:#fff;border-left:3px solid #111;margin:10px 0}
@media (max-width:640px){table{font-size:12px}th,td{padding:5px 4px}}
"""


def _f(x, d=3):
    return "" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}"


def _sig(t):
    if t is None or not np.isfinite(t):
        return ""
    cls = "good" if t < -1.96 else ("bad" if t > 1.96 else "")
    return f'<span class="{cls}">{t:+.1f}</span>'


def _models_table(sb: dict, ref="full") -> str:
    rows = []
    for k, m in sb["models"].items():
        skill = 100 * (1 - m["brier_1"] / m["base_1"])
        rows.append(
            f'<tr class="{"ref" if k == ref else ""}"><td>{html.escape(m["name"])}</td><td>{_f(m["qlike"])}</td>'
            f'<td>{_f(m.get("ref_gain_pct"), 1) + "%" if k != ref else "—"}</td><td>{_sig(m.get("dm_t_vs_ref")) if k != ref else "—"}</td>'
            f'<td>{_f(m["brier_1"], 4)}</td><td>{skill:.1f}%</td><td>{_f(100 * m["cover_80"], 1)}%</td><td>{_f(100 * m["cover_95"], 1)}%</td></tr>'
        )
    return ("<table><tr><th>Model</th><th>QLIKE (lower is better)</th><th>Voladaily better by</th><th>t-stat</th>"
            "<th>Brier, 1%+ move</th><th>Skill vs base rate</th><th>80% range hit</th><th>95% range hit</th></tr>" + "".join(rows) + "</table>")


def _verdict(t: str, sb: dict) -> str:
    m = sb["models"]
    v = m["vix_only"]
    h = m["har_vix"]
    parts = []
    if v["dm_t_vs_ref"] < -1.96:
        parts.append(f"beats VIX alone by {v['ref_gain_pct']:.1f}% (t {v['dm_t_vs_ref']:+.1f}, significant)")
    else:
        parts.append(f"does not significantly beat VIX alone (t {v['dm_t_vs_ref']:+.1f})")
    parts.append(f"event terms add {h['ref_gain_pct']:.1f}% over HAR + VIX (t {h['dm_t_vs_ref']:+.1f}"
                 + (", significant)" if h["dm_t_vs_ref"] < -1.96 else ", not significant on its own)"))
    rec = sb.get("recent", {}).get("models", {})
    if "vix1d" in rec:
        r = rec["vix1d"]
        parts.append(f"since mid-2022 vs VIX1D: t {r['dm_t_vs_ref']:+.1f} ("
                     + ("Voladaily better" if r["dm_t_vs_ref"] < -1.96 else "VIX1D better" if r["dm_t_vs_ref"] > 1.96 else "a tie") + ")")
    return f'<div class="verdict"><b>{t}</b>: ' + "; ".join(parts) + ".</div>"


def write(results: dict, month: pd.DataFrame | None, path) -> str:
    first = next(iter(results.values()))
    out = [f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>Voladaily scoreboard</title><style>{CSS}</style></head><body><main>",
           "<h1>Voladaily scoreboard</h1>",
           f"<p class=lead>Out-of-sample, walk-forward: every forecast uses only data from before that day. "
           f"{first['start']} to {first['end']}, {first['n']:,} sessions per asset. Generated {dt.datetime.now():%Y-%m-%d %H:%M}.</p>",
           "<p class=note>QLIKE is the standard loss for volatility forecasts. The t-stat is a Diebold-Mariano test of Voladaily "
           "against that model, with Newey-West errors; below −1.96 (green) means Voladaily is significantly better. "
           "Brier skill compares the \"chance of a 1%+ move\" with just using the historical frequency. A well-calibrated 80% range "
           "should contain the move about 80% of the time.</p>",
           "<h2>Verdicts</h2>"]
    out += [_verdict(t, sb) for t, sb in results.items()]
    for t, sb in results.items():
        out.append(f"<h2>{t}</h2>")
        out.append(_models_table(sb))
        if "recent" in sb:
            out.append(f"<p class=note>Since {sb['recent']['start']} (VIX1D era, {sb['recent']['n']} sessions):</p>")
            out.append(_models_table(sb["recent"]))
        if t == "SPY":
            cal = pd.DataFrame(sb["calibration_1pct"])
            out.append("<p class=note>Calibration of \"chance of a 1%+ move\" (SPY):</p><table><tr><th>Forecast</th><th>Happened</th><th>Days</th></tr>"
                       + "".join(f"<tr><td>{r.forecast:.0%}</td><td>{r.actual:.0%}</td><td>{int(r.n)}</td></tr>" for r in cal.itertuples()) + "</table>")
            ev = pd.DataFrame(sb["events"])
            out.append("<p class=note>Event days (SPY): QLIKE by model. Lower is better.</p><table><tr><th>Event</th><th>Days</th><th>Avg |move|</th>"
                       "<th>VIX alone</th><th>HAR + VIX</th><th>Voladaily</th></tr>"
                       + "".join(f"<tr><td>{r.event}</td><td>{r.n}</td><td>{r.avg_abs_move:.2%}</td><td>{r.vix_only:.3f}</td><td>{r.har_vix:.3f}</td><td>{r.full:.3f}</td></tr>"
                                 for r in ev.itertuples()) + "</table>")
    if month is not None and len(month):
        m = month
        rows = []
        for k, thr in (("p_dd5", -0.05), ("p_dd10", -0.10)):
            a = (m.dd <= thr).astype(float)
            clim = a.expanding().mean().shift(1).fillna(a.iloc[:24].mean())
            shrunk = 0.5 * m[k] + 0.5 * clim
            rows.append(f"<tr><td>{'5%' if thr == -0.05 else '10%'} pullback</td><td>{m[k].mean():.1%}</td><td>{a.mean():.1%}</td>"
                        f"<td>{((m[k] - a) ** 2).mean():.4f}</td><td>{((shrunk - a) ** 2).mean():.4f}</td><td>{((clim - a) ** 2).mean():.4f}</td></tr>")
        cover90 = ((m.ret >= m.q05) & (m.ret <= m.q95)).mean()
        cover50 = ((m.ret >= m.q25) & (m.ret <= m.q75)).mean()
        out.append("<h2>Month outlook backtest (SPY)</h2>")
        out.append(f"<p class=note>{len(m)} months, each forecast on its first session with only prior data, then compared with the next 21 sessions.</p>")
        out.append("<table><tr><th>Question</th><th>Avg forecast</th><th>Happened</th><th>Brier (raw)</th><th>Brier (half base rate, shipped)</th><th>Brier (base rate only)</th></tr>"
                   + "".join(rows) + "</table>")
        out.append(f"<table><tr><th>Check</th><th>Result</th></tr><tr><td>90% monthly range contained the move</td><td>{cover90:.1%}</td></tr>"
                   f"<tr><td>50% monthly range contained the move</td><td>{cover50:.1%}</td></tr>"
                   f"<tr><td>Days with 1%+ moves, forecast vs actual (avg)</td><td>{m.big_days.mean():.1f} vs {m.act_big.mean():.1f} (correlation {m.big_days.corr(m.act_big):.2f})</td></tr></table>")
        out.append("<p class=note>Direction is not forecast. The simulated chance of an up month scored worse than the plain historical "
                   "share of up months, so the app shows that share and labels it as history.</p>")
    out.append("</main></body></html>")
    path.write_text("\n".join(out))
    return str(path)
