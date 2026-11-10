import { bars, colorKey, diverging, heatmap, hideTip, histogram, lines, waterfall } from "./charts.js";

const D = await fetch("data.json").then((r) => r.json());
const $ = (id) => document.getElementById(id);

const TECHS = { solar: "Solar", wind: "Wind" };
const STEPS = [
  ["base", "Baseload forward"], ["profile", "Profile"], ["correlation", "Correlation"],
  ["cannibalisation", "Cannibalisation"], ["risk", "Risk premium"], ["balancing", "Balancing"],
  ["hedging", "Hedge cost"], ["credit", "Credit"], ["origin", "Guarantees of origin"],
];
const SETS = { lignite: "Lignite", coal: "Hard coal", gas: "Gas", renewables: "Wind and solar", other: "Trade and other" };
const LEFT = [["sd_slope", "Cannibalisation slope"], ["sd_year", "Capture rate, year to year"],
  ["sd_shape", "Hourly shape"], ["sd_volume", "Volume"]];

const INPUTS = { gas: "Gas, €/MWh", carbon: "Carbon, €/t", coal: "Coal, €/t" };

const eur = (v, d = 2) => `€${v.toFixed(d)}`;
const pct = (v) => `${Math.round(100 * v)}%`;
const signed = (v) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2)}`;
const slope = (m) => `${signed(m.beta)} ± ${m.beta_se.toFixed(2)}`;
const date = (d) => new Date(d).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const yy = (year) => `’${String(year).slice(2)}`;

// --- State lives in the URL: #view?tech=solar&site=south&tenor=10 -----------------------------

const state = {
  view: "price", tech: "solar", site: D.meta.headline.solar, tenor: D.meta.years.length, year: String(D.meta.years[0]),
};

function readHash() {
  const [view, query] = location.hash.slice(1).split("?");
  if (VIEWS[view]) state.view = view;
  const q = new URLSearchParams(query);
  if (TECHS[q.get("tech")]) state.tech = q.get("tech");
  if (D.meta.sites[q.get("site")]) state.site = q.get("site");
  const t = +q.get("tenor");
  if (t >= 1 && t <= D.meta.years.length) state.tenor = t;
  if (D.meta.years.includes(+q.get("year"))) state.year = q.get("year");
}

function writeHash() {
  const q = VIEWS[state.view].controls ? `?tech=${state.tech}&site=${state.site}&tenor=${state.tenor}`
    : state.view === "curve" ? `?year=${state.year}` : "";
  history.replaceState(null, "", `#${state.view}${q}`);
}

const scenario = () => D.scenarios[state.tech][state.site][state.tenor - 1];
const stat = (k, v) => `<div class="stat"><div class="v">${v}</div><div class="k">${k}</div></div>`;
const table = (head, rows) => `<table><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>
  ${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`;

// --- Views ---------------------------------------------------------------------------------------

const VIEWS = {
  price: { title: "Price", controls: true, render: price },
  hedge: { title: "Hedge", controls: true, render: hedge },
  curve: { title: "Curve", render: curve },
  capture: { title: "Capture", render: capture },
  fundamentals: { title: "Fundamentals", render: fundamentals },
  fairvalue: { title: "Fair value", render: fairValue },
  options: { title: "Options", controls: true, render: options },
  data: { title: "Data", render: data },
};

function price(el) {
  const v = scenario();
  const start = new Date(D.meta.ppa_start).toLocaleDateString("en-GB", { month: "short", year: "numeric" });
  el.innerHTML = `
    <div class="hero">
      <div class="big">${eur(v.price)}<small>/MWh</small></div>
      <div class="sub">${state.tenor}-year ${state.tech} PPA in the ${D.meta.sites[state.site].toLowerCase()},
        ${D.meta.capacity_mw} MW from ${start}</div>
    </div>
    <section><div class="chart" id="waterfall"></div></section>
    <div class="grid">
      <section><h2>By tenor</h2><div class="chart" id="tenors"></div></section>
      <section><h2>Structures</h2>${table(["", "€/MWh", "Generator P10–P90"], [
        ["Pay-as-produced", v.price.toFixed(2), v.price.toFixed(2)],
        [`Non-negative, ${pct(1 - v.negative.volume_share)}`, v.negative.price.toFixed(2),
          v.negative.price.toFixed(2)],
        [`Baseload, ${v.hedge[0].p50_mw.toFixed(1)} MW`, v.baseload.price.toFixed(2),
          `${v.baseload.p10.toFixed(2)}–${v.baseload.p90.toFixed(2)}`],
      ])}</section>
    </div>`;
  const rows = STEPS.map(([k, label]) => ({ label, value: v.steps[k], total: k === "base" }));
  rows.push({ label: "Price", value: v.price, total: true });
  waterfall($("waterfall"), rows, (x, total) => (total ? x.toFixed(2) : signed(x)));
  const prices = D.scenarios[state.tech][state.site].map((s) => s.price);
  const xs = prices.map((_, i) => i + 1);
  lines($("tenors"), xs, [{ name: "Fair price", color: "var(--accent)", values: prices }], {
    height: 180, dot: state.tenor - 1,
    yFmt: (y, tip) => (tip ? eur(y) : y.toFixed(0)), xFmt: (x, tip) => (tip ? `${x} years` : `${x}y`),
    xTicks: [1, 4, 7, 10], onPick: (i) => { state.tenor = i + 1; render(); },
  });
}

function hedge(el) {
  const v = scenario(), bt = D.forwards.backtest[state.tech];
  el.innerHTML = `
    <section><div class="stats">
      ${stat("Unhedged volatility", `€${v.risk.sd_open.toFixed(1)}m`)}
      ${stat("Hedged volatility", `€${v.risk.sd_hedged.toFixed(1)}m`)}
    </div></section>
    <div class="grid">
      <section><h2>Baseload to sell, MW</h2><div class="chart" id="hedge"></div></section>
      <section><h2>Hedged lifetime P&amp;L, €m</h2><div class="chart" id="pnl"></div></section>
    </div>
    <div class="grid">
      <section><h2>What the hedge leaves, €m</h2>${table(["", "Volatility"],
        LEFT.map(([k, label]) => [label, v.risk[k].toFixed(1)]))}</section>
      <section><h2>Risk premium by confidence, €/MWh</h2>${table(["", "Premium", "Price"],
        v.premiums.map((p) => [`P${p.confidence}`, p.premium.toFixed(2), p.price.toFixed(2)]))}</section>
    </div>
    <section><h2>Backtest, one-year ${state.tech} PPA bought each October, P&amp;L €m</h2>${table(
      ["", "Unhedged", "Volume hedge", "Value hedge"],
      [...bt.rows.map((r) => [r.year, r.open.toFixed(1), r.volume_hedge.toFixed(1), r.value_hedge.toFixed(1)]),
        ["Volatility", bt.sd.open.toFixed(1), bt.sd.volume_hedge.toFixed(1), bt.sd.value_hedge.toFixed(1)]])}</section>`;
  bars($("hedge"), v.hedge.map((h) => ({ label: yy(h.year), value: h.mw, year: h.year })),
    { tipFmt: (d) => `Cal-${String(d.year).slice(2)} · ${d.value.toFixed(1)} MW` });
  histogram($("pnl"), v.pnl.edges, v.pnl.hedged, { xFmt: (x) => x.toFixed(0) });
}

function curve(el) {
  const c = D.curve, f = D.forwards;
  const start = new Date(c.daily.start);
  const day = (i) => new Date(start.getTime() + i * 864e5);
  const xs = c.daily.values.map((_, i) => i);
  const dayOf = (iso) => Math.round((new Date(iso) - start) / 864e5);
  const hours = [...Array(24).keys()];
  const shape = hours.map((h) => c.profile.values.reduce((a, row) => a + row[h], 0) / 12);
  const years = Object.fromEntries(D.meta.years.map((y) => [y, String(y)]));
  el.innerHTML = `
    <section><div class="head"><h2>Hourly curve, €/MWh</h2><div class="choice" id="year" role="radiogroup"
      aria-label="Delivery year"></div></div><div class="chart" id="carpet"></div><div class="chart key" id="key"></div></section>
    <section><h2>Daily curve and futures, €/MWh</h2><div class="chart" id="hpfc"></div></section>
    <div class="grid">
      <section><h2>Cal-${String(c.profile.year).slice(2)} day, €/MWh</h2><div class="chart" id="day"></div></section>
      <section><h2>Futures</h2>${table(["", "Quote", "Curve"],
        c.quotes.filter((q) => !q.extrapolated).map((q) => [q.contract, q.price.toFixed(2), q.model.toFixed(2)]))}</section>
    </div>
    <section><h2>Next calendar year since ${f.first.slice(0, 4)}, €/MWh</h2><div class="chart" id="history"></div></section>
    <div class="grid">
      <section><h2>Realised volatility by time to delivery, last ${f.window_months} months</h2><div class="chart" id="term"></div></section>
      <section><div class="stats">
        ${stat("Next and third year correlation", f.corr_1_3.toFixed(2))}
      </div></section>
    </div>`;

  choice($("year"), years, "year");
  const first = new Date(c.hourly.start);
  const offset = Math.round((new Date(`${state.year}-01-01`) - first) / 864e5);
  const rows = c.hourly.values.slice(offset, offset + Math.round((new Date(`${+state.year + 1}-01-01`)
    - new Date(`${state.year}-01-01`)) / 864e5));
  const flat = rows.flat().filter((v) => v != null).sort((a, b) => a - b);
  const hi = flat[Math.floor(0.99 * (flat.length - 1))], lo = Math.min(flat[0], -10);
  const css = getComputedStyle(document.documentElement);
  const tokens = (k) => css.getPropertyValue(k).trim().split(/\s+/);
  const color = diverging(lo, hi, { zero: tokens("--heat-zero")[0], neg: tokens("--heat-neg"), pos: tokens("--heat-pos") });
  const date0 = new Date(`${state.year}-01-01`);
  const cell = (i) => new Date(date0.getTime() + i * 864e5);
  heatmap($("carpet"), rows, {
    color, yTicks: [0, 6, 12, 18],
    xTicks: rows.map((_, i) => i).filter((i) => cell(i).getDate() === 1)
      .map((i) => ({ i, label: cell(i).toLocaleDateString("en-GB", { month: "short" }) })),
    tipFmt: (i, h, v) => `${cell(i).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })}`
      + ` · ${String(h).padStart(2, "0")}:00 · ${eur(v, 1)}`,
  });
  colorKey($("key"), lo, hi, color, (v) => v.toFixed(0));

  const y0 = dayOf(`${state.year}-01-01`), y1 = dayOf(`${+state.year + 1}-01-01`) - 1;
  const yx = xs.slice(y0, y1 + 1);
  const steps = c.quotes.filter((q) => dayOf(q.start) <= y1 && dayOf(q.end) > y0)
    .map((q) => ({ x0: Math.max(dayOf(q.start), y0), x1: Math.min(dayOf(q.end) - 1, y1), y: q.price, q,
      color: q.extrapolated ? "var(--muted)" : "var(--ink)" }));
  lines($("hpfc"), yx, [{ name: "Curve", color: "var(--accent)", values: c.daily.values.slice(y0, y1 + 1) }], {
    height: 260, xTicks: yx.filter((i) => day(i).getDate() === 1 && day(i).getMonth() % 3 === 0),
    xFmt: (i) => day(i).toLocaleDateString("en-GB", { month: "short" }), yFmt: (y) => y.toFixed(0), steps,
    tipFmt: (k) => [date(day(yx[k])), eur(c.daily.values[yx[k]]),
      ...steps.filter((s) => s.x0 <= yx[k] && yx[k] <= s.x1).map((s) => `${s.q.contract} ${s.q.price.toFixed(2)}`)].join(" · "),
  });
  lines($("day"), hours, [{ name: "Day", color: "var(--accent)", values: shape }], {
    height: 180, xTicks: [0, 6, 12, 18, 23], xFmt: (h) => `${String(h).padStart(2, "0")}:00`,
    yFmt: (y, tip) => (tip ? eur(y) : y.toFixed(0)),
  });
  const hx = f.next_calendar.dates.map((_, i) => i);
  const firstOfYear = hx.filter((i) => i && f.next_calendar.dates[i].slice(0, 4) !== f.next_calendar.dates[i - 1].slice(0, 4)
    && +f.next_calendar.dates[i].slice(0, 4) % 2 === 0);
  lines($("history"), hx, [{ name: "Next year", color: "var(--accent)", values: f.next_calendar.values }], {
    height: 220, xTicks: firstOfYear,
    xFmt: (i, tip) => (tip ? date(f.next_calendar.dates[i]) : f.next_calendar.dates[i].slice(0, 4)),
    yFmt: (y, tip) => (tip ? eur(y) : y.toFixed(0)),
  });
  bars($("term"), f.term_recent.map((b) => ({ label: b.bucket, value: b.vol })), { valueFmt: pct });
}

function capture(el) {
  const a = D.capture.annual;
  const lastYear = a.at(-1).year;
  const years = [...a.map((r) => r.year), ...Object.keys(D.capture.solar.projection).map(Number).filter((y) => y > lastYear)];
  const rate = (k) => years.map((y) => a.find((r) => r.year === y)?.[k] ?? D.capture[k].projection[y]);
  const bt = D.capture.solar.backtest;
  const btX = bt.months.map((_, i) => i);
  el.innerHTML = `
    <section><h2>Capture rate, history and projection</h2><div class="chart" id="rates"></div></section>
    <div class="grid">
      <section><h2>Solar, one month ahead</h2><div class="chart" id="backtest"></div></section>
      <section><h2>Capture rate error</h2>${table(["", "Month ahead", "Seasonal naive", "Year ahead"],
        ["solar", "wind"].map((t) => [TECHS[t], D.capture[t].backtest.mae.toFixed(3), D.capture[t].backtest.naive_mae.toFixed(3),
          D.capture[t].year_ahead.sd.toFixed(3)]))}</section>
    </div>
    <section><h2>Slope by months of history, and the ${D.meta.years.length}-year price, €/MWh</h2>${table(
      ["", "Solar slope", "Solar price", "Wind slope", "Wind price"],
      [...D.capture.solar.windows.map((s, i) => [s.months, slope(s), s.price.toFixed(2),
        slope(D.capture.wind.windows[i]), D.capture.wind.windows[i].price.toFixed(2)]),
      ["Fundamental model, nothing else built", signed(D.fair_value.buildout.solar.slope), "",
        signed(D.fair_value.buildout.wind.slope), ""]])}</section>`;
  lines($("rates"), years, [
    { name: "Solar", color: "var(--accent)", values: rate("solar") },
    { name: "Wind", color: "var(--ink)", values: rate("wind") },
  ], { height: 240, yFmt: (y) => y.toFixed(2), xTicks: years.filter((y) => y % 5 === 0) });
  lines($("backtest"), btX, [
    { name: "Actual", color: "var(--ink)", values: bt.actual },
    { name: "Model", color: "var(--accent)", values: bt.predicted },
  ], { height: 180, yFmt: (y) => y.toFixed(1), xFmt: (i, tip) => (tip ? bt.months[i] : bt.months[i].slice(0, 4)),
    xTicks: btX.filter((i) => bt.months[i].endsWith("-01") && +bt.months[i].slice(0, 4) % 2 === 1) });
}

function fundamentals(el) {
  const f = D.fundamental, m = f.monthly;
  const mx = m.months.map((_, i) => i);
  const hours = [...Array(24).keys()];
  el.innerHTML = `
    <section><h2>Monthly price, €/MWh</h2><div class="chart" id="fmonthly"></div></section>
    <div class="grid">
      <section><h2>Average day, ${f.day.year}, €/MWh</h2><div class="chart" id="fday"></div></section>
      <section><h2>Solar capture and negative hours</h2>${table(["", "Capture", "Model", "Negative", "Model"],
        f.years.map((r) => [r.year, r.solar_capture.toFixed(2), r.solar_capture_model.toFixed(2),
          r.negative_hours, r.negative_hours_model]))}</section>
    </div>
    <section><h2>Each year fitted on the ${f.train_years} before it, €/MWh</h2>${table(
      ["", "Actual", "Model", "Hourly error", "Month-mean error"],
      f.years.map((r) => [r.year, r.price.toFixed(1), r.model.toFixed(1), r.mae.toFixed(1), r.month_mean_mae.toFixed(1)]))}
    </section>`;
  lines($("fmonthly"), mx, [
    { name: "Actual", color: "var(--ink)", values: m.actual },
    { name: "Model", color: "var(--accent)", values: m.model },
  ], { height: 240, xTicks: mx.filter((i) => m.months[i].endsWith("-01") && +m.months[i].slice(0, 4) % 2 === 1),
    xFmt: (i, tip) => (tip ? m.months[i] : m.months[i].slice(0, 4)), yFmt: (y, tip) => (tip ? eur(y) : y.toFixed(0)) });
  lines($("fday"), hours, [
    { name: "Actual", color: "var(--ink)", values: f.day.actual },
    { name: "Model", color: "var(--accent)", values: f.day.model },
  ], { height: 180, xTicks: [0, 6, 12, 18, 23], xFmt: (h) => `${String(h).padStart(2, "0")}:00`,
    yFmt: (y, tip) => (tip ? eur(y) : y.toFixed(0)) });
}

function fairValue(el) {
  const f = D.fair_value, t = f.today, s = f.signal;
  const xs = f.dates.map((_, i) => i);
  const cx = f.cumulative.dates.map((_, i) => i);
  const januaries = (dates, idx) => idx.filter((i) => dates[i].slice(5, 7) === "01" && +dates[i].slice(0, 4) % 2 === 1);
  const row = (label, k, fmt) => [label, ...["gap", "momentum", "long"].map((c) => fmt(s[c][k]))];
  el.innerHTML = `
    <section><div class="stats">
      ${stat(`${t.contract} futures`, eur(t.futures))}
      ${stat("Fair value", eur(t.fair_value))}
      ${stat("Gap", signed(t.gap))}
      ${stat(`Fair value at ${eur(t.gas_quote)} gas`, eur(t.at_quote))}
    </div></section>
    <section><h2>Next calendar year at each month end, €/MWh</h2><div class="chart" id="fv"></div></section>
    <section><h2>${t.contract} fair value per unit of fuel and carbon, €/MWh</h2>${table(
      ["", "Input", "Baseload", "Solar", "Wind"],
      f.sensitivity.map((r) => [INPUTS[r.input], r.level.toFixed(2), signed(r.base), signed(r.solar), signed(r.wind)]))}
    </section>
    <section><h2>What sets the ${t.contract} price</h2>${table(["", "Share of hours", "Bids, €/MWh"],
      f.drivers.map((r) => [SETS[r.source], pct(r.share), r.low == null ? "" : `${r.low.toFixed(0)}–${r.high.toFixed(0)}`]))}
    </section>
    <div class="grid">
      <section><h2>Gap trade, cumulative €/MWh</h2><div class="chart" id="fvpnl"></div></section>
      <section><h2>Next month's move</h2>${table(["", "Gap", "Momentum", "Long"], [
        row("Months", "months", (v) => v),
        row("Hit rate", "hit_rate", pct),
        row("Mean, €/MWh", "mean", signed),
        row("t-statistic", "t", (v) => v.toFixed(1)),
      ])}</section>
    </div>
    <section><h2>By delivery year, mean over the month ends before it, €/MWh</h2>${table(
      ["", "Futures", "Fair value", "Realised"],
      f.years.map((r) => [r.year, r.futures.toFixed(1), r.fair_value.toFixed(1), r.realised == null ? "" : r.realised.toFixed(1)]))}
    </section>`;
  lines($("fv"), xs, [
    { name: "Futures", color: "var(--ink)", values: f.futures },
    { name: "Fair value", color: "var(--accent)", values: f.fair_value },
  ], { height: 240, xTicks: januaries(f.dates, xs), xFmt: (i, tip) => (tip ? date(f.dates[i]) : f.dates[i].slice(0, 4)),
    yFmt: (y, tip) => (tip ? eur(y) : y.toFixed(0)) });
  lines($("fvpnl"), cx, [{ name: "Gap", color: "var(--accent)", values: f.cumulative.values }], {
    height: 180, xTicks: januaries(f.cumulative.dates, cx),
    xFmt: (i, tip) => (tip ? date(f.cumulative.dates[i]) : f.cumulative.dates[i].slice(0, 4)),
    yFmt: (y, tip) => (tip ? signed(y) : y.toFixed(0)) });
}

function options(el) {
  const v = scenario(), n = v.negative, sp = D.options.spark, b = D.options.battery;
  el.innerHTML = `
    <section><h2>Negative-price clause, €/MWh</h2><div class="stats">
      ${stat("Price per paid MWh", eur(n.price))}
      ${stat("Output paid", pct(1 - n.volume_share))}
      ${stat("Put strip, simulated", eur(n.put_mc))}
      ${stat("Put strip, normal model", eur(n.put_normal))}
    </div></section>
    <div class="grid">
      <section><h2>Clean spark spread, ${sp.contract}, €/MWh</h2>${table(["Power–gas correlation", "Kirk", "Monte Carlo"],
        sp.sweep.map((r) => [r.rho.toFixed(2), r.kirk.toFixed(2), r.mc.toFixed(2)]))}</section>
      <section><h2>Measured over ${D.forwards.window_months} months</h2>${table(["", "Volatility"], [
        ["Power", pct(sp.vol.power)], ["Gas", pct(sp.vol.gas)], ["Carbon", pct(sp.vol.carbon)]])}
        ${table(["", "Correlation"], [["Power–gas", sp.corr.power_gas.toFixed(2)],
          ["Power–carbon", sp.corr.power_carbon.toFixed(2)], ["Gas–carbon", sp.corr.gas_carbon.toFixed(2)]])}</section>
    </div>
    <section><h2>Battery, ${b.year}, k€/MW</h2>${table(["", "Value"], [
      ["Intrinsic", b.intrinsic.toFixed(1)],
      ["Least-squares Monte Carlo", b.lsmc.toFixed(1)],
      ["Perfect foresight", b.foresight.toFixed(1)],
    ])}</section>`;
}

function data(el) {
  el.innerHTML = `
    <section><h2>Checks</h2><table>
      ${D.quality.map((q) => `<tr><td class="wrap">${q.check}</td><td class="${q.ok ? "" : "flag"}">${q.value}</td></tr>`).join("")}
    </table></section>
    <div class="grid">
      <section><h2>Assumptions</h2>${table(["", ""], D.meta.assumptions.map((a) => [a.name, a.value]))}</section>
      <section><h2>Sources</h2><table>
        ${D.meta.sources.map((s) => `<tr><td><a href="${s.url}" target="_blank" rel="noopener">${s.name}</a></td>
          <td class="wrap">${s.use}</td></tr>`).join("")}
      </table></section>
    </div>`;
}

// --- Shell ---------------------------------------------------------------------------------------

function choice(el, options, key) {
  el.innerHTML = Object.entries(options).map(([value, label]) =>
    `<button role="radio" data-value="${value}" aria-checked="${state[key] === value}">${label}</button>`).join("");
  el.onclick = (e) => {
    const b = e.target.closest("button");
    if (!b || state[key] === b.dataset.value) return;
    state[key] = b.dataset.value;
    render();
  };
}

function render() {
  const view = VIEWS[state.view];
  hideTip();
  document.title = `${view.title} · PPA Desk`;
  $("title").textContent = view.title;
  $("controls").hidden = !view.controls;
  choice($("tech"), TECHS, "tech");
  choice($("site"), D.meta.sites, "site");
  $("tenor").value = state.tenor;
  $("tenor-out").textContent = `${state.tenor}y`;
  document.querySelectorAll("#nav a").forEach((a) => {
    if (a.dataset.view === state.view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  view.render($("view"));
  writeHash();
}

$("tenor").addEventListener("input", (e) => { state.tenor = +e.target.value; render(); });
$("asof").textContent = date(D.meta.valuation_date);
window.addEventListener("hashchange", () => { readHash(); render(); });
window.addEventListener("scroll", hideTip, { passive: true });
let resize;
window.addEventListener("resize", () => { clearTimeout(resize); resize = setTimeout(render, 120); });

readHash();
render();
