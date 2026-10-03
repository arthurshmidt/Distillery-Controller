// The Settings screen: saved versus default tuning for every profile and the
// supply loop, connection/token, and read-only controller information.

import { auth, get, put, request } from './api.js';
import { store, emit } from './state.js';

const SP_MIN = -40, SP_MAX = 300;
const spLocal = {};          // loop key -> setpoint shown before its debounced PUT lands
const spTimer = {};
let info = null;
let infoFailed = false;
let resetTarget = null;      // 'whiskey' | 'gin' | 'supply' while the confirm is open
let hooks = { signOut: () => {}, useToken: () => {} };
let toast = () => {};

export function setToast(fn) { toast = fn; }

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

// -- data ------------------------------------------------------------------

export async function load() {
  try {
    store.profiles = await get('/api/profiles');
  } catch (e) { /* the cards keep their last values */ }
  try {
    info = await get('/api/info');
    infoFailed = false;
  } catch (e) {
    infoFailed = !info;
  }
  emit();
}

async function send(fn) {
  $('#s-error').hidden = true;
  try {
    const state = await fn();
    store.state = state;
    store.profiles = await get('/api/profiles');
  } catch (e) {
    if (e.status !== 401) {
      $('#s-error').textContent = e.message || 'Request failed';
      $('#s-error').hidden = false;
    }
  }
  emit();
}

// -- view model ------------------------------------------------------------

function loops() {
  const p = store.profiles;
  if (!p) return [];
  const out = p.profiles.map((x) => ({
    key: x.name, title: x.name.toUpperCase(), kind: 'profile', label: 'DEPHLEGMATOR SETPOINT °F',
    def: { sp: x.setpoint_f, pid: x.pid, lim: x.output_limits }, saved: x.saved,
    active: p.active === x.name,
  }));
  out.push({
    key: 'supply', title: 'SUPPLY LOOP', kind: 'supply', label: 'BATH OUTLET SETPOINT °F',
    def: { sp: p.supply.setpoint_f, pid: p.supply.pid, lim: p.supply.output_limits }, saved: p.supply.saved,
  });
  return out;
}

const fmtLim = (l) => `${l[0]}–${l[1]}%`;
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function rows(L) {
  const s = L.saved, d = L.def;
  return [
    ['P gain', String(s.pid.p), String(d.pid.p), s.pid.p !== d.pid.p],
    ['I gain', String(s.pid.i), String(d.pid.i), s.pid.i !== d.pid.i],
    ['D gain', String(s.pid.d), String(d.pid.d), s.pid.d !== d.pid.d],
    ['Output limits', fmtLim(s.output_limits), fmtLim(d.lim), !same(s.output_limits, d.lim)],
  ];
}

const icon = (path, w = 3) => {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('width', '24'); svg.setAttribute('height', '24'); svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.style.cssText = `fill:none;stroke:currentColor;stroke-width:${w};stroke-linecap:round`;
  for (const d of path) { const p = document.createElementNS(ns, 'path'); p.setAttribute('d', d); svg.append(p); }
  return svg;
};

// -- render ----------------------------------------------------------------

export function renderStatus() {
  if (store.route !== 'settings') return;
  const s = store.state;
  const rejected = store.rejected;
  $('#s-conn').classList.toggle('lost', store.lost || rejected);
  $('#s-conn-label').textContent = rejected ? 'NO ACCESS' : store.lost ? 'NO STREAM' : 'LIVE';
  $('#s-conn-detail').textContent = rejected ? 'Token rejected' : s ? `${s.mode.toUpperCase()} · ${s.profile.toUpperCase()}` : '';
  $('#s-stream').textContent = rejected ? 'Stopped' : store.lost ? 'Disconnected' : 'Live';
  const t = $('#s-token-state');
  t.textContent = rejected ? 'Rejected (401)' : auth.token ? 'Accepted' : 'None';
  t.style.color = rejected ? 'var(--amber)' : '';
}

export function render() {
  if (store.route !== 'settings') return;
  renderStatus();
  const locked = store.lost || store.rejected;

  const cards = $('#s-cards');
  // Rebuild only when the data changed shape or values, never on a stream tick.
  const sig = JSON.stringify([store.profiles, spLocal, locked, store.state && store.state.profile]);
  if (cards.dataset.sig !== sig) {
    cards.dataset.sig = sig;
    const focus = document.activeElement && cards.contains(document.activeElement) ? document.activeElement.dataset : null;
    cards.replaceChildren(...loops().map((L) => card(L, locked)));
    if (focus && focus.action) {
      // keep the keyboard on the same button after the cards are rebuilt
      const again = cards.querySelector(`button[data-action="${focus.action}"][data-key="${focus.key}"]:not(:disabled)`);
      if (again) again.focus();
    }
  }

  $('#s-info-error').hidden = !infoFailed;
  const left = $('#info-left'), right = $('#info-right');
  if (info && left.dataset.v !== JSON.stringify(info)) {
    left.dataset.v = JSON.stringify(info);
    const c = info.thermistor;
    left.replaceChildren(...[
      ['Version', info.version],
      ['Hardware', info.hardware === 'simulated' ? 'Simulated' : 'PI-SPI-DIN boards'],
      ['Loop interval', `${info.interval_s.toFixed(1)} s`],
      ['History kept', `${info.retention_days} days`],
      ['Mode at startup', info.startup_mode === 'auto' ? 'Auto, always' : info.startup_mode],
      ['Thermistors', `${c.r_fixed / 1000} kΩ · β ${c.beta} · −${c.calibration_factor.toFixed(1)} °C`],
    ].map(irow));
    const names = {
      deph_return: 'Dephlegmator return', cond_return: 'Condenser return', deph_supply: 'Dephlegmator supply',
      cond_supply: 'Condenser supply', dephlegmator: 'Dephlegmator valve', condenser: 'Condenser valve',
      supply: 'Supply valve (city water)',
    };
    const ch = [];
    for (const [k, n] of Object.entries(info.channels.ai)) ch.push([names[k] || k, `AI ${n}`]);
    for (const [k, n] of Object.entries(info.channels.ao)) ch.push([names[k] || k, `AO ${n}`]);
    ch.sort((a, b) => a[1].localeCompare(b[1]));
    right.replaceChildren(...ch.map(irow));
  }

  const input = $('#st-token');
  $('#st-save').disabled = input.value.trim() === '' || input.value.trim() === auth.token;
}

function irow([label, value]) {
  const r = el('div', 'irow');
  r.append(el('span', null, label), el('b', null, value));
  return r;
}

function card(L, locked) {
  const c = el('div', 'panel scard' + (L.active ? ' active' : ''));
  c.setAttribute('role', 'group');
  c.setAttribute('aria-label', L.title.toLowerCase() + (L.kind === 'profile' ? ' profile' : ''));

  const head = el('div', 'head');
  head.append(el('h3', null, L.title));
  if (L.kind === 'supply') head.append(el('span', 'tag shared', 'SHARED'));
  else if (L.active) head.append(el('span', 'tag', 'ACTIVE'));
  else {
    const b = el('button', 'btn', 'MAKE ACTIVE');
    b.type = 'button';
    b.disabled = locked;
    b.dataset.action = 'activate';
    b.dataset.key = L.key;
    head.append(b);
  }
  c.append(head);

  const sp = spLocal[L.key] != null ? spLocal[L.key] : L.saved.setpoint_f;
  const spl = el('div', 'spl');
  spl.append(el('span', 'lbl', L.label), el('small', null, `Default ${L.def.sp.toFixed(1)}`));
  const spr = el('div', 'spr');
  const down = el('button', 'btn');
  down.type = 'button'; down.disabled = locked; down.dataset.action = 'down'; down.dataset.key = L.key;
  down.setAttribute('aria-label', `Lower ${L.key} setpoint by 1 degree`);
  down.append(icon(['M5 12 L19 12']));
  const up = el('button', 'btn');
  up.type = 'button'; up.disabled = locked; up.dataset.action = 'up'; up.dataset.key = L.key;
  up.setAttribute('aria-label', `Raise ${L.key} setpoint by 1 degree`);
  up.append(icon(['M5 12 L19 12', 'M12 5 L12 19']));
  const num = el('div', 'num');
  num.append(el('i', 'mark' + (sp !== L.def.sp ? ' on' : '')), document.createTextNode(sp.toFixed(1)));
  spr.append(down, num, up);
  c.append(spl, spr);

  const table = el('div');
  const h = el('div', 'trow h');
  h.append(el('span', null, 'TUNING'), el('span', null, 'SAVED'), el('span', null, 'DEFAULT'));
  table.append(h);
  let changed = sp !== L.def.sp;
  for (const [label, saved, def, differs] of rows(L)) {
    changed = changed || differs;
    const r = el('div', 'trow');
    const sv = el('span', 'sv');
    sv.append(el('i', 'mark' + (differs ? ' on' : '')), document.createTextNode(saved));
    r.append(el('span', 'nm', label), sv, el('span', 'dv', def));
    table.append(r);
  }
  c.append(table);

  const foot = el('div', 'foot');
  const reset = el('button', 'btn', 'RESET');
  reset.type = 'button'; reset.dataset.action = 'reset'; reset.dataset.key = L.key;
  reset.disabled = !changed || locked;
  foot.append(reset);
  if (L.kind === 'supply' || L.active) {
    const a = el('a', null, 'TUNE ON DASHBOARD');
    a.href = '#/tune';
    a.append(icon(['M9 5 L16 12 L9 19']));
    a.lastChild.setAttribute('width', '16'); a.lastChild.setAttribute('height', '16');
    foot.append(a);
  } else {
    foot.append(el('small', null, 'Make active to tune'));
  }
  c.append(foot);
  return c;
}

// -- events ----------------------------------------------------------------

function bump(key, by) {
  const L = loops().find((x) => x.key === key);
  if (!L) return;
  const base = spLocal[key] != null ? spLocal[key] : L.saved.setpoint_f;
  spLocal[key] = Math.max(SP_MIN, Math.min(SP_MAX, base + by));
  render();
  clearTimeout(spTimer[key]);
  spTimer[key] = setTimeout(async () => {
    const value = spLocal[key];
    const url = key === 'supply' ? '/api/supply/setpoint' : `/api/profiles/${encodeURIComponent(key)}/setpoint`;
    await send(() => put(url, { setpoint_f: value }));
    spLocal[key] = null;
    render();
  }, 300);
}

function openReset(key) {
  const L = loops().find((x) => x.key === key);
  if (!L) return;
  resetTarget = key;
  $('#rs-title').textContent = `Reset ${L.kind === 'supply' ? 'the SUPPLY LOOP' : L.title} to defaults?`;
  $('#rs-effect').textContent = L.kind === 'supply' || L.active
    ? 'This loop is in use, so the change takes effect on the next control loop.'
    : 'This profile is not active, so nothing changes on the still until you make it active.';
  $('#reset-confirm').hidden = false;
  $('#rs-cancel').focus();
}

export function closeReset() {
  resetTarget = null;
  $('#reset-confirm').hidden = true;
}

export function resetOpen() { return resetTarget != null; }

export function init(h) {
  hooks = h;
  $('#s-cards').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-action]');
    if (!b) return;
    const key = b.dataset.key;
    if (b.dataset.action === 'up') bump(key, 1);
    else if (b.dataset.action === 'down') bump(key, -1);
    else if (b.dataset.action === 'reset') openReset(key);
    else if (b.dataset.action === 'activate') send(() => put('/api/profile', { name: key }));
  });
  $('#rs-cancel').addEventListener('click', closeReset);
  $('#rs-ok').addEventListener('click', async () => {
    const key = resetTarget;
    closeReset();
    spLocal[key] = null;
    await send(() => request('DELETE', key === 'supply' ? '/api/supply/overrides' : `/api/profiles/${encodeURIComponent(key)}/overrides`));
  });

  const input = $('#st-token');
  input.addEventListener('input', render);
  $('#st-show').addEventListener('click', (e) => {
    const shown = input.type === 'password';
    input.type = shown ? 'text' : 'password';
    e.currentTarget.setAttribute('aria-pressed', String(shown));
    e.currentTarget.textContent = shown ? 'HIDE' : 'SHOW';
  });
  $('#st-save').addEventListener('click', () => {
    const token = input.value.trim();
    if (!token) return;
    input.value = '';
    hooks.useToken(token);
  });
  $('#signout').addEventListener('click', () => hooks.signOut());
}
