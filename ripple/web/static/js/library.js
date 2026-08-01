document.getElementById('upload').addEventListener('submit', async (event) => {
  event.preventDefault();
  const input = document.getElementById('file');
  if (!input.files.length) { toast('Choose a file first.', true); return; }

  const data = new FormData();
  data.append('file', input.files[0]);
  const target = document.getElementById('upload-result');
  target.innerHTML = '<span class="tiny muted">Importing…</span>';

  try {
    const result = await api('/api/scripts', { method: 'POST', body: data });
    const warnings = result.warnings.map((w) =>
      `<div class="tiny muted">${w.code}: ${w.message}</div>`).join('');
    target.innerHTML =
      `<div class="tag ok">${result.outcome.replace(/_/g, ' ')}</div>
       <span class="tiny muted"> ${result.scenes} scenes, ${result.units} units</span>
       ${warnings}`;
    setTimeout(() => window.location.reload(), 900);
  } catch (error) {
    target.innerHTML = `<div class="tag bad">rejected</div>
      <span class="tiny muted"> ${error.message}</span>`;
  }
});

document.querySelectorAll('[data-delete]').forEach((button) => {
  button.addEventListener('click', async () => {
    const id = button.dataset.delete;
    const counts = await api(`/api/scripts/${id}/deletion-preview`);
    const message =
      `Delete "${button.dataset.title}"?\n\n` +
      `${counts.scenes} scenes, ${counts.units} units, ` +
      `${counts.entities} entities, ${counts.assertions} assertions.`;
    if (!window.confirm(message)) return;
    await api(`/api/scripts/${id}`, { method: 'DELETE' });
    window.location.reload();
  });
});

const clear = document.getElementById('clear-graphs');
if (clear) {
  clear.addEventListener('click', async () => {
    if (!window.confirm('Delete all extracted graph data? Scripts and units stay.')) return;
    const counts = await api('/api/graphs/clear', { method: 'POST' });
    toast(`Cleared ${counts.assertions} assertions and ${counts.entities} entities.`);
    setTimeout(() => window.location.reload(), 800);
  });
}
