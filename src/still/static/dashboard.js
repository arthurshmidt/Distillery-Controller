// The dashboard screen: header controls, process mimic, trend chart, loop
// cards and the Advanced · PID tab. render() paints the DOM from the store.

import { get, put } from './api.js';
import { store, emit, pointFromState } from './state.js';
import { SERIES, layout, nearest, clock, fmt1 } from './chart.js';

const AMBER = '#fab219', LINE = '#3a4452', MUTED = '#9aa7b5', DARK = '#06080b';
const RED = '#b3261e', RED_BD = '#ff9d94';

const ui = { tab: 'process', range: 900, hidden: {}, hover: null, confirm: null };
const spLocal = { deph: null, sup: null };      // setpoint shown before the debounced PUT lands
const spTimer = {};
const valveDrag = {};                            // valve name -> value while the thumb is held
const valveSent = {};                            // valve name -> ms of last PUT during a drag
const draft = { deph: null, sup: null };         // unsaved PID gain edits

let toast = () => {};
export function setToast(fn) { toast = fn; }

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const pct = (v) => (v == null || isNaN(v) ? '—' : Math.round(v) + '%');
const f2 = (v) => (v == null || isNaN(v) ? '—' : v.toFixed(2));

function apply(state) {
  store.state = state;
  emit();
}

async function command(fn) {
  try {
    apply(await fn());
  } catch (e) {
    if (e.status === 409) {
      toast('Valve not changed: ' + e.message);
      try { apply(await get('/api/state')); } catch (e2) { /* stream will catch up */ }
    } else if (e.status !== 401) {
      toast(e.message || 'Request failed');
    }
    emit();
  }
}

export async function loadHistory() {
  const now = Date.now() / 1000;
  try {
    const rows = await get(`/api/history?since=${Math.floor(now - ui.range)}&limit=7200`);
    const fetched = rows.map((r) => pointFromState(r.state, r.timestamp));
    const last = fetched.length ? fetched[fetched.length - 1].t : 0;
    store.hist = fetched.concat(store.hist.filter((p) => p.t > last));
    emit();
  } catch (e) { /* trend fills from the stream instead */ }
}

export async function loadProfiles() {
  try {
    store.profiles = await get('/api/profiles');
    emit();
  } catch (e) { /* buttons stay empty until the next try */ }
}

// -- derived view model ------------------------------------------------------

function model() {
  const s = store.state;
  const mode = s ? s.mode : 'auto';
  const fault = s ? s.fault : null;
  const failsafe = !!fault || mode === 'off';
  const v = s ? s.valves_pct : {};
  const supplyV = v.supply == null ? null : v.supply;
  return { s, mode, fault, failsafe, v, supplyV, lost: store.lost, ready: !!s };
}

function tagInfo(name, m) {
  const val = name === 'supply' ? m.supplyV : m.v[name];
  const base = { bg: '#0c0f13' };
  if (name === 'supply') {
    if (val == null) return { ...base, label: 'NO CMD', value: '—', bd: m.fault ? RED_BD : LINE, lab: MUTED };
    if (m.fault) return { ...base, label: 'HOLDING', value: pct(val), bd: RED_BD, lab: RED_BD };
    if (m.mode === 'off') return { ...base, label: 'HOLDING', value: pct(val), bd: AMBER, lab: AMBER };
  } else {
    if (m.fault) return { label: 'FAILSAFE', value: pct(val), bg: RED, bd: RED_BD, lab: '#ffffff' };
    if (m.mode === 'off') return { ...base, label: 'FAILSAFE', value: pct(val), bd: AMBER, lab: AMBER };
  }
  return { ...base, label: 'VALVE', value: pct(val), bd: m.mode === 'manual' ? AMBER : '#dfe6ee', lab: MUTED };
}

function chipInfo(name, m) {
  if (name === 'sup' && m.supplyV == null) return ['NO COMMAND', m.fault ? 'chip red-outline' : 'chip'];
  if (m.fault) return name === 'sup' ? ['HOLDING', 'chip red-outline'] : ['FAILSAFE', 'chip red'];
  if (m.mode === 'off') return [name === 'sup' ? 'HOLDING' : 'OFF · 100%', 'chip amber'];
  if (m.mode === 'manual') return ['MANUAL', 'chip amber-solid'];
  return [name === 'cond' ? 'HELD 100%' : 'PID', 'chip'];
}

function activeLimits() {
  if (!store.profiles || !store.state) return null;
  const p = store.profiles.profiles.find((x) => x.name === store.state.profile);
  return p ? p.output_limits : null;
}

function hintInfo(name, m) {
  if (name === 'sup' && m.supplyV == null) return 'Not commanded yet';
  if (m.fault) return name === 'sup' ? 'Failsafe: holding last position' : 'Failsafe: 100%, full flow';
  if (m.mode === 'off') return name === 'sup' ? 'Off: holding last position' : 'Off: 100%, full flow';
  if (m.mode === 'manual') return 'Manual: drag to set';
  if (name === 'deph') {
    const l = activeLimits();
    return l ? `PID output, limits ${l[0]}–${l[1]}%` : 'PID output';
  }
  if (name === 'sup') return 'PID output';
  return 'Auto: held at 100%';
}

function dev(pv, sp) {
  if (pv == null) return '';
  const d = Math.round((pv - sp) * 10) / 10;
  return 'Δ ' + (d >= 0 ? '+' : '−') + Math.abs(d).toFixed(1) + '°';
}

// -- render ------------------------------------------------------------------

export function render() {
  const m = model();
  const { s } = m;
  const disabled = m.lost || !m.ready;
  const app = $('#app');
  app.classList.toggle('stale', m.lost);

  // header
  for (const b of $$('#modes button')) {
    b.setAttribute('aria-pressed', String(m.mode === b.dataset.mode));
    b.disabled = disabled;
  }
  const names = store.profiles ? store.profiles.profiles.map((p) => p.name) : [];
  const box = $('#profiles');
  if (box.children.length !== names.length) {
    box.replaceChildren(...names.map((n) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.dataset.profile = n;
      b.textContent = n.toUpperCase();
      b.style.minWidth = '92px';
      return b;
    }));
  }
  for (const b of $$('#profiles button')) {
    b.setAttribute('aria-pressed', String(s && s.profile === b.dataset.profile));
    b.disabled = disabled;
  }
  const at = store.lastMsg / 1000;
  $('#conn').classList.toggle('lost', m.lost);
  $('#conn-label').textContent = m.lost ? 'NO STREAM' : 'LIVE';
  $('#conn-detail').textContent = (m.lost ? 'Last data ' : 'Updated ') + (store.lastMsg ? clock(at, true) : '—');

  // tabs
  for (const b of $$('#tabs button')) b.setAttribute('aria-pressed', String(ui.tab === b.dataset.tab));
  $('#tab-process').hidden = ui.tab !== 'process';
  $('#tab-adv').hidden = ui.tab !== 'adv';

  // temperatures everywhere
  const temps = s && s.temps_f;
  for (const el of $$('[data-t]')) el.textContent = temps ? fmt1(temps[el.dataset.t]) : '—';

  // mimic valve tags
  for (const tag of $$('[data-vtag]')) {
    const t = tagInfo(tag.dataset.vtag, m);
    tag.style.background = t.bg;
    tag.style.borderColor = t.bd;
    tag.children[0].textContent = t.label;
    tag.children[0].style.color = t.lab;
    tag.children[1].textContent = t.value;
  }

  // loop cards
  const sliders = disabled || !!m.fault || m.mode !== 'manual';
  const cards = {
    deph: { valve: 'dephlegmator', pv: temps && temps.deph_return, sp: s && s.setpoint_f },
    sup: { valve: 'supply', pv: temps && temps.deph_supply, sp: s && s.supply_setpoint_f },
    cond: { valve: 'condenser' },
  };
  for (const [key, c] of Object.entries(cards)) {
    const card = $(`[data-card="${key}"]`);
    const [chipText, chipCls] = chipInfo(key, m);
    const chip = $('[data-f="chip"]', card);
    chip.textContent = chipText;
    chip.className = chipCls;
    $('[data-f="hint"]', card).textContent = m.ready ? hintInfo(key, m) : '';

    const slider = $('input[type="range"]', card);
    const raw = key === 'sup' ? m.supplyV : m.v[c.valve];
    const shown = valveDrag[c.valve] != null ? valveDrag[c.valve] : raw;
    $('[data-f="valve"]', card).textContent = pct(shown);
    slider.disabled = sliders;
    if (valveDrag[c.valve] == null) slider.value = shown == null ? 0 : Math.round(shown);
    const p = (shown == null ? 0 : Math.round(shown)) + '%';
    const fill = m.mode === 'manual' && !m.fault ? AMBER : '#8fa0b3';
    slider.style.background = `linear-gradient(to right, ${fill} 0%, ${fill} ${p}, #2a323c ${p}, #2a323c 100%) center / 100% 10px no-repeat`;

    if (c.sp !== undefined) {
      const sp = spLocal[key] != null ? spLocal[key] : c.sp;
      $('[data-f="sp"]', card).textContent = sp == null ? '—' : sp.toFixed(1);
      $('[data-f="dev"]', card).textContent = sp == null ? '' : dev(c.pv, sp);
      for (const b of $$('[data-f="up"],[data-f="down"]', card)) b.disabled = disabled;
    }
  }

  renderChart();
  renderAdvanced(m);
}

function renderChart() {
  for (const b of $$('#ranges button')) b.setAttribute('aria-pressed', String(Number(b.dataset.range) === ui.range));
  for (const b of $$('#legend button')) b.setAttribute('aria-pressed', String(!ui.hidden[b.dataset.key]));
  const tEnd = store.lastMsg ? store.lastMsg / 1000 : Date.now() / 1000;
  const L = layout(store.hist, ui.range, ui.hidden, tEnd);
  for (const [k, d] of Object.entries(L.paths)) $('#ln-' + k).setAttribute('d', d || 'M0 0');
  $$('#yaxis span').forEach((el, i) => { el.textContent = L.yTicks[i]; });
  $$('#xaxis span').forEach((el, i) => { el.textContent = L.xTicks[i]; });

  const cur = $('#cursor'), tip = $('#tip');
  if (ui.hover == null) { cur.hidden = true; tip.hidden = true; return; }
  const f = ui.hover;
  const t = tEnd - ui.range * (1 - f);
  const pt = nearest(L.pts, t, ui.range);
  cur.hidden = false;
  cur.style.left = (f * 100).toFixed(2) + '%';
  tip.hidden = false;
  tip.style.left = f > 0.55 ? '' : `calc(${(f * 100).toFixed(2)}% + 12px)`;
  tip.style.right = f > 0.55 ? `calc(${((1 - f) * 100).toFixed(2)}% + 12px)` : '';
  const rows = pt ? [
    ['Deph return', '#d95926', pt.dr], ['Deph setpoint', '#d95926', pt.sp],
    ['Deph supply', '#3987e5', pt.ds], ['Supply setpoint', '#3987e5', pt.ssp],
    ['Cond return', '#c98500', pt.cr], ['Cond supply', '#199e70', pt.cs],
  ] : [];
  tip.innerHTML = '';
  const head = document.createElement('div');
  head.className = 't';
  head.textContent = clock(t, true);
  tip.append(head);
  if (!rows.length) tip.append(Object.assign(document.createElement('div'), { textContent: 'No data' }));
  for (const [label, color, v] of rows) {
    const r = document.createElement('div');
    r.className = 'r';
    const sw = document.createElement('i');
    sw.style.background = color;
    const l = document.createElement('span');
    l.textContent = label;
    const b = document.createElement('b');
    b.textContent = fmt1(v);
    r.append(sw, l, b);
    tip.append(r);
  }
}

function renderAdvanced(m) {
  if (ui.tab !== 'adv') return;
  const s = m.s;
  const loops = {
    deph: s && { g: s.pid_gains, t: s.pid_terms, valve: m.v.dephlegmator },
    sup: s && { g: s.supply_pid_gains, t: s.supply_pid_terms, valve: m.supplyV },
  };
  for (const [key, L] of Object.entries(loops)) {
    const box = $(`.pidbox[data-loop="${key}"]`);
    if (!L) continue;
    for (const inp of $$('input[data-g]', box)) {
      if (draft[key] == null && document.activeElement !== inp) inp.value = String(L.g[inp.dataset.g]);
      inp.disabled = m.lost;
    }
    const nums = draft[key] ? ['p', 'i', 'd'].map((g) => (draft[key][g].trim() === '' ? NaN : Number(draft[key][g]))) : [];
    const bad = nums.some((x) => !isFinite(x));
    $('[data-f="warn"]', box).hidden = !bad;
    $('[data-f="apply"]', box).disabled = !draft[key] || bad || m.lost;
    const zero = m.fault ? [0, 0, 0] : [L.t.p, L.t.i, L.t.d];
    $('[data-f="tp"]', box).textContent = f2(zero[0]);
    $('[data-f="ti"]', box).textContent = f2(zero[1]);
    $('[data-f="td"]', box).textContent = f2(zero[2]);
    $('[data-f="valve"]', box).textContent = pct(L.valve);
  }
  const prof = $('.pidbox[data-loop="deph"] [data-f="profile"]');
  prof.textContent = s ? s.profile[0].toUpperCase() + s.profile.slice(1) + ' profile' : '';
  const l = activeLimits();
  $('.pidbox[data-loop="deph"] [data-f="limits"]').textContent = l ? `${l[0]}–${l[1]}%` : '—';
}

// -- confirm dialog ----------------------------------------------------------

const CONFIRM = {
  manual: {
    title: 'Switch to MANUAL?',
    cta: 'SWITCH TO MANUAL',
    items: ['PID control stops on both loops.', 'All three valves hold their current positions until you move them.'],
  },
  off: {
    title: 'Switch control OFF?',
    cta: 'SWITCH OFF',
    items: [
      'PID control stops on both loops.',
      'Dephlegmator and condenser valves go to 100%: full flow to each stage, no bypass (failsafe position).',
      'Supply valve holds its last position.',
    ],
  },
};

function openConfirm(mode) {
  ui.confirm = mode;
  const c = CONFIRM[mode];
  $('#cf-title').textContent = c.title;
  $('#cf-ok').textContent = c.cta;
  $('#cf-list').replaceChildren(...c.items.map((t) => Object.assign(document.createElement('li'), { textContent: t })));
  $('#confirm').hidden = false;
  $('#cf-cancel').focus();
}

export function closeConfirm() {
  ui.confirm = null;
  $('#confirm').hidden = true;
}

export function confirmOpen() { return ui.confirm != null; }

// -- events ------------------------------------------------------------------

function bumpSetpoint(key, by) {
  const s = store.state;
  if (!s) return;
  const base = spLocal[key] != null ? spLocal[key] : key === 'deph' ? s.setpoint_f : s.supply_setpoint_f;
  spLocal[key] = Math.max(-40, Math.min(300, base + by));
  render();
  clearTimeout(spTimer[key]);
  spTimer[key] = setTimeout(async () => {
    const value = spLocal[key];
    await command(() => put(key === 'deph' ? '/api/setpoint' : '/api/supply/setpoint', { setpoint_f: value }));
    spLocal[key] = null;
    render();
  }, 300);
}

function sendValve(name, value) {
  return command(() => put('/api/valves/' + name, { percent: value }));
}

export function init() {
  $('#modes').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-mode]');
    if (!b || !store.state || b.dataset.mode === store.state.mode) return;
    if (b.dataset.mode === 'auto') command(() => put('/api/mode', { mode: 'auto' }));
    else openConfirm(b.dataset.mode);
  });
  $('#profiles').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-profile]');
    if (b && store.state && b.dataset.profile !== store.state.profile) {
      draft.deph = null;
      command(() => put('/api/profile', { name: b.dataset.profile }));
    }
  });
  $('#cf-cancel').addEventListener('click', closeConfirm);
  $('#cf-ok').addEventListener('click', () => {
    const mode = ui.confirm;
    closeConfirm();
    command(() => put('/api/mode', { mode }));
  });

  $('#tabs').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-tab]');
    if (b) { ui.tab = b.dataset.tab; render(); }
  });
  $('#ranges').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-range]');
    if (b) { ui.range = Number(b.dataset.range); render(); loadHistory(); }
  });
  $('#legend').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-key]');
    if (b) { ui.hidden = { ...ui.hidden, [b.dataset.key]: !ui.hidden[b.dataset.key] }; render(); }
  });

  const plot = $('#plot');
  const hover = (e) => {
    const r = plot.getBoundingClientRect();
    if (r.width) { ui.hover = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)); renderChart(); }
  };
  plot.addEventListener('pointermove', hover);
  plot.addEventListener('pointerdown', hover);
  plot.addEventListener('pointerleave', () => { ui.hover = null; renderChart(); });

  for (const card of $$('[data-card]')) {
    const key = card.dataset.card;
    $('[data-f="up"]', card)?.addEventListener('click', () => bumpSetpoint(key, 1));
    $('[data-f="down"]', card)?.addEventListener('click', () => bumpSetpoint(key, -1));
    const slider = $('input[type="range"]', card);
    const name = slider.dataset.valve;
    slider.addEventListener('input', () => {
      valveDrag[name] = Number(slider.value);
      render();
      const now = Date.now();
      if (now - (valveSent[name] || 0) > 250) { valveSent[name] = now; sendValve(name, Number(slider.value)); }
    });
    slider.addEventListener('change', async () => {
      const value = Number(slider.value);
      await sendValve(name, value);
      valveDrag[name] = null;
      render();
    });
  }

  for (const box of $$('.pidbox')) {
    const key = box.dataset.loop;
    box.addEventListener('input', (e) => {
      if (!e.target.dataset.g) return;
      draft[key] = Object.fromEntries($$('input[data-g]', box).map((i) => [i.dataset.g, i.value]));
      render();
    });
    $('[data-f="apply"]', box).addEventListener('click', async () => {
      const d = draft[key];
      if (!d) return;
      const body = { p: Number(d.p), i: Number(d.i), d: Number(d.d) };
      draft[key] = null;
      await command(() => put(key === 'deph' ? '/api/pid' : '/api/supply/pid', body));
    });
  }
}
