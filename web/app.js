(function () {
  const $ = s => document.querySelector(s);
  const NAMES = { SPY: ["S&P", "500"], QQQ: ["Nasdaq", "100"], IWM: ["Russell", "2000"], DIA: ["Dow", "Jones"], TLT: ["Treasuries", "20Y+"], GLD: ["Gold", ""] };
  const PAGER = ["SPY", "QQQ", "IWM", "GLD", "TLT"];  // default list for new visitors
  const EV_INFO = {
    cpi: "Consumer inflation", nfp: "Payrolls and unemployment", fomc: "Rate decision 2:00, presser 2:30", post_fomc: "Digesting the Fed",
    gdp: "Economic growth", pce: "The Fed's inflation gauge", ppi: "Producer inflation", retail: "Consumer spending",
    opex: "Monthly options expire", quad: "Quarterly futures and options expiry", election: "Polls close in the evening",
    post_election: "Results trade at the open", earn: "Earnings reaction", post_earn: "Earnings follow-through",
  };
  const BOLT = `<svg class="bolt" viewBox="0 0 24 24" fill="currentColor" aria-label="High risk"><path d="M13.5 2 4 14h6.5L9 22l11-13h-6.8L13.5 2z"/></svg>`;

  const store = { get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
                  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} } };
  let tickers = store.get("vd_tickers", PAGER.slice());
  if (!Array.isArray(tickers) || !tickers.length) tickers = PAGER.slice();
  const start = (new URLSearchParams(location.search).get("t") || "SPY").toUpperCase();
  if (!tickers.includes(start)) { tickers.unshift(start); store.set("vd_tickers", tickers); }
  let cur = tickers.indexOf(start), view = "day", busy = false;
  const cache = {}, pending = {}, sel = {};
  let board = null;

  const pct = (x, d = 2) => (x * 100).toFixed(d) + "%";
  const sgn = (x, d = 2) => (x >= 0 ? "+" : "−") + Math.abs(x * 100).toFixed(d) + "%";
  const cls = x => (x >= 0 ? "up" : "down");
  const D = s => new Date(s + "T12:00:00");
  const fmt = (s, o) => D(s).toLocaleDateString("en-US", o);
  const usd = x => "$" + x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function load(t) {
    if (cache[t]) return Promise.resolve(cache[t]);
    if (!pending[t]) pending[t] = fetch("/api/forecast?t=" + encodeURIComponent(t)).then(r => r.json()).then(j => {
      if (j.error) throw new Error(j.error);
      cache[t] = j;
      return j;
    }).finally(() => { delete pending[t]; });
    return pending[t];
  }
  fetch("/api/scoreboard").then(r => r.json()).then(j => { board = j; }).catch(() => {});

  // ---------- pager + search
  function drawPager() {
    $("#pager").innerHTML = tickers.map((t, i) => `<div class="pg-wrap"><button class="pg${i === cur ? " on" : ""}" data-i="${i}" title="${t}">${t}</button>${tickers.length > 1 ? `<button class="pg-x" data-x="${i}" aria-label="Remove ${t}">×</button>` : ""}</div>`).join("") +
      `<div class="pg-wrap"><button class="pg add" id="pg-add" aria-label="Add a ticker">+</button></div>`;
  }
  function addTicker(t) {
    t = t.trim().toUpperCase().replace(/[^A-Z0-9.\-^=]/g, "");
    if (!t) return;
    let i = tickers.indexOf(t);
    if (i < 0) { tickers.push(t); i = tickers.length - 1; store.set("vd_tickers", tickers); }
    drawPager(); go(i); load(t).catch(fail);
  }
  function removeTicker(i) {
    if (tickers.length <= 1) return;
    const was = tickers[cur];
    tickers.splice(i, 1);
    store.set("vd_tickers", tickers);
    if (i === cur) { cur = Math.min(i, tickers.length - 1); drawPager(); show(cur); history.replaceState(null, "", "?t=" + tickers[cur]); }
    else { cur = tickers.indexOf(was); drawPager(); }
  }
  $("#pager").addEventListener("click", e => {
    const x = e.target.closest(".pg-x"); if (x) { e.stopPropagation(); removeTicker(+x.dataset.x); return; }
    if (e.target.closest("#pg-add")) {
      const wrap = e.target.closest(".pg-wrap");
      if (wrap.querySelector(".pg-in")) return;
      wrap.insertAdjacentHTML("beforeend", `<form class="pg-in"><input placeholder="Ticker" maxlength="8" spellcheck="false" autofocus><button>Add</button></form>`);
      const f = wrap.querySelector(".pg-in"), inp = f.querySelector("input");
      inp.focus();
      f.addEventListener("submit", ev => { ev.preventDefault(); addTicker(inp.value); f.remove(); });
      inp.addEventListener("keydown", ev => { if (ev.key === "Escape") f.remove(); });
      setTimeout(() => document.addEventListener("click", function off(ev) { if (!f.contains(ev.target)) { f.remove(); document.removeEventListener("click", off); } }), 0);
      return;
    }
    const b = e.target.closest(".pg[data-i]"); if (b) go(+b.dataset.i);
  });
  $("#search").addEventListener("submit", e => {
    e.preventDefault();
    const t = $("#q").value.trim().toUpperCase();
    if (!t) return;
    $("#q").value = "";
    addTicker(t);
  });

  // ---------- theme
  const themeBtn = $("#theme");
  const paintTheme = () => { themeBtn.textContent = document.documentElement.dataset.theme === "dark" ? "Light" : "Dark"; };
  themeBtn.addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme !== "dark";
    if (dark) document.documentElement.dataset.theme = "dark"; else delete document.documentElement.dataset.theme;
    try { localStorage.setItem("vd_theme", dark ? "dark" : "light"); } catch (e) {}
    paintTheme();
  });
  paintTheme();

  // ---------- field guide
  const guide = $("#guide");
  const openGuide = () => { guide.hidden = false; $("#g-close").focus(); };
  const closeGuide = () => { guide.hidden = true; };
  $("#info-btn").addEventListener("click", openGuide);
  $("#g-close").addEventListener("click", closeGuide);
  guide.addEventListener("click", e => { if (e.target === guide) closeGuide(); });

  // ---------- helpers
  const risky = d => d.condition === "storm" || d.events.some(e => e.variance_bump_pct >= 35);
  function title(f) {
    const n = NAMES[f.ticker];
    if (n) return n[1] ? `${n[0]} <em>${n[1]}</em>` : n[0];
    return f.name && f.name !== f.ticker ? f.name : f.ticker;
  }

  // ---------- morning brief: a plain read of the next session and the week, under the title
  function brief(f) {
    const d = f.days[0], w = f.weeks[0], r = d.expected_move / f.typical_sigma;
    const size = risky(d) ? "A high-risk" : r > 1.2 ? "A bigger-than-usual" : r < 0.85 ? "A quieter-than-usual" : "A normal-sized";
    const lean = Math.abs(d.exp_return) < 3e-4 ? "with no real lean" : `leaning ${d.exp_return > 0 ? "up" : "down"} <b class="${cls(d.exp_return)}">${sgn(d.exp_return)}</b>`;
    const top = ((d.why || {}).reasons || [])[0];
    const watch = f.days.slice(0, 5).flatMap(x => x.events.filter(e => Math.abs(e.variance_bump_pct) >= 10)
      .map(e => `<span>${e.variance_bump_pct >= 35 ? BOLT : ""}${e.label} <i>${x === d ? "today" : fmt(x.date, { weekday: "short" })}${e.time && /\d/.test(e.time) ? " " + e.time : ""}</i></span>`));
    return `<div class="brief">
      <div class="b-k mono">Morning brief · ${fmt(d.date, { weekday: "short", month: "short", day: "numeric" })}</div>
      <p class="b-head">${size} ${fmt(d.date, { weekday: "long" })}, ${lean}.${top ? ` <span>${top.text}.</span>` : ""}</p>
      <div class="b-facts">
        <div><span class="k mono">Move</span><b>±${pct(d.expected_move, 2)}</b><i>${pct(d.p_up, 0)} chance up · 80% range ${sgn(d.range_80[0], 1)} to ${sgn(d.range_80[1], 1)}</i></div>
        <div><span class="k mono">This week</span><b class="${cls(w.exp_return)}">${sgn(w.exp_return)}</b><i>${pct(w.p_up, 0)} chance up · ${w.sessions} sessions</i></div>
        <div><span class="k mono">Watch</span><p class="b-watch">${watch.length ? watch.join("") : "<span>Nothing scheduled</span>"}</p></div>
      </div>
    </div>`;
  }

  // ---------- one market = one space
  function build(t, f) {
    const el = document.createElement("section");
    el.className = "space";
    el.dataset.t = t;
    if (!f) { el.innerHTML = `<div class="loading-space">Reading <em>&nbsp;${t}</em>…</div>`; return el; }
    const m = f.month;
    el.innerHTML = `<div class="inner">
      <div class="hero">
        <div><h1 class="title">${title(f)}</h1>
          <div class="sub mono"><span><b>${f.ticker}</b></span><span>${usd(f.spot)}</span><span class="${cls(f.change_1d)}">${sgn(f.change_1d)} last session</span><span>VIX ${f.readings.temperature.vix}</span></div></div>
        <div class="hero-r"><p class="mono">Expected return over the next ${m.sessions} sessions, from the size and direction models, walk-forward tested since 2010</p>
          <span class="pill">✦ 21 sessions: ${sgn(m.exp_return)} <i>· ${pct(m.p_up, 0)} up</i></span></div>
      </div>
      ${brief(f)}
      <div class="views"><div class="seg" role="tablist">${["day", "week", "month"].map(v => `<button data-v="${v}" class="${v === view ? "on" : ""}">${v}</button>`).join("")}</div>
        <span class="note mono">${BOLT} = high-risk session · click any day, week or month</span></div>
      <div class="body"><div class="detail"></div><div class="list"></div></div>
      ${record(f)}
      <p class="foot">Voladaily forecasts each session's expected return and the size of its move. Expected returns are small by nature: a few tenths of a percent a day.
        The direction model is a heavily shrunk regression on published effects (pre-Fed drift, turn of the month, short-term reversal, the volatility risk premium);
        the size model is a realized-variance HAR with the VIX curve and the event calendar. Not investment advice.</p>
    </div>`;
    el.querySelector(".seg").addEventListener("click", e => {
      const v = e.target.dataset.v; if (!v || v === view) return;
      view = v;
      document.querySelectorAll(".seg button").forEach(b => b.classList.toggle("on", b.dataset.v === view));
      fill(el, f);
    });
    el.querySelector(".list").addEventListener("click", e => {
      const c = e.target.closest(".cc[data-k]");
      if (c) {
        sel[t + "cal"] = { kind: c.dataset.k, i: +c.dataset.i };
        el.querySelectorAll(".cc").forEach(x => x.classList.toggle("on", x === c));
        detail(el, f);
        return;
      }
      const r = e.target.closest(".row"); if (!r) return;
      sel[t + view] = +r.dataset.i;
      mark(el); detail(el, f);
    });
    fill(el, f);
    return el;
  }

  function items(f) {
    if (view === "day") return f.days.map(d => ({ kind: "day", d }));
    if (view === "week") return f.weeks.map(w => ({ kind: "week", w, risky: f.days.some(d => d.date >= w.start && d.date <= w.end && risky(d)) }));
    return [{ kind: "month", m: f.month }];
  }

  function calendar(f) {
    const byDate = Object.fromEntries(f.days.map((d, i) => [d.date, i]));
    const iso = d => d.toISOString().slice(0, 10);
    const cs = calSel(f.ticker);
    let h = `<div class="calg"><div class="ch mono">Mon</div><div class="ch mono">Tue</div><div class="ch mono">Wed</div><div class="ch mono">Thu</div><div class="ch mono">Fri</div><div class="ch mono wk">Week</div>`;
    f.weeks.forEach((w, wi) => {
      const s0 = D(w.start); s0.setDate(s0.getDate() - ((s0.getDay() + 6) % 7));
      for (let k = 0; k < 5; k++) {
        const d = new Date(s0); d.setDate(s0.getDate() + k);
        const key = iso(d), i = byDate[key];
        if (i == null) {
          h += key < f.days[0].date || key > f.days[f.days.length - 1].date ? `<div class="cc blank"></div>` : `<div class="cc closed"><span class="cd">${fmt(key, { day: "numeric", month: "short" })}</span><span class="cx mono">Closed</span></div>`;
          continue;
        }
        const x = f.days[i], on = cs.kind === "day" && cs.i === i;
        h += `<button class="cc${on ? " on" : ""}" data-k="day" data-i="${i}"><span class="cd"><em>${fmt(key, { day: "numeric" })}</em> ${fmt(key, { month: "short" })}${risky(x) ? BOLT : ""}</span>
          <span class="cv ${cls(x.exp_return)}">${sgn(x.exp_return)}</span><span class="cs mono">${pct(x.p_up, 0)} up · ±${pct(x.expected_move, 1)}</span>
          <span class="ce mono">${x.events.map(e => e.label).join(" · ")}</span></button>`;
      }
      const on = cs.kind === "week" && cs.i === wi;
      h += `<button class="cc wkc${on ? " on" : ""}" data-k="week" data-i="${wi}"><span class="cd mono">Week ${wi + 1}</span><span class="cv ${cls(w.exp_return)}">${sgn(w.exp_return)}</span><span class="cs mono">${pct(w.p_up, 0)} up</span></button>`;
    });
    const m = f.month, on = cs.kind === "month";
    h += `<button class="cc mon${on ? " on" : ""}" data-k="month" data-i="0"><span class="cd mono">Next ${m.sessions} sessions</span><span class="cv ${cls(m.exp_return)}">${sgn(m.exp_return)}</span><span class="cs mono">${pct(m.p_up, 0)} up · 5% pullback ${pct(m.p_drawdown_5, 0)}</span></button></div>`;
    return h;
  }

  function calSel(t) { return sel[t + "cal"] || { kind: "day", i: 0 }; }

  function fill(el, f) {
    const list = el.querySelector(".list"), its = items(f);
    if (view === "day") {
      list.innerHTML = calendar(f);
      list.classList.remove("fadein"); void list.offsetWidth; list.classList.add("fadein");
      detail(el, f);
      return;
    }
    let html = "", lastWeek = null;
    its.forEach((it, i) => {
      if (it.kind === "day") {
        const d = it.d, wk = f.weeks.find(w => d.date >= w.start && d.date <= w.end);
        if (wk && wk.start !== lastWeek) { lastWeek = wk.start; html += `<div class="grp">Week of ${fmt(wk.start, { month: "short", day: "numeric" })} · ${sgn(wk.exp_return)}</div>`; }
        html += `<div class="row" data-i="${i}"><div><span class="nm"><em>${fmt(d.date, { weekday: "short" })}</em> ${fmt(d.date, { day: "numeric", month: "short" })}${risky(d) ? BOLT : ""}</span>
          <span class="meta">${d.events.length ? d.events.map(e => e.label).join(" · ") : "&nbsp;"}</span></div>
          <div class="val ${cls(d.exp_return)}">${sgn(d.exp_return)}<small>${pct(d.p_up, 0)} up · ±${pct(d.expected_move, 1)}</small></div></div>`;
      } else if (it.kind === "week") {
        const w = it.w;
        html += `<div class="row" data-i="${i}"><div><span class="nm">${fmt(w.start, { month: "short", day: "numeric" })} <em>to</em> ${fmt(w.end, { month: "short", day: "numeric" })}${it.risky ? BOLT : ""}</span>
          <span class="meta">${w.events.length ? [...new Set(w.events)].join(" · ") : "Nothing scheduled"} · ${w.sessions} sessions</span></div>
          <div class="val ${cls(w.exp_return)}">${sgn(w.exp_return)}<small>${pct(w.p_up, 0)} up</small></div></div>`;
      } else {
        const m = it.m, last = f.days[Math.min(f.days.length, m.sessions) - 1];
        html += `<div class="row sel" data-i="0"><div><span class="nm">${fmt(f.days[0].date, { month: "short", day: "numeric" })} <em>to</em> ${fmt(last.date, { month: "short", day: "numeric" })}</span>
          <span class="meta">${m.sessions} sessions</span></div><div class="val ${cls(m.exp_return)}">${sgn(m.exp_return)}<small>${pct(m.p_up, 0)} up</small></div></div>
          <div class="mstats"><div><b>${pct(m.p_drawdown_5, 0)}</b><span>chance of a 5% pullback</span></div><div><b>${pct(m.p_drawdown_10, 0)}</b><span>chance of a 10% pullback</span></div>
          <div><b>${m.expected_big_days.toFixed(1)}</b><span>sessions moving 1%+</span></div><div><b>${pct(m.range_median, 1)}</b><span>typical high to low</span></div></div>
          ${fan(m)}`;
      }
    });
    list.innerHTML = html;
    list.classList.remove("fadein"); void list.offsetWidth; list.classList.add("fadein");
    mark(el); detail(el, f);
  }

  function fan(m) {
    const f = m.fan, W = 600, H = 220, n = f["0.5"].length;
    const all = [].concat(f["0.05"], f["0.95"], [1]), lo = Math.min(...all), hi = Math.max(...all);
    const X = i => (i / n) * W, Y = v => H - 10 - ((v - lo) / (hi - lo)) * (H - 20);
    const pts = k => [[0, 1]].concat(f[k].map((v, i) => [i + 1, v])).map(([i, v]) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`);
    const band = (a, b) => "M" + pts(a).join(" L") + " L" + pts(b).reverse().join(" L") + "Z";
    return `<svg id="fan" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><line x1="0" x2="${W}" y1="${Y(1)}" y2="${Y(1)}" stroke="#B9B3C2" stroke-dasharray="3 5"/>
      <path d="${band("0.05", "0.95")}" fill="rgba(90,61,216,.10)"/><path d="${band("0.25", "0.75")}" fill="rgba(90,61,216,.22)"/>
      <polyline points="${pts("0.5").join(" ")}" fill="none" stroke="#1C1424" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
  }

  function mark(el) {
    const i = sel[el.dataset.t + view] || 0;
    el.querySelectorAll(".row").forEach(r => r.classList.toggle("sel", +r.dataset.i === i));
  }

  function detail(el, f) {
    const box = el.querySelector(".detail");
    let i = sel[f.ticker + view] || 0, it = items(f)[i] || items(f)[0];
    if (view === "day") {
      const cs = calSel(f.ticker);
      i = cs.i;
      it = cs.kind === "day" ? { kind: "day", d: f.days[i] }
        : cs.kind === "week" ? { kind: "week", w: f.weeks[i], risky: f.days.some(d => d.date >= f.weeks[i].start && d.date <= f.weeks[i].end && risky(d)) }
        : { kind: "month", m: f.month };
    }
    let h = "";
    if (it.kind === "day") {
      const d = it.d, w = d.why || { base: 0, reasons: [], other: 0 };
      const rs = w.reasons.concat(Math.abs(w.other) > 1e-5 ? [{ text: "Everything else", effect: w.other }] : []);
      const mx = Math.max(Math.abs(w.base), ...rs.map(r => Math.abs(r.effect)), 1e-6);
      const bar = v => `<span class="bar"><i style="${v >= 0 ? "left:50%" : `right:50%`};width:${(Math.abs(v) / mx) * 50}%;background:var(--${v >= 0 ? "up" : "down"})"></i></span>`;
      const lead = rs[0] ? rs[0].text.split(":")[0].toLowerCase() : "an average day";
      const lo = d.range_80[0], hi = d.range_80[1], span = Math.max(Math.abs(lo), Math.abs(hi)) * 1.25, pos = x => 50 + (x / span) * 50;
      h = `<div class="d-k mono">${i === 0 ? "Next session" : `Session ${i + 1} of ${f.days.length}`}${risky(d) ? " · high risk" : ""}</div>
        <div class="d-when">${fmt(d.date, { weekday: "long" })} <em>${fmt(d.date, { month: "long", day: "numeric" })}</em></div>
        <div class="d-num ${cls(d.exp_return)}">${d.exp_return >= 0 ? "+" : "−"}${Math.abs(d.exp_return * 100).toFixed(2)}<small>%</small></div>
        <div class="d-line">Expected return, close to close. ${pct(d.p_up, 0)} chance it finishes up. The biggest factor: ${lead}.</div>

        <div class="sec-k"><span>Why</span><span>effect on expected return</span></div>
        <div class="why">
          <div class="wy"><span>An average ${f.ticker} day</span>${bar(w.base)}<span class="e">${sgn(w.base, 3)}</span></div>
          ${rs.map(r => `<div class="wy"><span>${r.text}</span>${bar(r.effect)}<span class="e ${cls(r.effect)}">${sgn(r.effect, 3)}</span></div>`).join("")}
          <div class="wy tot"><span>Forecast</span><span></span><span class="e ${cls(d.exp_return)}">${sgn(d.exp_return, 3)}</span></div>
        </div>

        <div class="sec-k"><span>How much it could move</span><span>either way</span></div>
        <div class="size"><span class="big">±${pct(d.expected_move)}</span><span class="lbl">typical move · about ±${usd(f.spot * d.expected_move)}<br>${(d.sigma / (f.typical_sigma || d.sigma)).toFixed(1)}× an ordinary day for ${f.ticker}</span></div>
        <div class="rng"><div class="ax"></div><div class="bd" style="left:${pos(lo)}%;width:${pos(hi) - pos(lo)}%"></div><div class="z" style="left:50%"></div></div>
        <div class="rng-l"><span>${usd(f.spot * (1 + lo))} (${sgn(lo, 1)})</span><span>80% range</span><span>${usd(f.spot * (1 + hi))} (${sgn(hi, 1)})</span></div>
        <div class="facts"><div class="fact"><div class="k mono">1%+ move</div><div class="v">${pct(d.p_move_1pct, 0)}</div></div>
          <div class="fact"><div class="k mono">2%+ move</div><div class="v">${pct(d.p_move_2pct, 0)}</div></div>
          <div class="fact"><div class="k mono">5%+ move</div><div class="v">${d.p_move_5pct < 0.01 ? "<1%" : pct(d.p_move_5pct, 0)}</div></div></div>

        ${d.events.length ? `<div class="sec-k"><span>Scheduled</span><span>volatility effect</span></div><div class="evs" style="margin-top:0">${d.events.map(e => `<div class="ev"><span class="t mono">${e.time || "—"}</span><span class="n">${e.label}<span>${EV_INFO[e.event] || ""}</span></span>
          <span class="x mono">${e.variance_bump_pct >= 0 ? "+" : "−"}${Math.abs(Math.round(e.variance_bump_pct))}%</span></div>`).join("")}</div>` : ""}`;
    } else if (it.kind === "week") {
      const w = it.w, days = f.days.filter(d => d.date >= w.start && d.date <= w.end);
      h = `<div class="d-k mono">Week ${i + 1} of ${f.weeks.length}${it.risky ? " · high risk" : ""}</div>
        <div class="d-when">${fmt(w.start, { month: "long", day: "numeric" })} <em>to</em> ${fmt(w.end, { month: "long", day: "numeric" })}</div>
        <div class="d-num ${cls(w.exp_return)}">${w.exp_return >= 0 ? "+" : "−"}${Math.abs(w.exp_return * 100).toFixed(2)}<small>%</small></div>
        <div class="d-line">Expected return for the week. ${pct(w.p_up, 0)} of 4,000 simulated paths finish the week higher.</div>
        <div class="facts"><div class="fact"><div class="k mono">Chance up</div><div class="v">${pct(w.p_up, 0)}</div><div class="s">${w.sessions} sessions</div></div>
          <div class="fact"><div class="k mono">80% range</div><div class="v">${sgn(w.q10, 1)} / ${sgn(w.q90, 1)}</div><div class="s">${usd(f.spot * (1 + w.q10))} – ${usd(f.spot * (1 + w.q90))}</div></div>
          <div class="fact"><div class="k mono">Week vol</div><div class="v">±${pct(w.sigma * 0.7979, 1)}</div><div class="s">typical move</div></div></div>
        <div class="evs">${days.map(d => `<div class="ev"><span class="t mono">${fmt(d.date, { weekday: "short" })}</span><span class="n">${sgn(d.exp_return)}<span>${d.events.map(e => e.label).join(" · ") || "Nothing scheduled"}</span></span>
          <span class="x mono">±${pct(d.expected_move, 1)}</span></div>`).join("")}</div>`;
    } else {
      const m = it.m;
      h = `<div class="d-k mono">Next ${m.sessions} sessions</div>
        <div class="d-when"><em>The month</em> ahead</div>
        <div class="d-num ${cls(m.exp_return)}">${m.exp_return >= 0 ? "+" : "−"}${Math.abs(m.exp_return * 100).toFixed(2)}<small>%</small></div>
        <div class="d-line">Expected return over the next ${m.sessions} sessions. ${pct(m.p_up, 0)} of simulated paths finish higher; historically ${pct(m.p_up_base_rate, 0)} of 21-session stretches have.</div>
        <div class="facts"><div class="fact"><div class="k mono">Chance up</div><div class="v">${pct(m.p_up, 0)}</div><div class="s">base rate ${pct(m.p_up_base_rate, 0)}</div></div>
          <div class="fact"><div class="k mono">90% range</div><div class="v">${sgn(m.return_q["0.05"], 1)} / ${sgn(m.return_q["0.95"], 1)}</div><div class="s">${usd(f.spot * (1 + m.return_q["0.05"]))} – ${usd(f.spot * (1 + m.return_q["0.95"]))}</div></div>
          <div class="fact"><div class="k mono">Median path</div><div class="v">${sgn(m.median, 1)}</div><div class="s">${usd(f.spot * (1 + m.median))}</div></div></div>`;
    }
    box.innerHTML = `<div class="slide">${h}</div>`;
  }

  function record(f) {
    const s = f.direction_score || {}, a = board && board.assets && board.assets[f.ticker], v = a && a.models.vix_only;
    const row = (k, sub, val) => `<div class="rec-row"><div class="k">${k}<span>${sub}</span></div><div class="v">${val}</div></div>`;
    return `<div class="record"><div><h3>Track <em>record</em></h3><p>Out of sample since ${(s.start || "2010").slice(0, 4)}: every forecast used only data from before that day.
      Direction is hard. The edge is real but small, and the sign alone does not beat "markets usually rise". We show it anyway.</p></div><div>
      ${row("Expected return accuracy", "out-of-sample R² vs the historical average (above 0 = better)", s.r2_oos != null ? (s.r2_oos * 100).toFixed(2) + "%" : "—")}
      ${row("Daily direction", `called right · always-up would score ${pct(s.base_up_day || 0, 1)}`, s.hit_day != null ? pct(s.hit_day, 1) : "—")}
      ${row("Weekly direction", `called right · always-up would score ${pct(s.base_up_week || 0, 1)}`, s.hit_week != null ? pct(s.hit_week, 1) : "—")}
      ${row("Monthly direction", `called right · always-up would score ${pct(s.base_up_month || 0, 1)}`, s.hit_month != null ? pct(s.hit_month, 1) : "—")}
      ${row("Days forecast above average", "their actual average return, vs the rest", s.avg_ret_when_above != null ? `${sgn(s.avg_ret_when_above, 3)} vs ${sgn(s.avg_ret_when_below, 3)}` : "—")}
      ${v ? row("Move-size accuracy", `vs the VIX alone (t ${v.dm_t_vs_ref.toFixed(1)})`, `${v.ref_gain_pct.toFixed(1)}% better`) : ""}
      ${a ? row("80% ranges held", "target 80%", pct(a.models.full.cover_80, 1)) : ""}</div></div>`;
  }

  // ---------- swipe between markets, like macOS desktops
  const stage = $("#stage");
  function topline(f) {
    $("#top-mid").textContent = f ? `Data to the close of ${fmt(f.as_of_close, { month: "short", day: "numeric" })} · next 21 sessions` : "";
  }
  function show(i) {
    const t = tickers[i];
    const el = build(t, cache[t]);
    stage.innerHTML = ""; stage.appendChild(el);
    topline(cache[t]);
    if (!cache[t]) load(t).then(f => { if (tickers[cur] === t) { const n = build(t, f); stage.replaceChild(n, stage.firstChild); topline(f); } }).catch(fail);
  }
  function go(i) {
    if (busy || i === cur || i < 0 || i >= tickers.length) return;
    const dir = i > cur ? 1 : -1;
    const prev = stage.firstChild;
    cur = i; drawPager();
    const t = tickers[i];
    history.replaceState(null, "", "?t=" + t);
    const next = build(t, cache[t]);
    stage.appendChild(next);
    busy = true;
    const ease = "cubic-bezier(.7,0,.25,1)", dur = 820;
    prev.classList.add("moving"); next.classList.add("moving");
    const a = prev.animate([{ transform: "translateX(0) scale(1)" }, { transform: "translateX(0) scale(.92)", offset: .22 }, { transform: `translateX(${-dir * 106}%) scale(.92)` }], { duration: dur, easing: ease, fill: "forwards" });
    next.animate([{ transform: `translateX(${dir * 106}%) scale(.92)` }, { transform: "translateX(0) scale(.92)", offset: .78 }, { transform: "translateX(0) scale(1)" }], { duration: dur, easing: ease, fill: "forwards" });
    a.onfinish = () => {
      prev.remove(); next.classList.remove("moving"); next.getAnimations().forEach(x => x.cancel());
      busy = false;
    };
    topline(cache[t]);
    if (!cache[t]) load(t).then(f => { if (tickers[cur] !== t) return; const n = build(t, f); const swap = () => { stage.replaceChild(n, stage.lastChild); topline(f); }; busy ? setTimeout(swap, dur) : swap(); }).catch(fail);
  }
  function fail(e) { $("#err").hidden = false; $("#err").textContent = e.message; setTimeout(() => { $("#err").hidden = true; }, 5000); }

  // trackpad: a sideways swipe moves one market
  let acc = 0, lockUntil = 0;
  window.addEventListener("wheel", e => {
    if (Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return;
    e.preventDefault();
    if (performance.now() < lockUntil) return;
    acc += e.deltaX;
    if (Math.abs(acc) > 70) { go(cur + (acc > 0 ? 1 : -1)); acc = 0; lockUntil = performance.now() + 1000; }
  }, { passive: false });
  let tx = null;
  window.addEventListener("touchstart", e => { tx = e.touches[0].clientX; }, { passive: true });
  window.addEventListener("touchend", e => { if (tx == null) return; const dx = e.changedTouches[0].clientX - tx; if (Math.abs(dx) > 70) go(cur + (dx < 0 ? 1 : -1)); tx = null; });
  document.addEventListener("keydown", e => {
    if (e.target.tagName === "INPUT") return;
    if (e.key === "Escape") { closeGuide(); return; }
    if (e.key === "?" || e.key === "i") { guide.hidden ? openGuide() : closeGuide(); return; }
    if (!guide.hidden) return;
    if (e.key === "ArrowRight") go(cur + 1);
    if (e.key === "ArrowLeft") go(cur - 1);
    const el = stage.lastChild, f = cache[tickers[cur]];
    if (!f || !el || !el.querySelector) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (view === "day") {
        const cs = calSel(tickers[cur]), i = cs.kind === "day" ? cs.i : 0;
        sel[tickers[cur] + "cal"] = { kind: "day", i: Math.max(0, Math.min(f.days.length - 1, i + (e.key === "ArrowDown" ? 5 : -5))) };
        fill(el, f);
        return;
      }
      const n = items(f).length, k = tickers[cur] + view;
      sel[k] = Math.max(0, Math.min(n - 1, (sel[k] || 0) + (e.key === "ArrowDown" ? 1 : -1)));
      mark(el); detail(el, f);
    }
    const map = { d: "day", w: "week", m: "month" };
    if (map[e.key]) { view = map[e.key]; el.querySelectorAll(".seg button").forEach(b => b.classList.toggle("on", b.dataset.v === view)); fill(el, f); }
  });
  setTimeout(() => { $("#hint").style.opacity = 0; }, 6000);

  drawPager();
  show(cur);
  // warm the other markets so swipes land on finished pages
  load(tickers[cur]).then(() => tickers.reduce((p, t) => p.then(() => load(t).catch(() => {})), Promise.resolve())).catch(fail);
})();
