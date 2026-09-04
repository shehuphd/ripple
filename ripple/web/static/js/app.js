/* Decision log.
   Handlers record what they decided as they decide it, into a bounded ring
   buffer that is always on. Each record also mirrors to console.debug, where
   the traceact-browser extension captures it into its trace file, so the
   page's reasoning is readable outside the page.
     ripple.trace('preview.result', {ops: 5})  record a decision
     ripple.debug()                            dump the buffer
     ripple.debug('preview')                   only kinds with that prefix
     ripple.debug.table('api')                 the same, as a table */
const TRACE_LIMIT = 500;
const traceRing = [];

function trace(kind, fields) {
  const record = Object.assign({ at: new Date().toISOString(), kind }, fields);
  traceRing.push(record);
  if (traceRing.length > TRACE_LIMIT) traceRing.shift();
  console.debug('[ripple]', kind, fields || {});
}

function debugDump(prefix) {
  const rows = prefix
    ? traceRing.filter((r) => r.kind.startsWith(prefix))
    : traceRing.slice();
  console.log(rows);
  return rows;
}
debugDump.table = (prefix) => {
  console.table(prefix
    ? traceRing.filter((r) => r.kind.startsWith(prefix))
    : traceRing);
};

window.ripple = { trace, debug: debugDump };

/* Shared helpers. */

/* Escape text before it enters innerHTML. Screenplay-derived names, unit
   text, evidence, and model output are all untrusted input: an uploaded
   script can carry markup, and unescaped it executes in every page that
   renders it. Quotes are escaped too, so the result is safe inside
   attribute values. */
function esc(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function toast(message, isError) {
  const node = document.createElement('div');
  node.className = 'toast' + (isError ? ' err' : '');
  node.setAttribute('role', isError ? 'alert' : 'status');
  node.textContent = message;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 5200);
}

/* A server rejection must reach the page as a sentence. FastAPI's 422
   carries `detail` as an array of field errors; other errors carry a
   message or detail string; anything else gets a plain description of
   the status instead of a bare number or "[object Object]". */
function apiErrorMessage(body, status) {
  if (body.message) return body.message;
  if (typeof body.detail === 'string') return body.detail;
  if (Array.isArray(body.detail)) {
    const parts = body.detail.map((item) => item && item.msg).filter(Boolean);
    if (parts.length) return `The request was refused: ${parts.join('; ')}.`;
  }
  if (status >= 500) {
    return 'Something went wrong on the server. The details are in the server log.';
  }
  return `The server refused the request (HTTP ${status}).`;
}

async function api(url, options) {
  const method = (options && options.method) || 'GET';
  const started = performance.now();
  let response;
  try {
    response = await fetch(url, options);
  } catch (error) {
    trace('api.unreachable', { method, url, error: String(error) });
    throw new Error('The server is not reachable. Is Ripple still running?');
  }
  const body = await response.json().catch(() => ({}));
  const ms = Math.round(performance.now() - started);
  if (!response.ok) {
    trace('api.rejected', {
      method, url, status: response.status, ms, code: body.code || null,
    });
    const error = new Error(apiErrorMessage(body, response.status));
    error.code = body.code || null;
    // A failed preview's response names the TraceAct trace that recorded
    // the run, so the error surface can offer to open it in the viewer.
    error.traceId = body.trace_id || null;
    throw error;
  }
  trace('api.ok', { method, url, status: response.status, ms });
  return body;
}

/* An in-app confirmation, replacing window.confirm: a bare browser dialog
   can't be themed, blocks the page, and looks foreign next to the app.
   Resolves true on confirm, false on cancel or Escape. */
function confirmDialog(message, confirmLabel, options = {}) {
  return new Promise((resolve) => {
    const veil = document.createElement('div');
    veil.className = 'confirm-veil';
    // A destructive confirm wears the app's red, so the weight of the action
    // reads on the button the user is about to press, not only in the message.
    const okClass = options.destructive ? 'btn danger' : 'btn pri';
    veil.innerHTML = `
      <div class="confirm-box" role="alertdialog" aria-modal="true"
           aria-label="Confirm" aria-describedby="confirm-msg">
        <p id="confirm-msg"></p>
        <div class="confirm-acts">
          <button class="btn" data-cancel>Cancel</button>
          <button class="${okClass}" data-ok></button>
        </div>
      </div>`;
    veil.querySelector('#confirm-msg').textContent = message;
    veil.querySelector('[data-ok]').textContent = confirmLabel || 'Confirm';
    const previousFocus = document.activeElement;
    const close = (answer) => {
      veil.remove();
      if (previousFocus && previousFocus.focus) previousFocus.focus();
      resolve(answer);
    };
    veil.querySelector('[data-cancel]').addEventListener('click', () => close(false));
    veil.querySelector('[data-ok]').addEventListener('click', () => close(true));
    veil.addEventListener('click', (event) => {
      if (event.target === veil) close(false);
    });
    veil.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); close(false); }
      // Keep tabbing inside the dialog: it's the only actionable surface.
      if (event.key === 'Tab') {
        const buttons = veil.querySelectorAll('button');
        const first = buttons[0];
        const last = buttons[buttons.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first.focus();
        }
      }
    });
    document.body.appendChild(veil);
    veil.querySelector('[data-cancel]').focus();
  });
}

function form(pairs) {
  const data = new FormData();
  for (const [key, value] of Object.entries(pairs)) {
    if (value !== null && value !== undefined) data.append(key, value);
  }
  return data;
}


/* Collapsible panes.
   Every side pane can be hidden and the choice persists, because a pane that
   is useful most of the time is in the way some of the time and the user
   should not have to hide it again on every page. */
function rememberPane(key, collapsed) {
  try {
    window.localStorage.setItem(`ripple.pane.${key}`, collapsed ? 'off' : 'on');
  } catch (error) {
    // Private browsing denies localStorage. The toggle still works for this
    // page; only the memory of it is lost.
  }
}

function paneIsCollapsed(key) {
  try {
    return window.localStorage.getItem(`ripple.pane.${key}`) === 'off';
  } catch (error) {
    return false;
  }
}

function setUpPaneToggles() {
  const shell = document.getElementById('shell');
  const sideButton = document.getElementById('toggle-side');
  if (shell && sideButton) {
    // Under 900px the sidebar is hidden by default and the toggle opens it, so
    // the persisted state means the opposite there.
    const compact = window.matchMedia('(max-width: 900px)');
    const narrow = () => compact.matches;
    const apply = (collapsed) => {
      shell.classList.toggle('compact', narrow());
      shell.classList.toggle('side-off', !narrow() && collapsed);
      shell.classList.toggle('side-on', narrow() && !collapsed);
      sideButton.classList.toggle('on', !collapsed);
      sideButton.setAttribute('aria-pressed', String(!collapsed));
    };
    // Narrow screens start with the drawer shut whatever was remembered: it
    // covers the content there, so restoring it open would hide the page.
    let collapsed = narrow() ? true : paneIsCollapsed('side');
    apply(collapsed);
    sideButton.addEventListener('click', () => {
      collapsed = !collapsed;
      apply(collapsed);
      rememberPane('side', collapsed);
      window.dispatchEvent(new Event('resize'));
    });
    compact.addEventListener('change', () => apply(collapsed));
    window.addEventListener('resize', () => apply(collapsed));
    // Tapping the backdrop shuts the drawer, which is what a tap outside a
    // drawer means everywhere else.
    shell.addEventListener('click', (event) => {
      if (narrow() && !collapsed && event.target === shell) {
        collapsed = true;
        apply(collapsed);
      }
    });
  }

  for (const button of document.querySelectorAll('[data-pane]')) {
    const target = document.querySelector(button.dataset.target);
    if (!target) continue;
    const key = button.dataset.pane;
    let collapsed = paneIsCollapsed(key);
    const apply = () => {
      target.classList.toggle('pane-off', collapsed);
      button.classList.toggle('on', !collapsed);
      button.setAttribute('aria-pressed', String(!collapsed));
    };
    apply();
    button.addEventListener('click', () => {
      collapsed = !collapsed;
      apply();
      rememberPane(key, collapsed);
      window.dispatchEvent(new Event('resize'));
    });
  }
}

/* Draggable pane edges.
   Each grip names the CSS custom property it drives, which edge of its pane
   it sits on, and the bounds it may take. The width persists like the
   collapsed state does, so a pane sized once stays that size. Arrow keys
   move it too: a drag-only control is unreachable from the keyboard. */
function rememberWidth(key, px) {
  try {
    window.localStorage.setItem(`ripple.width.${key}`, String(px));
  } catch (error) {
    // Private browsing denies localStorage; the drag still works.
  }
}

function storedWidth(key) {
  try {
    const value = Number(window.localStorage.getItem(`ripple.width.${key}`));
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch (error) {
    return null;
  }
}

function setUpPaneResizers() {
  const root = document.documentElement;
  for (const grip of document.querySelectorAll('[data-resize]')) {
    const pane = grip.parentElement;
    const key = grip.dataset.resize;
    const property = grip.dataset.resizeVar;
    const min = Number(grip.dataset.min || 180);
    const max = Number(grip.dataset.max || 520);
    const leading = grip.dataset.edge === 'right';
    const clamp = (px) => Math.min(max, Math.max(min, Math.round(px)));
    const setWidth = (px) => {
      const width = clamp(px);
      root.style.setProperty(property, `${width}px`);
      rememberWidth(key, width);
      window.dispatchEvent(new Event('resize'));
      return width;
    };

    const remembered = storedWidth(key);
    if (remembered) root.style.setProperty(property, `${clamp(remembered)}px`);

    grip.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      grip.setPointerCapture(event.pointerId);
      grip.classList.add('dragging');
      // The pane's own edge is the anchor, so the width follows the pointer
      // exactly however the pane is placed in the page.
      const box = pane.getBoundingClientRect();
      const anchor = leading ? box.left : box.right;
      const move = (moved) => {
        setWidth(leading ? moved.clientX - anchor : anchor - moved.clientX);
      };
      const stop = () => {
        grip.classList.remove('dragging');
        grip.removeEventListener('pointermove', move);
        grip.removeEventListener('pointerup', stop);
        grip.removeEventListener('pointercancel', stop);
      };
      grip.addEventListener('pointermove', move);
      grip.addEventListener('pointerup', stop);
      grip.addEventListener('pointercancel', stop);
    });

    grip.addEventListener('keydown', (event) => {
      const step = event.key === 'ArrowLeft' ? -16
        : event.key === 'ArrowRight' ? 16 : 0;
      if (!step) return;
      event.preventDefault();
      setWidth(pane.getBoundingClientRect().width + (leading ? step : -step));
    });
  }
}

document.addEventListener('DOMContentLoaded', setUpPaneToggles);
document.addEventListener('DOMContentLoaded', setUpPaneResizers);
