import { bars, hideTip, histogram, lines, waterfall } from "./charts.js";

const D = await fetch("data.json").then((r) => r.json());
const $ = (id) => document.getElementById(id);

const TECHS = { solar: "Solar", wind: "Wind" };
const STEPS = [
  ["base", "Baseload forward"], ["profile", "Profile"], ["correlation", "Correlation"],
  ["cannibalisation", "Cannibalisation"], ["risk", "Risk premium"], ["balancing", "Balancing"],
  ["clause", "Negative prices"],
];

const eur = (v, d = 2) => `€${v.toFixed(d)}`;
const signed = (v) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2)}`;
const date = (d) => new Date(d).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const yy = (year) => `’${String(year).slice(2)}`;

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
const stat = (k, v) => `<div class="stat"><div class="v">${v}</div><div class="k">${k}</div></div>`;
const table = (head, rows) => `<table><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>
  ${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`;

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
  el.innerHTML = `
    <div class="hero">
      <div class="big">${eur(v.price)}<small>/MWh</small></div>
      <div class="sub">${state.tenor}-year ${state.tech} PPA in the ${D.meta.sites[state.site].toLowerCase()},
        ${D.meta.capacity_mw} MW from ${start}</div>
    </div>
    <section><div class="chart" id="waterfall"></div></section>
    <div class="grid">
      <section><h2>By tenor</h2><div class="chart" id="tenors"></div></section>
      <section><h2>Versus baseload</h2>${table(["", "€/MWh", "Generator P10–P90"], [
        ["Pay-as-produced", v.price.toFixed(2), v.price.toFixed(2)],
        [`Baseload, ${v.hedge[0].p50_mw.toFixed(1)} MW`, v.baseload.price.toFixed(2),
          `${v.baseload.p10.toFixed(2)}–${v.baseload.p90.toFixed(2)}`],
      ])}</section>
    </div>`;
  const rows = STEPS.map(([k, label]) => ({ label, value: v.steps[k], total: k === "base" }));
  rows.push({ label: "Fair price", value: v.price, total: true });
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
  const v = scenario();
  el.innerHTML = `
    <section><div class="stats">
      ${stat("Unhedged volatility", `€${v.risk.sd_open.toFixed(1)}m`)}
      ${stat("Hedged volatility", `€${v.risk.sd_hedged.toFixed(1)}m`)}
    </div></section>
    <div class="grid">
      <section><h2>Baseload to sell, MW</h2><div class="chart" id="hedge"></div></section>
      <section><h2>Hedged lifetime P&amp;L, €m</h2><div class="chart" id="pnl"></div></section>
    </div>`;
  bars($("hedge"), v.hedge.map((h) => ({ label: yy(h.year), value: h.mw, year: h.year })),
    { tipFmt: (d) => `Cal-${String(d.year).slice(2)} · ${d.value.toFixed(1)} MW` });
  histogram($("pnl"), v.pnl.edges, v.pnl.hedged, { xFmt: (x) => x.toFixed(0) });
}

function curve(el) {
  const c = D.curve;
  const start = new Date(c.daily.start);
  const day = (i) => new Date(start.getTime() + i * 864e5);
  const xs = c.daily.values.map((_, i) => i);
  const smooth = xs.map((i) => {  // 28-day centred mean
    const w = c.daily.values.slice(Math.max(0, i - 14), i + 14);
    return w.reduce((a, b) => a + b, 0) / w.length;
  });
  const yearStarts = xs.filter((i) => day(i).getMonth() === 0 && day(i).getDate() === 1 && day(i).getFullYear() % 3 === 0);
  const hours = [...Array(24).keys()];
  const shape = hours.map((h) => c.profile.values.reduce((a, row) => a + row[h], 0) / 12);
  el.innerHTML = `
    <section><h2>Baseload forward, €/MWh</h2><div class="chart" id="hpfc"></div></section>
    <div class="grid">
      <section><h2>Cal-${String(c.profile.year).slice(2)} day, €/MWh</h2><div class="chart" id="day"></div></section>
      <section><h2>Futures</h2>${table(["", "Quote", "Curve"],
        c.quotes.filter((q) => !q.extrapolated).map((q) => [q.contract, q.price.toFixed(2), q.model.toFixed(2)]))}</section>
    </div>`;
  lines($("hpfc"), xs, [{ name: "Curve", color: "var(--accent)", values: smooth }], {
    height: 240, xTicks: yearStarts, xFmt: (i, tip) => (tip ? date(day(i)) : day(i).getFullYear()),
    yFmt: (y, tip) => (tip ? eur(y) : y.toFixed(0)),
  });
  lines($("day"), hours, [{ name: "Day", color: "var(--accent)", values: shape }], {
    height: 180, xTicks: [0, 6, 12, 18, 23], xFmt: (h) => `${String(h).padStart(2, "0")}:00`,
    yFmt: (y, tip) => (tip ? eur(y) : y.toFixed(0)),
  });
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
      <section><h2>Mean absolute error</h2>${table(["", "Model", "Seasonal naive"],
        ["solar", "wind"].map((t) => [TECHS[t], D.capture[t].backtest.mae.toFixed(3), D.capture[t].backtest.naive_mae.toFixed(3)]))}</section>
    </div>`;
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

function options(el) {
  const v = scenario(), n = v.negative, sp = D.options.spark, b = D.options.battery;
  el.innerHTML = `
    <section><h2>Negative-price clause, €/MWh</h2><div class="stats">
      ${stat("Clause value", eur(v.steps.clause))}
      ${stat("Put strip, simulated", eur(n.put_mc))}
      ${stat("Put strip, normal model", eur(n.put_normal))}
    </div></section>
    <div class="grid">
      <section><h2>Clean spark spread, ${sp.contract}, €/MWh</h2>${table(["Power–gas correlation", "Kirk", "Monte Carlo"],
        sp.sweep.map((r) => [r.rho.toFixed(2), r.kirk.toFixed(2), r.mc.toFixed(2)]))}</section>
      <section><h2>Battery, ${b.year}, k€/MW</h2>${table(["", "Value"], [
        ["Intrinsic", b.intrinsic.toFixed(1)],
        ["Least-squares Monte Carlo", b.lsmc.toFixed(1)],
        ["Perfect foresight", b.foresight.toFixed(1)],
      ])}</section>
    </div>`;
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
