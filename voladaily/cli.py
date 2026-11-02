"""voladaily today [TICKER] | voladaily month [TICKER] | voladaily score [TICKERS...]"""

from __future__ import annotations

import argparse
import json
import warnings

warnings.filterwarnings("ignore")

from . import data, forecast, model, report, score  # noqa: E402

OUT = data.ROOT / "out"
ICON = {"calm": "calm  ", "normal": "normal", "choppy": "choppy", "storm": "STORM "}


def _pct(x, d=2):
    return f"{x * 100:.{d}f}%"


def cmd_today(a):
    r = forecast.forecast(a.ticker.upper())
    OUT.mkdir(exist_ok=True)
    (OUT / f"forecast_{r['ticker']}.json").write_text(json.dumps(r, indent=1))
    rd = r["readings"]
    t = r["days"][0]
    print(f"\n{r['ticker']}  forecast for {t['weekday']} {t['date']}  (data to close {r['as_of_close']})")
    print(f"  {ICON[t['condition']].strip()}  expected move ±{_pct(t['expected_move'])}  ·  chance of a 1%+ move {_pct(t['p_move_1pct'], 0)}  ·  2%+ {_pct(t['p_move_2pct'], 0)}")
    print(f"  80% range {_pct(t['range_80'][0])} to {_pct(t['range_80'][1])}")
    for e in t["events"]:
        print(f"  + {e['label']} {e['time']}: +{e['variance_bump_pct']:.0f}% variance")
    print(f"\n  temperature  VIX {rd['temperature']['vix']}  ({rd['temperature']['pct_1y']:.0%} of the past year was lower)")
    print(f"  feels like   moving like {rd['feels_like']['realized_10d']} vs VIX {rd['feels_like']['vix']}")
    print(f"  pressure     {rd['pressure']['state']}  (VIX/VIX3M {rd['pressure']['vix_over_vix3m']})")
    print(f"  tail risk    VVIX {rd['tail']['vvix']}  ({rd['tail']['pct_1y']:.0%} pct)")
    print("\n  next 10 sessions")
    for d in r["days"]:
        ev = ", ".join(e["label"] for e in d["events"])
        print(f"   {d['weekday']} {d['date'][5:]}  {ICON[d['condition']]}  ±{_pct(d['expected_move'])}  1%+ {_pct(d['p_move_1pct'], 0):>4}  {ev}")
    m = r["month"]
    print(f"\n  next {m['sessions']} sessions: 90% range {_pct(m['return_q']['0.05'], 1)} to {_pct(m['return_q']['0.95'], 1)}"
          f"  ·  5% pullback {_pct(m['p_drawdown_5'], 0)}  ·  10% pullback {_pct(m['p_drawdown_10'], 0)}"
          f"  ·  ~{m['expected_big_days']:.0f} days with 1%+ moves  ·  up months historically {_pct(m['p_up'], 0)}")
    if r.get("earnings"):
        _print_earnings(r)
    print()


def _print_earnings(r):
    e = r.get("earnings")
    if not e:
        print(f"  {r['ticker']}: no upcoming earnings date found")
        return
    i = e.get("implied") or {}
    print(f"\n  EARNINGS  {e['date']} {e['timing']}  ->  reaction on {e['reaction_session']}")
    if "model_expected_move" in e:
        print(f"    Voladaily expects   ±{_pct(e['model_expected_move'], 1)}   (a normal day: ±{_pct(e['model_normal_day_sigma'] * 0.7979, 1)})")
    if i:
        print(f"    options imply       ±{_pct(i['implied_move'], 1)}   ({i['method']}; {i['expiry']} straddle {_pct(i['straddle_pct'], 1)} of spot)")
    print(f"    last 12 reports     ±{_pct(e['past_avg_abs_move'], 1)} average, ±{_pct(e['past_median_abs_move'], 1)} median, up {_pct(e['past_up_share'], 0)} of the time")
    if e.get("verdict"):
        print(f"    past ÷ implied      {e['past_over_implied']:.2f}  ->  {e['verdict']}")
    print("    recent reactions    " + "  ".join(f"{p['session'][2:]} {p['move'] * 100:+.1f}%" for p in e["past"][-6:]))


def cmd_earnings(a):
    for t in a.tickers:
        r = forecast.forecast(t.upper())
        print(f"\n{r['ticker']}  (data to close {r['as_of_close']})", end="")
        _print_earnings(r)
    print()


def cmd_score(a):
    tickers = [t.upper() for t in a.tickers] or ["SPY", "QQQ", "IWM", "TLT", "GLD"]
    results = {}
    for t in tickers:
        print(f"scoring {t} ...", flush=True)
        df = model.features(t)
        wf = score.add_vix1d(model.walk_forward(df), df)
        wf.to_pickle(data.CACHE / f"wf_{t}.pkl")
        sb = score.scoreboard(wf)
        sb["recent"] = score.scoreboard(wf.loc["2022-06-01":], specs=("vix_only", "vix1d", "har_vix", "full"))
        sb["events"] = score.event_days(wf, df).round(4).to_dict("records")
        results[t] = sb
    month = None
    if "SPY" in tickers:
        print("month outlook backtest (SPY) ...", flush=True)
        month = forecast.backtest_month("SPY")
        month.to_pickle(data.CACHE / "month_bt_SPY.pkl")
    OUT.mkdir(exist_ok=True)
    (OUT / "scoreboard.json").write_text(json.dumps(results, indent=1, default=str))
    path = report.write(results, month, OUT / "scoreboard.html")
    print(f"wrote {path}")


def cmd_serve(a):
    from . import server

    server.serve(a.port)


def main():
    p = argparse.ArgumentParser(prog="voladaily")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("today"); s.add_argument("ticker", nargs="?", default="SPY"); s.set_defaults(fn=cmd_today)
    s = sub.add_parser("earnings"); s.add_argument("tickers", nargs="+"); s.set_defaults(fn=cmd_earnings)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8769); s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("score"); s.add_argument("tickers", nargs="*"); s.set_defaults(fn=cmd_score)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
