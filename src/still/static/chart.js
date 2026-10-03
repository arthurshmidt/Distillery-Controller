// Hand-drawn SVG trend chart: four temperatures plus the two setpoints.

export const SERIES = [
  { k: 'dr', label: 'Deph return', color: '#d95926', sp: 'sp', spLabel: 'Deph setpoint' },
  { k: 'ds', label: 'Deph supply', color: '#3987e5', sp: 'ssp', spLabel: 'Supply setpoint' },
  { k: 'cr', label: 'Cond return', color: '#c98500' },
  { k: 'cs', label: 'Cond supply', color: '#199e70' },
];

const GAP_S = 5; // a longer silence than this breaks the line

export function clock(t, secs) {
  const d = new Date(t * 1000);
  const p = (v) => String(v).padStart(2, '0');
  return p(d.getHours()) + ':' + p(d.getMinutes()) + (secs ? ':' + p(d.getSeconds()) : '');
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

// Local time in the forms the History screen uses.
export function fmtTime(t, how) {
  const d = new Date(t * 1000);
  const p = (v) => String(v).padStart(2, '0');
  const hm = p(d.getHours()) + ':' + p(d.getMinutes());
  const day = MONTHS[d.getMonth()] + ' ' + d.getDate();
  if (how === 'hm') return hm;
  if (how === 'hms') return hm + ':' + p(d.getSeconds());
  if (how === 'day') return day;
  if (how === 'dayhm') return day + ' ' + hm;
  if (how === 'dayhms') return day + ' ' + hm + ':' + p(d.getSeconds());
  return DAYS[d.getDay()] + ' ' + day + ' \u00b7 ' + hm + ':' + p(d.getSeconds());
}

export function fmt1(v) {
  return v == null || isNaN(v) ? '—' : v.toFixed(1);
}

// Returns the y range for the visible series, rounded out to tens.
export function yRange(pts, hidden) {
  let lo = Infinity, hi = -Infinity;
  const see = (v) => { if (v != null) { if (v < lo) lo = v; if (v > hi) hi = v; } };
  for (const p of pts) {
    for (const q of SERIES) {
      if (hidden[q.k]) continue;
      see(p[q.k]);
      if (q.sp) see(p[q.sp]);
    }
  }
  if (lo === Infinity) { lo = 60; hi = 140; }
  lo = Math.floor((lo - 2) / 10) * 10;
  hi = Math.ceil((hi + 2) / 10) * 10;
  while ((hi - lo) % 20 !== 0) hi += 10;
  return [lo, hi];
}

// SVG path for series `k` in a viewBox 1000 wide and `h` tall. Null values and
// silences longer than `gap` seconds break the line instead of being bridged.
export function linePath(pts, k, t0, span, y0, y1, h, gap, stepN = 1) {
  let d = '', pen = false, prev = null;
  for (let i = (pts.length - 1) % stepN; i < pts.length; i += stepN) {
    const p = pts[i];
    const v = p[k];
    if (prev !== null && p.t - prev > gap) pen = false;
    prev = p.t;
    if (v == null) { pen = false; continue; }
    const x = ((p.t - t0) / span) * 1000;
    const y = h - ((v - y0) / (y1 - y0)) * h;
    d += (pen ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
    pen = true;
  }
  return d;
}

export function ticks(lo, hi) {
  return [0, 1, 2, 3, 4].map((i) => String(hi - ((hi - lo) / 4) * i));
}

// Compute everything the view needs. `tEnd` is the right edge in unix seconds.
export function layout(hist, range, hidden, tEnd) {
  const t0 = tEnd - range;
  const pts = hist.filter((p) => p.t >= t0 && p.t <= tEnd);
  const [lo, hi] = yRange(pts, hidden);
  const stepN = Math.max(1, Math.ceil(pts.length / 300));
  const gap = GAP_S * stepN;
  const paths = {};
  for (const q of SERIES) {
    paths[q.k] = hidden[q.k] ? '' : linePath(pts, q.k, t0, range, lo, hi, 300, gap, stepN);
    if (q.sp) paths[q.sp] = hidden[q.k] ? '' : linePath(pts, q.sp, t0, range, lo, hi, 300, gap, stepN);
  }
  const yTicks = ticks(lo, hi);
  const xTicks = [0, 1, 2, 3, 4].map((i) => clock(tEnd - range * (1 - i / 4), range <= 300));
  return { pts, paths, yTicks, xTicks, t0 };
}

// Nearest logged point to time t, or null if none within `range / 20`.
export function nearest(pts, t, range) {
  let best = null, bd = Infinity;
  for (const p of pts) {
    const d = Math.abs(p.t - t);
    if (d < bd) { bd = d; best = p; }
  }
  return best && bd <= Math.max(5, range / 20) ? best : null;
}
