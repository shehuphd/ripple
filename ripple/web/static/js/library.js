const fileInput = document.getElementById('file');
const drop = document.getElementById('drop');
const result = document.getElementById('upload-result');

// The page reloads after an import so the new row appears, which would wipe
// the outcome and its warnings; the rendered result is stashed across the
// reload and restored here. The stash holds only markup this file built,
// with every server value already escaped.
const IMPORT_STASH = 'ripple.import.result';
try {
  const stashed = sessionStorage.getItem(IMPORT_STASH);
  if (stashed) {
    sessionStorage.removeItem(IMPORT_STASH);
    result.innerHTML = stashed;
  }
} catch (error) { /* no storage, nothing to restore */ }

async function upload(file) {
  const data = new FormData();
  data.append('file', file);
  result.innerHTML = '<span class="muted">Importing…</span>';
  try {
    const body = await api('/api/scripts', { method: 'POST', body: data });
    ripple.trace('import.result', {
      outcome: body.outcome,
      scenes: body.scenes,
      units: body.units,
      warnings: body.warnings.length,
    });
    const warnings = body.warnings
      .map((w) => `<div class="muted">${esc(w.code).replace(/_/g, ' ')}:
        ${esc(w.message)}</div>`).join('');
    result.innerHTML =
      `<span class="status"><span class="dot ${body.outcome}"></span>
       ${body.outcome.replace(/_/g, ' ')}</span>
       <span class="muted"> · ${body.scenes} scenes, ${body.units} units</span>
       ${warnings}`;
    try {
      sessionStorage.setItem(IMPORT_STASH, result.innerHTML);
    } catch (error) { /* no storage, the reload wipes the notice */ }
    setTimeout(() => window.location.reload(), 900);
  } catch (error) {
    ripple.trace('import.rejected', { error: error.message });
    result.innerHTML = `<span class="status"><span class="dot rejected"></span>
      rejected</span> <span class="muted"> ${esc(error.message)}</span>`;
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

// Where a script opens is a Settings preference; the reader is the default,
// and the other view stays one click away either way.
const landing = document.getElementById('rows').dataset.landing;
document.querySelectorAll('#rows tr').forEach((row) => {
  row.addEventListener('click', (event) => {
    if (event.target.closest('[data-delete]')) return;
    window.location = landing === 'graph'
      ? `/scripts/${row.dataset.id}/graph`
      : `/scripts/${row.dataset.id}`;
  });
});

document.querySelectorAll('[data-delete]').forEach((button) => {
  button.addEventListener('click', async (event) => {
    event.stopPropagation();
    const id = button.dataset.delete;
    const counts = await api(`/api/scripts/${id}/deletion-preview`);
    const title = button.closest('tr').dataset.title;
    if (!(await confirmDialog(
      `Delete "${title}"?\n\n${counts.scenes} scenes, ${counts.units} units, ` +
      `${counts.entities} entities, ${counts.assertions} assertions.`,
      'Delete'))) return;
    ripple.trace('script.deleted', { script: id, scenes: counts.scenes });
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
