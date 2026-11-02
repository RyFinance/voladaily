"""Local web app: `python -m voladaily serve` -> http://127.0.0.1:8769"""

from __future__ import annotations

import json
import threading
import time
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

from . import data, forecast

WEB = data.ROOT / "web"
_cache: dict[str, tuple[float, dict]] = {}
_locks: dict[str, threading.Lock] = {}
TTL = 30 * 60


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, float) and not np.isfinite(o):
        return None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.strftime("%Y-%m-%d")
    return o


def get_forecast(t: str) -> dict:
    t = t.upper().strip()
    hit = _cache.get(t)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    lock = _locks.setdefault(t, threading.Lock())
    with lock:
        hit = _cache.get(t)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
        r = _clean(forecast.forecast(t, days=21))
        _cache[t] = (time.time(), r)
        return r


def get_scoreboard() -> dict:
    p = data.ROOT / "out" / "scoreboard.json"
    sb = json.loads(p.read_text()) if p.exists() else {}
    mb = data.CACHE / "month_bt_SPY.pkl"
    month = None
    if mb.exists():
        m = pd.read_pickle(mb)
        a5 = (m.dd <= -0.05).astype(float)
        clim = a5.expanding().mean().shift(1).fillna(a5.iloc[:24].mean())
        month = {
            "months": int(len(m)),
            "cover90": float(((m.ret >= m.q05) & (m.ret <= m.q95)).mean()),
            "cover50": float(((m.ret >= m.q25) & (m.ret <= m.q75)).mean()),
            "big_days_pred": float(m.big_days.mean()), "big_days_act": float(m.act_big.mean()),
            "big_days_corr": float(m.big_days.corr(m.act_big)),
            "dd5_brier_shipped": float(((0.5 * m.p_dd5 + 0.5 * clim - a5) ** 2).mean()),
            "dd5_brier_base": float(((clim - a5) ** 2).mean()),
        }
    return _clean({"assets": sb, "month": month})


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(WEB), **k)

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/forecast":
            t = (parse_qs(u.query).get("t") or ["SPY"])[0]
            if not t.replace(".", "").replace("-", "").isalnum() or len(t) > 10:
                return self._json({"error": "bad ticker"}, 400)
            try:
                return self._json(get_forecast(t))
            except Exception as e:
                traceback.print_exc()
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        if u.path == "/api/scoreboard":
            return self._json(get_scoreboard())
        return super().do_GET()


def serve(port: int = 8769):
    threading.Thread(target=lambda: get_forecast("SPY"), daemon=True).start()  # warm the default
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Voladaily on http://127.0.0.1:{port}")
    httpd.serve_forever()
