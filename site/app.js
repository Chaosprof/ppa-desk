import { bars, heatmap, hideTip, histogram, lines, waterfall } from "./charts.js";

const D = await fetch("data.json").then((r) => r.json());
const $ = (id) => document.getElementById(id);

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const TECHS = { solar: "Solar", wind: "Wind" };
const STEPS = [
  ["base", "Baseload forward"], ["profile", "Profile"], ["correlation", "Correlation"],
  ["cannibalisation", "Cannibalisation"], ["risk", "Risk premium"], ["balancing", "Balancing"],
  ["clause", "Negative prices"],
];

const eur = (v, d = 2) => `€${v.toFixed(d)}`;
const signed = (v, d = 2) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d)}`;
const pct = (v, d = 0) => `${(100 * v).toFixed(d)}%`;
const m = (v) => `€${v.toFixed(1)}m`;
const date = (iso) => new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

// --- State lives in the URL: #view?tech=solar&site=south&tenor=10 -----------------------------

const state = { view: "price", tech: "solar", site: D.meta.headline.solar, tenor: D.meta.years.length };

function readHash() {
  const [view, query] = location.hash.slice(1).split("?");
  if (VIEWS[view]) state.view = view;
  const q = new URLSearchParams(query);
  if (TECHS[q.get("tech")]) state.tech = q.get("tech");
  if (D.meta.sites[q.get("site")]) state.site = q.get("site");
  const t = +q.get("tenor");
  if (t >= 1 && t <= D.meta.years.length) state.tenor = t;
}

function writeHash() {
  const q = VIEWS[state.view].controls ? `?tech=${state.tech}&site=${state.site}&tenor=${state.tenor}` : "";
  history.replaceState(null, "", `#${state.view}${q}`);
}

const scenario = () => D.scenarios[state.tech][state.site][state.tenor - 1];
const html = (strings, ...values) => strings.reduce((s, str, i) => s + str + (values[i] ?? ""), "");
const stat = (k, v) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`;
const legend = (items) => `<div class="legend">${items.map(([name, color, dash]) =>
  `<span><i style="background:${dash ? "none" : color};${dash ? `border-top:2px dashed ${color};height:0` : ""}"></i>${name}</span>`).join("")}</div>`;

// --- Views ---------------------------------------------------------------------------------------

const VIEWS = {
  price: { title: "Price", controls: true, render: price },
  hedge: { title: "Hedge", controls: true, render: hedge },
  curve: { title: "Curve", render: curve },
  capture: { title: "Capture", render: capture },
  options: { title: "Options", controls: true, render: options },
  data: { title: "Data", render: data },
};

function price(el) {
  const v = scenario();
  const start = new Date(D.meta.ppa_start).toLocaleDateString("en-GB", { month: "short", year: "numeric" });
  el.innerHTML = html`
    <div class="hero">
      <div class="big">${eur(v.price)}<small>/MWh</small></div>
      <div class="sub">${state.tenor}-year ${state.tech} PPA · ${D.meta.sites[state.site]} · ${D.meta.capacity_mw} MW · from ${start}</div>
    </div>
    <section><h2>Decomposition</h2><div class="chart" id="waterfall"></div></section>
    <div class="grid">
      <section><h2>Fair price by tenor</h2><div class="chart" id="tenors"></div></section>
      <section>
        <h2>Versus baseload</h2>
        <table>
          <tr><th></th><th>€/MWh</th><th>Generator P10–P90</th></tr>
          <tr><td>Pay-as-produced</td><td>${v.price.toFixed(2)}</td><td>${v.price.toFixed(2)}</td></tr>
          <tr><td>Baseload, ${v.hedge[0].p50_mw.toFixed(1)} MW</td><td>${v.baseload.price.toFixed(2)}</td>
            <td>${v.baseload.p10.toFixed(2)}–${v.baseload.p90.toFixed(2)}</td></tr>
        </table>
      </section>
    </div>`;
  const rows = STEPS.map(([k, label]) => ({ label, value: v.steps[k], total: k === "base" }));
  rows.push({ label: "Fair price", value: v.price, total: true });
  waterfall($("waterfall"), rows, (x, total) => (total ? x.toFixed(2) : signed(x)));
  const series = D.scenarios[state.tech][state.site].map((s) => s.price);
  const xs = series.map((_, i) => i + 1);
  lines($("tenors"), xs, [{ name: "Fair", color: "var(--accent)", values: series,
    dots: (i) => (i === state.tenor - 1 ? 4.5 : 2.5) }],
    { height: 180, yFmt: (y, tip) => (tip ? eur(y) : y.toFixed(0)), xFmt: (x, tip) => (tip ? `${x} years` : `${x}y`),
      xTicks: xs.filter((x) => x === 1 || x % 3 === 1 || x === xs.length),
      onPick: (i) => { state.tenor = i + 1; render(); } });
}

function hedge(el) {
  const v = scenario(), r = v.risk;
  el.innerHTML = html`
    <section><div class="stats">
      ${stat("Unhedged σ", m(r.sd_open))}${stat("Hedged σ", m(r.sd_hedged))}
      ${stat("Shape σ", m(r.sd_shape))}${stat("Volume σ", m(r.sd_volume))}
    </div></section>
    <div class="grid">
      <section><h2>Sell baseload, MW</h2>
        ${legend([["Hedge", "var(--accent)"], ["P50 volume", "var(--ink)"]])}
        <div class="chart" id="hedge"></div></section>
      <section><h2>Hedged lifetime P&amp;L, €m</h2><div class="chart" id="pnl"></div></section>
    </div>`;
  bars($("hedge"), v.hedge.map((h) => ({ label: `'${String(h.year).slice(2)}`, value: h.mw, p50: h.p50_mw, year: h.year })),
    { marker: "p50", fmt: (x) => x.toFixed(0), tipFmt: (d) => `Cal-${String(d.year).slice(2)} · ${d.value.toFixed(1)} MW of ${d.p50.toFixed(1)} MW P50` });
  histogram($("pnl"), v.pnl.edges, [{ counts: v.pnl.hedged, color: "var(--accent)", opacity: 0.22 }],
    { xFmt: (x) => x.toFixed(0), mark: 0 });
}

function curve(el) {
  const c = D.curve;
  const start = new Date(c.daily.start);
  const day = (i) => new Date(start.getTime() + i * 864e5);
  const xs = c.daily.values.map((_, i) => i);
  const weekly = xs.map((i) => {
    const w = c.daily.values.slice(i - (i % 7), i - (i % 7) + 7);
    return w.reduce((a, b) => a + b, 0) / w.length;
  });
  const cal = (extrapolated) => xs.map((i) => {
    const d = day(i).toISOString().slice(0, 10);
    const q = c.quotes.find((q) => q.contract.startsWith("Cal") && q.start <= d && d < q.end);
    return q && q.extrapolated === extrapolated ? q.price : null;
  });
  const yearTicks = xs.filter((i) => day(i).getMonth() === 0 && day(i).getDate() === 1 && day(i).getFullYear() % 2 === 1);
  const quoted = c.quotes.filter((q) => !q.extrapolated);
  el.innerHTML = html`
    <section><h2>Baseload, €/MWh</h2>
      ${legend([["Hourly curve, weekly mean", "var(--accent)"], ["Calendar", "var(--ink)"], ["Extrapolated", "var(--ink)", true]])}
      <div class="chart" id="hpfc"></div></section>
    <div class="grid">
      <section><h2>Cal-${String(c.profile.year).slice(2)} shape, €/MWh</h2><div class="chart" id="heat"></div></section>
      <section><h2>Futures</h2>
        <table><tr><th>Contract</th><th>Quote</th><th>Curve</th></tr>
        ${quoted.map((q) => `<tr><td>${q.contract}</td><td>${q.price.toFixed(2)}</td><td>${q.model.toFixed(2)}</td></tr>`).join("")}
        </table></section>
    </div>`;
  lines($("hpfc"), xs, [
    { name: "Curve", color: "var(--accent)", width: 1.25, values: weekly },
    { name: "Cal", color: "var(--ink)", width: 1.5, values: cal(false), quiet: true },
    { name: "Extrapolated", color: "var(--ink)", width: 1.5, dash: "4 4", values: cal(true), quiet: true },
  ], { height: 260, xTicks: yearTicks, xFmt: (i, tip) => (tip ? date(day(i)) : day(i).getFullYear()),
    yFmt: (y, tip) => (tip ? eur(y) : y.toFixed(0)) });
  heatmap($("heat"), c.profile.values, { rows: MONTHS, cols: [...Array(24).keys()], fmt: (v) => eur(v) });
}

function capture(el) {
  const a = D.capture.annual;
  const years = [...a.map((r) => r.year), ...Object.keys(D.capture.solar.projection).map(Number).filter((y) => y > a.at(-1).year)];
  const hist = (k) => years.map((y) => a.find((r) => r.year === y)?.[k] ?? null);
  const proj = (k) => years.map((y) => (y === a.at(-1).year ? a.at(-1)[k] : D.capture[k].projection[y] ?? null));
  const bt = D.capture.solar.backtest;
  const btX = bt.months.map((_, i) => i);
  el.innerHTML = html`
    <section><h2>Capture rate</h2>
      ${legend([["Solar", "var(--accent)"], ["Wind", "var(--up)"], ["Projection", "var(--muted)", true]])}
      <div class="chart" id="rates"></div></section>
    <div class="grid">
      <section><h2>Negative-price hours</h2><div class="chart" id="neg"></div></section>
      <section><h2>Backtest, mean absolute error</h2>
        <table><tr><th></th><th>Model</th><th>Seasonal naive</th></tr>
          ${["solar", "wind"].map((t) => `<tr><td>${TECHS[t]}</td><td>${D.capture[t].backtest.mae.toFixed(3)}</td>
            <td>${D.capture[t].backtest.naive_mae.toFixed(3)}</td></tr>`).join("")}
        </table></section>
    </div>
    <section><h2>Solar, one month ahead</h2>
      ${legend([["Actual", "var(--ink)"], ["Model", "var(--accent)"]])}
      <div class="chart" id="backtest"></div></section>`;
  lines($("rates"), years, [
    { name: "Solar", color: "var(--accent)", values: hist("solar"), dots: true },
    { name: "Solar projection", color: "var(--accent)", dash: "4 4", values: proj("solar") },
    { name: "Wind", color: "var(--up)", values: hist("wind"), dots: true },
    { name: "Wind projection", color: "var(--up)", dash: "4 4", values: proj("wind") },
  ], { height: 260, yFmt: (y) => y.toFixed(2), xFmt: (x) => x, xTicks: years.filter((y) => y % 3 === 0) });
  bars($("neg"), a.map((r) => ({ label: `'${String(r.year).slice(2)}`, value: r.negative_hours, year: r.year, complete: r.complete })),
    { height: 180, fmt: (x) => x.toFixed(0), tipFmt: (d) => `${d.year}${d.complete ? "" : " to date"} · ${d.value} h` });
  lines($("backtest"), btX, [
    { name: "Actual", color: "var(--ink)", values: bt.actual },
    { name: "Model", color: "var(--accent)", values: bt.predicted },
  ], { height: 200, yFmt: (y) => y.toFixed(2), xFmt: (i, tip) => (tip ? bt.months[i] : bt.months[i].slice(0, 4)),
    xTicks: btX.filter((i) => bt.months[i].endsWith("-01")) });
}

function options(el) {
  const v = scenario(), n = v.negative, sp = D.options.spark, b = D.options.battery;
  el.innerHTML = html`
    <section><h2>Negative-price clause</h2><div class="stats">
      ${stat("Clause", `${eur(v.steps.clause)}`)}${stat("Put strip, simulated", eur(n.put_mc))}
      ${stat("Put strip, normal", eur(n.put_normal))}${stat("Volume below zero", pct(n.volume_share, 1))}
    </div></section>
    <div class="grid">
      <section><h2>Clean spark spread, ${sp.contract}</h2>
        <table><tr><th>Power–gas ρ</th><th>Kirk</th><th>Monte Carlo</th></tr>
          ${sp.sweep.map((r) => `<tr><td>${r.rho.toFixed(2)}</td><td>${r.kirk.toFixed(2)}</td>
            <td>${r.mc.toFixed(2)} ± ${r.se.toFixed(2)}</td></tr>`).join("")}
        </table></section>
      <section><h2>Battery, ${b.year}, k€/MW</h2>
        <table>
          <tr><th>${b.cycles} cycles, 2 hours</th><th>Value</th></tr>
          <tr><td>Intrinsic</td><td>${b.intrinsic.toFixed(1)}</td></tr>
          <tr><td>Least-squares Monte Carlo</td><td>${b.lsmc.toFixed(1)}</td></tr>
          <tr><td>Perfect foresight</td><td>${b.foresight.toFixed(1)}</td></tr>
        </table></section>
    </div>`;
}

function data(el) {
  el.innerHTML = html`
    <section><h2>Checks</h2><table>
      ${D.quality.map((q) => `<tr><td class="wrap">${q.check}</td><td>${q.value}<span class="dot ${q.ok ? "" : "warn"}"></span></td></tr>`).join("")}
    </table></section>
    <div class="grid">
      <section><h2>Assumptions</h2><table>
        ${D.meta.assumptions.map((a) => `<tr><td>${a.name}</td><td>${a.value}</td></tr>`).join("")}
      </table></section>
      <section><h2>Sources</h2><table class="links">
        ${D.meta.sources.map((s) => `<tr><td><a href="${s.url}" target="_blank" rel="noopener">${s.name}</a></td>
          <td class="wrap">${s.use}</td></tr>`).join("")}
      </table></section>
    </div>`;
}

// --- Shell ---------------------------------------------------------------------------------------

function segmented(el, options, key) {
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
  segmented($("tech"), TECHS, "tech");
  segmented($("site"), D.meta.sites, "site");
  $("tenor").value = state.tenor;
  $("tenor-out").textContent = `${state.tenor}y`;
  document.querySelectorAll("#nav a").forEach((a) =>
    a.toggleAttribute("aria-current", a.dataset.view === state.view) && a.setAttribute("aria-current", "page"));
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
