// DOM-only controller: one pending frame for the dashboard, no React state or idle loop.
export const TILT_SELECTOR = '.football-kpi, .football-match-card, .insight-card';
export const TILT_MEDIA = '(hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)';
const properties = ['--tilt-x', '--tilt-y', '--shadow-x', '--shadow-y', '--light-x', '--light-y'];

/** @param {HTMLElement} root @param {Window} view */
export function attachDashboardTilt(root, view) {
  const media = view.matchMedia(TILT_MEDIA);
  const coarse = view.matchMedia('(any-pointer: coarse)');
  /** @type {HTMLElement | null} */
  let card = null;
  /** @type {{x: number, y: number} | null} */
  let point = null;
  let frame = 0;
  /** @type {DOMRect | null} */
  let bounds = null;
  let disposed = false;
  const reset = () => {
    if (frame) view.cancelAnimationFrame(frame);
    frame = 0;
    point = null;
    bounds = null;
    if (card) {
      card.removeAttribute('data-tilt-active');
      for (const name of properties) card.style.removeProperty(name);
    }
    card = null;
  };
  const enabled = () => !disposed && media.matches && !coarse.matches;
  const paint = () => {
    frame = 0;
    if (!card || !point || !enabled() || !root.contains(card)) { reset(); return; }
    // Cache neutral geometry until exit/scroll/resize. Reading the transformed
    // rectangle on every move would feed the previous tilt back into itself.
    const rect = bounds ?? (bounds = card.getBoundingClientRect());
    if (!rect.width || !rect.height) { reset(); return; }
    const x = Math.max(0, Math.min(1, (point.x - rect.left) / rect.width));
    const y = Math.max(0, Math.min(1, (point.y - rect.top) / rect.height));
    card.style.setProperty('--tilt-x', `${((.5 - y) * 4).toFixed(2)}deg`);
    card.style.setProperty('--tilt-y', `${((x - .5) * 4).toFixed(2)}deg`);
    card.style.setProperty('--shadow-x', `${((.5 - x) * 12).toFixed(1)}px`);
    card.style.setProperty('--shadow-y', `${(16 + y * 8).toFixed(1)}px`);
    card.style.setProperty('--light-x', `${(x * rect.width).toFixed(1)}px`);
    card.style.setProperty('--light-y', `${(y * rect.height).toFixed(1)}px`);
    card.setAttribute('data-tilt-active', 'true');
  };
  /** @param {PointerEvent} event */
  const move = event => {
    if (!enabled() || event.pointerType !== 'mouse' || event.buttons) { reset(); return; }
    const target = event.target;
    const next = target instanceof view.Element ? target.closest(TILT_SELECTOR) : null;
    if (!next || !root.contains(next)) { reset(); return; }
    if (next !== card) { reset(); card = /** @type {HTMLElement} */ (next); }
    point = {x: event.clientX, y: event.clientY};
    if (!frame) frame = view.requestAnimationFrame(paint);
  };
  /** @param {PointerEvent} event */
  const out = event => {
    if (card && (!(event.relatedTarget instanceof view.Node) || !card.contains(event.relatedTarget))) reset();
  };
  const visibility = () => { if (root.ownerDocument.hidden) reset(); };
  root.addEventListener('pointermove', move);
  root.addEventListener('pointerout', out);
  root.addEventListener('pointerleave', reset);
  root.addEventListener('pointercancel', reset);
  root.addEventListener('pointerdown', reset);
  root.addEventListener('focusin', reset);
  media.addEventListener('change', reset);
  coarse.addEventListener('change', reset);
  view.addEventListener('blur', reset);
  view.addEventListener('resize', reset);
  view.addEventListener('scroll', reset, true);
  root.ownerDocument.addEventListener('visibilitychange', visibility);
  return () => {
    disposed = true;
    reset();
    root.removeEventListener('pointermove', move);
    root.removeEventListener('pointerout', out);
    root.removeEventListener('pointerleave', reset);
    root.removeEventListener('pointercancel', reset);
    root.removeEventListener('pointerdown', reset);
    root.removeEventListener('focusin', reset);
    media.removeEventListener('change', reset);
    coarse.removeEventListener('change', reset);
    view.removeEventListener('blur', reset);
    view.removeEventListener('resize', reset);
    view.removeEventListener('scroll', reset, true);
    root.ownerDocument.removeEventListener('visibilitychange', visibility);
  };
}
