/*
 * USB barcode scanners act like a keyboard: they "type" the code very fast.
 * Some press Enter (or Tab) at the end, some press nothing.
 * We catch both: a fast burst of keys ending in Enter/Tab, or a fast burst
 * followed by a short pause. Normal typing is much slower, so it's left alone.
 *
 * Scans are caught anywhere on the page except inside text boxes, where the
 * code is simply typed into the box (that's how "scan into search" works).
 */
(function () {
  const MAX_GAP_MS = 50;   // scanners send keys 5-30 ms apart; people type 100+ ms apart
  const PAUSE_MS = 120;    // no suffix: treat a pause this long after a burst as the end
  const MIN_LEN = 3;

  let buf = '';
  let times = [];
  let timer = null;
  const listeners = [];

  function isTyping(el) {
    if (!el) return false;
    const tag = el.tagName;
    return (tag === 'INPUT' && !['button', 'checkbox', 'radio', 'submit'].includes(el.type)) ||
      tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
  }

  function looksScanned() {
    if (buf.length < MIN_LEN) return false;
    let total = 0;
    for (let i = 1; i < times.length; i++) total += times[i] - times[i - 1];
    return total / Math.max(1, times.length - 1) <= MAX_GAP_MS;
  }

  function reset() { buf = ''; times = []; clearTimeout(timer); timer = null; }

  function finish() {
    const code = buf.trim();
    const ok = looksScanned();
    reset();
    if (ok && code) listeners.forEach(fn => fn(code));
    return ok;
  }

  document.addEventListener('keydown', (e) => {
    if (isTyping(document.activeElement) || e.ctrlKey || e.altKey || e.metaKey) return;
    if (e.key === 'Enter' || e.key === 'Tab') {
      if (buf && looksScanned()) { e.preventDefault(); finish(); } else reset();
      return;
    }
    if (e.key.length !== 1) return;
    const now = performance.now();
    if (times.length && now - times[times.length - 1] > 400) reset(); // stale partial input
    buf += e.key;
    times.push(now);
    if (buf.length >= 2 && looksScanned()) e.preventDefault(); // keep scanner keys from triggering page shortcuts
    clearTimeout(timer);
    timer = setTimeout(finish, PAUSE_MS);
  }, true);

  window.Scanner = {
    onScan(fn) { listeners.push(fn); },
    // For tests and the "type a code" box.
    simulate(code) { listeners.forEach(fn => fn(code)); },
  };
})();
