/* STJ Parts screens. Plain JavaScript, no build step. */
(function () {
  'use strict';
  const view = document.getElementById('view');

  // ------------------------------------------------------------ helpers
  /**
   * Description: Makes text safe to put inside HTML, so a part name like "<1/2 in>" shows as
   *   typed instead of being read as page code.
   * Parameters:
   *   1: v - [any] The text (null/undefined become "").
   * Returns: [string] The escaped text.
   */
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  /**
   * Description: Shortcuts for finding elements on the current screen.
   *   $('#qty') finds the first match; $$('.chip') finds all matches as an array.
   * Parameters:
   *   1: sel - [string] A CSS selector, e.g. '#qty' or '[data-who]'.
   *   2: root - [Element] Where to look (defaults to the screen area).
   * Returns: [Element|null] for $, [Array<Element>] for $$.
   */
  const $ = (sel, root = view) => root.querySelector(sel);
  const $$ = (sel, root = view) => Array.from(root.querySelectorAll(sel));

  /**
   * Description: Calls the app's server (an /api address) and returns the answer. Sends a body
   *   as JSON when one is given (and switches to POST). If the server reports a problem, it
   *   throws an Error carrying the server's plain message so the screen can show it.
   *   For example: await api('/parts/5/checkout', { body: { qty: 1, who: 'AB' } })
   * Parameters:
   *   1: path - [string] The address after /api, e.g. '/summary'.
   *   2: opts - [object] Optional fetch settings: body (object to send), method ('POST', ...).
   * Returns: [Promise<object>] The server's JSON answer.
   */
  async function api(path, opts = {}) {
    const init = { headers: {}, ...opts };
    if (opts.body !== undefined) { init.method = init.method || 'POST'; init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    const r = await fetch('/api' + path, init);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || (data.detail && JSON.stringify(data.detail)) || ('Error ' + r.status));
    return data;
  }

  let toastTimer;
  /**
   * Description: Shows a short message at the bottom of the screen that fades by itself.
   *   For example: "Checked out 2. 2 left." after a check out.
   * Parameters:
   *   1: msg - [string] The message.
   *   2: isError - [boolean] true shows it in red and leaves it up longer.
   * Returns: Nothing.
   */
  function toast(msg, isError) {
    const t = document.getElementById('toast');
    t.textContent = msg; t.className = 'show' + (isError ? ' error' : '');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.className = ''; }, isError ? 5000 : 2500);
  }

  /**
   * Description: Remembers small conveniences in this browser (last initials used, chosen sort).
   *   Wrapped in try/catch because some browser modes block storage; the app still works.
   *   store.get(key, default) -> [string]   store.set(key, value) -> nothing
   */
  const store = {
    get(k, d = '') { try { return localStorage.getItem('stj.' + k) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem('stj.' + k, v); } catch { /* private mode */ } },
  };

  /**
   * Description: Turns a database time into a short readable one: just the time for today
   *   ("10:05 AM"), or date and time for earlier days ("Nov 18, 2:10 PM").
   * Parameters:
   *   1: at - [string] Time as stored, e.g. "2026-11-23 10:05:00".
   * Returns: [string] The readable time.
   */
  function fmtTime(at) {
    if (!at) return '';
    const d = new Date(at.replace(' ', 'T'));
    if (isNaN(d)) return at;
    const today = new Date().toDateString() === d.toDateString();
    const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    return today ? time : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ', ' + time;
  }

  /**
   * Description: Words for the Action column of an activity line, e.g. "Out 2", "In 1",
   *   "Added (4)" or "Count set to 3".
   * Parameters:
   *   1: a - [object] One activity line from the server.
   * Returns: [string] The words to show.
   */
  function actionText(a) {
    const n = Math.abs(a.qty_change);
    return { out: 'Out ' + n, in: 'In ' + n, added: 'Added (' + a.qty_after + ')', adjust: 'Count set to ' + a.qty_after, merge: 'Merged' }[a.action] || a.action;
  }

  /**
   * Description: Builds the colored activity table used on the home screen, part page and
   *   Activity log. Each row's left stripe is red (none left), amber (at/below min) or green.
   * Parameters:
   *   1: rows - [Array<object>] Activity lines from the server.
   *   2: opts - [object] Optional: { vendor: true } adds the Vendor column.
   * Returns: [string] HTML for the table, or a "Nothing yet" card if there are no rows.
   */
  function activityTable(rows, opts = {}) {
    if (!rows.length) return '<div class="card muted">Nothing yet. Scan a label or add a part to get started.</div>';
    return `<div class="tablewrap"><table>
      <thead><tr><th class="stripe"></th><th>Time</th><th>Action</th><th>Part</th><th>Who</th><th>WO / PO</th><th>Left</th>${opts.vendor ? '<th>Vendor</th>' : ''}</tr></thead>
      <tbody>${rows.map((a) => `<tr class="clickable ${a.status}" data-part="${a.part_id}">
        <td class="stripe"></td><td>${esc(fmtTime(a.at))}</td><td>${esc(actionText(a))}</td>
        <td><b>${esc(a.name)}</b></td><td>${esc(a.who)}</td><td class="mono">${esc(a.ref || '–')}</td>
        <td class="qty ${a.status}">${a.qty_after}</td>
        ${opts.vendor ? `<td>${esc([a.vendor, a.vendor_phone].filter(Boolean).join(' · ') || '–')}</td>` : ''}</tr>`).join('')}
      </tbody></table></div>`;
  }

  /**
   * Description: Makes every table row that names a part (data-part="ID") open that part's page
   *   when clicked. Called after a screen with a table is drawn.
   * Parameters: None.
   * Returns: Nothing.
   */
  function wireRows() {
    $$('tr[data-part]').forEach((tr) => tr.addEventListener('click', () => go('#/part/' + tr.dataset.part)));
  }

  /**
   * Description: Switches to another screen by changing the address after the # sign. If it is
   *   already that address, the screen is redrawn instead (e.g. to refresh after saving).
   * Parameters:
   *   1: hash - [string] The screen address, e.g. '#/part/5'.
   * Returns: Nothing.
   */
  function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }

  // ------------------------------------------------------------ scanning
  /**
   * Description: What happens after any scan: asks the server whether the code belongs to a part,
   *   then opens that part's page, or the "Unregistered barcode" screen if it's new.
   *   The "Scanner ready" pill turns yellow while it looks.
   * Parameters:
   *   1: code - [string] The scanned or typed code.
   * Returns: [Promise] Resolves when the next screen has been chosen.
   */
  async function handleScan(code) {
    const pill = document.getElementById('scanPill');
    pill.classList.add('busy');
    try {
      const r = await api('/scan/' + encodeURIComponent(code));
      if (r.found) go('#/part/' + r.part.id); else go('#/unknown/' + encodeURIComponent(r.code));
    } catch (e) { toast(e.message, true); }
    finally { pill.classList.remove('busy'); }
  }
  /*
   * Every scan lands here. A screen can claim scans for itself by setting
   * currentScanHandler (the Add part form uses this to fill its Barcode box);
   * otherwise the scan opens the part or the Unregistered screen.
   */
  window.Scanner.onScan((code) => {
    // A screen can claim scans for itself (e.g. the new-part form fills its barcode box).
    if (typeof currentScanHandler === 'function' && currentScanHandler(code) === true) return;
    handleScan(code);
  });
  let currentScanHandler = null;

  // ------------------------------------------------------------ screens
  /*
   * Each screen is a function on this object. route() picks one based on the address,
   * the function asks the server for its data, then fills in the page (view.innerHTML)
   * and attaches the button actions.
   */
  const screens = {};

  /**
   * Description: Screen: Home. Scan prompt, "type a code" box, the four quick tiles with counts,
   *   the most recent activity, and a notice when label jobs are waiting to print.
   *   Address: #/
   * Parameters: None.
   * Returns: [Promise] Resolves when drawn.
   */
  screens.home = async () => {
    const [sum, acts, printer] = await Promise.all([api('/summary'), api('/activity?limit=12'), api('/printer')]);
    view.innerHTML = `
      ${printer.waiting ? `<div class="notice row between"><span><b>${printer.waiting} label job${printer.waiting === 1 ? '' : 's'} waiting to print.</b> The printer was off or not found.</span>
        <span class="row"><a class="btn" href="#/print-queue">See list</a><button class="btn primary" id="retryPrint">Print now</button></span></div>` : ''}
      <div class="scan-card">
        <div class="grow">
          <div class="title">Scan any label</div>
          <div class="muted">Scan a part label or a manufacturer barcode. A known part opens; a new code lets you add it.</div>
        </div>
        <form class="row" id="typeCode">
          <label class="muted" for="codeBox">No scanner? Type a code</label>
          <input id="codeBox" class="mono" placeholder="STJ-000123" autocomplete="off">
          <button class="btn">Open</button>
        </form>
      </div>
      <div class="tiles">
        <a class="tile" href="#/new"><b>+ Add part</b><span class="muted">Rapid entry with barcode</span></a>
        <a class="tile" href="#/library"><b>Parts library</b><span class="muted">${sum.parts.toLocaleString()} parts · search and sort</span></a>
        <a class="tile" href="#/vendors"><b>Vendors</b><span class="muted">${sum.vendors} vendors · reps and phones</span></a>
        <a class="tile" href="#/log?to_order=1"><b>To order</b><span class="muted">${sum.out} out · ${sum.low} low</span></a>
      </div>
      <div class="row between"><h2>Recent activity</h2><a class="btn" href="#/log">See full activity log</a></div>
      ${activityTable(acts)}`;
    const rp = $('#retryPrint');
    if (rp) rp.addEventListener('click', retryQueue);
    $('#typeCode').addEventListener('submit', (e) => { e.preventDefault(); const v = $('#codeBox').value.trim(); if (v) handleScan(v); });
    wireRows();
  };

  /**
   * Description: Screen: Unregistered barcode. Shown when a scanned code isn't on any part; offers
   *   "Create new part with this barcode" or "Rescan".
   *   Address: #/unknown/CODE
   * Parameters:
   *   1: code - [string] The code that was scanned.
   * Returns: [Promise] Resolves when drawn.
   */
  screens.unknown = async (code) => {
    view.innerHTML = `
      <div class="card" style="border-left:6px solid var(--amber-bar)">
        <div class="caption">Unregistered barcode scanned</div>
        <h1 class="mono" style="margin-top:6px">${esc(code)}</h1>
        <p class="muted">This code isn't on any part yet.</p>
      </div>
      <div class="row">
        <a class="btn big primary grow" href="#/new?code=${encodeURIComponent(code)}">Create new part with this barcode</a>
        <a class="btn big grow" href="#/">Rescan</a>
      </div>
      <p class="muted">Is this a second barcode for a part you already have? Open that part and use "Add barcode".</p>`;
  };

  /**
   * Description: Screen: Part page. Name, on-hand count, Check out / Check in / Print label buttons,
   *   details, barcodes (with "Add barcode"), the on-screen barcode and recent activity.
   *   Address: #/part/ID
   * Parameters:
   *   1: id - [string] The part's database ID from the address.
   * Returns: [Promise] Resolves when drawn.
   */
  screens.part = async (id) => {
    const p = await api('/parts/' + id);
    const v = p.vendor;
    const loc = [p.room && `<b>${esc(p.room)}</b>`, esc(p.location_text)].filter(Boolean).join(' · ') || '<span class="muted">Not set</span>';
    view.innerHTML = `
      <section class="row between" style="align-items:flex-start">
        <div class="grow" style="min-width:0">
          <div class="muted">${esc(p.brand || 'No brand')}${p.mfr_part_no ? ` · Part no. <span class="mono"><b>${esc(p.mfr_part_no)}</b></span>` : ''}</div>
          <h1>${esc(p.name)}</h1>
          <div class="mono muted">${esc(p.part_no)}</div>
        </div>
        <div style="text-align:right">
          <div class="caption">On hand</div>
          <div class="onhand qty ${p.status}">${p.qty}</div>
          <div class="muted" style="font-size:13px">${p.min_qty != null ? 'min ' + p.min_qty : 'no min'}${p.max_qty != null ? ' · max ' + p.max_qty : ''}</div>
        </div>
      </section>
      <section style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px">
        <a class="btn big primary" href="#/part/${p.id}/out" ${p.qty <= 0 ? 'aria-disabled="true"' : ''}>Check out part</a>
        <a class="btn big green" href="#/part/${p.id}/in">Check in part</a>
        <a class="btn big" href="#/part/${p.id}/print">Print new label</a>
      </section>
      <section class="row" style="align-items:flex-start;gap:20px">
        <div class="card" style="flex:2 1 520px;min-width:0">
          <dl class="details">
            <dt>Size / spec</dt><dd>${esc(p.size_spec) || '–'}</dd>
            <dt>Usage</dt><dd>${esc(p.usage) || '–'}</dd>
            <dt>Location</dt><dd>${loc}</dd>
            <dt>Vendor</dt><dd>${v ? esc([v.name, v.rep, v.phone].filter(Boolean).join(' · ')) : '–'}</dd>
            <dt>Barcodes</dt><dd class="mono">${p.barcodes.map((b) => esc(b.code)).join(' · ')} <button class="btn" id="addCode" style="min-height:34px;margin-left:8px">Add barcode</button></dd>
            ${p.supplier_part_no ? `<dt>Supplier part no.</dt><dd class="mono">${esc(p.supplier_part_no)}</dd>` : ''}
            ${p.description ? `<dt>Description</dt><dd>${esc(p.description)}</dd>` : ''}
            ${p.notes ? `<dt>Notes</dt><dd>${esc(p.notes)}</dd>` : ''}
          </dl>
        </div>
        <div class="card" style="flex:1 1 280px;text-align:center">
          <img class="barcode" alt="Barcode ${esc(p.part_no)}" src="/api/barcode/${encodeURIComponent(p.part_no)}.svg">
          <div class="mono">${esc(p.part_no)}</div>
        </div>
      </section>
      <div class="caption">Recent activity</div>
      ${activityTable(p.activity.map((a) => ({ ...a, name: p.name, status: statusOf(a.qty_after, p.min_qty) })))}`;
    $('#addCode').addEventListener('click', () => addBarcodeFlow(p));
  };

  /**
   * Description: Stock color for a quantity, the same rule the server uses.
   * Parameters:
   *   1: qty - [number] Quantity on hand.
   *   2: min - [number|null] Reorder point, or null for none.
   * Returns: [string] 'out', 'low' or 'ok'.
   */
  function statusOf(qty, min) { return qty <= 0 ? 'out' : (min != null && qty <= min ? 'low' : 'ok'); }

  /**
   * Description: The "Add barcode" button on the part page: asks the person to scan, then
   *   attaches the next scanned code to this part instead of opening it.
   * Parameters:
   *   1: p - [object] The part currently shown.
   * Returns: Nothing.
   */
  function addBarcodeFlow(p) {
    toast('Scan the extra barcode now.');
    currentScanHandler = (code) => {
      currentScanHandler = null;
      api('/parts/' + p.id + '/barcodes', { body: { code, kind: 'mfr' } })
        .then(() => { toast('Barcode added.'); route(); })
        .catch((e) => toast(e.message, true));
      return true;
    };
  }

  /**
   * Description: Screen: Check out or Check in. Pick who (buttons, or type initials, remembered for
   *   next time), set the quantity, enter the WO/PO, see a warning if stock will hit the
   *   minimum, then confirm.
   *   Address: #/part/ID/out or #/part/ID/in
   * Parameters:
   *   1: id - [string] The part's database ID.
   *   2: dir - [string] 'out' or 'in'.
   * Returns: [Promise] Resolves when drawn.
   */
  screens.move = async (id, dir) => {
    const [p, people] = await Promise.all([api('/parts/' + id), api('/people')]);
    const isOut = dir === 'out';
    let qty = 1;
    let who = store.get('who');
    view.innerHTML = `
      <div class="card row">
        <div style="width:64px;height:64px;border-radius:8px;background:#DCE3E8"></div>
        <div class="grow" style="min-width:0">
          <div class="caption" style="color:var(--accent)">${isOut ? 'Checking out' : 'Checking in'}</div>
          <div style="font-size:20px;font-weight:600">${esc(p.name)}</div>
          <div class="muted">${p.qty} on hand${p.location_text || p.room ? ' · ' + esc([p.room, p.location_text].filter(Boolean).join(', ')) : ''}</div>
        </div>
      </div>
      <section>
        <h2>${isOut ? "Who's taking it?" : "Who's returning or receiving it?"}</h2>
        <div class="people" style="margin-top:10px">${people.map((x) => `<button class="btn" data-who="${esc(x.initials)}">${esc(x.initials)}${x.role ? ' · ' + esc(x.role) : ''}</button>`).join('')}</div>
        <div class="row" style="margin-top:10px">
          <label class="muted" for="whoBox">${people.length ? 'Or type initials' : 'Your initials'}</label>
          <input id="whoBox" class="mono" maxlength="4" style="width:120px;text-transform:uppercase" placeholder="AB">
        </div>
      </section>
      <section class="row" style="align-items:flex-start;gap:24px">
        <div><h2>Quantity</h2><div class="stepper" style="margin-top:10px">
          <button class="btn" id="dec" aria-label="One less">−</button><output id="qty">1</output><button class="btn" id="inc" aria-label="One more">+</button>
        </div></div>
        <label class="field grow" style="flex:1 1 280px">${isOut ? 'Work order / PO number' : 'PO number (optional)'}
          <input id="ref" class="mono" placeholder="${isOut ? 'WO 24-1187' : 'PO 88240'}"></label>
      </section>
      <p class="notice" id="lowWarn" hidden></p>
      <p class="notice error" id="err" hidden></p>
      <div class="row">
        <button class="btn big primary grow" id="confirm">${isOut ? 'Confirm check out' : 'Confirm check in'}</button>
        <a class="btn big" href="#/part/${p.id}" style="flex:0 1 200px">Cancel</a>
      </div>`;
    const whoBox = $('#whoBox');
    const paint = () => {
      $('#qty').textContent = qty;
      $$('[data-who]').forEach((b) => b.classList.toggle('on', b.dataset.who === who));
      whoBox.value = people.some((x) => x.initials === who) ? '' : who;
      const left = isOut ? p.qty - qty : p.qty + qty;
      const warn = $('#lowWarn');
      warn.hidden = !(isOut && p.min_qty != null && left <= p.min_qty);
      warn.textContent = left <= 0 ? 'After this, none will be left. It will show on the To order list.'
        : `After this, ${left} will be left, at or below the minimum of ${p.min_qty}. It will show on the To order list.`;
    };
    $$('[data-who]').forEach((b) => b.addEventListener('click', () => { who = b.dataset.who; paint(); }));
    whoBox.addEventListener('input', () => { who = whoBox.value.toUpperCase().replace(/[^A-Z]/g, ''); $$('[data-who]').forEach((b) => b.classList.toggle('on', b.dataset.who === who)); });
    $('#inc').addEventListener('click', () => { qty = isOut ? Math.min(Math.max(1, p.qty), qty + 1) : qty + 1; paint(); });
    $('#dec').addEventListener('click', () => { qty = Math.max(1, qty - 1); paint(); });
    $('#confirm').addEventListener('click', async () => {
      try {
        const r = await api(`/parts/${p.id}/${isOut ? 'checkout' : 'checkin'}`, { body: { qty, who, ref: $('#ref').value } });
        store.set('who', who);
        toast(`${isOut ? 'Checked out' : 'Checked in'} ${qty}. ${r.qty} left.`);
        go('#/part/' + p.id);
      } catch (e) { const el = $('#err'); el.hidden = false; el.textContent = e.message; }
    });
    paint();
  };

  /**
   * Description: Screen: Add part. The rapid-entry form. A scan while it's open fills the Barcode
   *   box instead of opening a part. "Save and print label" goes to the print screen;
   *   "Save and add another" clears the form for the next part.
   *   Address: #/new  (or #/new?code=CODE coming from the Unregistered screen)
   * Parameters:
   *   1: params - [URLSearchParams] Values after the ?, e.g. code.
   * Returns: [Promise] Resolves when drawn.
   */
  screens.newPart = async (params) => {
    const [vendors, facets] = await Promise.all([api('/vendors'), api('/parts/facets')]);
    const code = params.get('code') || '';
    view.innerHTML = `
      <div class="row between"><h1>Add part</h1><span class="muted">Only the name is required. Everything else can be filled in later.</span></div>
      <form id="pf" class="card" autocomplete="off">
        <div class="form-grid">
          <label class="field wide">Name <span class="hint">What people call it, e.g. "Bearing, ball, 6204-2RS"</span><input name="name" required></label>
          <label class="field">Brand<input name="brand" list="brands"></label>
          <label class="field">Manufacturer part no.<input name="mfr_part_no" class="mono"></label>
          <label class="field">Size / spec<input name="size_spec" placeholder="1/2 in quick connect, M10 thread"></label>
          <label class="field wide">Usage <span class="hint">Where it's used, e.g. "Palletizer conveyors, HPP"</span><textarea name="usage"></textarea></label>
          <label class="field">Room<input name="room" list="rooms" placeholder="Maintenance shop"></label>
          <label class="field">Location in the room<input name="location_text" placeholder="Gray cabinet 3, drawer 2"></label>
          <label class="field">Quantity on hand<input name="qty" type="number" min="0" value="1" inputmode="numeric"></label>
          <label class="field">Min (reorder at)<input name="min_qty" type="number" min="0" inputmode="numeric"></label>
          <label class="field">Max<input name="max_qty" type="number" min="0" inputmode="numeric"></label>
          <label class="field">Vendor<select name="vendor_id"><option value="">–</option>${vendors.map((v) => `<option value="${v.id}">${esc(v.name)}</option>`).join('')}<option value="new">+ New vendor…</option></select></label>
          <label class="field">Supplier part no.<input name="supplier_part_no" class="mono"></label>
          <label class="field">Barcode on the part <span class="hint">Scan it now, or leave blank</span><input name="barcode" class="mono" value="${esc(code)}"></label>
          <label class="field">Part number <span class="hint">Blank = next STJ number</span><input name="part_no" class="mono"></label>
          <label class="field wide">Description<textarea name="description"></textarea></label>
          <label class="field">Your initials<input name="who" class="mono" maxlength="4" style="text-transform:uppercase" value="${esc(store.get('who'))}" required></label>
        </div>
        <p class="notice error" id="err" hidden></p>
        <div class="row" style="margin-top:16px">
          <button class="btn big primary grow" name="go" value="open">Save and print label</button>
          <button class="btn big grow" name="go" value="again">Save and add another</button>
        </div>
      </form>
      <datalist id="rooms">${facets.rooms.map((r) => `<option value="${esc(r)}">`).join('')}</datalist>
      <datalist id="brands">${facets.brands.map((r) => `<option value="${esc(r)}">`).join('')}</datalist>`;
    const form = $('#pf');
    form.name.focus();
    currentScanHandler = (scanned) => { form.barcode.value = scanned; toast('Barcode filled in.'); return true; };
    form.vendor_id.addEventListener('change', async () => {
      if (form.vendor_id.value !== 'new') return;
      const name = window.prompt('New vendor name');
      if (!name) { form.vendor_id.value = ''; return; }
      try {
        const v = await api('/vendors', { body: { name } });
        form.vendor_id.insertAdjacentHTML('afterbegin', `<option value="${v.id}">${esc(v.name)}</option>`);
        form.vendor_id.value = String(v.id);
      } catch (e) { toast(e.message, true); form.vendor_id.value = ''; }
    });
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = new FormData(form);
      const num = (k) => (f.get(k) === '' ? null : Number(f.get(k)));
      const body = {
        name: f.get('name'), brand: f.get('brand'), mfr_part_no: f.get('mfr_part_no'), size_spec: f.get('size_spec'),
        usage: f.get('usage'), room: f.get('room'), location_text: f.get('location_text'), qty: num('qty') ?? 0,
        min_qty: num('min_qty'), max_qty: num('max_qty'), vendor_id: f.get('vendor_id') && f.get('vendor_id') !== 'new' ? Number(f.get('vendor_id')) : null,
        supplier_part_no: f.get('supplier_part_no'), part_no: f.get('part_no'), description: f.get('description'),
        who: String(f.get('who') || '').toUpperCase(), barcodes: f.get('barcode') ? [f.get('barcode')] : [],
      };
      try {
        const p = await api('/parts', { body });
        store.set('who', body.who);
        toast(`Saved as ${p.part_no}.`);
        if (e.submitter && e.submitter.value === 'again') { go('#/new?r=' + Date.now()); } else { go('#/part/' + p.id + '/print?new=1'); }
      } catch (err) { const el = $('#err'); el.hidden = false; el.textContent = err.message; }
    });
  };

  /**
   * Description: Screen: Parts library. Search box (scan-friendly), room/brand filters, stock chips
   *   and sort. Results update as you type; the chosen sort is remembered.
   *   Address: #/library  (or #/library?q=TEXT)
   * Parameters:
   *   1: params - [URLSearchParams] Values after the ?, e.g. q.
   * Returns: [Promise] Resolves when drawn.
   */
  screens.library = async (params) => {
    const facets = await api('/parts/facets');
    const state = { q: params.get('q') || '', room: '', brand: '', stock: '', sort: store.get('sort', 'name') };
    view.innerHTML = `
      <div class="row between"><h1>Parts library</h1><a class="btn primary" href="#/new">+ Add part</a></div>
      <form id="sf" onsubmit="return false">
        <label class="field" for="q" style="font-size:15px">Search parts</label>
        <input id="q" type="search" style="width:100%;font-size:18px;padding:14px 16px;margin-top:6px"
          placeholder="Name, part number, size, brand, usage, location, vendor… or scan a label" value="${esc(state.q)}">
        <div class="muted" style="font-size:13px;margin-top:4px">Typos are OK: "quik conect" finds "quick connect".</div>
      </form>
      <div class="row" style="align-items:flex-end">
        <label class="field">Room<select id="room"><option value="">All rooms</option>${facets.rooms.map((r) => `<option>${esc(r)}</option>`).join('')}</select></label>
        <label class="field">Brand<select id="brand"><option value="">All brands</option>${facets.brands.map((r) => `<option>${esc(r)}</option>`).join('')}</select></label>
        <div class="field"><span>Stock</span><div class="chips" id="stock">
          <button class="chip on" data-v="">All</button><button class="chip" data-v="out">Out</button><button class="chip" data-v="low">Low</button><button class="chip" data-v="ok">In stock</button></div></div>
        <span class="grow"></span>
        <label class="field">Sort<select id="sort">
          <option value="name">Name A–Z</option><option value="stock">Lowest stock first</option><option value="used">Recently used</option><option value="added">Recently added</option></select></label>
      </div>
      <div id="dym" class="muted"></div>
      <div id="results"></div>`;
    $('#sort').value = state.sort;
    let seq = 0;
    const load = async () => {
      const my = ++seq;
      const qs = new URLSearchParams({ q: state.q, room: state.room, brand: state.brand, stock: state.stock, sort: state.sort });
      const r = await api('/parts?' + qs);
      if (my !== seq) return;
      $('#dym').innerHTML = r.did_you_mean ? `Showing results for <b>${esc(r.did_you_mean)}</b>` : '';
      const rows = r.results;
      $('#results').innerHTML = !rows.length ? '<div class="card muted">No parts match. Try fewer words, or clear the filters.</div>'
        : `<div class="tablewrap"><table><thead><tr><th class="stripe"></th><th>Part</th><th>Brand</th><th>Part no.</th><th>Location</th><th>Vendor</th><th>On hand</th></tr></thead><tbody>
          ${rows.map((p) => `<tr class="clickable ${p.status}" data-part="${p.id}"><td class="stripe"></td>
            <td><b>${esc(p.name)}</b><div class="muted" style="font-size:13px">${esc(p.size_spec)}</div></td><td>${esc(p.brand)}</td>
            <td class="mono">${esc(p.mfr_part_no || p.part_no)}</td><td>${esc([p.room, p.location_text].filter(Boolean).join(' · '))}</td>
            <td>${esc(p.vendor)}</td><td class="qty ${p.status}">${p.qty}</td></tr>`).join('')}</tbody></table></div>`;
      wireRows();
    };
    let t;
    const q = $('#q');
    q.addEventListener('input', () => { state.q = q.value; clearTimeout(t); t = setTimeout(load, 150); });
    q.addEventListener('keydown', async (e) => {
      // A scan into the search box (ending in Enter) opens the part directly when it's an exact barcode.
      if (e.key !== 'Enter') return;
      const r = await api('/scan/' + encodeURIComponent(q.value)).catch(() => null);
      if (r && r.found) go('#/part/' + r.part.id);
    });
    $('#room').addEventListener('change', (e) => { state.room = e.target.value; load(); });
    $('#brand').addEventListener('change', (e) => { state.brand = e.target.value; load(); });
    $('#sort').addEventListener('change', (e) => { state.sort = e.target.value; store.set('sort', state.sort); load(); });
    $$('#stock .chip').forEach((c) => c.addEventListener('click', () => {
      state.stock = c.dataset.v; $$('#stock .chip').forEach((x) => x.classList.toggle('on', x === c)); load();
    }));
    q.focus();
    load();
  };

  /**
   * Description: Screen: Activity log, colored by what's left, with All / Today / To order only.
   *   Address: #/log, #/log?day=today or #/log?to_order=1
   * Parameters:
   *   1: params - [URLSearchParams] Values after the ?.
   * Returns: [Promise] Resolves when drawn.
   */
  screens.log = async (params) => {
    const toOrder = params.get('to_order') === '1';
    const today = params.get('day') === 'today';
    const d = new Date();
    const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const rows = await api(`/activity?limit=500${toOrder ? '&to_order=true' : ''}${today ? '&day=' + day : ''}`);
    const link = (label, on, href) => `<a class="btn ${on ? 'dark' : ''}" href="${href}">${label}</a>`;
    view.innerHTML = `
      <div class="row between"><h1>Activity log</h1><div class="row">
        ${link('All', !toOrder && !today, '#/log')}${link('Today', today, '#/log?day=today')}${link('To order only', toOrder, '#/log?to_order=1')}
      </div></div>
      <div class="row muted" style="font-size:14px;gap:16px">
        <span><span style="display:inline-block;width:12px;height:12px;border-radius:3px;background:var(--red-bar)"></span> None left</span>
        <span><span style="display:inline-block;width:12px;height:12px;border-radius:3px;background:var(--amber-bar)"></span> At or below min</span>
        <span><span style="display:inline-block;width:12px;height:12px;border-radius:3px;background:var(--green-bar)"></span> Stock remains</span>
      </div>
      ${activityTable(rows, { vendor: true })}`;
    wireRows();
  };

  /**
   * Description: Screen: Vendors. Add-vendor form and the vendor list with part counts.
   *   Address: #/vendors
   * Parameters: None.
   * Returns: [Promise] Resolves when drawn.
   */
  screens.vendors = async () => {
    const list = await api('/vendors');
    view.innerHTML = `
      <div class="row between"><h1>Vendors</h1></div>
      <form id="vf" class="card">
        <h2>Add vendor</h2>
        <div class="form-grid" style="margin-top:12px">
          <label class="field">Company<input name="name" required></label>
          <label class="field">Main rep<input name="rep"></label>
          <label class="field">Phone<input name="phone" class="mono"></label>
          <label class="field">Email<input name="email" type="email"></label>
          <label class="field">After hours<input name="after_hours" class="mono"></label>
          <label class="field">Account no.<input name="account_no" class="mono"></label>
          <label class="field">Typical lead time<input name="lead_time"></label>
          <label class="field">Website<input name="website"></label>
          <label class="field wide">Notes<textarea name="notes"></textarea></label>
        </div>
        <div class="row" style="margin-top:12px"><button class="btn primary">Save vendor</button></div>
      </form>
      ${list.length ? `<div class="tablewrap"><table><thead><tr><th>Company</th><th>Rep</th><th>Phone</th><th>Email</th><th>Lead time</th><th>Parts</th></tr></thead><tbody>
        ${list.map((v) => `<tr><td><b>${esc(v.name)}</b></td><td>${esc(v.rep)}</td><td class="mono">${esc(v.phone)}</td><td>${esc(v.email)}</td><td>${esc(v.lead_time)}</td><td>${v.part_count}</td></tr>`).join('')}
        </tbody></table></div>` : '<div class="card muted">No vendors yet.</div>'}`;
    $('#vf').addEventListener('submit', async (e) => {
      e.preventDefault();
      try { await api('/vendors', { body: Object.fromEntries(new FormData(e.target)) }); toast('Vendor saved.'); route(); }
      catch (err) { toast(err.message, true); }
    });
  };

  /**
   * Description: The "Print now" button: asks the server to print every waiting label job, then
   *   shows how many printed (or why it stopped) and redraws the screen.
   * Parameters: None.
   * Returns: [Promise] Resolves when done.
   */
  async function retryQueue() {
    try {
      const r = await api('/print-jobs/retry', { method: 'POST' });
      if (r.error) toast(`Printed ${r.printed}. ${r.waiting} still waiting: ${r.error}`, true);
      else toast(`Printed ${r.printed} label job${r.printed === 1 ? '' : 's'}.`);
      route();
    } catch (e) { toast(e.message, true); }
  }

  /**
   * Description: Screen: Print label. Live preview at true proportions, label size buttons, name and
   *   location toggles, copies, Print, and "Download PDF instead". If the printer isn't
   *   available, the job is saved to print later.
   *   Address: #/part/ID/print  (?new=1 after saving a new part adds a Skip button)
   * Parameters:
   *   1: id - [string] The part's database ID.
   *   2: params - [URLSearchParams] Values after the ?.
   * Returns: [Promise] Resolves when drawn.
   */
  screens.print = async (id, params) => {
    const [p, presets, printer] = await Promise.all([api('/parts/' + id), api('/labels/presets'), api('/printer')]);
    const st = { preset: presets[0], copies: 1, name: !!presets[0].show_name, loc: !!presets[0].show_location };
    const printerLine = printer.selected
      ? `<span class="pill" style="background:#E3F3E8;color:var(--green)"><span class="dot"></span>${esc(printer.selected)} ready</span>`
      : `<span class="pill" style="background:var(--amber-bg);color:var(--amber)"><span class="dot"></span>${printer.can_print ? 'No Brady printer found' : 'Printing works on the shop PC only'}</span>`;
    view.innerHTML = `
      <div class="row between">
        <div><a href="#/part/${p.id}" style="font-weight:600;text-decoration:none">‹ Back to part</a>
          <h1>${params.get('new') ? 'Saved. Print a label?' : 'Print label'}</h1><div class="muted">${esc(p.name)} · <span class="mono">${esc(p.part_no)}</span></div></div>
        ${printerLine}
      </div>
      <div class="row" style="align-items:flex-start;gap:16px">
        <section class="card" style="flex:1.3 1 520px;min-width:0;display:flex;flex-direction:column;gap:12px">
          <div class="caption">Preview, true proportions</div>
          <div style="background:repeating-conic-gradient(#EEF1F3 0% 25%,#F7F9FA 0% 50%) 0 0/20px 20px;border-radius:8px;padding:28px 16px;display:flex;justify-content:center;align-items:center;min-height:260px">
            <img id="prev" alt="Label preview" style="background:#fff;box-shadow:0 2px 8px rgba(0,0,0,.25);border-radius:4px">
          </div>
          <p class="notice error" id="err" hidden></p>
          <span class="muted" style="font-size:14px">Code 128 barcode, which your scanner reads. The barcode and part number always print.</span>
        </section>
        <section style="flex:1 1 360px;min-width:0;display:flex;flex-direction:column;gap:16px">
          <div style="display:flex;flex-direction:column;gap:8px"><h2>Label size</h2>
            ${presets.map((x) => `<button class="btn" data-preset="${x.id}" style="justify-content:space-between;min-height:58px;text-align:left">
              <span><b>${esc(x.name)}</b><br><span class="muted" style="font-weight:400;font-size:13px">${esc(x.description)}</span></span>
              <span class="mono muted">${x.width_in} × ${x.height_in} in</span></button>`).join('')}
          </div>
          <div style="display:flex;flex-direction:column;gap:8px"><h2>Also show</h2>
            <button class="btn" id="tName" style="justify-content:flex-start"></button>
            <button class="btn" id="tLoc" style="justify-content:flex-start"></button>
          </div>
          <div class="row"><h2>Copies</h2>
            <div class="stepper"><button class="btn" id="dec" aria-label="One less">−</button><output id="copies">1</output><button class="btn" id="inc" aria-label="One more">+</button></div>
            ${p.qty > 1 ? `<button class="btn" id="perItem">One per item (${p.qty})</button>` : ''}
          </div>
          <button class="btn big primary" id="go"></button>
          <div class="row"><a class="btn" id="pdf">Download PDF instead</a>${params.get('new') ? `<a class="btn" href="#/part/${p.id}">Skip</a>` : ''}</div>
          <span class="muted" style="font-size:14px">Printer off or out of tape? The label waits on the home screen and prints when you press Print now.</span>
        </section>
      </div>`;
    const paint = () => {
      $$('[data-preset]').forEach((b) => b.classList.toggle('on', Number(b.dataset.preset) === st.preset.id));
      $('#tName').textContent = (st.name ? '☑' : '☐') + ' Part name';
      $('#tLoc').textContent = (st.loc ? '☑' : '☐') + ' Location';
      $('#tName').classList.toggle('on', st.name); $('#tLoc').classList.toggle('on', st.loc);
      $('#copies').textContent = st.copies;
      $('#go').textContent = st.copies === 1 ? 'Print 1 label' : `Print ${st.copies} labels`;
      const q = `preset=${st.preset.id}&name=${st.name}&loc=${st.loc}`;
      $('#pdf').href = `/api/parts/${p.id}/label.pdf?${q}&copies=${st.copies}`;
      const img = $('#prev');
      const scale = 150; // screen pixels per inch for the preview
      img.style.width = (st.preset.width_in * scale) + 'px';
      img.style.height = (st.preset.height_in * scale) + 'px';
      fetch(`/api/parts/${p.id}/label.png?${q}`).then(async (r) => {
        const err = $('#err');
        if (!r.ok) { err.hidden = false; err.textContent = (await r.json()).error; img.hidden = true; $('#go').disabled = true; return; }
        err.hidden = true; img.hidden = false; $('#go').disabled = false;
        img.src = URL.createObjectURL(await r.blob());
      });
    };
    $$('[data-preset]').forEach((b) => b.addEventListener('click', () => {
      st.preset = presets.find((x) => x.id === Number(b.dataset.preset)); st.name = !!st.preset.show_name; st.loc = !!st.preset.show_location; paint();
    }));
    $('#tName').addEventListener('click', () => { st.name = !st.name; paint(); });
    $('#tLoc').addEventListener('click', () => { st.loc = !st.loc; paint(); });
    $('#inc').addEventListener('click', () => { st.copies = Math.min(200, st.copies + 1); paint(); });
    $('#dec').addEventListener('click', () => { st.copies = Math.max(1, st.copies - 1); paint(); });
    if ($('#perItem')) $('#perItem').addEventListener('click', () => { st.copies = Math.min(200, p.qty); paint(); });
    $('#go').addEventListener('click', async () => {
      try {
        const r = await api(`/parts/${p.id}/print`, { body: { preset_id: st.preset.id, copies: st.copies, show_name: st.name, show_location: st.loc, who: store.get('who') } });
        if (r.printed) { toast(`Printed ${st.copies} label${st.copies === 1 ? '' : 's'}.`); }
        else { toast(`Saved to print later: ${r.error}`, true); }
        go('#/part/' + p.id);
      } catch (e) { toast(e.message, true); }
    });
    paint();
  };

  /**
   * Description: Screen: Waiting to print. Lists label jobs that couldn't print yet, with the
   *   reason, a Cancel button for each, and "Print all now".
   *   Address: #/print-queue
   * Parameters: None.
   * Returns: [Promise] Resolves when drawn.
   */
  screens.printQueue = async () => {
    const jobs = await api('/print-jobs');
    view.innerHTML = `
      <div class="row between"><h1>Waiting to print</h1>${jobs.length ? '<button class="btn primary" id="retry">Print all now</button>' : ''}</div>
      ${jobs.length ? `<div class="tablewrap"><table><thead><tr><th>Added</th><th>Part</th><th>Label</th><th>Copies</th><th>Problem</th><th></th></tr></thead><tbody>
        ${jobs.map((j) => `<tr><td>${esc(fmtTime(j.created_at))}</td><td><b>${esc(j.name)}</b> <span class="mono muted">${esc(j.part_no)}</span></td>
          <td>${esc(j.preset)}</td><td>${j.copies}</td><td class="muted">${esc(j.error)}</td>
          <td><button class="btn" data-cancel="${j.id}">Cancel</button></td></tr>`).join('')}</tbody></table></div>`
        : '<div class="card muted">Nothing waiting. All labels have printed.</div>'}`;
    if ($('#retry')) $('#retry').addEventListener('click', retryQueue);
    $$('[data-cancel]').forEach((b) => b.addEventListener('click', async () => {
      await api('/print-jobs/' + b.dataset.cancel + '/cancel', { method: 'POST' }); route();
    }));
  };

  // ------------------------------------------------------------ router
  /**
   * Description: The screen switcher. Reads the address after the # sign and draws the matching
   *   screen, e.g. '#/part/5/out' draws the check-out screen for part 5. Also highlights the
   *   right top-bar link and shows a friendly message if a screen fails to load.
   *   Runs on startup and every time the address changes.
   * Parameters: None.
   * Returns: [Promise] Resolves when the screen is drawn.
   */
  async function route() {
    currentScanHandler = null;
    const [path, query] = (location.hash.slice(1) || '/').split('?');
    const params = new URLSearchParams(query || '');
    const parts = path.split('/').filter(Boolean);
    let nav = 'home';
    try {
      if (!parts.length) await screens.home();
      else if (parts[0] === 'part' && parts[2] === 'out') { nav = 'library'; await screens.move(parts[1], 'out'); }
      else if (parts[0] === 'part' && parts[2] === 'print') { nav = 'library'; await screens.print(parts[1], params); }
      else if (parts[0] === 'print-queue') { nav = ''; await screens.printQueue(); }
      else if (parts[0] === 'part' && parts[2] === 'in') { nav = 'library'; await screens.move(parts[1], 'in'); }
      else if (parts[0] === 'part') { nav = 'library'; await screens.part(parts[1]); }
      else if (parts[0] === 'unknown') { nav = ''; await screens.unknown(decodeURIComponent(parts.slice(1).join('/'))); }
      else if (parts[0] === 'new') { nav = 'new'; await screens.newPart(params); }
      else if (parts[0] === 'library') { nav = 'library'; await screens.library(params); }
      else if (parts[0] === 'log') { nav = 'log'; await screens.log(params); }
      else if (parts[0] === 'vendors') { nav = 'vendors'; await screens.vendors(); }
      else { view.innerHTML = '<div class="card">Page not found. <a href="#/">Go home</a></div>'; }
    } catch (e) {
      view.innerHTML = `<div class="card notice error">${esc(e.message)}</div><a class="btn" href="#/">Go home</a>`;
    }
    $$('#nav a', document).forEach((a) => a.classList.toggle('active', a.dataset.nav === nav));
    window.scrollTo(0, 0);
  }
  window.addEventListener('hashchange', route);
  route();
})();
