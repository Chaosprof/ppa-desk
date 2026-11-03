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
export function lines(el, xs, series, { height = 220, yFmt = (v) => v, xFmt = (v) => v, xTicks, dot, onPick } = {}) {
  const named = series.length > 1;
  const [s, width] = svg(el, height);
  const m = { l: 40, r: named ? 56 : 8, t: 8, b: 24 };
  const all = series.flatMap((d) => d.values).filter((v) => v != null);
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
