/* Drafts are edits the user has typed but not accepted. They live only in the
   page: the accepted script is unchanged until a ripple is accepted, which is
   the guarantee the whole product rests on. */
const state = {
  unit: null, text: '', sceneNo: null, changeSet: null,
  drafts: new Map(), applying: false,
};

const draftCount = document.getElementById('draft-count');
const revertAll = document.getElementById('revert-all');

const acceptedTextOf = (node) => node.dataset.accepted;

function currentTextOf(unitId) {
  if (state.drafts.has(unitId)) return state.drafts.get(unitId);
  const node = document.querySelector(`.u[data-unit="${unitId}"]`);
  return node ? acceptedTextOf(node) : '';
}

function refreshDraftIndicator() {
  const count = state.drafts.size;
  draftCount.classList.toggle('hide', count === 0);
  draftCount.querySelector('span').textContent =
    `${count} line${count === 1 ? '' : 's'} edited`;
  revertAll.disabled = count === 0;
}

function noteDraft(node) {
  const unitId = node.dataset.unit;
  if (node.textContent === acceptedTextOf(node)) {
    state.drafts.delete(unitId);
    node.classList.remove('edited');
  } else {
    state.drafts.set(unitId, node.textContent);
    node.classList.add('edited');
  }
  refreshDraftIndicator();
}

function revertLine(node) {
  node.textContent = acceptedTextOf(node);
  state.drafts.delete(node.dataset.unit);
  node.classList.remove('edited');
  refreshDraftIndicator();
}

const requirements = document.getElementById('requirements');
const reqChips = document.getElementById('req-chips');
const reqMeta = document.getElementById('req-meta');
const graph = document.getElementById('graph');
const graphMeta = document.getElementById('graph-meta');
const seeRipple = document.getElementById('see-ripple');
const crumb = document.getElementById('crumb');
const preview = document.getElementById('preview');

function edgeRow(edge, cls, sign) {
  return `<div class="edge ${cls || ''}">
    ${sign ? `<span class="sign">${sign}</span>` : ''}
    <span>${edge.subject}</span>
    <span class="p">${edge.predicate}</span>
    <span>${edge.object}</span>
    <span class="c">${edge.confidence ?? ''}</span></div>`;
}

/* Scene list selection scrolls the page rather than filtering it, so the
   surrounding scenes stay readable. */
document.querySelectorAll('.scene-row').forEach((row) => {
  row.addEventListener('click', () => {
    document.querySelectorAll('.scene-row.on').forEach((n) => n.classList.remove('on'));
    row.classList.add('on');
    const target = document.querySelector(`[data-scene-body="${row.dataset.scene}"]`);
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
});

async function selectUnit(node) {
    document.querySelectorAll('.u.sel').forEach((n) => n.classList.remove('sel'));
    document.querySelectorAll('.ucap').forEach((n) => n.remove());
    node.classList.add('sel');
    state.unit = node.dataset.unit;
    state.text = node.textContent;
    state.sceneNo = node.dataset.sceneNo;
    seeRipple.disabled = false;

    const detail = await api(`/api/units/${state.unit}/requirements`);
    const caption = document.createElement('div');
    caption.className = 'ucap';
    caption.textContent =
      `unit ${state.unit.slice(0, 8)} · ${detail.unit.type} · ` +
      `${detail.assertions.length} assertions`;
    node.after(caption);

    crumb.textContent = `Scene ${state.sceneNo} · ${detail.assertions.length} assertions`;
    reqMeta.textContent = `unit ${state.unit.slice(0, 8)}`;
    reqChips.innerHTML = detail.entities
      .map((e) => `<span class="tag ${e.type}">${e.name}</span>`).join('');
    requirements.innerHTML = detail.assertions.length
      ? detail.assertions.map((a) => edgeRow(a)).join('')
      : '<div class="empty">No assertions yet. Build the graph to extract them.</div>';

    const local = await api(`/api/units/${state.unit}/graph`);
    graphMeta.textContent = `${local.nodes.length} nodes · ${local.links.length} edges`;
    document.getElementById('expand').href = `/graph/${state.unit}`;
    if (local.links.length) {
      graph.innerHTML = '<div class="gcanvas mini"></div>';
      // Draw after layout so the canvas has measurable dimensions.
      requestAnimationFrame(() =>
        draw(graph.querySelector('.gcanvas'), local, () => {}));
    } else {
      graph.innerHTML =
        '<div class="empty">Nothing in the graph yet. Build it to see edges.</div>';
    }
}

document.querySelectorAll('.u').forEach((node) => {
  // Focus is selection: clicking into a line to type is the same gesture as
  // choosing it, so the two are not separate interactions.
  node.addEventListener('focus', () => selectUnit(node));
  node.addEventListener('input', () => {
    noteDraft(node);
    state.text = node.textContent;
  });
  node.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      revertLine(node);
      node.blur();
    }
    // A screenplay unit is one block. Enter would split it into markup the
    // parser never produced, so it opens the ripple instead.
    if (event.key === 'Enter') {
      event.preventDefault();
      if (state.drafts.has(node.dataset.unit)) openPreview();
    }
  });
  // Paste as plain text, flattened: pasted markup or line breaks would become
  // part of a unit that the parser never produced that way.
  node.addEventListener('paste', (event) => {
    event.preventDefault();
    const text = (event.clipboardData || window.clipboardData).getData('text');
    document.execCommand('insertText', false, text.replace(/\s*\n\s*/g, ' '));
  });
});

revertAll.addEventListener('click', () => {
  if (!window.confirm(`Discard ${state.drafts.size} unapplied edit(s)?`)) return;
  document.querySelectorAll('.u.edited').forEach(revertLine);
});

// Unapplied edits live only in the page, so leaving loses them.
window.addEventListener('beforeunload', (event) => {
  if (state.drafts.size && !state.applying) {
    event.preventDefault();
    event.returnValue = '';
  }
});

/* Ripple preview */
function openPreview() {
  if (!state.unit) return;
  const node = document.querySelector(`.u[data-unit="${state.unit}"]`);
  preview.classList.remove('hide');
  document.getElementById('pv-accepted').textContent = acceptedTextOf(node);
  document.getElementById('pv-proposed').value = currentTextOf(state.unit);
  document.getElementById('pv-crumb').textContent =
    `unit ${state.unit.slice(0, 8)} · scene ${state.sceneNo} · nothing is applied until you accept`;
  runPreview();
}

async function runPreview() {
  const summary = document.getElementById('pv-summary');
  summary.textContent = 'Computing…';
  document.getElementById('pv-diff').innerHTML = '';
  try {
    const body = await api(`/api/units/${state.unit}/preview`, {
      method: 'POST',
      body: form({ proposed_text: document.getElementById('pv-proposed').value }),
    });
    state.changeSet = body.change_set_id;

    const sev = document.getElementById('pv-sev');
    sev.querySelector('.dot').className = `dot ${body.severity}`;
    sev.querySelector('span:last-child').textContent = `${body.severity} severity`;

    summary.textContent = body.summary;
    document.getElementById('pv-meta').textContent =
      `${body.diff.operations} graph operations · ${body.findings.length} findings · ` +
      (body.model_id || body.summary_source);

    const d = body.diff;
    document.getElementById('pv-diffmeta').textContent =
      `${d.summary.added} added · ${d.summary.removed} removed · ${d.summary.changed} changed`;
    document.getElementById('pv-diff').innerHTML = [
      ...d.added.map((e) => edgeRow(e, 'add', '+')),
      ...d.removed.map((e) => edgeRow(e, 'del', '−')),
      ...d.changed.map((c) => edgeRow(
        { ...c.before, object: `${c.before.object} → ${c.after.object}` }, 'chg', '~')),
    ].join('') || '<div class="empty">No graph change.</div>';

    document.getElementById('pv-findings').innerHTML = body.findings.length
      ? body.findings.map((f) => `
        <div class="finding">
          <div class="ttl"><i class="dot ${f.severity}"></i>${f.title}</div>
          <p>${f.message}</p>
          <div class="acts"><span class="cited-link">${f.cited_units.length} cited units</span>
            <button class="btn sm">Review units</button>
            <button class="btn sm">Dismiss</button></div>
        </div>`).join('')
      : '<div class="empty">No continuity findings.</div>';

    document.getElementById('pv-pipemeta').textContent =
      `${body.pipeline.length} stages · ` +
      `${body.pipeline.reduce((t, s) => t + s.seconds, 0).toFixed(2)}s`;
    document.getElementById('pv-pipeline').innerHTML = body.pipeline
      .map((s) => `<div class="stage"><span class="tick">✓</span>${s.name}
        <span class="ms">${s.seconds.toFixed(2)}s</span></div>`).join('');

    document.getElementById('pv-origin').innerHTML =
      `<mark>${body.origin.text}</mark>`;
    document.getElementById('pv-prov').textContent = [
      body.origin.page ? `source p. ${body.origin.page}` : null,
      body.origin.start !== null && body.origin.start !== undefined
        ? `char ${body.origin.start}–${body.origin.end}` : null,
      body.origin.method,
    ].filter(Boolean).join(' · ');

    document.getElementById('pv-foot').textContent =
      `${d.operations} graph operations · ${body.findings.length} continuity warnings`;
  } catch (error) {
    summary.textContent = error.message;
  }
}

seeRipple.addEventListener('click', openPreview);
document.getElementById('pv-close').addEventListener('click',
  () => preview.classList.add('hide'));
// Keep editing writes the overlay's text back to the line, so the script and
// the preview never disagree about what the proposal is.
document.getElementById('pv-keep').addEventListener('click', () => {
  const node = document.querySelector(`.u[data-unit="${state.unit}"]`);
  if (node) {
    node.textContent = document.getElementById('pv-proposed').value;
    noteDraft(node);
  }
  preview.classList.add('hide');
  if (node) node.focus();
});

document.getElementById('pv-reject').addEventListener('click', async () => {
  if (!state.changeSet) return;
  await api(`/api/changes/${state.changeSet}/reject`, { method: 'POST', body: form({}) });
  preview.classList.add('hide');
  toast('Rejected. Nothing changed.');
});

document.getElementById('pv-accept').addEventListener('click', async () => {
  if (!state.changeSet) return;
  try {
    const body = await api(`/api/changes/${state.changeSet}/accept`, { method: 'POST' });
    state.drafts.delete(state.unit);
    state.applying = true;
    toast(`Accepted · ${body.operations_applied} operations · now v${body.script_version}`);
    setTimeout(() => window.location.reload(), 900);
  } catch (error) {
    toast(error.message, true);
  }
});

/* Browser-driven extraction: one scene per request, each committed on its own,
   so a reload resumes rather than restarting. */
const extract = document.getElementById('extract');
if (extract) {
  extract.addEventListener('click', async () => {
    const scriptId = window.location.pathname.split('/').pop();
    const panel = document.getElementById('run');
    const bar = document.getElementById('run-bar');
    const count = document.getElementById('run-count');
    const label = document.getElementById('run-label');
    panel.style.display = 'block';
    extract.disabled = true;
    try {
      const started = await api(`/api/scripts/${scriptId}/extract`, { method: 'POST' });
      for (;;) {
        const step = await api(`/api/extract/${started.run_id}/next`, { method: 'POST' });
        const p = step.progress;
        const done = p.completed + p.failed;
        bar.style.width = `${Math.round((done / Math.max(p.total, 1)) * 100)}%`;
        count.textContent = `${done} of ${p.total} · ${p.failed} failed`;
        if (step.done || p.pending === 0) break;
      }
      label.textContent = 'Extraction finished';
      setTimeout(() => window.location.reload(), 900);
    } catch (error) {
      label.textContent = 'Extraction stopped';
      toast(error.message, true);
      extract.disabled = false;
    }
  });
}
