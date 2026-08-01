const ask = document.getElementById('ask');
if (ask) {
  const question = document.getElementById('q');
  const out = document.getElementById('out');
  // The chosen script comes from the server, not the query string: the page
  // falls back to the most recent script when none is named, and reading the
  // absent parameter sent "null" to the API.
  const scriptId = document.getElementById('askform').dataset.script;

  async function run() {
    if (!question.value.trim()) return;
    out.innerHTML = '<div class="empty">Asking…</div>';
    try {
      const body = await api(`/api/scripts/${scriptId}/ask`, {
        method: 'POST', body: form({ question: question.value }),
      });
      const chips = body.entities
        .map((e) => `<span class="tag">${e}</span>`).join(' ');
      const cited = body.cited_units.map((u) => `
        <div class="cited">
          <span class="no">${u.scene ?? ''}</span>
          <span class="bd">${u.text}</span>
        </div>`).join('');
      out.innerHTML = `
        <div class="tiny muted" style="margin-bottom:12px">
          Grounded in ${body.grounded_in} assertions · ${body.cited_units.length} units ·
          mean confidence ${body.mean_confidence}
          ${body.generated ? '' : ' · deterministic answer, no model configured'}
        </div>
        <div class="card">
          <div class="answer">${body.answer}</div>
          <div class="chips" style="margin:14px 0 0">${chips}</div>
          <div class="tiny muted" style="margin-top:12px">
            Answers come from accepted assertions only. Nothing here is generated
            from the screenplay text.</div>
        </div>
        <div class="card" style="margin-top:14px">
          <div class="hd"><h2>Cited units</h2>
            <span class="meta">${body.cited_units.length} · ordered by scene</span></div>
          ${cited || '<div class="empty">None.</div>'}
        </div>`;
    } catch (error) {
      out.innerHTML = `<div class="empty">${error.message}</div>`;
    }
  }

  ask.addEventListener('click', run);
  question.addEventListener('keydown', (e) => { if (e.key === 'Enter') run(); });
}
