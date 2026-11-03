// Small SVG charts. Each function renders into a container and sizes itself to its width.

const tip = () => document.getElementById("tip");

export function showTip(event, html) {
  const t = tip();
  t.innerHTML = html;
  t.classList.add("on");
  const pad = 12, w = t.offsetWidth, h = t.offsetHeight;
  const x = Math.min(event.clientX + pad, window.innerWidth - w - 8);
  const y = event.clientY - h - pad < 8 ? event.clientY + pad : event.clientY - h - pad;
  t.style.transform = `translate(${x}px, ${y}px)`;
  t.style.left = "0";
  t.style.top = "0";
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

function ticks(lo, hi, n = 4) {
  const span = hi - lo || 1;
  const raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
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

const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);

function yAxis(y, values, x0, x1, fmt) {
  return values
    .map((v) => `<g class="grid"><line x1="${x0}" x2="${x1}" y1="${y(v)}" y2="${y(v)}"/></g>
      <text x="${x0 - 8}" y="${y(v) + 4}" text-anchor="end">${fmt(v)}</text>`)
    .join("");
}

// Horizontal waterfall: totals sit on zero, steps float from the running sum.
export function waterfall(el, rows, fmt) {
  const rowH = 34, labelW = Math.min(150, el.clientWidth * 0.34), valueW = 64;
  const [s, width] = svg(el, rows.length * rowH);
  let run = 0;
  const bars = rows.map((r) => {
    const start = r.total ? 0 : run;
    const end = r.total ? r.value : run + r.value;
    run = end;
    return { ...r, start, end };
  });
  const hi = Math.max(...bars.map((b) => Math.max(b.start, b.end)));
  const x = scale([0, hi], [labelW, width - valueW]);
  s.innerHTML = bars
    .map((b, i) => {
      const y = i * rowH;
      const color = b.total ? "var(--accent)" : b.value >= 0 ? "var(--up)" : "var(--down)";
      const x0 = x(Math.min(b.start, b.end)), w = Math.max(Math.abs(x(b.end) - x(b.start)), 1.5);
      const link = i > 0 ? `<line x1="${x(b.start)}" x2="${x(b.start)}" y1="${y - rowH + 24}" y2="${y + 8}"
        stroke="var(--faint)" stroke-dasharray="2 2"/>` : "";
      return `${link}
        <text x="0" y="${y + 21}" class="${b.total ? "label" : ""}">${esc(b.label)}</text>
        <rect x="${x0}" y="${y + 8}" width="${w}" height="16" rx="3" fill="${color}" opacity="${b.total ? 1 : 0.9}"/>
        <text x="${width}" y="${y + 21}" text-anchor="end" class="label">${fmt(b.value, b.total)}</text>`;
    })
    .join("");
}

// Vertical bars with an optional marker per bar.
export function bars(el, data, { height = 200, fmt = (v) => v, tipFmt, marker } = {}) {
  const [s, width] = svg(el, height);
  const m = { l: 44, r: 4, t: 8, b: 22 };
  const hi = Math.max(...data.map((d) => Math.max(d.value, marker ? d[marker] ?? 0 : 0))) * 1.08;
  const y = scale([0, hi], [height - m.b, m.t]);
  const band = (width - m.l - m.r) / data.length, bw = Math.min(band * 0.62, 36);
  const xs = data.map((_, i) => m.l + band * i + band / 2);
  s.innerHTML = yAxis(y, ticks(0, hi, 4), m.l, width - m.r, fmt) +
    data.map((d, i) => `
      <rect x="${xs[i] - bw / 2}" y="${y(d.value)}" width="${bw}" height="${y(0) - y(d.value)}" rx="3"
        fill="var(--accent)" data-i="${i}"/>
      ${marker ? `<line x1="${xs[i] - bw / 2 - 3}" x2="${xs[i] + bw / 2 + 3}" y1="${y(d[marker])}" y2="${y(d[marker])}"
        stroke="var(--ink)" stroke-width="2"/>` : ""}
      <text x="${xs[i]}" y="${height - 6}" text-anchor="middle">${esc(d.label)}</text>`).join("");
  s.querySelectorAll("rect").forEach((r) => {
    const d = data[+r.dataset.i];
    r.addEventListener("mousemove", (e) => showTip(e, tipFmt ? tipFmt(d) : `${esc(d.label)} · ${fmt(d.value)}`));
    r.addEventListener("mouseleave", hideTip);
  });
}

// Line chart over a shared numeric x. series: [{name, color, dash, values: [y | null]}], xs: [x].
export function lines(el, xs, series, { height = 220, yFmt = (v) => v, xFmt = (v) => v, xTicks, zero, mark, onPick } = {}) {
  const [s, width] = svg(el, height);
  const m = { l: 44, r: 8, t: 10, b: 22 };
  const all = series.flatMap((d) => d.values).filter((v) => v != null);
  let lo = Math.min(...all), hi = Math.max(...all);
  if (zero) lo = Math.min(lo, 0);
  const pad = (hi - lo) * 0.08 || 1;
  lo -= zero && lo >= 0 ? 0 : pad;
  hi += pad;
  const x = scale([xs[0], xs[xs.length - 1]], [m.l, width - m.r]);
  const y = scale([lo, hi], [height - m.b, m.t]);
  const path = (vals) => {
    let d = "", pen = false;
    vals.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(xs[i]).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  const xt = xTicks ?? ticks(xs[0], xs[xs.length - 1], 6);
  s.innerHTML = yAxis(y, ticks(lo, hi, 4), m.l, width - m.r, yFmt) +
    xt.map((v) => `<text x="${x(v)}" y="${height - 6}" text-anchor="middle">${xFmt(v)}</text>`).join("") +
    (mark != null ? `<line x1="${x(mark)}" x2="${x(mark)}" y1="${m.t}" y2="${height - m.b}" stroke="var(--faint)" stroke-dasharray="3 3"/>` : "") +
    series.map((d) => `<path d="${path(d.values)}" fill="none" stroke="${d.color}" stroke-width="${d.width ?? 1.75}"
      ${d.dash ? `stroke-dasharray="${d.dash}"` : ""} stroke-linejoin="round" stroke-linecap="round"/>`).join("") +
    series.filter((d) => d.dots).map((d) => d.values.map((v, i) => v == null ? "" :
      `<circle cx="${x(xs[i])}" cy="${y(v)}" r="${d.dots === true ? 2.5 : d.dots(i)}" fill="${d.color}"/>`).join("")).join("") +
    `<line class="cross" y1="${m.t}" y2="${height - m.b}" stroke="var(--faint)" opacity="0"/>
     <rect x="${m.l}" y="0" width="${width - m.l - m.r}" height="${height}" fill="transparent" class="hit"/>`;
  const cross = s.querySelector(".cross");
  const hit = s.querySelector(".hit");
  const nearest = (e) => {
    const xv = x.invert(e.clientX - s.getBoundingClientRect().left);
    let i = 0;
    for (let k = 1; k < xs.length; k++) if (Math.abs(xs[k] - xv) < Math.abs(xs[i] - xv)) i = k;
    return i;
  };
  if (onPick) {
    hit.style.cursor = "pointer";
    hit.addEventListener("click", (e) => onPick(nearest(e)));
  }
  hit.addEventListener("mousemove", (e) => {
    const i = nearest(e);
    cross.setAttribute("x1", x(xs[i]));
    cross.setAttribute("x2", x(xs[i]));
    cross.setAttribute("opacity", 1);
    const rows = series.filter((d) => d.values[i] != null && !d.quiet)
      .map((d) => `${series.length > 1 ? `${esc(d.name)} ` : ""}${yFmt(d.values[i], true)}`);
    showTip(e, `${xFmt(xs[i], true)}${rows.length ? " · " + rows.join(" · ") : ""}`);
  });
  hit.addEventListener("mouseleave", () => { cross.setAttribute("opacity", 0); hideTip(); });
}

// Overlaid histograms on shared bin edges.
export function histogram(el, edges, series, { height = 200, xFmt = (v) => v, mark } = {}) {
  const [s, width] = svg(el, height);
  const m = { l: 8, r: 8, t: 8, b: 22 };
  const hi = Math.max(...series.flatMap((d) => d.counts));
  const x = scale([edges[0], edges[edges.length - 1]], [m.l, width - m.r]);
  const y = scale([0, hi * 1.05], [height - m.b, m.t]);
  const step = (counts) => counts.map((c, i) => `L${x(edges[i])},${y(c)}L${x(edges[i + 1])},${y(c)}`).join("");
  s.innerHTML = `<g class="axis"><line x1="${m.l}" x2="${width - m.r}" y1="${y(0)}" y2="${y(0)}"/></g>` +
    ticks(edges[0], edges[edges.length - 1], 6)
      .map((v) => `<text x="${x(v)}" y="${height - 6}" text-anchor="middle">${xFmt(v)}</text>`).join("") +
    series.map((d) => `<path d="M${x(edges[0])},${y(0)}${step(d.counts)}L${x(edges[edges.length - 1])},${y(0)}Z"
      fill="${d.color}" fill-opacity="${d.opacity ?? 0.18}" stroke="${d.color}" stroke-width="1.25"/>`).join("") +
    (mark != null ? `<line x1="${x(mark)}" x2="${x(mark)}" y1="${m.t}" y2="${y(0)}" stroke="var(--ink)" stroke-dasharray="3 3"/>` : "");
}

// Month x hour heatmap with a diverging scale around zero.
export function heatmap(el, matrix, { rows, cols, fmt = (v) => v } = {}) {
  const labelW = 32, cellH = 18;
  const [s, width] = svg(el, rows.length * cellH + 20);
  const cw = (width - labelW) / cols.length;
  const flat = matrix.flat();
  const hi = Math.max(...flat), lo = Math.min(...flat, 0);
  const color = (v) => v >= 0
    ? `color-mix(in srgb, var(--accent) ${Math.round(8 + 92 * (v / hi))}%, var(--bg))`
    : `color-mix(in srgb, var(--down) ${Math.round(20 + 80 * (v / lo))}%, var(--bg))`;
  s.innerHTML = matrix.map((row, r) => `<text x="0" y="${r * cellH + 13}">${rows[r]}</text>` +
    row.map((v, c) => `<rect x="${labelW + c * cw}" y="${r * cellH}" width="${cw - 1}" height="${cellH - 1}" rx="2"
      style="fill:${color(v)}" data-r="${r}" data-c="${c}"/>`).join("")).join("") +
    cols.map((c, i) => i % 3 === 0 ? `<text x="${labelW + i * cw + cw / 2}" y="${rows.length * cellH + 14}"
      text-anchor="middle">${c}</text>` : "").join("");
  s.querySelectorAll("rect").forEach((rect) => {
    const r = +rect.dataset.r, c = +rect.dataset.c;
    rect.addEventListener("mousemove", (e) => showTip(e, `${rows[r]} ${cols[c]}:00 · ${fmt(matrix[r][c])}`));
    rect.addEventListener("mouseleave", hideTip);
  });
}
