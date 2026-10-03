// Entry point: login, the live stream, hash routing and the global banners.

import { auth, get, onUnauthorized } from './api.js';
import { store, emit, onChange, pointFromState } from './state.js';
import * as dashboard from './dashboard.js';
import * as history from './history.js';
import * as settings from './settings.js';
import { clock } from './chart.js';

const $ = (sel) => document.querySelector(sel);

const STALE_MS = 5000;
const KEEP_S = 3600;

let source = null;
let retryTimer = null;
let backoff = 1000;

// -- toast --------------------------------------------------------------------

let toastTimer = null;
function toast(message) {
  let el = $('#toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast';
    el.setAttribute('role', 'status');
    el.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:40;max-width:90vw;padding:12px 18px;background:#0c0f13;border:2px solid #fab219;border-radius:6px;color:#e6ebf1;font-size:15px';
    document.body.append(el);
  }
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 5000);
}
dashboard.setToast(toast);
history.setToast(toast);
settings.setToast(toast);

// -- login ----------------------------------------------------------------------

function showLogin(rejected) {
  closeStream();
  store.signedIn = false;
  store.rejected = !!rejected;
  $('#login').hidden = false;
  $('#lg-error').hidden = !rejected;
  $('#lg-token').value = '';
  $('#lg-token').focus();
  emit();
}

onUnauthorized(() => showLogin(true));

$('#login-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const token = $('#lg-token').value.trim();
  if (!token) return;
  auth.set(token);
  start();
});

function signOut() {
  auth.clear();
  store.state = null;
  store.hist = [];
  showLogin(false);
}

function useToken(token) {
  auth.set(token);
  store.rejected = false;
  start();
}

// -- stream ---------------------------------------------------------------------

function closeStream() {
  clearTimeout(retryTimer);
  if (source) { source.close(); source = null; }
}

function onState(state) {
  const now = Date.now();
  store.state = state;
  store.lastMsg = now;
  store.lost = false;
  backoff = 1000;
  store.hist.push(pointFromState(state, now / 1000));
  const cutoff = now / 1000 - KEEP_S;
  while (store.hist.length && store.hist[0].t < cutoff) store.hist.shift();
  emit();
}

function connect() {
  closeStream();
  if (!auth.token) return;
  source = new EventSource('/api/stream?token=' + encodeURIComponent(auth.token));
  source.onmessage = (e) => onState(JSON.parse(e.data));
  source.onerror = async () => {
    // EventSource errors carry no status. Close, then probe once to tell a
    // rejected token from a network drop.
    closeStream();
    store.lost = true;
    emit();
    try {
      await get('/api/state');
    } catch (e) {
      if (e.status === 401) return; // onUnauthorized already showed the login
    }
    retryTimer = setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 10000);
  };
}

async function start() {
  $('#login').hidden = true;
  store.rejected = false;
  if (!auth.token) { showLogin(false); return; }
  try {
    store.state = await get('/api/state');
    store.lastMsg = Date.now();
    store.lost = false;
    store.signedIn = true;
  } catch (e) {
    if (e.status === 401) return; // login already showing
    store.lost = true; // daemon unreachable: keep retrying below
    store.signedIn = true;
  }
  emit();
  connect();
  dashboard.loadProfiles();
  dashboard.loadHistory();
  if (store.route === 'history') history.load();
  if (store.route === 'settings') settings.load();
}

$('#retry').addEventListener('click', () => { backoff = 1000; connect(); });

// The stream sends once a second; silence for ~5 s counts as a drop.
setInterval(() => {
  if (store.signedIn && store.state && !store.lost && Date.now() - store.lastMsg > STALE_MS) {
    store.lost = true;
    emit();
  }
  if (store.lost) renderBanners();
}, 1000);

// -- routing and banners ------------------------------------------------------------

// #/tune is the dashboard opened on the Advanced · PID tab (linked from Settings).
const ROUTES = { '': 'dashboard', '/': 'dashboard', '/tune': 'dashboard', '/history': 'history', '/settings': 'settings' };

function route() {
  const hash = location.hash.replace(/^#/, '');
  store.route = ROUTES[hash] || 'dashboard';
  if (hash === '/tune') dashboard.showTab('adv');
  for (const name of ['dashboard', 'history', 'settings']) {
    $('#view-' + name).hidden = store.route !== name;
  }
  $('#view-' + store.route).querySelector('header').after($('#banners'));
  if (store.route === 'history' && store.signedIn) history.load();
  if (store.route === 'settings' && store.signedIn) settings.load();
  emit();
}
window.addEventListener('hashchange', route);

function renderBanners() {
  const s = store.state;
  const fault = s && s.fault;
  $('#bn-fault').hidden = !fault;
  if (fault) {
    $('#fault-text').textContent = fault;
    const sv = s.valves_pct.supply;
    $('#fault-supply').textContent = sv == null ? 'Supply valve not commanded yet' : `Supply valve holding at ${Math.round(sv)}%`;
  }
  $('#fault-go').hidden = store.route === 'dashboard';
  $('#bn-lost').hidden = !store.lost;
  if (store.lost) {
    $('#lost-at').textContent = store.lastMsg ? clock(store.lastMsg / 1000, true) : '—';
    $('#lost-ago').textContent = store.lastMsg ? Math.round((Date.now() - store.lastMsg) / 1000) + ' s' : '—';
  }
  $('#bn-off').hidden = !(s && s.mode === 'off' && !fault && !store.lost);
  $('#banners').hidden = $$all('#banners .banner').every((b) => b.hidden);
}
const $$all = (sel) => Array.from(document.querySelectorAll(sel));

onChange(() => {
  renderBanners();
  dashboard.render();
  history.renderStatus();
  settings.render();
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && dashboard.confirmOpen()) dashboard.closeConfirm();
  if (e.key === 'Escape' && settings.resetOpen()) settings.closeReset();
});

dashboard.init();
history.init();
settings.init({ signOut, useToken });
route();
start();
