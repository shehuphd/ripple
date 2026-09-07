/* Tab rail. One category per tab, and the chosen tab survives a reload so a
   save that refreshes the page returns to where the user was working. */
const tabs = document.querySelectorAll('.stab');
function showTab(name, focus) {
  tabs.forEach((tab) => {
    const on = tab.dataset.tab === name;
    tab.classList.toggle('on', on);
    tab.setAttribute('aria-selected', on ? 'true' : 'false');
    // Roving tabindex: the rail is one tab stop, arrows move inside it.
    tab.tabIndex = on ? 0 : -1;
    if (on && focus) tab.focus();
  });
  document.querySelectorAll('[data-pane]').forEach((pane) =>
    pane.classList.toggle('hide', pane.dataset.pane !== name));
  try { localStorage.setItem('settings-tab', name); } catch (e) { /* private mode */ }
}
tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => showTab(tab.dataset.tab));
  tab.addEventListener('keydown', (event) => {
    const order = [...tabs];
    let next = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      next = order[(index + 1) % order.length];
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      next = order[(index - 1 + order.length) % order.length];
    } else if (event.key === 'Home') {
      next = order[0];
    } else if (event.key === 'End') {
      next = order[order.length - 1];
    }
    if (next) {
      event.preventDefault();
      showTab(next.dataset.tab, true);
    }
  });
});
let openingTab = tabs.length ? tabs[0].dataset.tab : null;
try {
  const remembered = localStorage.getItem('settings-tab');
  if (remembered && [...tabs].some((t) => t.dataset.tab === remembered)) {
    openingTab = remembered;
  }
} catch (e) { /* private mode */ }
if (openingTab) showTab(openingTab);

/* Main and fallback model pickers. Both hot-save on change; the fallback
   answers a call the main model refused for an availability reason. */
const modelsCard = document.getElementById('models-card');

let pickersWired = false;

async function loadModelPickers() {
  if (!modelsCard || modelsCard.dataset.configured !== '1') return;
  const main = document.getElementById('main-model');
  const fallback = document.getElementById('fallback-model');
  const result = document.getElementById('models-result');
  const provider = modelsCard.dataset.provider;
  let models;
  try {
    models = await api(`/api/settings/models?provider=${provider}`);
  } catch (error) {
    result.textContent = error.message;
    return;
  }
  // A dead model stays listed but marked: hiding it would make the account's
  // entitlement problem look like a catalog change.
  const options = models.map((m) =>
    `<option value="${esc(m.id)}"${m.unavailable ? ' disabled' : ''}>` +
    `${esc(m.display_name)} · ${esc(m.tier)}` +
    `${m.unavailable ? ' · unavailable on this key' : ''}</option>`).join('');
  main.innerHTML = `<option value="">Choose a model…</option>${options}`;
  fallback.innerHTML = `<option value="">No fallback</option>${options}`;
  main.value = modelsCard.dataset.main || '';
  fallback.value = modelsCard.dataset.fallback || '';
  main.disabled = false;
  fallback.disabled = false;

  // Saving a key loads the pickers a second time, and one change listener
  // per picker is enough: two would save the same choice twice.
  if (pickersWired) return;
  pickersWired = true;

  main.addEventListener('change', async () => {
    if (!main.value) return;
    try {
      await api('/api/settings/model', {
        method: 'POST', body: form({ provider, model_id: main.value }),
      });
      ripple.trace('settings.model_selected', { provider, model: main.value });
      modelsCard.dataset.main = main.value;
      result.textContent = `Main model set to ${main.value}.`;
      toast('Main model saved.');
    } catch (error) {
      result.textContent = error.message;
      main.value = modelsCard.dataset.main || '';
    }
  });

  fallback.addEventListener('change', async () => {
    try {
      await api('/api/settings/fallback', {
        method: 'POST', body: form({ provider, model_id: fallback.value }),
      });
      ripple.trace('settings.fallback_selected', {
        provider, model: fallback.value || null,
      });
      modelsCard.dataset.fallback = fallback.value;
      result.textContent = fallback.value
        ? `Fallback set to ${fallback.value}. It answers when the main model `
          + 'is unavailable.'
        : 'Fallback cleared. An unavailable main model now fails the call.';
      toast('Fallback saved.');
    } catch (error) {
      result.textContent = error.message;
      fallback.value = modelsCard.dataset.fallback || '';
    }
  });
}
loadModelPickers();

document.querySelectorAll('[data-provider].card').forEach((card) => {
  const provider = card.dataset.provider;
  const result = card.querySelector('.result');
  if (!card.querySelector('.key')) return;
  let configured = card.querySelector('.dot').classList.contains('accepted');

  // An empty field is answered where it happened, with no request and no
  // error text: there is nothing to send and nothing for a provider to judge.
  function flashEmpty(input) {
    input.classList.remove('empty');
    void input.offsetWidth;  // restart the animation on a repeated click
    input.classList.add('empty');
    input.setAttribute('aria-invalid', 'true');
    input.focus();
    setTimeout(() => input.classList.remove('empty'), 900);
  }

  async function check(save) {
    const field = card.querySelector('.key');
    const key = field.value.trim();
    // Validate with an empty field re-checks a stored key. With nothing typed
    // and nothing stored, and for any Save, there is no credential to check.
    if (!key && (save || !configured)) { flashEmpty(field); return; }
    result.innerHTML = '<span class="muted">Checking…</span>';
    try {
      const url = save ? '/api/settings/save' : '/api/settings/validate';
      const outcome = await api(url, {
        method: 'POST', body: form({ provider, api_key: key || null }),
      });
      // The credential itself never reaches a trace; only the outcome does.
      ripple.trace('settings.credential_checked', {
        provider,
        save,
        valid: outcome.valid,
        models: outcome.model_count || 0,
        code: outcome.error_code || null,
      });
      if (outcome.valid && save) {
        // The card holds both states, so the saved key fills the pickers
        // where they stand rather than through a reload.
        toast('Key saved.');
        field.value = '';
        configured = true;
        card.querySelector('.dot').classList.replace('needs_review', 'accepted');
        card.querySelector('.forget').classList.remove('hide');
        result.innerHTML =
          `<span class="tag ok">valid</span> <span class="muted">
           Key saved. ${outcome.model_count} text models available.</span>`;
        if (modelsCard) {
          modelsCard.dataset.configured = '1';
          document.getElementById('models-body').classList.remove('hide');
          document.getElementById('models-empty').classList.add('hide');
          loadModelPickers();
        }
      } else if (outcome.valid) {
        result.innerHTML =
          `<span class="tag ok">valid</span> <span class="muted">
           ${outcome.model_count} text models available. Save it to pick
           models below.</span>`;
      } else {
        result.innerHTML =
          `<span class="tag bad">${esc(outcome.error_code).replace(/_/g, ' ')}</span>
           <span class="muted"> ${esc(outcome.error_message)}</span>`;
      }
    } catch (error) {
      result.innerHTML = `<span class="tag bad">failed</span>
        <span class="muted"> ${esc(error.message)}</span>`;
    }
  }

  // Both buttons stay live. An empty required field is an incomplete input
  // rather than an unavailable feature, so it gets a flash on click, not a
  // disabled control the user can press and get nothing from.
  const key = card.querySelector('.key');
  const save = card.querySelector('.save');
  const validate = card.querySelector('.validate');

  key.addEventListener('input', () => {
    key.classList.remove('empty');
    key.removeAttribute('aria-invalid');
  });

  validate.addEventListener('click', () => check(false));
  save.addEventListener('click', () => check(true));
  const forget = card.querySelector('.forget');
  if (forget) {
    forget.addEventListener('click', async () => {
      if (!(await confirmDialog(
        'Forget the stored key? Building graphs and previews will need a '
        + 'key again.', 'Forget', { destructive: true }))) return;
      await api(`/api/settings/${provider}`, { method: 'DELETE' });
      ripple.trace('settings.credential_forgotten', { provider });
      configured = false;
      card.querySelector('.dot').classList.replace('accepted', 'needs_review');
      forget.classList.add('hide');
      result.innerHTML = '<span class="muted">Key forgotten. Enter one to '
        + 'build graphs and judge edits again.</span>';
      if (modelsCard) {
        modelsCard.dataset.configured = '';
        document.getElementById('models-body').classList.add('hide');
        document.getElementById('models-empty').classList.remove('hide');
      }
      toast('Key forgotten.');
    });
  }
});

/* The token budget. Blank clears the cap. */
const budgetSave = document.getElementById('budget-save');
if (budgetSave) {
  budgetSave.addEventListener('click', async () => {
    const field = document.getElementById('budget-field');
    const result = document.getElementById('budget-result');
    try {
      const body = await api('/api/settings/budget', {
        method: 'POST', body: form({ max_total_tokens: field.value }),
      });
      ripple.trace('settings.budget_set', { cap: body.max_total_tokens });
      result.textContent = body.max_total_tokens
        ? `Budget set to ${body.max_total_tokens.toLocaleString()} tokens.`
        : 'Budget cleared. Calls run whenever a model is selected.';
      toast('Budget updated.');
    } catch (error) {
      result.textContent = error.message;
    }
  });
}

/* Where a script opens from the library. Saved the moment it changes. */
document.querySelectorAll('input[name="landing"]').forEach((radio) => {
  radio.addEventListener('change', async () => {
    const result = document.getElementById('landing-result');
    try {
      await api('/api/settings/landing', {
        method: 'POST', body: form({ landing_view: radio.value }),
      });
      ripple.trace('settings.landing_selected', { view: radio.value });
      ripple.announceSetting('landing', { landing_view: radio.value });
      result.textContent = radio.value === 'graph'
        ? 'Scripts now open on their production graph.'
        : 'Scripts now open on the script itself.';
    } catch (error) {
      result.textContent = error.message;
    }
  });
});

/* The billable-actions table: search, sort, and paging, all over the rows
   already in the page. No request, no reload. */
const spendSearch = document.getElementById('spend-search');
if (spendSearch) {
  const allRows = [...document.querySelectorAll('#spend-rows tr')];
  const body = document.getElementById('spend-rows');
  const noMatch = document.getElementById('spend-nomatch');
  const count = document.getElementById('spend-count');
  const range = document.getElementById('spend-range');
  const sizeField = document.getElementById('spend-size');
  const prev = document.getElementById('spend-prev');
  const next = document.getElementById('spend-next');
  const headers = [...document.querySelectorAll('.spend-table th[data-sort]')];
  const NUMERIC = new Set(['calls', 'tokens', 'cost']);

  let sortKey = 'when';
  let ascending = false;
  let page = 0;
  let size = 25;
  try {
    const saved = parseInt(localStorage.getItem('spend-page-size'), 10);
    if ([25, 50, 100].includes(saved)) size = saved;
  } catch (e) { /* private mode */ }
  sizeField.value = String(size);

  const value = (row, key) => (NUMERIC.has(key)
    ? Number(row.dataset[key] || 0)
    : (row.dataset[key] || '').toLowerCase());

  function draw() {
    const needle = spendSearch.value.trim().toLowerCase();
    const matched = allRows.filter(
      (row) => !needle || row.textContent.toLowerCase().includes(needle)
    );
    matched.sort((a, b) => {
      const left = value(a, sortKey);
      const right = value(b, sortKey);
      if (left === right) return 0;
      return (left < right ? -1 : 1) * (ascending ? 1 : -1);
    });
    const pages = Math.max(1, Math.ceil(matched.length / size));
    if (page >= pages) page = pages - 1;
    const from = page * size;
    const shown = matched.slice(from, from + size);
    // Reordering rows in place keeps one DOM node per action, so a sort
    // never rebuilds the table from strings the page has already parsed.
    allRows.forEach((row) => { row.style.display = 'none'; });
    shown.forEach((row) => { row.style.display = ''; body.appendChild(row); });

    noMatch.classList.toggle('hide', matched.length > 0);
    count.textContent = needle
      ? `${matched.length} of ${allRows.length} action(s)`
      : `${allRows.length} action(s)`;
    range.textContent = matched.length
      ? `${from + 1}–${from + shown.length} of ${matched.length}`
      : '';
    prev.disabled = page === 0;
    next.disabled = page >= pages - 1;
    document.getElementById('spend-foot').classList.toggle(
      'hide', matched.length <= size && page === 0
    );
    headers.forEach((header) => {
      const on = header.dataset.sort === sortKey;
      header.setAttribute(
        'aria-sort', on ? (ascending ? 'ascending' : 'descending') : 'none'
      );
      header.classList.toggle('sorted', on);
      header.classList.toggle('asc', on && ascending);
    });
  }

  spendSearch.addEventListener('input', () => { page = 0; draw(); });
  headers.forEach((header) => {
    header.querySelector('.sortbtn').addEventListener('click', () => {
      const key = header.dataset.sort;
      // A new column starts on the reading most people want first: biggest
      // number, earliest word, newest date.
      if (key === sortKey) ascending = !ascending;
      else { sortKey = key; ascending = !NUMERIC.has(key) && key !== 'when'; }
      page = 0;
      draw();
    });
  });
  sizeField.addEventListener('change', () => {
    size = parseInt(sizeField.value, 10) || 25;
    page = 0;
    try { localStorage.setItem('spend-page-size', String(size)); }
    catch (e) { /* private mode */ }
    draw();
  });
  prev.addEventListener('click', () => { page -= 1; draw(); });
  next.addEventListener('click', () => { page += 1; draw(); });
  draw();
}

/* Ask Ripple behaviour. Each control writes one preference the moment it
   changes; the agent reads them when it runs. */
const agentCard = document.getElementById('agent-card');
if (agentCard) {
  const result = document.getElementById('agent-result');

  async function save(key, value, say) {
    try {
      await api('/api/settings/agent', {
        method: 'POST', body: form({ key, value }),
      });
      ripple.trace('settings.agent_changed', { setting: key, value });
      result.textContent = say;
    } catch (error) {
      result.textContent = error.message;
      return false;
    }
    return true;
  }

  agentCard.querySelectorAll('.switch[data-agent]').forEach((toggle) => {
    toggle.addEventListener('click', async () => {
      const next = toggle.classList.contains('on') ? 'off' : 'on';
      const label = toggle.getAttribute('aria-label');
      if (!await save(toggle.dataset.agent, next, `${label}: ${next}.`)) return;
      toggle.classList.toggle('on', next === 'on');
      toggle.setAttribute('aria-checked', next === 'on' ? 'true' : 'false');
    });
  });

  agentCard.querySelectorAll('.segbtn[data-agent]').forEach((button) => {
    button.addEventListener('click', async () => {
      const group = button.closest('.seg');
      const value = button.dataset.value;
      const say = value === 'on'
        ? 'Ripple drafts patches around a cut scene.'
        : 'Ripple reports what a cut breaks and stops there.';
      if (!await save(button.dataset.agent, value, say)) return;
      group.querySelectorAll('.segbtn').forEach((one) => {
        const on = one === button;
        one.classList.toggle('on', on);
        one.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
    });
  });

  const ceiling = document.getElementById('agent-ceiling');
  if (ceiling) {
    ceiling.addEventListener('change', () => {
      save('agent_tool_ceiling', ceiling.value,
        `A turn stops after ${ceiling.value} tool calls.`);
    });
  }

  const floor = document.getElementById('agent-floor');
  if (floor) {
    floor.addEventListener('change', () => {
      const say = floor.value === '0'
        ? 'Every fact the judge accepts is proposed.'
        : `Facts scoring below ${floor.value} are held back and named.`;
      save('agent_confidence_floor', floor.value, say);
    });
  }
}

/* The lake screensaver.
   Every row saves the moment it changes, like the rest of this pane. The
   shortcut row is the one that needs more than a click: it captures the next
   chord pressed, and refuses a chord that would take a key the writing
   surface already answers. */
const saverCard = document.getElementById('screensaver-card');
if (saverCard) {
  const result = document.getElementById('saver-result');

  /* A saved row says what it is worth in the row itself, so a sentence
     underneath repeating it is noise. The line is kept for what the rows
     cannot show: a refusal, and the shortcut capture's guidance. */
  async function saveSaver(key, value) {
    try {
      const saved = await api('/api/settings/screensaver', {
        method: 'POST', body: form({ key, value }),
      });
      ripple.trace('settings.screensaver_changed', { setting: key, value });
      // The overlay read its settings when the page loaded, so it is told
      // rather than left to find out on the next reload.
      ripple.announceSetting('screensaver', saved);
      result.textContent = '';
    } catch (error) {
      result.textContent = error.message;
      return false;
    }
    return true;
  }

  saverCard.querySelectorAll('.switch[data-saver]').forEach((toggle) => {
    toggle.addEventListener('click', async () => {
      const next = toggle.classList.contains('on') ? 'off' : 'on';
      if (!await saveSaver(toggle.dataset.saver, next)) return;
      toggle.classList.toggle('on', next === 'on');
      toggle.setAttribute('aria-checked', next === 'on' ? 'true' : 'false');
    });
  });

  saverCard.querySelectorAll('.segbtn[data-saver]').forEach((button) => {
    button.addEventListener('click', async () => {
      const key = button.dataset.saver;
      const value = button.dataset.value;
      const idle = key === 'screensaver_idle_minutes';
      const look = key === 'screensaver_theme';
      const hue = key === 'screensaver_colour';
      const fade = key === 'screensaver_fade_seconds';
      if (!await saveSaver(key, value)) return;
      button.closest('.seg').querySelectorAll('.segbtn').forEach((one) => {
        const on = one === button;
        one.classList.toggle('on', on);
        one.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      const named = { idle, theme: look, colour: hue, fade };
      const readout = document.getElementById(
        `saver-${Object.keys(named).find((one) => named[one]) || 'throttle'}`);
      if (readout) readout.textContent = button.textContent.trim();
    });
  });

  /* What each chord already does, so a rebind can say which one it clashes
     with rather than silently shadowing it. Only chords of two modifiers or
     more can be entered here, so only those are listed. Modifiers are put in
     one fixed order on both sides of the comparison: written the way a
     person says them, Cmd+Shift+Z would never match what the browser
     reports. */
  const MOD_ORDER = ['ctrl', 'alt', 'shift', 'meta'];
  const MOD_LABEL = {
    ctrl: 'Ctrl', alt: 'Opt', shift: '⇧', meta: '⌘',
  };

  function chordOf(mods, code) {
    return [...MOD_ORDER.filter((one) => mods.includes(one)), code].join('+');
  }

  const TAKEN = {};
  [
    [['meta', 'shift'], 'KeyZ', 'Redo'],
    [['ctrl', 'shift'], 'KeyZ', 'Redo'],
    [['meta', 'shift'], 'ArrowUp', 'Jump to the first line'],
    [['meta', 'shift'], 'ArrowDown', 'Jump to the last line'],
  ].forEach(([mods, code, action]) => { TAKEN[chordOf(mods, code)] = action; });
  const chord = document.getElementById('saver-chord');
  const rebind = document.getElementById('saver-rebind');

  function paintChord(value) {
    const parts = value.split('+');
    const key = parts.pop();
    chord.innerHTML = parts.map((one) =>
      `<kbd>${esc(MOD_LABEL[one] || one)}</kbd>`).join('')
      + `<kbd>${esc(key.replace(/^Key|^Digit/, ''))}</kbd>`;
  }
  paintChord(saverCard.dataset.shortcut);

  rebind.addEventListener('click', () => {
    if (rebind.classList.contains('on')) return;
    rebind.classList.add('on');
    rebind.textContent = 'Press a chord';
    chord.textContent = 'Listening…';

    const stop = () => {
      rebind.classList.remove('on');
      rebind.textContent = 'Change';
      window.removeEventListener('keydown', capture, true);
      paintChord(saverCard.dataset.shortcut);
    };

    async function capture(event) {
      event.preventDefault();
      event.stopPropagation();
      if (event.key === 'Escape') { stop(); return; }
      const mods = MOD_ORDER.filter((one) => event[`${one}Key`]);
      // A modifier held on its own is the first half of a chord, not a
      // chord, so listening continues rather than rejecting it.
      if (['Control', 'Alt', 'Shift', 'Meta'].includes(event.key)) return;
      if (mods.length < 2) {
        result.textContent = 'A shortcut needs at least two modifiers and a '
          + 'key, or an ordinary keystroke would open the screensaver.';
        return;
      }
      const next = chordOf(mods, event.code);
      if (TAKEN[next]) {
        result.textContent = `That shortcut is already used by ${TAKEN[next]}.`;
        return;
      }
      window.removeEventListener('keydown', capture, true);
      rebind.classList.remove('on');
      rebind.textContent = 'Change';
      if (await saveSaver('screensaver_shortcut', next)) {
        saverCard.dataset.shortcut = next;
      }
      paintChord(saverCard.dataset.shortcut);
    }
    window.addEventListener('keydown', capture, true);
  });
}
