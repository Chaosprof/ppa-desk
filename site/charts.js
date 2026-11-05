// Minimal SVG charts: no grid lines, legends or axis rules. Each sizes itself to its container.

const tip = () => document.getElementById("tip");

export function showTip(event, text) {
  const t = tip();
  t.textContent = text;
  t.classList.add("on");
  const pad = 12, w = t.offsetWidth, h = t.offsetHeight;
  const x = Math.min(event.clientX + pad, window.innerWidth - w - 8);
  const y = event.clientY - h - pad < 8 ? event.clientY + pad : event.clientY - h - pad;
  t.style.transform = `translate(${x}px, ${y}px)`;
}

export function hideTip() {
  tip().classList.remove("on");
}

function scale([d0, d1], [r0, r1]) {
  const k = (r1 - r0) / (d1 - d0 || 1);
  const f = (v) => r0 + (v - d0) * k;
  f.invert = (p) => d0 + (p - r0) / k;
  return f;
}

function ticks(lo, hi, n = 3) {
  const span = hi - lo || 1;
  const mag = 10 ** Math.floor(Math.log10(span / n));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n + 0.5);
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

function svg(el, height) {
  const width = el.clientWidth || 600;
  el.innerHTML = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"></svg>`;
  return [el.firstChild, width];
}

// Horizontal waterfall: totals in the accent from zero, steps in grey from the running sum.
export function waterfall(el, rows, fmt) {
  const rowH = 36, labelW = Math.min(150, el.clientWidth * 0.36), valueW = 64;
  const [s, width] = svg(el, rows.length * rowH);
  let run = 0;
  const bars = rows.map((r) => {
    const start = r.total ? 0 : run;
    run = r.total ? r.value : run + r.value;
    return { ...r, start, end: run };
  });
  const hi = Math.max(...bars.map((b) => Math.max(b.start, b.end)));
  const x = scale([0, hi], [labelW, width - valueW]);
  s.innerHTML = bars.map((b, i) => {
    const y = i * rowH;
    const x0 = x(Math.min(b.start, b.end)), w = Math.max(Math.abs(x(b.end) - x(b.start)), 2);
    return `<text x="0" y="${y + 22}" class="${b.total ? "name" : ""}">${b.label}</text>
      <rect x="${x0}" y="${y + 10}" width="${w}" height="16" rx="2" fill="${b.total ? "var(--accent)" : "var(--muted)"}"/>
      <text x="${width}" y="${y + 22}" text-anchor="end" class="name">${fmt(b.value, b.total)}</text>`;
  }).join("");
}

// Vertical bars, labelled underneath; values on hover, or above the bars with valueFmt.
export function bars(el, data, { height = 180, tipFmt, valueFmt } = {}) {
  const [s, width] = svg(el, height);
  const bottom = 24;
  const hi = Math.max(...data.map((d) => d.value));
  const y = scale([0, hi], [height - bottom, valueFmt ? 22 : 4]);
  const band = width / data.length, bw = Math.min(band * 0.56, 32);
  s.innerHTML = data.map((d, i) => {
    const cx = band * i + band / 2;
    return `<rect x="${cx - bw / 2}" y="${y(d.value)}" width="${bw}" height="${y(0) - y(d.value)}" rx="2"
        fill="var(--accent)" data-i="${i}"/>
      <text x="${cx}" y="${height - 6}" text-anchor="middle">${d.label}</text>` +
      (valueFmt ? `<text x="${cx}" y="${y(d.value) - 8}" text-anchor="middle" class="name">${valueFmt(d.value)}</text>` : "");
  }).join("");
  if (!tipFmt) return;
  s.querySelectorAll("rect").forEach((r) => {
    r.addEventListener("mousemove", (e) => showTip(e, tipFmt(data[+r.dataset.i])));
    r.addEventListener("mouseleave", hideTip);
  });
}

// Lines over a shared numeric x. series: [{name, color, values}]; names are drawn at the line ends.
// steps: flat segments [{x0, x1, y, color}], drawn over the lines; tipFmt(i) replaces the default tooltip.
export function lines(el, xs, series, { height = 220, yFmt = (v) => v, xFmt = (v) => v, xTicks, dot, onPick,
  steps = [], tipFmt } = {}) {
  const named = series.length > 1;
  const [s, width] = svg(el, height);
  const m = { l: 40, r: named ? 56 : 8, t: 8, b: 24 };
  const all = [...series.flatMap((d) => d.values), ...steps.map((d) => d.y)].filter((v) => v != null);
  const lo = Math.min(...all), hi = Math.max(...all), pad = (hi - lo) * 0.08 || 1;
  const x = scale([xs[0], xs[xs.length - 1]], [m.l, width - m.r]);
  const y = scale([lo - pad, hi + pad], [height - m.b, m.t]);
  const path = (vals) => vals.map((v, i) => (v == null ? ""
    : `${i && vals[i - 1] != null ? "L" : "M"}${x(xs[i]).toFixed(1)},${y(v).toFixed(1)}`)).join("");
  const last = (vals) => vals.findLastIndex((v) => v != null);
  s.innerHTML =
    ticks(lo - pad, hi + pad).map((v) => `<text x="${m.l - 10}" y="${y(v) + 4}" text-anchor="end">${yFmt(v)}</text>`).join("") +
    (xTicks ?? ticks(xs[0], xs[xs.length - 1], 5))
      .map((v) => `<text x="${x(v)}" y="${height - 4}" text-anchor="middle">${xFmt(v)}</text>`).join("") +
    series.map((d) => `<path d="${path(d.values)}" fill="none" stroke="${d.color}" stroke-width="2"
      stroke-linejoin="round" stroke-linecap="round"/>`).join("") +
    steps.map((d) => `<line x1="${x(d.x0)}" x2="${x(d.x1)}" y1="${y(d.y)}" y2="${y(d.y)}" stroke="${d.color}"
      stroke-width="2"/>`).join("") +
    (named ? endLabels(series.map((d) => {
      const i = last(d.values);
      return { name: d.name, x: x(xs[i]) + 8, y: y(d.values[i]) + 4 };
    })) : "") +
    (dot != null ? `<circle cx="${x(xs[dot])}" cy="${y(series[0].values[dot])}" r="5" fill="${series[0].color}"/>` : "") +
    `<rect x="${m.l}" y="0" width="${width - m.l - m.r}" height="${height}" fill="transparent" class="hit"/>`;
  const hit = s.querySelector(".hit");
  const nearest = (e) => {
    const xv = x.invert(e.clientX - s.getBoundingClientRect().left);
    return xs.reduce((best, v, k) => (Math.abs(v - xv) < Math.abs(xs[best] - xv) ? k : best), 0);
  };
  if (onPick) {
    hit.style.cursor = "pointer";
    hit.addEventListener("click", (e) => onPick(nearest(e)));
  }
  hit.addEventListener("mousemove", (e) => {
    const i = nearest(e);
    if (tipFmt) return showTip(e, tipFmt(i));
    const vals = series.filter((d) => d.values[i] != null)
      .map((d) => `${named ? `${d.name} ` : ""}${yFmt(d.values[i], true)}`);
    showTip(e, [xFmt(xs[i], true), ...vals].join(" · "));
  });
  hit.addEventListener("mouseleave", hideTip);
}

// Line-end names, pushed apart so they never overlap.
function endLabels(labels, gap = 15) {
  const sorted = [...labels].sort((a, b) => a.y - b.y);
  sorted.forEach((l, i) => { if (i && l.y - sorted[i - 1].y < gap) l.y = sorted[i - 1].y + gap; });
  return sorted.map((l) => `<text x="${l.x}" y="${l.y}" class="name">${l.name}</text>`).join("");
}

// Histogram as solid bars on shared bin edges.
export function histogram(el, edges, counts, { height = 180, xFmt = (v) => v } = {}) {
  const [s, width] = svg(el, height);
  const bottom = 24;
  const x = scale([edges[0], edges[edges.length - 1]], [0, width]);
  const y = scale([0, Math.max(...counts)], [height - bottom, 4]);
  s.innerHTML = counts.map((c, i) => `<rect x="${x(edges[i]) + 1}" y="${y(c)}"
      width="${Math.max(x(edges[i + 1]) - x(edges[i]) - 2, 1)}" height="${y(0) - y(c)}" rx="1" fill="var(--accent)"/>`).join("") +
    ticks(edges[0], edges[edges.length - 1], 5)
      .map((v) => `<text x="${x(v)}" y="${height - 4}" text-anchor="middle">${xFmt(v)}</text>`).join("");
}

// Heatmap of rows x cols (here days x hours), one pixel per cell scaled up without smoothing.
// color(v) returns [r, g, b]; null cells stay transparent. xTicks: [{i, label}] along the rows.
export function heatmap(el, rows, { height = 264, color, xTicks = [], yTicks = [], tipFmt } = {}) {
  const [s, width] = svg(el, height);
  const m = { l: 40, r: 0, t: 0, b: 24 };
  const n = rows.length, k = rows[0].length, w = width - m.l - m.r, h = height - m.t - m.b;
  const canvas = document.createElement("canvas");
  canvas.width = n;
  canvas.height = k;
  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(n, k);
  rows.forEach((row, i) => row.forEach((v, j) => {
    if (v == null) return;
    const [r, g, b] = color(v), p = 4 * (j * n + i);
    img.data.set([r, g, b, 255], p);
  }));
  ctx.putImageData(img, 0, 0);
  const cy = (j) => m.t + (j + 0.5) * (h / k);
  s.innerHTML = `<image href="${canvas.toDataURL()}" x="${m.l}" y="${m.t}" width="${w}" height="${h}"
      preserveAspectRatio="none" style="image-rendering: pixelated"/>` +
    yTicks.map((j) => `<text x="${m.l - 10}" y="${cy(j) + 4}" text-anchor="end">${String(j).padStart(2, "0")}:00</text>`).join("") +
    xTicks.map((t) => `<text x="${m.l + t.i * (w / n)}" y="${height - 4}">${t.label}</text>`).join("") +
    `<rect x="${m.l}" y="${m.t}" width="${w}" height="${h}" fill="transparent" class="hit"/>`;
  const hit = s.querySelector(".hit");
  hit.addEventListener("mousemove", (e) => {
    const box = hit.getBoundingClientRect();
    const i = Math.min(n - 1, Math.floor(((e.clientX - box.left) / box.width) * n));
    const j = Math.min(k - 1, Math.floor(((e.clientY - box.top) / box.height) * k));
    if (rows[i][j] == null) return hideTip();
    showTip(e, tipFmt(i, j, rows[i][j]));
  });
  hit.addEventListener("mouseleave", hideTip);
}

// Colour key for a heatmap: a gradient bar with its end and zero values underneath.
export function colorKey(el, lo, hi, color, fmt) {
  const height = 36, [s, width] = svg(el, height);
  const x = scale([lo, hi], [0, width]);
  const stops = Array.from({ length: 33 }, (_, i) => lo + (i / 32) * (hi - lo))
    .map((v) => `<stop offset="${(x(v) / width).toFixed(4)}" stop-color="rgb(${color(v).join(",")})"/>`).join("");
  s.innerHTML = `<defs><linearGradient id="heat-key">${stops}</linearGradient></defs>
    <rect x="0" y="0" width="${width}" height="10" rx="2" fill="url(#heat-key)"/>` +
    [[lo, "start"], [0, "middle"], [hi, "end"]]
      .filter(([v, a]) => a !== "middle" || Math.min(x(v), width - x(v)) > 44)
      .map(([v, a]) => `<text x="${x(v)}" y="30" text-anchor="${a}">${fmt(v)}</text>`).join("");
}

// Diverging scale through a neutral zero, interpolated in OKLab. Each arm runs from zero to its own end,
// so a few shallow negative hours stay visible next to a much larger positive range.
export function diverging(lo, hi, { neg, zero, pos }) {
  const ramp = (stops) => (t) => {
    const u = Math.min(Math.max(t, 0), 1) * (stops.length - 1), a = Math.min(Math.floor(u), stops.length - 2);
    const p = stops[a], q = stops[a + 1], f = u - a;
    return fromLab(p.map((c, i) => c + (q[i] - c) * f));
  };
  const up = ramp([zero, ...pos].map(toLab)), down = ramp([zero, ...neg].map(toLab));
  return (v) => (v >= 0 ? up(v / hi) : down(v / lo));
}

const hex = (c) => [1, 3, 5].map((i) => parseInt(c.trim().slice(i, i + 2), 16) / 255);
const lin = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const gam = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);

function toLab(color) {
  const [r, g, b] = hex(color).map(lin);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
}

function fromLab([L, A, B]) {
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3;
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3;
  const s = (L - 0.0894841775 * A - 1.291485548 * B) ** 3;
  return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s].map((c) => Math.round(255 * Math.min(Math.max(gam(c), 0), 1)));
}
