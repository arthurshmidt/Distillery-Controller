// Thin fetch wrapper. Every /api call sends the bearer token; 401 is reported
// through `onUnauthorized` so the app can show the login screen.

const KEY = 'still.token';

let memToken = ''; // used when localStorage is blocked

export const auth = {
  get token() {
    try { return localStorage.getItem(KEY) || memToken; } catch (e) { return memToken; }
  },
  set(token) {
    memToken = token;
    try { localStorage.setItem(KEY, token); } catch (e) { /* page-only token */ }
  },
  clear() {
    memToken = '';
    try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ }
  },
};

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

let unauthorizedHandler = () => {};
export function onUnauthorized(fn) { unauthorizedHandler = fn; }

export async function request(method, path, body) {
  const headers = { Authorization: 'Bearer ' + auth.token };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) {
    let message = res.statusText || String(res.status);
    try {
      const j = await res.json();
      if (typeof j.detail === 'string') message = j.detail;
      else if (Array.isArray(j.detail) && j.detail.length) message = j.detail.map((d) => d.msg).join('; ');
    } catch (e) { /* body was not JSON */ }
    if (res.status === 401) unauthorizedHandler();
    throw new ApiError(res.status, message);
  }
  return res.json();
}

export const get = (path) => request('GET', path);

// Fetch a file with the token in the header (a plain link cannot send it).
export async function getBlob(path) {
  const res = await fetch(path, { headers: { Authorization: 'Bearer ' + auth.token } });
  if (!res.ok) {
    if (res.status === 401) unauthorizedHandler();
    throw new ApiError(res.status, res.statusText || String(res.status));
  }
  return res.blob();
}
export const put = (path, body) => request('PUT', path, body);
