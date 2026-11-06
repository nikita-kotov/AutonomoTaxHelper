/* Quarterly bars of the year overview: income, deductible expenses, IRPF and IVA for each quarter.
   Reads {series: [{key, label, cls}], quarters: [{label, cells: {key: {cents, text}}}]} from #overview-data. */
(function () {
  const holder = document.getElementById('overview-chart');
  const source = document.getElementById('overview-data');
  if (!holder || !source) return;
  const data = JSON.parse(source.textContent);
  const NS = 'http://www.w3.org/2000/svg';
  const W = 720, H = 300, L = 56, R = 12, T = 14, B = 30;
  const GAP = 2, MAX_BAR = 24, RADIUS = 4;

  const el = (name, attrs, parent) => {
    const node = document.createElementNS(NS, name);
    for (const k in attrs) node.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(node);
    return node;
  };

  // ---- scale: whole numbers of euros, a few round ticks, zero always inside
  const euros = data.quarters.flatMap(q => data.series.map(s => q.cells[s.key].cents / 100));
  let lo = Math.min(0, ...euros), hi = Math.max(0, ...euros);
  if (hi === lo) hi = 100;  // a year without figures still gets a readable axis
  const rough = (hi - lo) / 4, mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= rough);
  hi = Math.ceil(hi / step) * step;
  lo = lo < 0 ? lo * 1.15 : 0;  // room for the lowest bar, not a whole empty step below zero
  const y = v => T + (hi - v) / (hi - lo) * (H - T - B);
  const fmt = new Intl.NumberFormat(document.documentElement.lang || undefined, {maximumFractionDigits: 0});

  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, role: 'group', 'aria-label': data.label});
  for (let k = Math.ceil(lo / step); k <= Math.floor(hi / step + 1e-9); k++) {
    const v = k * step + 0;  // + 0 turns -0 into 0
    el('line', {class: v === 0 ? 'axis' : 'grid', x1: L, x2: W - R, y1: y(v), y2: y(v)}, svg);
    const t = el('text', {x: L - 8, y: y(v) + 4, 'text-anchor': 'end'}, svg);
    t.textContent = fmt.format(v);
  }

  // ---- one band per quarter: wash, bars, label; the whole band is the hover and focus target
  const tip = document.createElement('div');
  tip.className = 'tip'; tip.hidden = true;
  holder.append(svg, tip);
  const band = (W - L - R) / data.quarters.length;
  const n = data.series.length;
  const barW = Math.min(MAX_BAR, (band * 0.7 - (n - 1) * GAP) / n);
  const y0 = y(0);

  function bar(x, value, cls, parent) {
    const top = y(value);
    const h = Math.abs(top - y0);
    if (h < 0.5) return;
    const r = Math.min(RADIUS, h, barW / 2);
    // square at the baseline, rounded at the data end
    const d = value >= 0
      ? `M${x},${y0}V${top + r}a${r},${r} 0 0 1 ${r},${-r}H${x + barW - r}a${r},${r} 0 0 1 ${r},${r}V${y0}Z`
      : `M${x},${y0}V${top - r}a${r},${r} 0 0 0 ${r},${r}H${x + barW - r}a${r},${r} 0 0 0 ${r},${-r}V${y0}Z`;
    el('path', {class: 'bar ' + cls, d}, parent);
  }

  function show(q, g) {
    tip.replaceChildren();
    const title = document.createElement('h4');
    title.textContent = q.label;
    tip.append(title);
    for (const s of data.series) {
      const row = document.createElement('div'); row.className = 'row ' + s.cls;
      const key = document.createElement('span'); key.className = 'key';
      const name = document.createElement('span'); name.className = 'name'; name.textContent = s.label;
      const val = document.createElement('span'); val.className = 'val'; val.textContent = q.cells[s.key].text;
      row.append(key, name, val); tip.append(row);
    }
    tip.hidden = false;
    const box = holder.getBoundingClientRect(), at = g.getBoundingClientRect();
    const room = box.right - at.right;
    const left = room > tip.offsetWidth + 8 ? at.right - box.left + 4 : at.left - box.left - tip.offsetWidth - 4;
    tip.style.left = Math.max(0, left) + 'px';
    tip.style.top = Math.max(0, Math.min(at.top - box.top + 8, box.height - tip.offsetHeight)) + 'px';
  }
  const hide = () => { tip.hidden = true; };

  data.quarters.forEach((q, i) => {
    const x0 = L + i * band;
    const g = el('g', {class: 'band', tabindex: 0, role: 'img',
      'aria-label': q.label + ': ' + data.series.map(s => s.label + ' ' + q.cells[s.key].text).join(', ')}, svg);
    el('rect', {class: 'wash', x: x0 + 2, y: T, width: band - 4, height: H - T - B, rx: 4}, g);
    const start = x0 + band / 2 - (n * barW + (n - 1) * GAP) / 2;
    data.series.forEach((s, j) => bar(start + j * (barW + GAP), q.cells[s.key].cents / 100, s.cls, g));
    el('rect', {x: x0, y: T, width: band, height: H - T - B, fill: 'transparent'}, g);  // hit area: the whole band
    const label = el('text', {x: x0 + band / 2, y: H - 8, 'text-anchor': 'middle'}, svg);
    label.textContent = q.label;
    g.addEventListener('pointerenter', () => show(q, g));
    g.addEventListener('pointermove', () => show(q, g));
    g.addEventListener('pointerleave', hide);
    g.addEventListener('focus', () => show(q, g));
    g.addEventListener('blur', hide);
  });
})();
