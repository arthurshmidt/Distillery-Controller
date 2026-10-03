// The History screen: any window in the last 14 days, thinned by the server,
// with a pinned cursor, the mode/fault timeline and the events list.

import { get, getBlob } from './api.js';
import { store } from './state.js';
import { yRange, linePath, ticks, fmtTime, fmt1, SERIES } from './chart.js';

const KEEP_S = 14 * 86400;
const TARGET_POINTS = 600;
const REFRESH_MS = 10000;
const MAX_EVENTS_SHOWN = 80;

const ui = { span: 14400, end: null, cursor: null, hidden: {} };
const data = { status: 'loading', pts: [], events: [], segments: [], start: 0, end: 0, now: 0, step: 1 };
let seq = 0;
let toast = () => {};
export function setToast(fn) { toast = fn; }

const $ = (sel) => document.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const pct = (v) => (v == null || isNaN(v) ? '—' : Math.round(v) + '%');

const MODE_LOOK = {
  auto: { bg: '#1f2731', fg: '#c9d2dc', label: 'AUTO' },
  manual: { bg: '#fab219', fg: '#06080b', label: 'MANUAL' },
  off: { bg: '#56616f', fg: '#ffffff', label: 'OFF' },
  fault: { bg: '#e5484d', fg: '#ffffff', label: 'FAULT' },
};

function toPoint(row) {
  const s = row.state;
  const t = s.temps_f;
  return {
    t: row.timestamp,
    dr: t ? t.deph_return : null, ds: t ? t.deph_supply : null,
    cr: t ? t.cond_return : null, cs: t ? t.cond_supply : null,
    sp: s.setpoint_f, ssp: s.supply_setpoint_f,
    vd: s.valves_pct.dephlegmator, vc: s.valves_pct.condenser, vs: s.valves_pct.supply,
    mode: s.mode, profile: s.profile, fault: s.fault,
  };
}

function windowBounds() {
  const now = data.now || Date.now() / 1000;
  const end = ui.end == null ? now : ui.end;
  return { now, end, start: end - ui.span };
}

export async function load({ quiet = false } = {}) {
  const mine = ++seq;
  if (!quiet) {
    data.status = 'loading';
    data.pts = [];
    data.events = [];
    data.segments = [];
    render();
  }
  data.now = Date.now() / 1000;
  const { start, end } = windowBounds();
  const step = Math.max(1, ui.span / TARGET_POINTS);
  const q = `since=${start.toFixed(3)}&until=${end.toFixed(3)}`;
  try {
    const [rows, ev] = await Promise.all([
      get(`/api/history?${q}&step=${step.toFixed(3)}&limit=5000`),
      get(`/api/events?${q}`),
    ]);
    if (mine !== seq) return;
    data.pts = rows.map(toPoint);
    data.events = ev.events;
    data.segments = ev.segments;
    data.start = start;
    data.end = end;
    data.step = step;
    data.status = 'ok';
  } catch (e) {
    if (mine !== seq) return;
    if (!quiet) data.status = 'failed';
  }
  render();
}

// -- window controls ---------------------------------------------------------

function setWindow(span, end) {
  const now = Date.now() / 1000;
  const e = Math.max(now - KEEP_S + span, Math.min(now, end));
  ui.span = span;
  ui.end = e >= now - 1 ? null : e;
  load();
}

function cursorInWindow() {
  const { start, end } = windowBounds();
  return ui.cursor != null && ui.cursor >= start && ui.cursor <= end;
}

// -- render ------------------------------------------------------------------

function nearestPoint(t) {
  let best = null, bd = Infinity;
  for (const p of data.pts) {
    const d = Math.abs(p.t - t);
    if (d < bd) { bd = d; best = p; }
  }
  const gap = Math.max(60, data.step * 3);
  return best && bd <= gap ? best : null;
}

// Header only: cheap enough to run on every stream message.
export function renderStatus() {
  if (store.route !== 'history') return;
  const s = store.state;
  $('#h-conn').classList.toggle('lost', store.lost);
  $('#h-conn-label').textContent = store.lost ? 'NO STREAM' : 'LIVE';
  $('#h-conn-detail').textContent = s ? `${s.mode.toUpperCase()} · ${s.profile.toUpperCase()}` : '';
}

export function render() {
  if (store.route !== 'history') return;
  renderStatus();
  const { now, end, start } = windowBounds();
  const span = ui.span;
  const blank = data.status !== 'ok' || (data.pts.length === 0 && data.segments.length === 0);
  $('#h-failed').hidden = data.status !== 'failed';

  // window bar
  for (const b of $$('#h-spans button')) b.setAttribute('aria-pressed', String(Number(b.dataset.span) === span));
  const atNow = ui.end == null;
  $('#h-earlier').disabled = start <= now - KEEP_S;
  $('#h-later').disabled = atNow;
  $('#h-now').disabled = atNow;
  const sameDay = fmtTime(start, 'day') === fmtTime(end, 'day');
  $('#h-window').textContent = sameDay
    ? `${fmtTime(start, 'day')} · ${fmtTime(start, 'hm')} – ${fmtTime(end, 'hm')}`
    : `${fmtTime(start, 'dayhm')} – ${fmtTime(end, 'dayhm')}`;
  $('#h-note').textContent = atNow ? 'Ends now · follows live data' : `${span / 3600} h window`;
  $('#h-export').disabled = blank;

  // legend
  for (const b of $$('#h-legend button')) b.setAttribute('aria-pressed', String(!ui.hidden[b.dataset.key]));

  // temperature + valve plots
  const win = data.pts.filter((p) => p.t >= start && p.t <= end);
  const [lo, hi] = yRange(win, ui.hidden);
  const gap = Math.max(60, data.step * 3);
  for (const q of SERIES) {
    $('#hl-' + q.k).setAttribute('d', ui.hidden[q.k] ? '' : linePath(win, q.k, start, span, lo, hi, 300, gap));
    if (q.sp) $('#hl-' + q.sp).setAttribute('d', ui.hidden[q.k] ? '' : linePath(win, q.sp, start, span, lo, hi, 300, gap));
  }
  for (const k of ['vd', 'vc', 'vs']) $('#hl-' + k).setAttribute('d', linePath(win, k, start, span, 0, 100, 150, gap));
  const yt = ticks(lo, hi);
  $$('#h-yaxis span').forEach((el, i) => { el.textContent = yt[i]; });
  const long = span > 86400;
  $$('#h-xaxis span').forEach((el, i) => { el.textContent = fmtTime(start + (span * i) / 4, long ? 'dayhm' : 'hm'); });

  // mode / fault strip
  const strip = $('#h-strip');
  strip.replaceChildren();
  for (const g of data.segments) {
    const l = Math.max(0, (g.start - start) / span);
    const w = Math.min(1 - l, Math.max(0, g.end - Math.max(g.start, start)) / span);
    if (w <= 0 && l >= 1) continue;
    const look = MODE_LOOK[g.kind] || MODE_LOOK.auto;
    const el = document.createElement('div');
    el.style.cssText = `left:${(l * 100).toFixed(3)}%;width:${(w * 100).toFixed(3)}%;min-width:${g.kind === 'auto' ? 0 : 4}px;background:${look.bg};color:${look.fg}`;
    el.textContent = w > 0.08 ? look.label : '';
    strip.append(el);
  }

  // blank overlay
  const blankEl = $('#h-blank');
  if (data.status === 'loading') {
    $('#h-blank-title').textContent = 'LOADING HISTORY…';
    $('#h-blank-body').textContent = 'Fetching the logged states for this window from the controller.';
    blankEl.hidden = false;
  } else if (data.status === 'failed') {
    $('#h-blank-title').textContent = 'HISTORY NOT LOADED';
    $('#h-blank-body').textContent = 'Use Retry in the banner above.';
    blankEl.hidden = false;
  } else if (blank) {
    $('#h-blank-title').textContent = 'NO DATA IN THIS WINDOW';
    $('#h-blank-body').textContent = 'The controller logged nothing between these times. Logging runs only while the controller is on, and the last 14 days are kept.';
    blankEl.hidden = false;
  } else {
    blankEl.hidden = true;
  }

  // cursor + reading
  let ct = ui.cursor;
  if (ct == null || ct < start || ct > end) ct = data.pts.length ? data.pts[data.pts.length - 1].t : end;
  const cursorEl = $('#h-cursor');
  cursorEl.hidden = blank;
  cursorEl.style.left = (((ct - start) / span) * 100).toFixed(3) + '%';
  renderReading(blank ? null : nearestPoint(ct), ct);

  renderEvents(start, end, long);
}

function renderReading(pt, ct) {
  $('#r-time').textContent = fmtTime(pt ? pt.t : ct, 'full');
  // Mode and fault come from the full-resolution segments and events: a short
  // fault can fall between two thinned points.
  const seg = pt && data.segments.filter((g) => g.start <= ct && ct <= g.end + 1).pop(); // touching segments: the later one
  const kind = seg ? seg.kind : pt ? (pt.fault ? 'fault' : pt.mode) : null;
  let faultText = '';
  if (kind === 'fault') {
    const ev = data.events.filter((e) => e.kind === 'fault' && e.tone === 'red' && e.timestamp <= ct).pop();
    faultText = ev ? ev.text : pt.fault || '';
  }
  const chip = $('#r-chip');
  chip.textContent = !pt ? 'NO DATA' : kind === 'fault' ? 'FAULT' : `${kind.toUpperCase()} · ${pt.profile.toUpperCase()}`;
  chip.className = 'chip' + (kind === 'fault' ? ' red' : kind === 'manual' ? ' amber-solid' : kind === 'off' ? ' amber' : '');
  const ft = $('#r-fault');
  ft.hidden = kind !== 'fault';
  ft.textContent = faultText;
  const rows = [
    ['Deph return', '#d95926', pt && fmt1(pt.dr) + ' °F'], ['Deph setpoint', 'transparent', pt && fmt1(pt.sp) + ' °F'],
    ['Deph supply', '#3987e5', pt && fmt1(pt.ds) + ' °F'], ['Supply setpoint', 'transparent', pt && fmt1(pt.ssp) + ' °F'],
    ['Cond return', '#c98500', pt && fmt1(pt.cr) + ' °F'], ['Cond supply', '#199e70', pt && fmt1(pt.cs) + ' °F'],
    ['Dephlegmator valve', '#d95926', pt && pct(pt.vd)], ['Condenser valve', '#c98500', pt && pct(pt.vc)],
    ['Supply valve', '#3987e5', pt && pct(pt.vs)],
  ];
  $('#r-rows').replaceChildren(...rows.map(([label, color, value]) => {
    const r = document.createElement('div');
    r.className = 'rw';
    const sw = document.createElement('i');
    sw.style.background = color;
    const l = document.createElement('span');
    l.textContent = label;
    const b = document.createElement('b');
    b.textContent = value || '—';
    r.append(sw, l, b);
    return r;
  }));
}

function renderEvents(start, end, long) {
  const evs = data.events.filter((e) => e.timestamp >= start && e.timestamp <= end).reverse();
  $('#ev-note').textContent = evs.length ? `${evs.length} in window, newest first` : '';
  const list = $('#ev-list');
  if (!evs.length) {
    const none = document.createElement('div');
    none.className = 'none';
    none.textContent = data.status === 'ok' ? 'No mode, setpoint, profile or fault changes in this window.' : '';
    list.replaceChildren(none);
    return;
  }
  list.replaceChildren(...evs.slice(0, MAX_EVENTS_SHOWN).map((e) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.time = e.timestamp;
    const head = document.createElement('span');
    head.style.cssText = 'display:flex;align-items:center;gap:8px';
    const tag = document.createElement('span');
    tag.className = 'tag' + (e.tone ? ' ' + e.tone : '');
    tag.textContent = e.kind.toUpperCase();
    const when = document.createElement('span');
    when.className = 'when';
    when.textContent = fmtTime(e.timestamp, long ? 'dayhms' : 'hms');
    head.append(tag, when);
    const txt = document.createElement('span');
    txt.className = 'txt';
    txt.textContent = e.text;
    b.append(head, txt);
    return b;
  }));
}

// -- events ------------------------------------------------------------------

async function exportCsv() {
  const { start, end } = windowBounds();
  try {
    const blob = await getBlob(`/api/history.csv?since=${start.toFixed(3)}&until=${end.toFixed(3)}`);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `still-history-${fmtTime(start, 'dayhm').replace(/[ :]/g, '-')}.csv`;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  } catch (e) {
    if (e.status !== 401) toast('Could not export the CSV: ' + e.message);
  }
}

export function init() {
  $('#h-spans').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-span]');
    if (!b) return;
    const { end } = windowBounds();
    const span = Number(b.dataset.span);
    // zoom around the cursor when one is set, so a run picked in 14 D can be opened in 4 H
    setWindow(span, cursorInWindow() ? ui.cursor + span / 2 : end);
  });
  $('#h-earlier').addEventListener('click', () => setWindow(ui.span, windowBounds().end - ui.span / 2));
  $('#h-later').addEventListener('click', () => setWindow(ui.span, windowBounds().end + ui.span / 2));
  $('#h-now').addEventListener('click', () => { ui.cursor = null; setWindow(ui.span, Infinity); });
  $('#h-retry').addEventListener('click', () => load());
  $('#h-export').addEventListener('click', exportCsv);
  $('#h-legend').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-key]');
    if (b) { ui.hidden = { ...ui.hidden, [b.dataset.key]: !ui.hidden[b.dataset.key] }; render(); }
  });
  $('#ev-list').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-time]');
    if (b) { ui.cursor = Number(b.dataset.time); render(); }
  });

  // tap or drag on any plot pins the cursor; a bare mouse hover does not move it
  const move = (e) => {
    if (e.type === 'pointermove' && e.pointerType === 'mouse' && e.buttons === 0) return;
    const r = e.currentTarget.getBoundingClientRect();
    if (!r.width) return;
    const { start } = windowBounds();
    ui.cursor = Math.round(start + Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * ui.span);
    render();
  };
  for (const el of $$('[data-plot]')) {
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerdown', move);
  }

  setInterval(() => {
    if (store.route === 'history' && ui.end == null && data.status === 'ok' && !document.hidden) load({ quiet: true });
  }, REFRESH_MS);
}
