const state = { unit: null, text: '', sceneNo: null, changeSet: null };

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

document.querySelectorAll('.u').forEach((node) => {
  node.addEventListener('click', async () => {
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
    graph.innerHTML = local.nodes.length
      ? `<div class="chips">${local.nodes
          .map((n) => `<span class="tag ${n.entity_type || ''}">${n.label}</span>`)
          .join('')}</div>`
      : '<div class="empty">Nothing in the graph yet.</div>';
  });
});

/* Ripple preview */
function openPreview() {
  preview.classList.remove('hide');
  document.getElementById('pv-accepted').textContent = state.text;
  document.getElementById('pv-proposed').value = state.text;
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
document.getElementById('pv-keep').addEventListener('click', runPreview);

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
