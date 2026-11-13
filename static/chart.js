// Petit graphique en lignes, sans dépendance.
// series = [{name, points: [[dateISO, valeur|null], ...]}] ; opts.invert pour les positions (1 en haut).
function lineChart(el, series, opts) {
  opts = opts || {};
  const COLORS = ['#2563eb', '#dc2626', '#059669', '#d97706', '#7c3aed', '#0891b2', '#be185d', '#4d7c0f'];
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const dates = [...new Set(series.flatMap(s => s.points.map(p => p[0])))].sort();
  const vals = series.flatMap(s => s.points.map(p => p[1])).filter(v => v != null);
  if (!dates.length || !vals.length) {
    el.innerHTML = '<p class="muted">Pas encore de données à afficher.</p>';
    return;
  }
  const W = Math.max(el.clientWidth || 800, 320), H = opts.height || 280;
  const m = {l: 40, r: 14, t: 12, b: 26};
  let lo = opts.invert ? 1 : Math.min(0, ...vals), hi = Math.max(...vals);
  if (opts.invert) hi = Math.max(hi, 10);
  if (opts.max) hi = opts.max;  // échelle fixe (ex. 100)
  if (hi === lo) hi = lo + 1;
  const idx = Object.fromEntries(dates.map((d, i) => [d, i]));
  const x = i => m.l + (dates.length === 1 ? (W - m.l - m.r) / 2 : i * (W - m.l - m.r) / (dates.length - 1));
  const y = v => {
    const t = (v - lo) / (hi - lo);
    return opts.invert ? m.t + t * (H - m.t - m.b) : H - m.b - t * (H - m.t - m.b);
  };
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img">`;
  for (let i = 0; i <= 4; i++) {
    const v = lo + (hi - lo) * i / 4, yy = y(v).toFixed(1);
    svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${yy}" y2="${yy}" stroke="currentColor" opacity=".12"/>` +
           `<text x="${m.l - 6}" y="${+yy + 4}" text-anchor="end" font-size="11" fill="currentColor" opacity=".6">${Math.round(v)}</text>`;
  }
  const ticks = [...new Set([0, Math.floor((dates.length - 1) / 2), dates.length - 1])];
  ticks.forEach((i, n) => {
    const anchor = dates.length === 1 ? 'middle' : n === 0 ? 'start' : n === ticks.length - 1 ? 'end' : 'middle';
    svg += `<text x="${x(i).toFixed(1)}" y="${H - 8}" text-anchor="${anchor}" font-size="11" fill="currentColor" opacity=".6">${esc(dates[i])}</text>`;
  });
  series.forEach((s, n) => {
    const c = COLORS[n % COLORS.length];
    let seg = [];
    const flush = () => {
      if (seg.length > 1) svg += `<polyline fill="none" stroke="${c}" stroke-width="2" stroke-linejoin="round" points="${seg.join(' ')}"/>`;
      seg = [];
    };
    const dots = s.points.length <= 120;
    s.points.forEach(p => {
      if (p[1] == null) return flush();
      const px = x(idx[p[0]]).toFixed(1), py = y(p[1]).toFixed(1);
      seg.push(px + ',' + py);
      if (dots) svg += `<circle cx="${px}" cy="${py}" r="3" fill="${c}"><title>${esc(s.name)} · ${esc(p[0])} : ${p[1]}</title></circle>`;
    });
    flush();
  });
  svg += '</svg>';
  const legend = series.map((s, n) =>
    `<span><i style="background:${COLORS[n % COLORS.length]}"></i>${esc(s.name)}</span>`).join('');
  el.innerHTML = svg + `<div class="legend">${legend}</div>`;
}
