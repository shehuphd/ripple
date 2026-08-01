const fileInput = document.getElementById('file');
const drop = document.getElementById('drop');
const result = document.getElementById('upload-result');

async function upload(file) {
  const data = new FormData();
  data.append('file', file);
  result.innerHTML = '<span class="muted">Importing…</span>';
  try {
    const body = await api('/api/scripts', { method: 'POST', body: data });
    const warnings = body.warnings
      .map((w) => `<div class="muted">${w.code}: ${w.message}</div>`).join('');
    result.innerHTML =
      `<span class="status"><span class="dot ${body.outcome}"></span>
       ${body.outcome.replace(/_/g, ' ')}</span>
       <span class="muted"> · ${body.scenes} scenes, ${body.units} units</span>
       ${warnings}`;
    setTimeout(() => window.location.reload(), 900);
  } catch (error) {
    result.innerHTML = `<span class="status"><span class="dot rejected"></span>
      rejected</span> <span class="muted"> ${error.message}</span>`;
  }
}

document.getElementById('pick').addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => {
  if (fileInput.files.length) upload(fileInput.files[0]);
});
drop.addEventListener('click', () => fileInput.click());
['dragenter', 'dragover'].forEach((event) =>
  drop.addEventListener(event, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((event) =>
  drop.addEventListener(event, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (e) => {
  if (e.dataTransfer.files.length) upload(e.dataTransfer.files[0]);
});

document.querySelectorAll('#rows tr').forEach((row) => {
  row.addEventListener('click', (event) => {
    if (event.target.closest('[data-delete]')) return;
    window.location = `/scripts/${row.dataset.id}`;
  });
});

document.querySelectorAll('[data-delete]').forEach((button) => {
  button.addEventListener('click', async (event) => {
    event.stopPropagation();
    const id = button.dataset.delete;
    const counts = await api(`/api/scripts/${id}/deletion-preview`);
    const title = button.closest('tr').dataset.title;
    if (!window.confirm(
      `Delete "${title}"?\n\n${counts.scenes} scenes, ${counts.units} units, ` +
      `${counts.entities} entities, ${counts.assertions} assertions.`)) return;
    await api(`/api/scripts/${id}`, { method: 'DELETE' });
    window.location.reload();
  });
});

const search = document.getElementById('search');
search.addEventListener('input', () => {
  const needle = search.value.toLowerCase();
  document.querySelectorAll('#rows tr').forEach((row) => {
    row.style.display = row.dataset.title.toLowerCase().includes(needle) ? '' : 'none';
  });
});
