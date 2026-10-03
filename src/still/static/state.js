// One copy of the app state, shared by every screen. Modules mutate it and
// call emit(); the active screen re-renders from it.

export const store = {
  state: null,          // latest StateModel from the stream or a PUT response
  profiles: null,       // GET /api/profiles
  hist: [],             // trend points: {t, ds, dr, cs, cr, sp, ssp}, oldest first
  lost: false,          // stream dropped or silent for ~5 s
  lastMsg: 0,           // ms timestamp of the last stream message
  signedIn: false,
  route: 'dashboard',
};

const listeners = [];
export const onChange = (fn) => listeners.push(fn);
export const emit = () => listeners.forEach((fn) => fn());

export function pointFromState(s, t) {
  const v = s.temps_f;
  return {
    t,
    ds: v ? v.deph_supply : null,
    dr: v ? v.deph_return : null,
    cs: v ? v.cond_supply : null,
    cr: v ? v.cond_return : null,
    sp: s.setpoint_f,
    ssp: s.supply_setpoint_f,
  };
}
