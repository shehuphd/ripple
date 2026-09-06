/* Drafts are edits the user has typed but not accepted. They live only in the
   page: the accepted script is unchanged until a ripple is accepted, which is
   the guarantee the whole product rests on. */
const state = {
  unit: null, text: '', sceneNo: null, changeSet: null,
  drafts: new Map(), applying: false,
};

const draftCount = document.getElementById('draft-count');
const revertAll = document.getElementById('revert-all');

// A person is looking at the script, which is what "Recently opened" means.
// A POST from the page rather than a side effect of the GET, so a prefetch
// or a crawler cannot reorder the list. Failure only loses the ordering.
api(`/api/scripts/${window.location.pathname.split('/').pop()}/opened`, {
  method: 'POST',
}).catch(() => {});

const acceptedTextOf = (node) => node.dataset.accepted;

// contentEditable inserts non-breaking spaces where the user typed plain
// ones, so an edit typed and then retyped back would compare unequal and
// stay marked as a draft forever. All draft text flows through this.
const flatten = (text) => text.replace(/ /g, ' ');

function refreshDraftIndicator() {
  const count = state.drafts.size;
  draftCount.classList.toggle('hide', count === 0);
  draftCount.querySelector('span').textContent =
    `${count} line${count === 1 ? '' : 's'} edited`;
  revertAll.disabled = count === 0;
  revertAll.dataset.tip = count === 0
    ? 'No edits to revert — edit a line first'
    : 'Discard every unapplied edit';
  // The preview reads drafts, so the button follows their existence in both
  // directions: enabling without a draft offers a preview of nothing. A
  // ripple is also meaningless before a graph exists, so an unbuilt script
  // keeps the button off and leaves Build graph as the next step.
  seeRipple.disabled = count === 0 || seeRipple.dataset.graphReady !== 'true';
}

function noteDraft(node) {
  const unitId = node.dataset.unit;
  const wasDraft = state.drafts.has(unitId);
  if (flatten(node.textContent) === acceptedTextOf(node)) {
    state.drafts.delete(unitId);
    node.classList.remove('edited');
    // Trace the transition, not every keystroke: the decision is a line
    // becoming a draft or ceasing to be one.
    if (wasDraft) ripple.trace('draft.cleared', { unit: unitId });
  } else {
    state.drafts.set(unitId, flatten(node.textContent));
    node.classList.add('edited');
    if (!wasDraft) ripple.trace('draft.started', { unit: unitId });
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
    <span>${esc(edge.subject)}</span>
    <span class="p">${esc(edge.predicate).replace(/_/g, ' ')}</span>
    <span>${esc(edge.object)}</span>
    <span class="c" tabindex="0"
      data-tip="Confidence: the model's certainty this fact is stated, from 0 to 1"
      aria-label="Confidence ${esc(edge.confidence ?? '')}, from 0 to 1"
      >${esc(edge.confidence ?? '')}</span></div>`;
}

/* One judged assertion: the edge plus the verdict the model returned, after
   code verification. Mirrors edgeRow but ends in the verdict tag, not the
   confidence. */
function verdictRow(edge, cls, sign, tag) {
  return `<div class="edge ${cls}">
    <span class="sign">${sign}</span>
    <span>${esc(edge.subject)}</span>
    <span class="p">${esc(edge.predicate).replace(/_/g, ' ')}</span>
    <span>${esc(edge.object)}</span>
    <span class="verdict ${cls}">${tag}</span></div>`;
}

/* Open one run's full trace in the TraceAct viewer, map view. Shared by the
   failure banner and the judgement card's Open full trace button. */
async function openTraceViewer(traceId) {
  if (!traceId) return;
  try {
    const body = await api(`/api/traces/${traceId}/viewer`, { method: 'POST' });
    window.open(body.url, '_blank', 'noopener');
    ripple.trace('trace.viewer_opened', { trace: traceId });
  } catch (error) {
    toast(error.message, true);
  }
}

/* The inline "how this was computed" card: the judge's verdicts after code
   verification, verdict by verdict, plus what verification dropped. Beat 5 of
   the demo, and the honest answer to "why did the graph change like that". */
function renderJudgement(body) {
  const box = document.getElementById('pv-judgement');
  const meta = document.getElementById('pv-judgemeta');
  const traceBtn = document.getElementById('pv-judge-trace');
  const j = body.judgement;
  const d = body.diff;

  meta.textContent = body.cached
    ? 'cached, no model call'
    : (j ? (body.model_id || 'judged') : 'deterministic');

  const rows = [
    ...d.changed.map((c) => verdictRow(
      { ...c.before, object: `${c.before.object} → ${c.after.object}` },
      'chg', '~', 'changed')),
    ...d.removed.map((e) => verdictRow(e, 'del', '−', 'removed')),
    ...d.added.map((e) => verdictRow(e, 'add', '+', 'new')),
  ];

  const parts = [];
  if (j) {
    const held = Math.max(0, j.assertion_verdicts - d.changed.length - d.removed.length);
    parts.push(
      `<div class="judge-tally">${j.assertion_verdicts} assertion verdict` +
      `${j.assertion_verdicts === 1 ? '' : 's'} · ${d.changed.length} changed · ` +
      `${d.removed.length} removed · ${held} held · ${d.added.length} new</div>`);
  }
  parts.push(rows.length
    ? `<div class="judge-verdicts">${rows.join('')}</div>`
    : '<div class="empty">Every judged assertion held unchanged.</div>');

  if (j && j.rejected && j.rejected.length) {
    parts.push(
      `<div class="judge-drop"><div class="drop-hd">Verification dropped ` +
      `${j.rejected.length}</div>` +
      j.rejected.map((r) => `<div class="drop-row">${esc(r[1])}</div>`).join('') +
      '</div>');
  } else if (j) {
    parts.push('<div class="judge-drop ok">Verification dropped nothing.</div>');
  }

  box.innerHTML = parts.join('');
  traceBtn.dataset.trace = body.trace_id || '';
  traceBtn.classList.toggle('hide', !body.trace_id);
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
  row.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      row.click();
    }
  });
});

// Focusing line B while line A's requests are in flight must not let A's
// late replies overwrite B's panes. Each selection takes a ticket; replies
// for an outdated ticket are dropped.
let selectionTicket = 0;

async function selectUnit(node) {
    document.querySelectorAll('.u.sel').forEach((n) => n.classList.remove('sel'));
    document.querySelectorAll('.ucap').forEach((n) => n.remove());
    node.classList.add('sel');
    state.unit = node.dataset.unit;
    state.text = flatten(node.textContent);
    state.sceneNo = node.dataset.sceneNo;
    const ticket = ++selectionTicket;

    try {
      const detail = await api(`/api/units/${state.unit}/requirements`);
      if (ticket !== selectionTicket) return;
      const caption = document.createElement('div');
      caption.className = 'ucap';
      caption.textContent =
        `unit ${state.unit.slice(0, 8)} · ${detail.unit.type} · ` +
        `${detail.assertions.length} assertions`;
      node.after(caption);

      crumb.textContent =
        `Scene ${state.sceneNo} · ${detail.assertions.length} assertions`;
      reqMeta.textContent = `unit ${state.unit.slice(0, 8)}`;
      reqChips.innerHTML = detail.entities
        .map((e) => `<span class="tag ${esc(e.type)}">${esc(e.name)}</span>`).join('');
      requirements.innerHTML = detail.assertions.length
        ? detail.assertions.map((a) => edgeRow(a)).join('')
        : '<div class="empty">No assertions yet. Build the graph to extract them.</div>';

      const local = await api(`/api/units/${state.unit}/graph`);
      if (ticket !== selectionTicket) return;
      graphMeta.textContent =
        `${local.nodes.length} nodes · ${local.links.length} edges`;
      // Expand only means something when this line has edges to open. With
      // none, it is a disabled control that says why, not a link to an empty
      // canvas.
      const expand = document.getElementById('expand');
      if (local.links.length) {
        expand.href = `/graph/${state.unit}`;
        expand.removeAttribute('aria-disabled');
        expand.removeAttribute('tabindex');
        expand.dataset.tip = "Open this line's neighbourhood in the full graph";
        graph.innerHTML = '<div class="gcanvas mini"></div>';
        // Draw after layout so the canvas has measurable dimensions.
        requestAnimationFrame(() =>
          draw(graph.querySelector('.gcanvas'), local, () => {}));
      } else {
        expand.removeAttribute('href');
        expand.setAttribute('aria-disabled', 'true');
        expand.tabIndex = -1;
        expand.dataset.tip = 'This line has no graph edges to expand yet';
        graph.innerHTML =
          '<div class="empty">Nothing in the graph yet. Build it to see edges.</div>';
      }
    } catch (error) {
      if (ticket !== selectionTicket) return;
      ripple.trace('unit.select_failed', { unit: state.unit, error: error.message });
      requirements.innerHTML =
        `<div class="empty">${esc(error.message)}</div>`;
    }
}

/* Revision marks render inside the line, but editing operates on plain
   text: on focus the marks flatten away, with the caret kept where the
   click put it. They return on the next page load for unedited lines. */
function flattenMarks(node) {
  if (!node.querySelector('mark.rev')) return;
  const selection = window.getSelection();
  let offset = 0;
  if (selection.rangeCount && node.contains(selection.anchorNode)) {
    const range = selection.getRangeAt(0);
    const before = range.cloneRange();
    before.selectNodeContents(node);
    before.setEnd(range.startContainer, range.startOffset);
    offset = before.toString().length;
  }
  node.textContent = node.textContent;
  const caret = document.createRange();
  caret.setStart(
    node.firstChild || node,
    Math.min(offset, node.textContent.length));
  caret.collapse(true);
  selection.removeAllRanges();
  selection.addRange(caret);
}

document.querySelectorAll('.u').forEach((node) => {
  // Focus is selection: clicking into a line to type is the same gesture as
  // choosing it, so the two are not separate interactions.
  node.addEventListener('focus', () => {
    flattenMarks(node);
    selectUnit(node);
  });
  node.addEventListener('input', () => {
    noteDraft(node);
    state.text = flatten(node.textContent);
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

revertAll.addEventListener('click', async () => {
  const count = state.drafts.size;
  if (!(await confirmDialog(
    `Discard ${count} unapplied edit(s)?`, 'Discard'))) return;
  ripple.trace('drafts.revert_all', { count });
  document.querySelectorAll('.u.edited').forEach(revertLine);
});

// Unapplied edits live only in the page, so leaving loses them.
window.addEventListener('beforeunload', (event) => {
  if (state.drafts.size && !state.applying) {
    event.preventDefault();
    event.returnValue = '';
  }
});

/* Ripple preview. esc() comes from app.js. */

// Every draft rides along: the judge reads whole scenes, so a preview that
// silently dropped the other edited lines would judge a scene that nobody
// proposed.
function draftEdits() {
  return Array.from(state.drafts, ([unitId, text]) => ({
    unit_id: unitId, proposed_text: text,
  }));
}

function segmentHtml(segments) {
  return segments.map((s) => {
    if (s.op === 'del') return `<del>${esc(s.text)}</del>`;
    if (s.op === 'ins') return `<mark>${esc(s.text)}</mark>`;
    return esc(s.text);
  }).join(' ');
}

function openPreview() {
  if (!state.drafts.size) {
    toast('Edit a line first. The preview reads your drafts.');
    return;
  }
  preview.classList.remove('hide');
  const count = state.drafts.size;
  document.getElementById('pv-crumb').textContent =
    `${count} edited line${count === 1 ? '' : 's'}`;
  runPreview();
}

async function runPreview() {
  // A failed run must not leave the previous proposal acceptable.
  state.changeSet = null;
  const acceptBtn = document.getElementById('pv-accept');
  const rejectBtn = document.getElementById('pv-reject');
  acceptBtn.disabled = true;
  rejectBtn.disabled = true;
  const summary = document.getElementById('pv-summary');
  summary.textContent = 'Computing…';
  document.getElementById('pv-explain').classList.add('hide');
  document.getElementById('pv-diff').innerHTML = '';
  document.getElementById('pv-edits').innerHTML =
    '<div class="empty">Computing…</div>';
  document.getElementById('pv-judgement').innerHTML =
    '<div class="empty">Computing…</div>';
  document.getElementById('pv-judge-trace').classList.add('hide');
  document.getElementById('pv-judgemeta').textContent = '';
  document.getElementById('pv-warning').classList.add('hide');
  setWarningTrace(null);
  showWait();
  const scriptId = window.location.pathname.split('/').pop();
  try {
    const body = await api(`/api/scripts/${scriptId}/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ edits: draftEdits() }),
    });
    state.changeSet = body.change_set_id;
    acceptBtn.disabled = false;
    rejectBtn.disabled = false;

    // The continuity pass is advisory: when its call failed, the preview
    // stands on the deterministic findings and says so.
    if (body.continuity_error) {
      document.getElementById('pv-warning-text').textContent =
        body.continuity_error;
      document.getElementById('pv-warning').classList.remove('hide');
    }

    const sev = document.getElementById('pv-sev');
    sev.querySelector('.dot').className = `dot ${body.severity}`;
    sev.querySelector('span:last-child').textContent = `${body.severity} severity`;

    document.getElementById('pv-edits').innerHTML = body.edits.map((e) => `
      <div class="pv-edit">
        <div class="tiny muted">Scene ${e.scene_number} · unit ${e.unit_id.slice(0, 8)}</div>
        <p class="wdiff">${segmentHtml(e.segments)}</p>
      </div>`).join('');
    document.getElementById('pv-editmeta').textContent =
      `${body.edits.length} line${body.edits.length === 1 ? '' : 's'}` +
      (body.cached ? ' · cached, no model call' : '');

    document.getElementById('pv-attrs').innerHTML = body.attribute_changes.length
      ? body.attribute_changes.map((a) => `
        <div class="edge chg"><span class="sign">~</span>
          <span>${esc(a.entity)}</span>
          <span class="p">${esc(a.key)}</span>
          <span>${esc(a.before ?? '—')} → ${esc(a.after ?? '—')}</span>
          <span class="c" tabindex="0"
            data-tip="Confidence: the model's certainty this fact is stated, from 0 to 1"
            aria-label="Confidence ${a.confidence}, from 0 to 1"
            >${a.confidence}</span></div>`).join('')
      : '<div class="empty">No attribute changes.</div>';

    ripple.trace('preview.result', {
      edits: body.edits.length,
      changeSet: body.change_set_id,
      ops: body.diff.operations,
      attributes: body.attribute_changes.length,
      findings: body.findings.length,
      severity: body.severity,
      cached: body.cached,
    });

    summary.textContent = body.summary;
    document.getElementById('pv-meta').textContent =
      `${body.diff.operations} graph operations · ${body.findings.length} findings · ` +
      (body.cached ? 'cached' : body.model_id || body.summary_source);
    // The summary is assembled from the diff at no cost. Prose from the
    // model is a click, offered until the stored report carries some.
    document.getElementById('pv-explain').classList.toggle(
      'hide', body.summary_source === 'model');

    const d = body.diff;
    document.getElementById('pv-diffmeta').textContent =
      `${d.summary.added} added · ${d.summary.removed} removed · ${d.summary.changed} changed`;
    document.getElementById('pv-diff').innerHTML = [
      ...d.added.map((e) => edgeRow(e, 'add', '+')),
      ...d.removed.map((e) => edgeRow(e, 'del', '−')),
      ...d.changed.map((c) => edgeRow(
        { ...c.before, object: `${c.before.object} → ${c.after.object}` }, 'chg', '~')),
    ].join('') || '<div class="empty">No graph change.</div>';

    const findingsPane = document.getElementById('pv-findings');
    findingsPane.innerHTML = body.findings.length
      ? body.findings.map((f, index) => `
        <div class="finding${f.status === 'dismissed' ? ' dismissed' : ''}">
          <div class="ttl"><i class="dot ${esc(f.severity)}"></i>${esc(f.title)}</div>
          <p>${esc(f.message)}</p>
          <div class="acts"><span class="cited-link">${f.cited_units.length} cited units</span>
            <button class="btn sm" data-review="${index}"
              ${f.cited_units.length ? '' : 'disabled'}>Review units</button>
            <button class="btn sm" data-dismiss="${esc(f.id || '')}"
              ${f.id && f.status === 'open' ? '' : 'disabled'}>Dismiss</button></div>
        </div>`).join('')
      : '<div class="empty">No continuity findings.</div>';
    findingsPane.querySelectorAll('[data-review]').forEach((button) => {
      button.addEventListener('click', () => {
        preview.classList.add('hide');
        reviewUnits(body.findings[Number(button.dataset.review)].cited_units);
      });
    });
    findingsPane.querySelectorAll('[data-dismiss]').forEach((button) => {
      button.addEventListener('click', async () => {
        try {
          await api(`/api/findings/${button.dataset.dismiss}/dismiss`,
            { method: 'POST', body: form({}) });
        } catch (error) {
          toast(error.message, true);
          return;
        }
        button.disabled = true;
        button.closest('.finding').classList.add('dismissed');
        ripple.trace('finding.dismissed', { finding: button.dataset.dismiss });
      });
    });

    document.getElementById('pv-pipemeta').textContent =
      `${body.pipeline.length} stages · ` +
      `${body.pipeline.reduce((t, s) => t + s.seconds, 0).toFixed(2)}s`;
    document.getElementById('pv-pipeline').innerHTML = body.pipeline
      .map((s) => `<div class="stage"><span class="tick">✓</span>${s.name}
        <span class="ms">${s.seconds.toFixed(2)}s</span></div>`).join('');

    renderJudgement(body);

    document.getElementById('pv-origin').innerHTML =
      `<mark>${esc(body.origin.text)}</mark>`;
    document.getElementById('pv-prov').textContent = [
      body.origin.page ? `source p. ${body.origin.page}` : null,
      body.origin.start !== null && body.origin.start !== undefined
        ? `char ${body.origin.start}–${body.origin.end}` : null,
      body.origin.method,
    ].filter(Boolean).join(' · ');

    hideWait(true);
    document.getElementById('pv-foot').textContent =
      `${d.operations} graph operations · ${body.findings.length} continuity warnings`
      + spendLabel(body.spend);
  } catch (error) {
    hideWait(false);
    ripple.trace('preview.failed', {
      edits: state.drafts.size, error: error.message,
    });
    summary.textContent = 'The preview did not run. Nothing was recorded.';
    document.getElementById('pv-warning-text').textContent = error.message;
    setWarningTrace(error.traceId || null);
    document.getElementById('pv-warning').classList.remove('hide');
    document.getElementById('pv-edits').innerHTML =
      '<div class="empty">The preview did not run. Nothing was recorded.</div>';
    document.getElementById('pv-judgement').innerHTML =
      '<div class="empty">The preview did not run. Nothing was recorded.</div>';
  }
}

/* The waiting interstitial: water and a spreading ripple while the
   judgement runs, faded out into the results when they arrive. */
function showWait() {
  const wait = document.getElementById('pv-wait');
  if (!wait) return;
  wait.classList.remove('fading');
  wait.classList.remove('hide');
}
function hideWait(fade) {
  const wait = document.getElementById('pv-wait');
  if (!wait) return;
  if (!fade) {
    wait.classList.add('hide');
    return;
  }
  wait.classList.add('fading');
  setTimeout(() => wait.classList.add('hide'), 520);
}

/* The warning banner's "Open trace" button: shown only when the failure
   response named the TraceAct trace that recorded the run. Clicking asks
   the server to start (or reuse) the local viewer and opens the returned
   deep link: the trace's map, pre-filtered and selected. */
function setWarningTrace(traceId) {
  const button = document.getElementById('pv-warning-trace');
  if (!button) return;
  button.dataset.trace = traceId || '';
  button.classList.toggle('hide', !traceId);
}

document.getElementById('pv-warning-trace').addEventListener('click',
  (event) => openTraceViewer(event.currentTarget.dataset.trace));
document.getElementById('pv-judge-trace').addEventListener('click',
  (event) => openTraceViewer(event.currentTarget.dataset.trace));

/* Centre a node in the reader pane. Scrolls only that pane, computed
   directly: scrollIntoView walks every scrollable ancestor and, at load
   time, overshoots and drags the layout sideways. */
function scrollPaneTo(node, behavior) {
  let pane = null;
  for (let e = node.parentElement; e; e = e.parentElement) {
    const style = getComputedStyle(e);
    if (
      (style.overflowY === 'auto' || style.overflowY === 'scroll')
      && e.scrollHeight > e.clientHeight
    ) { pane = e; break; }
  }
  if (!pane) {
    node.scrollIntoView({ block: 'center', behavior });
    return;
  }
  const target = node.getBoundingClientRect();
  const box = pane.getBoundingClientRect();
  pane.scrollTo({
    top: pane.scrollTop + (target.top - box.top)
      - (pane.clientHeight - target.height) / 2,
    behavior,
  });
}

/* Scroll to the units a finding cites and mark them for a moment. */
function reviewUnits(unitIds, behavior = 'smooth') {
  const nodes = unitIds
    .map((id) => document.querySelector(`.u[data-unit="${id}"]`))
    .filter(Boolean);
  if (!nodes.length) {
    toast('The cited units are not on this page.');
    return;
  }
  scrollPaneTo(nodes[0], behavior);
  ripple.trace('units.reviewed', {
    cited: unitIds.length,
    found: nodes.length,
    behavior,
    top: Math.round(nodes[0].getBoundingClientRect().top),
  });
  nodes.forEach((node) => {
    node.classList.add('cited');
    setTimeout(() => node.classList.remove('cited'), 4000);
  });
}

/* The findings page's Review deep-links here with ?finding=<id>: fetch the
   finding's cited lines, scroll to them, and mark them for a moment. */
const pendingFinding = new URLSearchParams(window.location.search).get('finding');
if (pendingFinding) {
  (async () => {
    try {
      const detail = await api(`/api/findings/${pendingFinding}`);
      // After the webfont applies, as an instant jump: the screenplay is
      // over ten times taller in the fallback font, so any position
      // computed before fonts settle scrolls to the wrong place.
      const fonts = document.fonts ? document.fonts.ready : Promise.resolve();
      const after = (act) => {
        const go = () => fonts.then(() => requestAnimationFrame(act));
        if (document.readyState === 'complete') go();
        else window.addEventListener('load', go, { once: true });
      };
      if (detail.cited_units.length) {
        after(() => reviewUnits(detail.cited_units, 'auto'));
      } else if (detail.cited_scenes.length) {
        // Evidence citing only scene headings: show the scene itself.
        after(() => {
          const scene = document.querySelector(
            `[data-scene-body="${detail.cited_scenes[0]}"]`);
          if (scene) scrollPaneTo(scene, 'auto');
          else toast('The cited scene is not on this page.');
        });
      } else {
        toast('This finding cites no lines in this script.');
      }
      ripple.trace('finding.reviewed', {
        finding: pendingFinding, units: detail.cited_units.length,
      });
    } catch (error) {
      toast(error.message, true);
    }
    window.history.replaceState(null, '', window.location.pathname);
  })();
}

seeRipple.addEventListener('click', openPreview);
document.getElementById('pv-close').addEventListener('click',
  () => preview.classList.add('hide'));
// The drafts stay on the lines themselves, so keep-editing only returns focus.
document.getElementById('pv-keep').addEventListener('click', () => {
  preview.classList.add('hide');
  const node = document.querySelector('.u.edited');
  if (node) node.focus();
});

document.getElementById('pv-reject').addEventListener('click', async () => {
  if (!state.changeSet) return;
  const rejected = state.changeSet;
  try {
    await api(`/api/changes/${rejected}/reject`, { method: 'POST', body: form({}) });
  } catch (error) {
    ripple.trace('ripple.reject_failed', { changeSet: rejected, error: error.message });
    toast(error.message, true);
    return;
  }
  // The proposal is now rejected on the server, so it must stop being
  // acceptable in the page: a later Accept against it would 400.
  state.changeSet = null;
  document.getElementById('pv-accept').disabled = true;
  document.getElementById('pv-reject').disabled = true;
  ripple.trace('ripple.rejected', { changeSet: rejected });
  preview.classList.add('hide');
  toast('Rejected. Nothing changed.');
});

document.getElementById('pv-explain').addEventListener('click', async () => {
  if (!state.changeSet) return;
  const button = document.getElementById('pv-explain');
  const summary = document.getElementById('pv-summary');
  const explained = state.changeSet;
  button.disabled = true;
  button.textContent = 'Writing…';
  try {
    const body = await api(`/api/changes/${explained}/explain`, { method: 'POST' });
    ripple.trace('ripple.explained', {
      changeSet: explained, source: body.source, error: body.error,
    });
    if (body.source !== 'model') {
      toast(body.error || 'The model wrote nothing; the summary stands.', true);
      return;
    }
    summary.textContent = body.summary;
    document.getElementById('pv-meta').textContent += ` · explained by ${body.model_id}`;
    button.classList.add('hide');
  } catch (error) {
    ripple.trace('ripple.explain_failed', {
      changeSet: explained, error: error.message,
    });
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = 'Write the explanation';
  }
});

document.getElementById('pv-accept').addEventListener('click', async () => {
  if (!state.changeSet) return;
  const accepted = state.changeSet;
  const editedUnits = [...state.drafts.keys()];
  try {
    const body = await api(`/api/changes/${accepted}/accept`, { method: 'POST' });
    ripple.trace('ripple.accepted', {
      changeSet: accepted,
      operations: body.operations_applied,
      scriptVersion: body.script_version,
    });
    // Undo is latest-only, so the page after the reload only needs one unit
    // of the change it may undo.
    try {
      if (editedUnits.length) {
        sessionStorage.setItem(undoStashKey(), editedUnits[0]);
      }
    } catch (error) { /* no storage, the Undo button stays disabled */ }
    state.changeSet = null;
    state.drafts.clear();
    state.applying = true;
    toast(`Accepted · ${body.operations_applied} operations · now v${body.script_version}`);
    setTimeout(() => window.location.reload(), 900);
  } catch (error) {
    ripple.trace('ripple.accept_failed', {
      changeSet: accepted, error: error.message,
    });
    toast(error.message, true);
  }
});

/* Undo of the latest accepted change. The server allows one level of undo,
   so the button only knows about the change this page accepted: a unit id
   stashed across the accept's reload. */
function undoStashKey() {
  return `ripple.undo.${window.location.pathname.split('/').pop()}`;
}

const undoLast = document.getElementById('undo-last');
if (undoLast) {
  let stashedUnit = null;
  try {
    stashedUnit = sessionStorage.getItem(undoStashKey());
  } catch (error) { /* no storage */ }
  undoLast.disabled = !stashedUnit;
  undoLast.dataset.tip = stashedUnit
    ? 'Undo the last change you accepted'
    : 'Nothing to undo yet — accept a ripple first';
  undoLast.addEventListener('click', async () => {
    if (!stashedUnit) return;
    if (!(await confirmDialog(
      'Undo the last accepted change? The previous text and graph state '
      + 'come back.', 'Undo'))) return;
    try {
      await api(`/api/units/${stashedUnit}/undo`, { method: 'POST', body: form({}) });
    } catch (error) {
      ripple.trace('ripple.undo_failed', { unit: stashedUnit, error: error.message });
      toast(error.message, true);
      // Whatever refused it (a later change, a vanished unit) will refuse
      // it again; the stash is spent.
      try { sessionStorage.removeItem(undoStashKey()); } catch (removeError) { /* gone */ }
      undoLast.disabled = true;
      return;
    }
    ripple.trace('ripple.undone', { unit: stashedUnit });
    try { sessionStorage.removeItem(undoStashKey()); } catch (removeError) { /* gone */ }
    toast('Undone. The previous text is back.');
    setTimeout(() => window.location.reload(), 900);
  });
}

/* " · 12,345 tokens · $0.31" from a progress or preview spend payload;
   empty when nothing was spent and cost-less when the model has no rate. */
function spendLabel(spend) {
  if (!spend || !spend.tokens) return '';
  let label = ` · ${spend.tokens.toLocaleString()} tokens`;
  if (spend.cost) label += ` · ${spend.cost}`;
  return label;
}

/* Browser-driven extraction: one scene per request, each committed on its own,
   so a reload resumes rather than restarting. Shared by the Build graph
   button and the extraction of a freshly inserted scene. */
async function driveRun(runId) {
  const panel = document.getElementById('run');
  const bar = document.getElementById('run-bar');
  const count = document.getElementById('run-count');
  const cancel = document.getElementById('run-cancel');
  panel.style.display = 'block';
  // Cancelling asks twice: the first press arms the button, the second stops
  // the run once the scene in flight has been saved.
  if (cancel) {
    cancel.style.display = '';
    cancel.disabled = false;
    cancel.textContent = 'Cancel';
    cancel.onclick = async () => {
      if (cancel.textContent === 'Cancel') {
        cancel.textContent = 'Confirm';
        return;
      }
      cancel.disabled = true;
      cancel.textContent = 'Cancelling…';
      try {
        await api(`/api/extract/${runId}/cancel`, { method: 'POST' });
        ripple.trace('extract.cancelled', { run: runId });
      } catch (error) {
        toast(error.message, true);
      }
    };
  }
  // The server drains the run, so this reports rather than drives it: closing
  // the tab no longer stops the build.
  let progress = await api(`/api/extract/${runId}/background`, {
    method: 'POST',
  });
  for (;;) {
    const done = progress.completed + progress.failed;
    bar.style.width =
      `${Math.round((done / Math.max(progress.total, 1)) * 100)}%`;
    count.textContent =
      `${done} of ${progress.total} · ${progress.failed} failed`
      + (progress.assertions
        ? ` · ${progress.assertions.toLocaleString()} assertions`
        : '')
      + spendLabel(progress);
    if (progress.status === 'cancelled') break;
    if (progress.pending === 0 && !progress.working) break;
    await new Promise((resume) => { setTimeout(resume, 900); });
    progress = await api(`/api/extract/${runId}/progress`);
  }
  if (cancel) cancel.style.display = 'none';
  return progress;
}

/* A draft link hands its extraction run over in the URL, so the reader
   finishes the changed scenes the moment it opens. */
const pendingRun = new URLSearchParams(window.location.search).get('run');
if (pendingRun) {
  (async () => {
    const scriptId = window.location.pathname.split('/').pop();
    document.getElementById('run-label').textContent =
      'Extracting the changed scenes';
    try {
      const outcome = await driveRun(pendingRun);
      if (outcome && outcome.status === 'cancelled') {
        document.getElementById('run-label').textContent =
          'Extraction cancelled';
        toast('Extraction cancelled before the draft report could be '
          + 'built.');
        window.history.replaceState(null, '', window.location.pathname);
        setTimeout(() => window.location.reload(), 1600);
        return;
      }
      document.getElementById('run-label').textContent =
        'Comparing the drafts';
      const report = await api(`/api/scripts/${scriptId}/draft-report`, {
        method: 'POST',
      });
      ripple.trace('draft.report', {
        severity: report.severity,
        conflicts: report.conflicts,
      });
      toast(`Draft report ready (${report.severity}); it is on the `
        + 'Reports page, its findings on the Findings page.',
      report.severity === 'high');
    } catch (error) {
      toast(error.message, true);
    }
    window.history.replaceState(null, '', window.location.pathname);
    setTimeout(() => window.location.reload(), 1600);
  })();
}

/* A link with nothing to extract built its report server-side. */
if (new URLSearchParams(window.location.search).get('report') === 'ready') {
  toast('Draft linked; the report is on the Reports page.');
  window.history.replaceState(null, '', window.location.pathname);
}

const extract = document.getElementById('extract');
if (extract) {
  extract.addEventListener('click', async () => {
    const scriptId = window.location.pathname.split('/').pop();
    const label = document.getElementById('run-label');
    // A rebuild reads every scene again rather than replaying the cache, so
    // it bills where an ordinary build of an unchanged script costs nothing.
    // That deserves a question before it runs.
    const force = extract.dataset.force === '1';
    if (force) {
      const scenes = extract.dataset.scenes || 'all';
      const ok = await confirmDialog(
        `Rebuild the graph? This reads all ${scenes} scenes again with the `
        + 'model and bills for each, rather than replaying the stored '
        + 'answers. Facts already in the graph stay; a rebuild adds and '
        + 'refreshes, it does not clear them.',
        'Rebuild',
      );
      if (!ok) return;
    }
    extract.disabled = true;
    try {
      const started = await api(`/api/scripts/${scriptId}/extract`, {
        method: 'POST', body: form(force ? { force: 'true' } : {}),
      });
      ripple.trace('extract.started', { run: started.run_id });
      const progress = await driveRun(started.run_id);
      ripple.trace('extract.finished', {
        run: started.run_id,
        completed: progress ? progress.completed : null,
        failed: progress ? progress.failed : null,
      });
      const stopped = progress && progress.status === 'cancelled';
      label.textContent =
        (stopped ? 'Extraction cancelled' : 'Extraction finished')
        + spendLabel(progress);
      setTimeout(() => window.location.reload(), 900);
    } catch (error) {
      ripple.trace('extract.stopped', { error: error.message });
      document.getElementById('run-label').textContent = 'Extraction stopped';
      toast(error.message, true);
      extract.disabled = false;
    }
  });
}

/* Scene insertion. The dialog collects a heading and a body; the server
   parses them with the same rules an imported file gets, inserts the scene,
   and extracts just that scene when a model is selected. */
const addVeil = document.getElementById('add-scene-veil');
if (addVeil) {
  const headingInput = document.getElementById('add-scene-heading');
  const bodyInput = document.getElementById('add-scene-body');
  const whereLabel = document.getElementById('add-scene-where');
  let afterScene = null;

  const closeAdd = () => addVeil.classList.add('hide');
  document.getElementById('add-scene-cancel').addEventListener('click', closeAdd);
  addVeil.addEventListener('click', (event) => {
    if (event.target === addVeil) closeAdd();
  });
  addVeil.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); closeAdd(); }
  });

  document.querySelectorAll('.scene-add').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      afterScene = button.dataset.scene || null;
      whereLabel.textContent = afterScene
        ? `Inserted after scene ${button.dataset.number || 'this one'}. `
          + 'The scenes that follow keep their numbers.'
        : 'Inserted at the top of the script.';
      headingInput.value = '';
      bodyInput.value = '';
      addVeil.classList.remove('hide');
      headingInput.focus();
    });
  });

  document.getElementById('add-scene-save').addEventListener('click', async () => {
    const scriptId = window.location.pathname.split('/').pop();
    const save = document.getElementById('add-scene-save');
    save.disabled = true;
    try {
      const result = await api(`/api/scripts/${scriptId}/scenes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          heading: headingInput.value,
          body: bodyInput.value,
          after_scene_id: afterScene,
        }),
      });
      ripple.trace('scene.inserted', { scene: result.scene.scene_id });
      closeAdd();
      if (result.run) {
        document.getElementById('run-label').textContent =
          'Extracting the new scene';
        await driveRun(result.run.run_id);
        toast('Scene inserted and extracted.');
      } else {
        toast('Scene inserted. Choose a model to extract its requirements.');
      }
      setTimeout(() => window.location.reload(), 700);
    } catch (error) {
      toast(error.message, true);
      save.disabled = false;
    }
  });
}

/* Omission and restoration, per the production convention: the scene keeps
   its number and reads OMITTED; Restore brings it back. */
document.querySelectorAll('.scene-omit').forEach((button) => {
  button.addEventListener('click', async (event) => {
    event.stopPropagation();
    const number = button.dataset.number;
    const ok = await confirmDialog(
      `Mark scene ${number || 'this scene'} OMITTED? Its stored facts `
      + 'deactivate, later scenes that depend on them are flagged, and '
      + 'Restore undoes all of it.',
      'Omit scene',
    );
    if (!ok) return;
    try {
      const result = await api(`/api/scenes/${button.dataset.scene}/omit`, {
        method: 'POST',
      });
      ripple.trace('scene.omitted', {
        scene: result.scene_id,
        findings: result.findings.length,
      });
      const warning = result.findings.length
        ? ` ${result.findings.length} orphaned dependant(s) flagged.`
        : '';
      toast(`Scene omitted. ${result.assertions_deactivated} fact(s) `
        + `deactivated.${warning}`, result.findings.length > 0);
      setTimeout(() => window.location.reload(), 900);
    } catch (error) {
      toast(error.message, true);
    }
  });
});

document.querySelectorAll('.scene-restore').forEach((button) => {
  button.addEventListener('click', async (event) => {
    event.stopPropagation();
    try {
      const result = await api(`/api/scenes/${button.dataset.scene}/restore`, {
        method: 'POST',
      });
      ripple.trace('scene.restored', { scene: result.scene_id });
      toast('Scene restored; its facts are active again.');
      setTimeout(() => window.location.reload(), 700);
    } catch (error) {
      toast(error.message, true);
    }
  });
});

/* Instant search over the scene list. A long play runs to dozens of scenes,
   and the rows are already on the page, so filtering is a display toggle:
   no request, no reload. Matching runs over the heading and the scene
   number together, so "12" and "castle" both find their row. */
const sceneSearch = document.getElementById('scene-search');
if (sceneSearch) {
  const sceneRows = [...document.querySelectorAll('.scene-row')];
  const sceneNoMatch = document.getElementById('scene-nomatch');
  sceneSearch.addEventListener('input', () => {
    const needle = sceneSearch.value.trim().toLowerCase();
    let shown = 0;
    sceneRows.forEach((row) => {
      const hit = !needle || row.textContent.toLowerCase().includes(needle);
      row.style.display = hit ? '' : 'none';
      if (hit) shown += 1;
    });
    // An empty list is not a failed search, so the notice only appears when
    // there were rows to filter in the first place.
    sceneNoMatch.classList.toggle('hide', shown > 0 || !sceneRows.length);
  });
}

/* A needs_review import shows its stored warnings until a human closes them. */
const markReviewed = document.getElementById('mark-reviewed');
if (markReviewed) {
  markReviewed.addEventListener('click', async () => {
    const scriptId = window.location.pathname.split('/').pop();
    markReviewed.disabled = true;
    try {
      await api(`/api/scripts/${scriptId}/mark-reviewed`, {
        method: 'POST', body: form({}),
      });
      ripple.trace('import.marked_reviewed', { script: scriptId });
      document.getElementById('review-banner').remove();
      toast('Marked reviewed. The warnings stay on the library row.');
    } catch (error) {
      toast(error.message, true);
      markReviewed.disabled = false;
    }
  });
}
