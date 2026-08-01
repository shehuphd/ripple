/* Shared helpers. */
function toast(message, isError) {
  const node = document.createElement('div');
  node.className = 'toast' + (isError ? ' err' : '');
  node.textContent = message;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 5200);
}

async function api(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.message || body.detail || `Request failed (${response.status})`);
  }
  return body;
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

document.addEventListener('DOMContentLoaded', setUpPaneToggles);
