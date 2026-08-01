let selectedUnit = null;
let selectedText = '';

const requirements = document.getElementById('requirements');
const graph = document.getElementById('graph');
const proposed = document.getElementById('proposed');
const seeRipple = document.getElementById('see-ripple');
const ripple = document.getElementById('ripple');

function edgeRow(edge, cls) {
  return `<div class="edge ${cls || ''}">
    <span>${edge.subject}</span>
    <span class="p">${edge.predicate}</span>
    <span>${edge.object}</span>
    <span class="c num">${edge.confidence}</span>
  </div>`;
}

document.querySelectorAll('.unit').forEach((node) => {
  node.addEventListener('click', async () => {
    document.querySelectorAll('.unit.sel').forEach((n) => n.classList.remove('sel'));
    node.classList.add('sel');
    selectedUnit = node.dataset.unit;
    selectedText = node.textContent;
    proposed.value = selectedText;
    seeRipple.disabled = false;
    ripple.innerHTML = '';

    requirements.innerHTML = '<p class="tiny muted">Loading…</p>';
    graph.innerHTML = '<p class="tiny muted">Loading…</p>';

    const detail = await api(`/api/units/${selectedUnit}/requirements`);
    if (!detail.assertions.length) {
      requirements.innerHTML =
        `<p class="tiny muted" style="margin:0">No assertions yet for this line.
         Build the graph to extract them.</p>`;
    } else {
      requirements.innerHTML = detail.assertions.map((a) => edgeRow(a)).join('');
    }

    const local = await api(`/api/units/${selectedUnit}/graph`);
    if (!local.nodes.length) {
      graph.innerHTML = '<p class="tiny muted" style="margin:0">Nothing in the graph yet.</p>';
    } else {
      const nodes = local.nodes
        .map((n) => `<span class="tag ${n.entity_type || ''}">${n.label}</span>`)
        .join(' ');
      const links = local.links
        .map((l) => `<div class="tiny muted mono">${l.predicate}</div>`).join('');
      graph.innerHTML =
        `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:9px">${nodes}</div>
         <div class="tiny muted">${local.links.length} edges</div>${links ? '' : ''}`;
    }
  });
});

seeRipple.addEventListener('click', async () => {
  if (!selectedUnit) return;
  ripple.innerHTML = '<p class="tiny muted">Computing the diff…</p>';
  try {
    const result = await api(`/api/units/${selectedUnit}/preview`, {
      method: 'POST', body: form({ proposed_text: proposed.value }),
    });
    const d = result.diff;
    const rows = [
      ...d.removed.map((e) => edgeRow(e, 'diff-del')),
      ...d.changed.map((c) =>
        edgeRow({ ...c.before, object: `${c.before.object} → ${c.after.object}` }, 'diff-chg')),
      ...d.added.map((e) => edgeRow(e, 'diff-add')),
    ].join('');
    const findings = result.findings.map((f) =>
      `<div class="finding">
         <strong>${f.severity} severity</strong>
         <div class="tiny" style="margin-top:4px">${f.message}</div>
         <div class="tiny muted" style="margin-top:4px">
           ${f.cited_units.length} cited units</div>
       </div>`).join('');

    ripple.innerHTML =
      `<div class="tiny muted" style="margin-bottom:8px">
         ${d.summary.added} added · ${d.summary.removed} removed ·
         ${d.summary.changed} changed · ${d.operations} graph operations
       </div>
       ${rows || '<p class="tiny muted">No graph change.</p>'}
       <div style="margin-top:12px">${findings}</div>
       <p class="tiny muted" style="margin-top:10px">
         The written explanation needs the synthesizer, which is not wired yet.
         The diff and the findings above are computed in code, with no model.
       </p>`;
  } catch (error) {
    ripple.innerHTML = `<span class="tag bad">failed</span>
      <span class="tiny muted"> ${error.message}</span>`;
  }
});

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
      const runId = started.run_id;

      // The browser drives the loop: one scene per request, each committed
      // independently, so a reload resumes rather than restarting.
      for (;;) {
        const step = await api(`/api/extract/${runId}/next`, { method: 'POST' });
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
