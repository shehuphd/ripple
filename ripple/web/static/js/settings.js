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

  async function check(save) {
    const key = card.querySelector('.key').value.trim();
    if (save && !key) { toast('Paste a key first.', true); return; }
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

  card.querySelector('.validate').addEventListener('click', () => check(false));
  card.querySelector('.save').addEventListener('click', () => check(true));
  const forget = card.querySelector('.forget');
  if (forget) {
    forget.addEventListener('click', async () => {
      await api(`/api/settings/${provider}`, { method: 'DELETE' });
      window.location.reload();
    });
  }
});
