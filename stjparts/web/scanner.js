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

  /**
   * Description: Checks whether the person is typing in a text box, drop-down or similar.
   *   Scans are not intercepted there, so scanning into the search box simply types the code.
   * Parameters:
   *   1: el - [Element] The element that has keyboard focus (document.activeElement).
   * Returns: [boolean] true if keys should go to that box instead of the scan detector.
   */
  function isTyping(el) {
    if (!el) return false;
    const tag = el.tagName;
    return (tag === 'INPUT' && !['button', 'checkbox', 'radio', 'submit'].includes(el.type)) ||
      tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
  }

  /**
   * Description: Decides whether the keys collected so far came from a scanner, by checking that
   *   there are at least MIN_LEN of them and the average gap between keys is MAX_GAP_MS or less.
   *   People type about 100+ ms apart; scanners send keys 5-30 ms apart.
   * Parameters: None (reads buf and times).
   * Returns: [boolean] true if it looks like a scan.
   */
  function looksScanned() {
    if (buf.length < MIN_LEN) return false;
    let total = 0;
    for (let i = 1; i < times.length; i++) total += times[i] - times[i - 1];
    return total / Math.max(1, times.length - 1) <= MAX_GAP_MS;
  }

  /**
   * Description: Throws away the keys collected so far and stops the pause timer.
   * Parameters: None.
   * Returns: Nothing.
   */
  function reset() { buf = ''; times = []; clearTimeout(timer); timer = null; }

  /**
   * Description: Called at the end of a burst (Enter/Tab pressed, or a pause after the last key).
   *   If the burst looks like a scan, every onScan listener gets the code. Either way the
   *   buffer is cleared for the next scan.
   * Parameters: None.
   * Returns: [boolean] true if it was treated as a scan.
   */
  function finish() {
    const code = buf.trim();
    const ok = looksScanned();
    reset();
    if (ok && code) listeners.forEach(fn => fn(code));
    return ok;
  }

  /*
   * Watches every key press on the page (capture phase, so it sees keys first).
   * Printable keys are collected with their times. Enter/Tab ends a scan right away for
   * scanners that send them; for scanners that send nothing, the PAUSE_MS timer ends it.
   * Keys that are clearly part of a scan are stopped from reaching the page.
   */
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

  /*
   * What the rest of the app uses:
   *   Scanner.onScan(fn)       fn(code) is called for every scan.
   *   Scanner.simulate(code)   pretend a code was scanned (tests, the "type a code" box).
   */
  window.Scanner = {
    onScan(fn) { listeners.push(fn); },
    // For tests and the "type a code" box.
    simulate(code) { listeners.forEach(fn => fn(code)); },
  };
})();
