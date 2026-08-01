function renderModels(card, provider, models) {
  const target = card.querySelector('.models');
  if (!models.length) { target.innerHTML = ''; return; }
  const options = models
    .map((m) => `<option value="${m.id}">${m.display_name} · ${m.tier}</option>`)
    .join('');
  target.innerHTML =
    `<label>Model</label>
     <div class="row">
       <select class="field model">${options}</select>
       <button class="btn use">Use this model</button>
     </div>`;
  target.querySelector('.use').addEventListener('click', async () => {
    const modelId = target.querySelector('.model').value;
    await api('/api/settings/model', {
      method: 'POST', body: form({ provider, model_id: modelId }),
    });
    toast(`Active model set to ${modelId}.`);
    setTimeout(() => window.location.reload(), 700);
  });
}

document.querySelectorAll('[data-provider]').forEach((card) => {
  const provider = card.dataset.provider;
  const result = card.querySelector('.result');
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
      if (outcome.valid) {
        result.innerHTML =
          `<span class="tag ok">valid</span> <span class="muted">
           ${outcome.model_count} text models available</span>`;
        card.querySelector('.key').value = '';
        renderModels(card, provider, await api(`/api/settings/models?provider=${provider}`));
      } else {
        result.innerHTML =
          `<span class="tag bad">${outcome.error_code}</span>
           <span class="muted"> ${outcome.error_message}</span>`;
      }
    } catch (error) {
      result.innerHTML = `<span class="tag bad">failed</span>
        <span class="muted"> ${error.message}</span>`;
    }
  }

  // Both buttons stay live. An empty required field is an incomplete input
  // rather than an unavailable feature, so it earns a flash on click, not a
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
      await api(`/api/settings/${provider}`, { method: 'DELETE' });
      window.location.reload();
    });
  }
});
