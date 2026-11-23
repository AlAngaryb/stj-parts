/* STJ Parts screens. Plain JavaScript, no build step. */
(function () {
  'use strict';
  const view = document.getElementById('view');

  // ------------------------------------------------------------ helpers
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = (sel, root = view) => root.querySelector(sel);
  const $$ = (sel, root = view) => Array.from(root.querySelectorAll(sel));

  async function api(path, opts = {}) {
    const init = { headers: {}, ...opts };
    if (opts.body !== undefined) { init.method = init.method || 'POST'; init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    const r = await fetch('/api' + path, init);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || (data.detail && JSON.stringify(data.detail)) || ('Error ' + r.status));
    return data;
  }

  let toastTimer;
  function toast(msg, isError) {
    const t = document.getElementById('toast');
    t.textContent = msg; t.className = 'show' + (isError ? ' error' : '');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.className = ''; }, isError ? 5000 : 2500);
  }

  const store = {
    get(k, d = '') { try { return localStorage.getItem('stj.' + k) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem('stj.' + k, v); } catch { /* private mode */ } },
  };

  function fmtTime(at) {
    if (!at) return '';
    const d = new Date(at.replace(' ', 'T'));
    if (isNaN(d)) return at;
    const today = new Date().toDateString() === d.toDateString();
    const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    return today ? time : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ', ' + time;
  }

  function actionText(a) {
    const n = Math.abs(a.qty_change);
    return { out: 'Out ' + n, in: 'In ' + n, added: 'Added (' + a.qty_after + ')', adjust: 'Count set to ' + a.qty_after, merge: 'Merged' }[a.action] || a.action;
  }

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

  function wireRows() {
    $$('tr[data-part]').forEach((tr) => tr.addEventListener('click', () => go('#/part/' + tr.dataset.part)));
  }

  function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }

  // ------------------------------------------------------------ scanning
  async function handleScan(code) {
    const pill = document.getElementById('scanPill');
    pill.classList.add('busy');
    try {
      const r = await api('/scan/' + encodeURIComponent(code));
      if (r.found) go('#/part/' + r.part.id); else go('#/unknown/' + encodeURIComponent(r.code));
    } catch (e) { toast(e.message, true); }
    finally { pill.classList.remove('busy'); }
  }
  window.Scanner.onScan((code) => {
    // A screen can claim scans for itself (e.g. the new-part form fills its barcode box).
    if (typeof currentScanHandler === 'function' && currentScanHandler(code) === true) return;
    handleScan(code);
  });
  let currentScanHandler = null;

  // ------------------------------------------------------------ screens
  const screens = {};

  screens.home = async () => {
    const [sum, acts] = await Promise.all([api('/summary'), api('/activity?limit=12')]);
    view.innerHTML = `
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
    $('#typeCode').addEventListener('submit', (e) => { e.preventDefault(); const v = $('#codeBox').value.trim(); if (v) handleScan(v); });
    wireRows();
  };

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
        <button class="btn big" id="printBtn" title="Printing arrives with the label printer screen">Print new label</button>
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
    $('#printBtn').addEventListener('click', () => toast('Label printing is the next piece being built.'));
    $('#addCode').addEventListener('click', () => addBarcodeFlow(p));
  };

  function statusOf(qty, min) { return qty <= 0 ? 'out' : (min != null && qty <= min ? 'low' : 'ok'); }

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
          <button class="btn big primary grow" name="go" value="open">Save part</button>
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
        if (e.submitter && e.submitter.value === 'again') { go('#/new?r=' + Date.now()); } else { go('#/part/' + p.id); }
      } catch (err) { const el = $('#err'); el.hidden = false; el.textContent = err.message; }
    });
  };

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

  // ------------------------------------------------------------ router
  async function route() {
    currentScanHandler = null;
    const [path, query] = (location.hash.slice(1) || '/').split('?');
    const params = new URLSearchParams(query || '');
    const parts = path.split('/').filter(Boolean);
    let nav = 'home';
    try {
      if (!parts.length) await screens.home();
      else if (parts[0] === 'part' && parts[2] === 'out') { nav = 'library'; await screens.move(parts[1], 'out'); }
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
