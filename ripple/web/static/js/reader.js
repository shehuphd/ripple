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
const continuity = document.getElementById('continuity');
const contMeta = document.getElementById('cont-meta');
const seeRipple = document.getElementById('see-ripple');
const crumb = document.getElementById('crumb');
const preview = document.getElementById('preview');

/* How a cast member is present, shown beside an appears_in edge. on_stage is
   the plain default and stays unlabelled; the marked cases are the ones worth
   calling out. */
const MANNER_LABEL = { referenced: 'referenced', depicted: 'depicted' };

function mannerTag(edge) {
  const manner = edge.manner;
  if (!manner || manner === 'on_stage' || !MANNER_LABEL[manner]) return '';
  return `<span class="manner" data-tip="How this cast member is present in the scene"
    >${MANNER_LABEL[manner]}</span>`;
}

function edgeRow(edge, cls, sign) {
  return `<div class="edge ${cls || ''}">
    ${sign ? `<span class="sign">${sign}</span>` : ''}
    <span>${esc(edge.subject)}</span>
    <span class="p">${esc(edge.predicate).replace(/_/g, ' ')}</span>${mannerTag(edge)}
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
    <span class="p">${esc(edge.predicate).replace(/_/g, ' ')}</span>${mannerTag(edge)}
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
  // Only the verdict rows themselves; the tally above already states how many
  // held, changed, and dropped, so a prose "every assertion held" or
  // "dropped nothing" line beneath it only repeats the counts.
  if (rows.length) {
    parts.push(`<div class="judge-verdicts">${rows.join('')}</div>`);
  } else if (!j) {
    parts.push('<div class="empty">No graph to judge this against.</div>');
  }

  if (j && j.dropped) {
    // The count, not the reasons: why a proposal failed verification is the
    // pipeline's own vocabulary, and the trace is where it belongs.
    parts.push(
      `<div class="judge-drop"><div class="drop-hd">Verification dropped ` +
      `${j.dropped} ${j.dropped === 1 ? 'proposal' : 'proposals'} the model ` +
      `made</div><div class="drop-row">The full record is in the trace.` +
      `</div></div>`);
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
      renderContinuity(detail.findings || []);

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
/* Writing a script in the page itself.

   Explicit beats implicit, which is the whole idea of typing a script: a
   leading marker states the element and is consumed, so what you meant is
   what you get. Where no marker is typed, position decides, and position
   has exactly two modes: right under a cue or a parenthetical is dialogue,
   everywhere else is action. An all-caps short line is a cue in either
   mode. Tab cycles a wrong guess, Shift with Tab opens the list.

   Nothing here blocks the writer. The requirement pane is not consulted
   while a script has no graph, an edit saves itself on the way out of the
   line, and the script stays fluid until Build graph reads it. */

const WRITE_TYPES = [
  'action', 'character', 'dialogue', 'parenthetical', 'transition',
  'shot', 'note',
];
const TYPE_LABELS = {
  action: 'action', character: 'character', dialogue: 'dialogue',
  parenthetical: 'parenthetical', transition: 'transition', shot: 'shot',
  note: 'note', heading: 'new scene',
};
// What Enter leaves you in when nothing is marked. Two modes, no more: a
// cue and a parenthetical open a speech, and everything else is action.
const OPENS_SPEECH = ['character', 'parenthetical'];
// The camera words the importer knows, so a shot written here reads back
// as a shot when the export is imported again.
const SHOT_WORDS = /^(ANGLE ON|CLOSE ON|CLOSE UP|EXTREME CLOSE|WIDE ON|WIDE SHOT|POV|INSERT|BACK TO|SERIES OF SHOTS|MONTAGE|INTERCUT|AERIAL|TRACKING)\b/;

const authoring = () =>
  document.getElementById('see-ripple').dataset.graphReady !== 'true';

const readsAsHeading = (text) =>
  /^(INT|EXT|EST|I\/E|INT\.?\/EXT)[. ]/i.test(text) || /^\.[A-Za-z]/.test(text);

/* One marker, one element, consumed on commit. `>>` is read before `>`, so
   a shot never arrives as a transition. */
function forcedBy(text) {
  if (text.startsWith('>>')) return 'shot';
  if (text.startsWith('@')) return 'character';
  if (text.startsWith('!')) return 'action';
  if (text.startsWith('"')) return 'dialogue';
  if (text.startsWith('(')) return 'parenthetical';
  if (text.startsWith('>') && !text.endsWith('<')) return 'transition';
  if (text.startsWith('[[')) return 'note';
  if (/^\.[A-Za-z]/.test(text)) return 'heading';
  return null;
}

/* The same test the importer applies, so the label and the saved line
   never disagree. A name runs on letters, digits, and the handful of marks
   a name carries, which is what keeps "BAM!" and "WHAT?" out: no one is
   called those, and a sound is not a speaker. */
const CUE_SHAPE = /^\p{L}[\p{L}\p{N} .'\-#&_]*?(?:\s*\([^)]*\))*\s*\^?\s*$/u;

function looksLikeCue(text) {
  if (!text || text !== text.toUpperCase()) return false;
  if (readsAsHeading(text) || /^[A-Z0-9 .'-]+TO:$/.test(text)) return false;
  if (!CUE_SHAPE.test(text)) return false;
  const name = text.replace(/\s*\([^)]*\)/g, '').replace(/[_^]/g, '')
    .replace(/\.+$/, '').trim();
  return /\p{L}/u.test(name);
}

function guessType(text, previousType) {
  const forced = forcedBy(text);
  if (forced) return forced;
  if (readsAsHeading(text)) return 'heading';
  const speaking = OPENS_SPEECH.includes(previousType);
  if (/^[A-Z0-9 .'-]+TO:$/.test(text)) return 'transition';
  if (text === text.toUpperCase() && /[A-Z]/.test(text)) {
    if (SHOT_WORDS.test(text)) return 'shot';
    if (!speaking && text.length <= 40 && looksLikeCue(text)) return 'character';
  }
  return speaking ? 'dialogue' : 'action';
}

/* A cue the write proved was never a cue is retyped in place, so the page
   shows what the database holds without a reload. */
function repaintDemoted(ids) {
  (ids || []).forEach((id) => {
    const line = document.querySelector(`[data-unit="${id}"]`);
    if (!line) return;
    WRITE_TYPES.forEach((one) => line.classList.remove(one));
    line.classList.add('action');
  });
}

/* The type list, opened by Shift with Tab, so the eight elements are
   readable rather than remembered. Arrow keys walk it, Enter takes one. */
function openTypeList(line, current, choose) {
  document.querySelectorAll('.typelist').forEach((old) => old.remove());
  const list = document.createElement('div');
  list.className = 'typelist';
  list.setAttribute('role', 'listbox');
  WRITE_TYPES.forEach((type) => {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = `typerow${type === current ? ' on' : ''}`;
    row.dataset.type = type;
    row.setAttribute('role', 'option');
    row.innerHTML = '<span class="ty"></span><span class="mk"></span>';
    row.querySelector('.ty').textContent = TYPE_LABELS[type];
    row.querySelector('.mk').textContent = {
      action: '!', character: '@', dialogue: '"', parenthetical: '(',
      transition: '>', shot: '>>', note: '[[',
    }[type] || '';
    list.appendChild(row);
  });
  const box = line.getBoundingClientRect();
  list.style.top = `${box.bottom + 4}px`;
  list.style.left = `${box.left}px`;
  document.body.appendChild(list);

  let at = Math.max(0, WRITE_TYPES.indexOf(current));
  const rows = [...list.querySelectorAll('.typerow')];
  const mark = () => rows.forEach((row, index) => {
    row.classList.toggle('on', index === at);
    if (index === at) row.scrollIntoView({ block: 'nearest' });
  });
  mark();
  const close = () => {
    list.remove();
    document.removeEventListener('keydown', onKey, true);
    document.removeEventListener('mousedown', onDown, true);
  };
  const onKey = (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      event.stopPropagation();
      at = (at + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length;
      mark();
    } else if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault();
      event.stopPropagation();
      close();
      choose(WRITE_TYPES[at]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
      line.focus();
    }
  };
  const onDown = (event) => {
    const row = event.target.closest('.typerow');
    close();
    if (row) choose(row.dataset.type);
    else line.focus();
  };
  document.addEventListener('keydown', onKey, true);
  document.addEventListener('mousedown', onDown, true);
}

/* ---- undo and redo ------------------------------------------------- */

/* An editor's undo, over the writing operations rather than over
   keystrokes: each entry knows how to put the page and the database back,
   and how to do the thing again. Cmd or Ctrl with Z walks back, adding
   Shift walks forward. Accepting a ripple clears the stack, because the
   graph has moved and an undo of a written line would argue with it. */
const history = { done: [], undone: [] };

function record(entry) {
  history.done.push(entry);
  history.undone.length = 0;
  if (history.done.length > 200) history.done.shift();
}

async function stepHistory(back) {
  const from = back ? history.done : history.undone;
  const to = back ? history.undone : history.done;
  const entry = from.pop();
  if (!entry) {
    toast(back ? 'Nothing to undo.' : 'Nothing to redo.');
    return;
  }
  try {
    await (back ? entry.undo() : entry.redo())();
    to.push(entry);
    ripple.trace(back ? 'writing.undone' : 'writing.redone', { of: entry.what });
  } catch (error) {
    from.push(entry);
    toast(error.message, true);
  }
}

/* ---- the lines ----------------------------------------------------- */

function unitsOf(section) {
  return [...section.querySelectorAll('.u:not(.ghost)')];
}

function buildLine(composed, text, sceneNo) {
  const line = document.createElement('div');
  line.className = `u ${composed.unit_type}`;
  line.contentEditable = 'plaintext-only';
  line.spellcheck = false;
  line.textContent = text;
  line.dataset.unit = composed.unit_id;
  line.dataset.accepted = text;
  line.dataset.sceneNo = sceneNo || '';
  line.setAttribute('role', 'textbox');
  line.setAttribute('aria-label', 'Script line');
  wireUnit(line);
  return line;
}

async function writeLine(section, afterUnitId, text, type, place) {
  const composed = await api(`/api/scenes/${section.dataset.sceneBody}/units`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text, after_unit_id: afterUnitId || null, unit_type: type || null,
    }),
  });
  const line = buildLine(composed, composed.text || text,
    section.querySelector('.u') ? section.querySelector('.u').dataset.sceneNo : '');
  place(line);
  repaintDemoted(composed.demoted);
  return line;
}

async function saveLine(node) {
  const text = flatten(node.textContent).trim();
  if (!text) return deleteLine(node);
  const before = node.dataset.accepted;
  if (text === before) return null;
  try {
    await api(`/api/units/${node.dataset.unit}/text`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    node.dataset.accepted = text;
    state.drafts.delete(node.dataset.unit);
    node.classList.remove('edited');
    refreshDraftIndicator();
    const write = async (value) => {
      await api(`/api/units/${node.dataset.unit}/text`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: value }),
      });
      node.textContent = value;
      node.dataset.accepted = value;
    };
    record({
      what: 'edit',
      undo: () => () => write(before),
      redo: () => () => write(text),
    });
    ripple.trace('unit.direct_saved', { unit: node.dataset.unit });
  } catch (error) {
    toast(error.message, true);
  }
  return null;
}

async function deleteLine(node) {
  const section = node.closest('[data-scene-body]');
  const lines = unitsOf(section);
  const at = lines.indexOf(node);
  const previous = at > 0 ? lines[at - 1] : null;
  const snapshot = {
    text: node.dataset.accepted || flatten(node.textContent).trim(),
    type: node.classList[1],
    after: previous ? previous.dataset.unit : null,
  };
  try {
    await api(`/api/units/${node.dataset.unit}`, { method: 'DELETE' });
    state.drafts.delete(node.dataset.unit);
    refreshDraftIndicator();
    const neighbour = previous || lines[at + 1] || null;
    document.querySelectorAll('.ucap').forEach((cap) => cap.remove());
    node.remove();
    if (neighbour) caretToEnd(neighbour);
    record({
      what: 'delete',
      undo: () => async () => {
        const anchor = snapshot.after
          ? section.querySelector(`[data-unit="${snapshot.after}"]`) : null;
        const line = await writeLine(
          section, snapshot.after, snapshot.text, snapshot.type,
          (made) => (anchor ? anchor.after(made)
            : section.querySelector('.sh').after(made)),
        );
        snapshot.id = line.dataset.unit;
      },
      redo: () => async () => {
        const line = section.querySelector(`[data-unit="${snapshot.id}"]`);
        if (!line) return;
        await api(`/api/units/${line.dataset.unit}`, { method: 'DELETE' });
        line.remove();
      },
    });
    ripple.trace('unit.deleted', {});
  } catch (error) {
    toast(error.message, true);
  }
  return null;
}

function caretToEnd(node) {
  node.focus();
  const range = document.createRange();
  range.selectNodeContents(node);
  range.collapse(false);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}

/* ---- moving around ------------------------------------------------- */

function everyLine() {
  return [...document.querySelectorAll('#page .u')];
}

function onEdgeLine(node, top) {
  // Whether the caret is on the first or last painted line of a block that
  // wraps: only then does an arrow leave the block for its neighbour.
  const selection = window.getSelection();
  if (!selection.rangeCount) return true;
  const range = selection.getRangeAt(0).cloneRange();
  range.collapse(true);
  const spot = range.getClientRects()[0] || node.getBoundingClientRect();
  const box = node.getBoundingClientRect();
  return top ? spot.top - box.top < 6 : box.bottom - spot.bottom < 6;
}

function moveTo(node) {
  if (!node) return;
  node.scrollIntoView({ block: 'nearest' });
  caretToEnd(node);
}

function stepLine(from, forward) {
  const lines = everyLine();
  const at = lines.indexOf(from);
  moveTo(lines[at + (forward ? 1 : -1)]);
}

/* ---- writing into a scene ------------------------------------------ */

async function createSceneInline(heading, afterSection) {
  const scriptId = window.location.pathname.split('/').pop();
  const result = await api(`/api/scripts/${scriptId}/scenes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      heading,
      body: '',
      after_scene_id: afterSection ? afterSection.dataset.sceneBody : null,
      extract: false,
    }),
  });
  repaintDemoted(result.demoted);
  const scene = result.scene;
  const section = document.createElement('section');
  section.dataset.sceneBody = scene.scene_id;
  const header = document.createElement('div');
  header.className = 'sh';
  header.innerHTML = '<span class="no num"></span><span></span>';
  header.querySelector('.no').textContent = scene.display_number || '';
  header.querySelector('span:not(.no)').textContent = scene.heading;
  const ghost = makeGhost(scene.display_number);
  section.append(header, ghost);
  if (afterSection) afterSection.after(section);
  else {
    const first = document.getElementById('first-ghost');
    if (first) {
      const hint = first.nextElementSibling;
      if (hint && hint.classList.contains('empty')) hint.remove();
      first.replaceWith(section);
    } else {
      document.querySelector('#page .scene-insert').after(section);
    }
  }
  wireGhost(ghost);
  ghost.focus();
  ripple.trace('scene.written', { scene: scene.scene_id });
  record({
    what: 'scene',
    undo: () => async () => {
      await api(`/api/scenes/${scene.scene_id}`, { method: 'DELETE' });
      section.remove();
    },
    redo: () => async () => {
      toast('Type the heading again to bring the scene back.');
    },
  });
  return section;
}

function makeGhost(sceneNumber) {
  const ghost = document.createElement('div');
  ghost.className = 'u ghost';
  ghost.contentEditable = 'plaintext-only';
  ghost.spellcheck = false;
  ghost.dataset.sceneNo = sceneNumber || '';
  ghost.dataset.hint = 'Write the next line, or a heading for the next scene';
  ghost.setAttribute('role', 'textbox');
  ghost.setAttribute('aria-label', 'Write the next line');
  return ghost;
}

function modeUnder(section) {
  const lines = unitsOf(section);
  const last = lines[lines.length - 1];
  return last ? last.classList[1] : null;
}

function paintGhost(ghost) {
  const text = flatten(ghost.textContent).trim();
  const type = text
    ? (ghost._forcedType || guessType(text, modeUnder(ghost.closest('[data-scene-body]') || document.body)))
    : (ghost._forcedType || null);
  WRITE_TYPES.forEach((one) => ghost.classList.remove(one));
  if (type && type !== 'heading') ghost.classList.add(type);
  ghost.dataset.type = type ? TYPE_LABELS[type] : '';
  return type;
}

function resetGhost(ghost) {
  ghost.textContent = '';
  ghost._forcedType = null;
  ghost.dataset.type = '';
  WRITE_TYPES.forEach((one) => ghost.classList.remove(one));
}

async function commitGhost(ghost) {
  if (ghost._busy) return;
  const raw = flatten(ghost.textContent).trim();
  if (!raw) return;
  const section = ghost.closest('[data-scene-body]');
  const type = ghost._forcedType
    || guessType(raw, section ? modeUnder(section) : null);
  if (type === 'heading' || !section) {
    if (!section && type !== 'heading') {
      toast('Start with a scene heading, such as INT. OFFICE - DAY.');
      return;
    }
    ghost._busy = true;
    resetGhost(ghost);
    try {
      await createSceneInline(raw, section);
    } catch (error) {
      toast(error.message, true);
    }
    ghost._busy = false;
    return;
  }
  const lines = unitsOf(section);
  const last = lines[lines.length - 1] || null;
  ghost._busy = true;
  try {
    const line = await writeLine(
      section, last ? last.dataset.unit : null, raw,
      ghost._forcedType || null, (made) => ghost.before(made),
    );
    resetGhost(ghost);
    record({
      what: 'write',
      undo: () => async () => {
        await api(`/api/units/${line.dataset.unit}`, { method: 'DELETE' });
        line.remove();
      },
      redo: () => async () => {
        const again = await writeLine(
          section, last ? last.dataset.unit : null, raw,
          line.classList[1], (made) => ghost.before(made),
        );
        line.dataset.unit = again.dataset.unit;
      },
    });
    ripple.trace('unit.written', { type: line.classList[1] });
  } catch (error) {
    toast(error.message, true);
  }
  ghost._busy = false;
}

function wireGhost(ghost) {
  ghost.addEventListener('input', () => paintGhost(ghost));
  ghost.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      commitGhost(ghost);
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      resetGhost(ghost);
      ghost.blur();
      return;
    }
    if (event.key === 'Tab') {
      const text = flatten(ghost.textContent).trim();
      if (!text) return; // an empty line leaves Tab to keyboard navigation
      event.preventDefault();
      const section = ghost.closest('[data-scene-body]');
      const current = ghost._forcedType || guessType(text, modeUnder(section));
      if (event.shiftKey) {
        openTypeList(ghost, current, (picked) => {
          ghost._forcedType = picked;
          paintGhost(ghost);
          caretToEnd(ghost);
        });
        return;
      }
      const from = Math.max(0, WRITE_TYPES.indexOf(current));
      ghost._forcedType = WRITE_TYPES[(from + 1) % WRITE_TYPES.length];
      paintGhost(ghost);
      return;
    }
    if (navigate(event, ghost)) return;
  });
  ghost.addEventListener('focusout', () => {
    setTimeout(() => { if (!ghost._busy) commitGhost(ghost); }, 0);
  });
  ghost.addEventListener('paste', (event) => {
    event.preventDefault();
    const text = (event.clipboardData || window.clipboardData).getData('text');
    document.execCommand('insertText', false, text.replace(/\s*\n\s*/g, ' '));
  });
}

/* The arrow keys, shared by a written line and the write-here line.
   Returns whether the key was taken. */
function navigate(event, node) {
  const up = event.key === 'ArrowUp';
  const down = event.key === 'ArrowDown';
  if (!up && !down) return false;
  const lines = everyLine();
  if (event.shiftKey && (event.metaKey || event.ctrlKey)) {
    event.preventDefault();
    moveTo(up ? lines[0] : lines[lines.length - 1]);
    return true;
  }
  if (event.shiftKey) {
    // By section: a wrapped block of ten painted lines is one step.
    event.preventDefault();
    stepLine(node, down);
    return true;
  }
  // Plain arrows move the caret, and only leave the block at its edge.
  if (onEdgeLine(node, up)) {
    event.preventDefault();
    stepLine(node, down);
    return true;
  }
  return false;
}

function wireUnit(node) {
  // Focus is selection: clicking into a line to type is the same gesture as
  // choosing it. On a script with no graph there is nothing to select into,
  // so the pane is left alone and typing is never waiting on a request.
  node.addEventListener('focus', () => {
    flattenMarks(node);
    if (!authoring()) selectUnit(node);
  });
  node.addEventListener('input', () => {
    noteDraft(node);
    state.text = flatten(node.textContent);
  });
  node.addEventListener('focusout', () => {
    // On a graphless script an edit has no ripple to wait for, so leaving
    // the line is what saves it.
    if (authoring() && state.drafts.has(node.dataset.unit)) saveLine(node);
  });
  node.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      revertLine(node);
      node.blur();
      return;
    }
    if (event.key === 'Tab') {
      if (!authoring()) return;
      event.preventDefault();
      const current = node.classList[1];
      const retype = async (picked) => {
        const text = flatten(node.textContent).trim();
        const section = node.closest('[data-scene-body]');
        const lines = unitsOf(section);
        const at = lines.indexOf(node);
        const after = at > 0 ? lines[at - 1].dataset.unit : null;
        try {
          await api(`/api/units/${node.dataset.unit}`, { method: 'DELETE' });
          const line = await writeLine(section, after, text, picked,
            (made) => node.replaceWith(made));
          caretToEnd(line);
        } catch (error) {
          toast(error.message, true);
        }
      };
      if (event.shiftKey) {
        openTypeList(node, current, retype);
        return;
      }
      const from = Math.max(0, WRITE_TYPES.indexOf(current));
      retype(WRITE_TYPES[(from + 1) % WRITE_TYPES.length]);
      return;
    }
    // A screenplay unit is one block, so Enter never splits it: on a
    // drafted line of a graphed script it opens the ripple, and anywhere
    // else it goes to the write-here line at the end of the scene.
    if (event.key === 'Enter') {
      event.preventDefault();
      const drafted = state.drafts.has(node.dataset.unit);
      if (drafted && !authoring()) {
        openPreview();
        return;
      }
      if (drafted) saveLine(node);
      const section = node.closest('[data-scene-body]');
      const ghost = section && section.querySelector('.u.ghost');
      if (ghost) caretToEnd(ghost);
      return;
    }
    // An emptied line is deleted the way an editor deletes one, by one
    // more Backspace, unless a fact still cites it.
    if (event.key === 'Backspace' && !flatten(node.textContent).trim()) {
      event.preventDefault();
      deleteLine(node);
      return;
    }
    navigate(event, node);
  });
  // Paste as plain text, flattened: pasted markup or line breaks would become
  // part of a unit that the parser never produced that way.
  node.addEventListener('paste', (event) => {
    event.preventDefault();
    const text = (event.clipboardData || window.clipboardData).getData('text');
    document.execCommand('insertText', false, text.replace(/\s*\n\s*/g, ' '));
  });
}

document.querySelectorAll('.u:not(.ghost)').forEach(wireUnit);
document.querySelectorAll('.u.ghost').forEach(wireGhost);
const firstGhost = document.getElementById('first-ghost');
if (firstGhost) firstGhost.focus();

document.addEventListener('keydown', (event) => {
  if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 'z') return;
  if (!authoring()) return;
  event.preventDefault();
  stepHistory(!event.shiftKey);
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

/* The Continuity card reads two ways. With a line selected that an open
   finding cites, it is that line's findings and the two ways to close one:
   Resolve says the script now answers the warning, Dismiss says it was never
   one. Neither touches the graph, so a conflict a rewrite settles is closed
   here and re-extracted by that rewrite's ripple. With no such line, it is
   the script's own contents list, one row per open finding in script order,
   each a way to the line it is about. */
let scriptFindings = [];

async function loadScriptFindings() {
  const scriptId = window.location.pathname.split('/').pop();
  try {
    const body = await api(`/api/scripts/${scriptId}/findings`);
    scriptFindings = body.findings;
  } catch (error) {
    ripple.trace('findings.load_failed', { error: error.message });
  }
}

/* The contents list: every open finding on the script, in script order. */
function renderFindingContents() {
  contMeta.textContent = scriptFindings.length
    ? `${scriptFindings.length} on this script` : '';
  if (!scriptFindings.length) {
    continuity.innerHTML = '<div class="empty">No open findings. '
      + 'Edit a line and see its ripple to check a change against the graph.'
      + '</div>';
    return;
  }
  continuity.innerHTML = scriptFindings.map((finding, index) => `
    <button class="tocrow" data-goto="${index}"
            ${finding.units.length ? '' : 'disabled data-tip="This finding cites no line"'}>
      <div class="ttl"><i class="dot ${esc(finding.severity)}"></i>
        ${esc(finding.message)}</div>
      ${finding.line
    ? `<div class="where">Scene ${esc(finding.scene || '—')} · ${esc(finding.line)}</div>`
    : ''}
    </button>`).join('');
  continuity.querySelectorAll('[data-goto]').forEach((row) => {
    row.addEventListener('click', () => {
      const finding = scriptFindings[Number(row.dataset.goto)];
      const node = document.querySelector(
        `.u[data-unit="${finding.units[0]}"]`);
      if (!node) {
        toast('The cited line is not on this page.');
        return;
      }
      scrollPaneTo(node);
      selectUnit(node);
      ripple.trace('finding.opened', { finding: finding.id });
    });
  });
}

function renderContinuity(findings) {
  if (!findings.length) {
    renderFindingContents();
    return;
  }
  contMeta.textContent = `${findings.length} on this line`;
  continuity.innerHTML = findings.map((finding, index) => `
    <div class="finding" data-finding="${esc(finding.id)}">
      <div class="ttl"><i class="dot ${esc(finding.severity)}"></i>
        ${esc(finding.message)}</div>
      <div class="acts">
        ${finding.elsewhere.length
    ? `<span class="cited-link" data-elsewhere="${index}">The other line${
      finding.elsewhere.length > 1 ? 's' : ''}</span>`
    : '<span class="cited-link"></span>'}
        <button class="btn sm" data-close="resolve"
          data-tip="The script now answers this; close it as handled"
          >Mark resolved</button>
        <button class="btn sm danger" data-close="dismiss"
          data-tip="This was never a problem; close it and change nothing"
          >Dismiss</button>
      </div>
    </div>`).join('');

  continuity.querySelectorAll('[data-elsewhere]').forEach((link) => {
    link.addEventListener('click', () => {
      reviewUnits(findings[Number(link.dataset.elsewhere)].elsewhere);
    });
  });
  continuity.querySelectorAll('[data-close]').forEach((button) => {
    button.addEventListener('click', () => closeFinding(button, findings));
  });
}

/* Resolve or dismiss one finding, then repaint from what the server reports
   is left: the marks in the script and the toolbar count both come back from
   the same read, so neither drifts from the database. */
async function closeFinding(button, findings) {
  const row = button.closest('.finding');
  const id = row.dataset.finding;
  const how = button.dataset.close;
  if (how === 'dismiss') {
    const ok = await confirmDialog(
      'Dismiss this continuity finding? It leaves the open list, and nothing '
      + 'in the script changes.', 'Dismiss', { destructive: true });
    if (!ok) return;
  }
  button.disabled = true;
  let state;
  try {
    state = await api(`/api/findings/${id}/${how}`,
      { method: 'POST', body: form({}) });
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
    return;
  }
  ripple.trace('finding.closed', { finding: id, how, open: state.open });
  applyFindingState(state);
  await loadScriptFindings();
  renderContinuity(findings.filter((finding) => finding.id !== id));
  toast(how === 'resolve'
    ? 'Marked resolved.'
    : 'Dismissed. Nothing in the script changed.');
}

/* Repaint the marks and the toolbar count from the server's own reading. */
function applyFindingState(state) {
  const flagged = new Set(state.flagged_units || []);
  document.querySelectorAll('.u').forEach((node) => {
    node.classList.toggle('flagged', flagged.has(node.dataset.unit));
  });
  const chip = document.getElementById('finding-jump');
  if (!chip) return;
  chip.classList.toggle('hide', !state.open);
  document.getElementById('finding-label').textContent =
    `${state.open} continuity finding${state.open === 1 ? '' : 's'}`;
}

/* The toolbar chip walks the marked lines in script order, selecting each so
   the pane fills with the finding as well as the line. Finding a continuity
   problem is either the Findings page or this, and this one keeps the script
   in front of you. */
const findingJump = document.getElementById('finding-jump');
let jumpIndex = -1;
if (findingJump) {
  findingJump.addEventListener('click', () => {
    const nodes = [...document.querySelectorAll('.u.flagged')];
    if (!nodes.length) {
      toast('No line carries an open finding.');
      return;
    }
    jumpIndex = (jumpIndex + 1) % nodes.length;
    const node = nodes[jumpIndex];
    scrollPaneTo(node);
    selectUnit(node);
    ripple.trace('finding.jumped', { at: jumpIndex + 1, of: nodes.length });
  });
}

loadScriptFindings().then(() => {
  // Only while nothing is selected: a deep link that selects a line has
  // already filled the card with that line's findings.
  if (!state.unit) renderFindingContents();
});

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
        after(() => {
          reviewUnits(detail.cited_units, 'auto');
          // Review means "show me this finding", so the line is selected and
          // the pane opens on the finding itself, not only scrolled to.
          const node = document.querySelector(
            `.u[data-unit="${detail.cited_units[0]}"]`);
          if (node) selectUnit(node);
        });
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
        + 'answers. Each scene’s facts are replaced by the fresh read, so '
        + 'the graph reflects the latest pass; anything you accepted or edited '
        + 'by hand is kept.',
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
      // A build resolves the same thing under two names often enough that
      // the count is said here rather than left for whoever opens Entities.
      if (!stopped && progress && progress.duplicates) {
        const many = progress.duplicates !== 1;
        toast(
          `${progress.duplicates} suspected duplicate entit${many ? 'ies' : 'y'}`
          + ' to review on the Entities page.',
        );
      }
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

/* Search inside the script, stepping through the matches one at a time.

   The lines are contenteditable, so a match is never wrapped in an element:
   injected markup would end up inside an edit and inside the text a ripple
   compares. The custom highlight API paints ranges without touching the
   DOM, which leaves the revision tints and the accepted text alone. Where a
   browser has no highlight API, stepping still works and the line the match
   is on is outlined instead. */
const findBox = document.getElementById('find');
if (findBox) {
  const findQ = document.getElementById('find-q');
  const findCount = document.getElementById('find-count');
  const findPrev = document.getElementById('find-prev');
  const findNext = document.getElementById('find-next');
  const findClear = document.getElementById('find-clear');
  const painting = typeof Highlight === 'function' && window.CSS && CSS.highlights;
  let hits = [];
  let at = -1;
  let typing = null;

  function lineHolders() {
    // Only the script's own words: a heading and a line, never the toolbar,
    // the scene controls, or the insert buttons between scenes.
    return [...document.querySelectorAll('#page .u, #page .sh > span:not(.no)')];
  }

  function segmentsOf(holder) {
    // A line's text nodes in order, each tagged with its offset in the line's
    // full text. An accepted edit wraps its words in a <mark>, so one line is
    // several text nodes; searching them one at a time misses any match that
    // straddles the boundary ("young man" split across "A young " and the
    // marked "man ..."). The full string is searched instead, and a match maps
    // back to a range that can start in one node and end in another.
    const walker = document.createTreeWalker(holder, NodeFilter.SHOW_TEXT);
    const segments = [];
    let full = '';
    let node = walker.nextNode();
    while (node) {
      segments.push({ node, start: full.length });
      full += node.nodeValue;
      node = walker.nextNode();
    }
    return { segments, full };
  }

  function rangeAt(segments, from, to) {
    // Map a [from, to) span in the line's full text onto a DOM range, spanning
    // text nodes when the match crosses an inline element.
    const range = document.createRange();
    for (const { node, start } of segments) {
      const end = start + node.nodeValue.length;
      if (from >= start && from < end) range.setStart(node, from - start);
      if (to > start && to <= end) {
        range.setEnd(node, to - start);
        break;
      }
    }
    return range;
  }

  function paint() {
    if (!painting) return;
    CSS.highlights.delete('find-hit');
    CSS.highlights.delete('find-here');
    if (!hits.length) return;
    const others = hits.filter((_, index) => index !== at);
    if (others.length) CSS.highlights.set('find-hit', new Highlight(...others));
    if (hits[at]) CSS.highlights.set('find-here', new Highlight(hits[at]));
  }

  function search(needle) {
    hits = [];
    at = -1;
    if (needle.length >= 2) {
      const lower = needle.toLowerCase();
      lineHolders().forEach((holder) => {
        const { segments, full } = segmentsOf(holder);
        if (!segments.length) return;
        const haystack = full.toLowerCase();
        let from = haystack.indexOf(lower);
        while (from !== -1) {
          hits.push(rangeAt(segments, from, from + needle.length));
          from = haystack.indexOf(lower, from + needle.length);
        }
      });
    }
    const some = hits.length > 0;
    findPrev.disabled = findNext.disabled = !some;
    findClear.disabled = !needle;
    if (!needle) findCount.textContent = '';
    else if (needle.length < 2) findCount.textContent = 'keep typing';
    else findCount.textContent = some ? `1 of ${hits.length}` : 'no matches';
    if (some) step(1);
    else paint();
  }

  function step(by) {
    if (!hits.length) return;
    // The last match steps to the first: a search loops rather than stopping
    // at the end of the script.
    at = (at + by + hits.length) % hits.length;
    findCount.textContent = `${at + 1} of ${hits.length}`;
    paint();
    const line = hits[at].startContainer.parentElement.closest('.u, .sh');
    if (line) {
      line.scrollIntoView({ block: 'center', behavior: 'smooth' });
      if (!painting) {
        document.querySelectorAll('.u.cited').forEach(
          (node) => node.classList.remove('cited'));
        line.classList.add('cited');
      }
    }
  }

  function clear() {
    findQ.value = '';
    search('');
    document.querySelectorAll('.u.cited').forEach(
      (node) => node.classList.remove('cited'));
  }

  findQ.addEventListener('input', () => {
    // A play runs to thousands of lines, so the sweep waits for a pause in
    // typing rather than running on every keystroke.
    clearTimeout(typing);
    typing = setTimeout(() => search(findQ.value.trim()), 160);
  });
  findQ.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      step(event.shiftKey ? -1 : 1);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      clear();
    }
  });
  findNext.addEventListener('click', () => step(1));
  findPrev.addEventListener('click', () => step(-1));
  findClear.addEventListener('click', () => { clear(); findQ.focus(); });

  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'f') {
      event.preventDefault();
      findQ.focus();
      findQ.select();
    }
  });
  // An edit moves the text the ranges point into, so the matches are dropped
  // rather than left pointing at words that have moved.
  document.getElementById('page').addEventListener('input', () => {
    if (hits.length) clear();
  });
}

/* The title renames in place: click it, type, and Enter or leaving the
   field saves. The id, and every link to the script, stays. */
const titleNode = document.getElementById('script-title');
if (titleNode) {
  let before = titleNode.textContent;
  const editTitle = () => {
    if (titleNode.isContentEditable) return;
    before = titleNode.textContent;
    titleNode.contentEditable = 'plaintext-only';
    titleNode.focus();
    document.getSelection().selectAllChildren(titleNode);
  };
  const settleTitle = async (keep) => {
    titleNode.contentEditable = 'false';
    const title = titleNode.textContent.replace(/\s+/g, ' ').trim();
    if (!keep || !title || title === before) {
      titleNode.textContent = before;
      return;
    }
    try {
      const scriptId = window.location.pathname.split('/').pop();
      const renamed = await api(`/api/scripts/${scriptId}/title`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      });
      titleNode.textContent = renamed.title;
      document.title = `${renamed.title} · Ripple`;
      toast('Renamed.');
    } catch (error) {
      titleNode.textContent = before;
      toast(error.message, true);
    }
  };
  titleNode.addEventListener('click', editTitle);
  titleNode.addEventListener('keydown', (event) => {
    if (!titleNode.isContentEditable) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        editTitle();
      }
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      titleNode.blur();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      titleNode.textContent = before;
      titleNode.blur();
    }
  });
  titleNode.addEventListener('blur', () => {
    if (titleNode.isContentEditable) settleTitle(true);
  });
}

/* The writing shortcuts, behind a button rather than printed down the
   sidebar. The modifier key is named for the machine it is read on. */
const keysVeil = document.getElementById('shortcuts-veil');
if (keysVeil) {
  const apple = /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent);
  if (!apple) {
    keysVeil.querySelectorAll('.mod').forEach((key) => {
      key.textContent = 'Ctrl';
    });
  }
  let cameFrom = null;
  const openKeys = () => {
    cameFrom = document.activeElement;
    keysVeil.classList.remove('hide');
    document.getElementById('shortcuts-close').focus();
  };
  const closeKeys = () => {
    keysVeil.classList.add('hide');
    if (cameFrom && cameFrom.focus) cameFrom.focus();
  };
  document.getElementById('shortcuts-open').addEventListener('click', openKeys);
  document.getElementById('shortcuts-close').addEventListener('click', closeKeys);
  keysVeil.addEventListener('click', (event) => {
    if (event.target === keysVeil) closeKeys();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !keysVeil.classList.contains('hide')) {
      event.preventDefault();
      closeKeys();
      return;
    }
    // "?" reaches the list from anywhere the writer is not typing into.
    if (event.key !== '?' || event.metaKey || event.ctrlKey) return;
    const typing = document.activeElement
      && (document.activeElement.isContentEditable
        || document.activeElement.tagName === 'INPUT'
        || document.activeElement.tagName === 'TEXTAREA');
    if (typing) return;
    event.preventDefault();
    openKeys();
  });
}
