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
  const configured = card.querySelector('.dot').classList.contains('accepted');

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
        // Reload so the Models card renders its pickers against the saved key.
        toast('Key saved.');
        setTimeout(() => window.location.reload(), 600);
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
        + 'key again.', 'Forget'))) return;
      await api(`/api/settings/${provider}`, { method: 'DELETE' });
      ripple.trace('settings.credential_forgotten', { provider });
      window.location.reload();
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
      result.textContent = radio.value === 'graph'
        ? 'Scripts now open on their production graph.'
        : 'Scripts now open on the script itself.';
    } catch (error) {
      result.textContent = error.message;
    }
  });
});
