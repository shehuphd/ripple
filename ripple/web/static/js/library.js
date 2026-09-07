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
      `<span class="status">${body.outcome.replace(/_/g, ' ')}</span>
       <span class="muted"> · ${body.scenes} scenes, ${body.units} units</span>
       ${warnings}`;
    try {
      sessionStorage.setItem(IMPORT_STASH, result.innerHTML);
    } catch (error) { /* no storage, the reload wipes the notice */ }

    /* A same-titled script with a graph: offer to continue it as a new
       draft. The title is the invitation; the user's answer is the link. */
    const candidate = (body.draft_candidates || [])[0];
    if (candidate) {
      const linkIt = await confirmDialog(
        `"${body.title}" matches an existing script (draft `
        + `${candidate.draft_number}). Import this file as draft `
        + `${candidate.draft_number + 1}? Unchanged scenes keep their `
        + 'graph at no model cost; changed scenes are re-read. '
        + 'Cancel keeps it as a separate script.',
        'Link as new draft',
      );
      if (linkIt) {
        const preview = await api(`/api/scripts/${body.id}/link-draft/preview`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ predecessor_script_id: candidate.id }),
        });
        const accepted = preview.suggestions.length
          ? await reviewSuggestions(preview)
          : {};
        if (accepted === null) {
          setTimeout(() => window.location.reload(), 400);
          return;
        }
        const linked = await api(`/api/scripts/${body.id}/link-draft`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            predecessor_script_id: candidate.id,
            accepted_pairs: accepted,
          }),
        });
        ripple.trace('draft.linked', {
          unchanged: linked.link.unchanged,
          toExtract: linked.link.to_extract,
          confirmed: Object.keys(accepted).length,
        });
        const target = linked.run
          ? `/scripts/${body.id}?run=${linked.run.run_id}`
          : `/scripts/${body.id}?report=ready`;
        window.location.assign(target);
        return;
      }
    }
    setTimeout(() => window.location.reload(), 900);
  } catch (error) {
    ripple.trace('import.rejected', { error: error.message });
    result.innerHTML = `<span class="status">rejected</span>
      <span class="muted"> ${esc(error.message)}</span>`;
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
// and the other view stays one click away either way. The rows were built
// against the preference as it stood when the page loaded, so a change made
// in Settings while this page is open rewrites them where they are.
const rows = document.getElementById('rows');
const target = (id) => (rows.dataset.landing === 'graph'
  ? `/scripts/${id}/graph` : `/scripts/${id}`);

document.querySelectorAll('#rows tr').forEach((row) => {
  row.addEventListener('click', (event) => {
    if (event.target.closest('[data-delete]')) return;
    if (event.target.closest('.checkcol')) return;
    window.location = target(row.dataset.id);
  });
});

ripple.onSetting('landing', (saved) => {
  rows.dataset.landing = saved.landing_view;
  document.querySelectorAll('#rows tr').forEach((row) => {
    const link = row.querySelector('a.title');
    if (link) link.href = target(row.dataset.id);
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
      'Delete', { destructive: true }))) return;
    ripple.trace('script.deleted', { script: id, scenes: counts.scenes });
    await api(`/api/scripts/${id}`, { method: 'DELETE' });
    window.location.reload();
  });
});

/* Search and sort, both over the rows already in the page. The library is
   one page of scripts, so neither needs a request. */
const search = document.getElementById('search');
const libraryBody = document.getElementById('rows');
const libraryRows = [...document.querySelectorAll('#rows tr')];
const NUMERIC_COLUMNS = new Set(['pages', 'scenes', 'runtime']);
let sortKey = null;
let ascending = true;

function libraryValue(row, key) {
  return NUMERIC_COLUMNS.has(key)
    ? Number(row.dataset[key] || 0)
    : (row.dataset[key] || '').toLowerCase();
}

function drawLibrary() {
  const needle = (search.value || '').toLowerCase();
  if (sortKey) {
    const ordered = libraryRows.slice().sort((a, b) => {
      const left = libraryValue(a, sortKey);
      const right = libraryValue(b, sortKey);
      if (left === right) return 0;
      return (left < right ? -1 : 1) * (ascending ? 1 : -1);
    });
    // Moving the existing rows keeps one DOM node per script, so a sort
    // never rebuilds what the page has already parsed.
    ordered.forEach((row) => libraryBody.appendChild(row));
  }
  libraryRows.forEach((row) => {
    const matches = row.dataset.title.toLowerCase().includes(needle);
    row.style.display = matches ? '' : 'none';
    // A row the search hides leaves the selection: a batch must never act
    // on a row the person can no longer see is ticked.
    if (!matches) row.querySelector('.row-check').checked = false;
  });
  refreshBatch();
}

search.addEventListener('input', drawLibrary);

document.querySelectorAll('#library-table th[data-sort]').forEach((header) => {
  header.querySelector('.sortbtn').addEventListener('click', () => {
    const key = header.dataset.sort;
    // A second click on the sorted column reverses it. A new column starts
    // ascending for text and descending for a number, which is what a
    // reader wants first from each: A to Z, and the longest script.
    if (sortKey === key) ascending = !ascending;
    else ascending = !NUMERIC_COLUMNS.has(key);
    sortKey = key;
    document.querySelectorAll('#library-table th[data-sort]').forEach((one) => {
      const on = one === header;
      one.classList.toggle('sorted', on);
      one.classList.toggle('asc', on && ascending);
      one.setAttribute(
        'aria-sort', on ? (ascending ? 'ascending' : 'descending') : 'none');
    });
    drawLibrary();
  });
});

/* Batch selection. Checkboxes drive a floating bar whose one action deletes
   the selected scripts wholesale. "Select all" ticks the rows the search
   shows, and a row the search hides drops out of the selection. */
const batchBar = document.getElementById('batch-bar');
const batchAll = document.getElementById('batch-all');
const batchCount = document.getElementById('batch-count');
const checkOf = (row) => row.querySelector('.row-check');
const selectedRows = () => libraryRows.filter((row) => checkOf(row).checked);
const visibleRows = () => libraryRows.filter((row) => row.style.display !== 'none');

function refreshBatch() {
  if (!batchBar) return;
  const chosen = selectedRows();
  batchCount.textContent = `${chosen.length} selected`;
  batchBar.classList.toggle('hide', chosen.length === 0);
  const shown = visibleRows();
  batchAll.checked = shown.length > 0 && shown.every((row) => checkOf(row).checked);
  batchAll.indeterminate = chosen.length > 0 && !batchAll.checked;
}

if (batchBar) {
  libraryRows.forEach((row) => checkOf(row).addEventListener('change', refreshBatch));

  batchAll.addEventListener('change', () => {
    const on = batchAll.checked;
    visibleRows().forEach((row) => { checkOf(row).checked = on; });
    refreshBatch();
  });

  document.getElementById('batch-clear').addEventListener('click', () => {
    libraryRows.forEach((row) => { checkOf(row).checked = false; });
    refreshBatch();
  });

  document.getElementById('batch-delete').addEventListener('click', async () => {
    const chosen = selectedRows();
    if (!chosen.length) return;
    const scenes = chosen.reduce(
      (sum, row) => sum + (Number(row.dataset.scenes) || 0), 0);
    const ok = await confirmDialog(
      `Delete ${chosen.length} selected script(s)? This permanently removes `
      + `their ${scenes} scene(s) with every entity, assertion, and record `
      + 'built on them, and cannot be undone.',
      'Delete selected scripts', { destructive: true });
    if (!ok) return;
    const ids = chosen.map((row) => row.dataset.id);
    ripple.trace('scripts.batch_deleted', { scripts: ids.length });
    try {
      await api('/api/scripts/batch/delete', {
        method: 'POST', body: form({ ids: JSON.stringify(ids) }),
      });
    } catch (error) {
      toast(error.message, true);
      return;
    }
    window.location.reload();
  });
}

/* The alignment review screen: pairs the aligner suspects but refuses to
   make alone. Each suggestion is a checkbox; unticked means both scenes are
   treated as new and deleted, the safe reading. Resolves to the accepted
   {newSceneId: oldSceneId} map, or null on cancel. */
function reviewSuggestions(preview) {
  return new Promise((resolve) => {
    const veil = document.createElement('div');
    veil.className = 'confirm-veil';
    const rows = preview.suggestions.map((s, index) => `
      <label class="sugg">
        <input type="checkbox" data-index="${index}">
        <span class="pairing">
          <span>${esc(s.old.number ? `Sc ${s.old.number} · ` : '')}${esc(s.old.heading)}
            <span class="muted tiny">${esc(s.old.first_line)}</span></span>
          <span class="muted">continues as</span>
          <span>${esc(s.new.number ? `Sc ${s.new.number} · ` : '')}${esc(s.new.heading)}
            <span class="muted tiny">${esc(s.new.first_line)}</span></span>
          <span class="muted tiny">${Math.round(s.score * 100)}% similar</span>
        </span>
      </label>`).join('');
    veil.innerHTML = `
      <div class="confirm-box review-align" role="dialog" aria-modal="true"
           aria-label="Review uncertain scene matches">
        <h2 style="margin-bottom:4px">Uncertain scene matches</h2>
        <p class="tiny muted" style="margin-bottom:10px">
          These pairs look related but not similar enough to link without
          you. Tick a pair to carry its identity across; anything unticked
          is treated as a new scene and re-read.</p>
        ${rows}
        <div class="confirm-acts">
          <button class="btn" data-cancel>Cancel the link</button>
          <button class="btn pri" data-ok>Continue</button>
        </div>
      </div>`;
    const close = (answer) => { veil.remove(); resolve(answer); };
    veil.querySelector('[data-cancel]').addEventListener('click', () => close(null));
    veil.querySelector('[data-ok]').addEventListener('click', () => {
      const accepted = {};
      veil.querySelectorAll('input[type="checkbox"]').forEach((box) => {
        if (box.checked) {
          const s = preview.suggestions[Number(box.dataset.index)];
          accepted[s.new.id] = s.old.id;
        }
      });
      close(accepted);
    });
    veil.addEventListener('click', (event) => {
      if (event.target === veil) close(null);
    });
    veil.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); close(null); }
    });
    document.body.appendChild(veil);
    veil.querySelector('input, button').focus();
  });
}

/* A script can start empty and be written in Ripple, not only imported. */
const newScript = document.getElementById('new-script');
if (newScript) {
  newScript.addEventListener('click', async () => {
    const title = await askDialog(
      'Name the script. You can rename it any time from the reader.',
      'Create', 'WORKING TITLE');
    if (title === null) return;
    try {
      const created = await api('/api/scripts/new', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      });
      window.location.href = `/scripts/${created.id}`;
    } catch (error) {
      toast(error.message, true);
    }
  });
}
