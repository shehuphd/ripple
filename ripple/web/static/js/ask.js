const ask = document.getElementById('ask');
if (ask) {
  const question = document.getElementById('q');
  const out = document.getElementById('out');
  // The chosen script comes from the server, not the query string: the page
  // falls back to the most recent script when none is named, and reading the
  // absent parameter sent "null" to the API.
  const askform = document.getElementById('askform');
  const scriptId = askform.dataset.script;
  const scriptTitle = askform.dataset.title || '';

  // One question in flight at a time: a second Enter while the first is
  // running would bill a second model call for the same question.
  let running = false;

  // Ask is inert with nothing to send. It carries the reason on the button
  // while it is off, so the disabled state is never a dead end.
  function syncAsk() {
    const empty = !question.value.trim();
    ask.disabled = empty || running;
    ask.dataset.tip = empty ? 'Type a question to ask' : 'Ask the graph';
  }
  question.addEventListener('input', syncAsk);
  syncAsk();

  // The last rendered answer, kept for the export button, which lives in the
  // toolbar and stays hidden until there is something to export.
  let last = null;
  const exportButton = document.getElementById('export-answer');
  if (exportButton) exportButton.addEventListener('click', exportAnswer);

  function groundingBadge(body) {
    // Only a fresh, written answer carries the check; a stored answer's
    // grounding set may have changed since it was asked, so no badge.
    if (!Array.isArray(body.ungrounded_entities) || !body.generated) return '';
    if (body.ungrounded_entities.length === 0) {
      return '<span class="tag set_design">✓ no ungrounded entities</span>';
    }
    // Each ungrounded name links to the entities list, where the reader can
    // find it: a warning that names something should reach it.
    const names = body.ungrounded_entities
      .map((n) => `<a href="/entities?q=${encodeURIComponent(n)}">${esc(n)}</a>`)
      .join(', ');
    return `<span class="tag prop">names outside grounding: ${names}</span>`;
  }

  function render(body) {
    last = body;
    const chips = body.entities
      .map((e) => `<span class="tag">${esc(e)}</span>`).join(' ');
    const cited = body.cited_units.map((u) => `
      <div class="cited">
        <span class="no">${esc(u.scene ?? '')}</span>
        <span class="bd">${esc(u.text)}</span>
      </div>`).join('');
    const storedNote = body.stored
      ? `Stored answer from ${esc(body.asked_at)}, zero cost · `
      : '';
    const shown = body.cited_units.length;
    const total = body.total_units ?? shown;
    const evidenceMeta = total > shown
      ? `sample of ${shown} of ${total}, ordered by scene`
      : `${total}, ordered by scene`;
    out.innerHTML = `
      <div class="ask-meta">
        <span>${storedNote}Grounded in ${body.grounded_in} assertions across
          ${total} units, mean confidence
          ${body.mean_confidence}${body.generated || !body.grounded_in
            ? '' : ' · deterministic answer, no model configured'}</span>
        <span class="grounded">${groundingBadge(body)}</span>
      </div>
      <div class="card ask-card">
        <div class="answer">${esc(body.answer)}</div>
        ${chips ? `<div class="chips" style="margin:16px 0 0">${chips}</div>` : ''}
        <div class="tiny muted" style="margin-top:14px">
          Answers come from accepted assertions only. Nothing here is generated
          from the screenplay text.</div>
      </div>
      <div class="card ask-card">
        <div class="hd"><h2>Evidence</h2>
          <span class="meta">${evidenceMeta}</span></div>
        ${cited || '<div class="empty">None.</div>'}
      </div>`;
    if (exportButton) exportButton.classList.remove('hide');
  }

  function exportAnswer() {
    if (!last) return;
    const q = last.question || question.value.trim();
    const lines = [
      `# ${q}`,
      '',
      last.answer,
      '',
      `Grounded in ${last.grounded_in} accepted assertion(s) across `
        + `${last.total_units ?? last.cited_units.length} unit(s), `
        + `mean confidence ${last.mean_confidence}.`
        + (scriptTitle ? ` Script: ${scriptTitle}.` : ''),
      '',
    ];
    if (last.entities.length) {
      lines.push(`Entities: ${last.entities.join(', ')}`, '');
    }
    if (last.cited_units.length) {
      const total = last.total_units ?? last.cited_units.length;
      const heading = total > last.cited_units.length
        ? `## Evidence (sample of ${last.cited_units.length} of ${total})`
        : '## Evidence';
      lines.push(heading, '');
      last.cited_units.forEach((u) => {
        lines.push(`- Scene ${u.scene ?? '?'}: ${u.text}`);
      });
      lines.push('');
    }
    lines.push(
      'Answers come from accepted assertions only. '
      + 'Nothing is generated from the screenplay text.',
    );
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    const slug = q.toLowerCase().replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '').slice(0, 60) || 'answer';
    link.download = `${slug}.md`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  async function run() {
    if (!question.value.trim() || running) return;
    running = true;
    ask.disabled = true;
    out.innerHTML = '<div class="ask-hint">Asking…</div>';
    if (exportButton) exportButton.classList.add('hide');
    const asked = question.value.trim();
    try {
      const body = await api(`/api/scripts/${scriptId}/ask`, {
        method: 'POST', body: form({ question: asked }),
      });
      body.question = asked;
      render(body);
      prependHistory(body.query_id, asked);
    } catch (error) {
      out.innerHTML = `<div class="ask-hint">${esc(error.message)}</div>`;
      if (exportButton) exportButton.classList.add('hide');
    } finally {
      running = false;
      syncAsk();
    }
  }

  /* History lives in the page's own left pane: a stored answer replays from
     the log at zero cost, and the question box fills so Ask re-runs it fresh
     against the current graph. */
  const historyPane = document.getElementById('ask-history');

  async function showStored(queryId) {
    out.innerHTML = '<div class="ask-hint">Loading the stored answer…</div>';
    if (exportButton) exportButton.classList.add('hide');
    try {
      const body = await api(`/api/queries/${queryId}`);
      question.value = body.question;
      render(body);
    } catch (error) {
      out.innerHTML = `<div class="ask-hint">${esc(error.message)}</div>`;
      if (exportButton) exportButton.classList.add('hide');
    }
  }

  function wireHistoryRow(row) {
    row.addEventListener('click', () => showStored(row.dataset.query));
  }

  function prependHistory(queryId, asked) {
    if (!historyPane || !queryId) return;
    const empty = document.getElementById('history-empty');
    if (empty) empty.classList.add('hide');
    const row = document.createElement('button');
    row.className = 'qrow';
    row.dataset.query = queryId;
    row.title = asked;
    const q = document.createElement('span');
    q.className = 'qq';
    q.textContent = asked;
    const when = document.createElement('span');
    when.className = 'qt num';
    when.textContent = 'just now';
    row.append(q, when);
    wireHistoryRow(row);
    historyPane.prepend(row);
    const count = document.getElementById('history-count');
    if (count) count.textContent = historyPane.querySelectorAll('.qrow').length;
  }

  if (historyPane) {
    historyPane.querySelectorAll('.qrow').forEach(wireHistoryRow);
  }

  ask.addEventListener('click', run);
  question.addEventListener('keydown', (e) => { if (e.key === 'Enter') run(); });
}

/* Instant search over a pane's rows: the rows are already on the page, so
   filtering is a display toggle, no request and no reload. Runs for the
   scripts pane and the history pane alike. */
function filterPane(inputId, listId, rowSelector, emptyId) {
  const input = document.getElementById(inputId);
  const list = document.getElementById(listId);
  if (!input || !list) return;
  const noMatch = emptyId ? document.getElementById(emptyId) : null;
  const apply = () => {
    const needle = input.value.trim().toLowerCase();
    const rows = list.querySelectorAll(rowSelector);
    let shown = 0;
    rows.forEach((row) => {
      const hit = !needle || row.textContent.toLowerCase().includes(needle);
      row.style.display = hit ? '' : 'none';
      if (hit) shown += 1;
    });
    // An empty pane is not a failed search: its own empty line covers that.
    if (noMatch) noMatch.classList.toggle('hide', shown > 0 || !rows.length);
  };
  input.addEventListener('input', apply);
  apply();
}

filterPane('script-search', 'script-list', '.srow', 'script-nomatch');
filterPane('history-search', 'ask-history', '.qrow', 'history-nomatch');

/* The graphless empty state: Build graph runs the extraction loop right
   here, one scene per request, each committed on its own. Leaving the page
   pauses the loop; pressing Build again resumes from the unfinished scenes,
   because completed scenes replay from cache at no cost. */
const build = document.getElementById('ask-build');
if (build) {
  const sceneLabel = document.getElementById('bp-scene');
  const countLabel = document.getElementById('bp-count');
  const bar = document.getElementById('bp-bar');
  const spend = document.getElementById('bp-spend');
  const rate = document.getElementById('bp-rate');
  const cancel = document.getElementById('bp-cancel');
  let activeRun = null;

  /* The server drains the run; this only reports it. Reloading or leaving
     the page changes nothing about the build. */
  function paint(progress, started) {
    const done = progress.completed + progress.failed;
    sceneLabel.textContent = done < progress.total
      ? `Extracting scene ${done + 1}`
      : 'Finishing';
    countLabel.textContent = `${done} of ${progress.total} scenes`
      + (progress.failed ? ` · ${progress.failed} failed` : '');
    bar.style.width =
      `${Math.round((done / Math.max(progress.total, 1)) * 100)}%`;
    spend.textContent =
      `${(progress.assertions || 0).toLocaleString()} assertions extracted`
      + ` · ${(progress.tokens || 0).toLocaleString()} tokens`
      + (progress.cost ? ` · ${progress.cost}` : '');
    if (done) {
      const perScene = (performance.now() - started) / 1000 / done;
      rate.textContent = `${perScene.toFixed(1)}s per scene`;
    }
  }

  async function follow(runId, started) {
    for (;;) {
      await new Promise((resume) => { setTimeout(resume, 900); });
      const progress = await api(`/api/extract/${runId}/progress`);
      paint(progress, started);
      if (progress.status === 'cancelled') return 'cancelled';
      if (progress.pending === 0 && !progress.working) return 'done';
    }
  }

  build.addEventListener('click', async () => {
    build.disabled = true;
    document.getElementById('ask-idle').classList.add('hide');
    document.getElementById('ask-building').classList.remove('hide');
    const sub = document.getElementById('ask-sub');
    if (sub) sub.textContent = `${sub.dataset.title}, building graph`;
    const started = performance.now();
    let runId = null;
    try {
      const run = await api(`/api/scripts/${build.dataset.script}/extract`, {
        method: 'POST', body: form({ background: 'true' }),
      });
      runId = run.run_id;
      activeRun = runId;
      ripple.trace('ask.build_started', { run: runId });
      paint(run, started);
      const outcome = await follow(runId, started);
      if (outcome === 'cancelled') {
        toast('Extraction cancelled. The scenes already read are in the graph.');
      }
      window.location.reload();
    } catch (error) {
      toast(error.message, true);
      document.getElementById('ask-building').classList.add('hide');
      document.getElementById('ask-idle').classList.remove('hide');
      build.disabled = false;
    }
  });

  // Cancelling asks twice, then stops the run after the scene in flight.
  if (cancel) {
    cancel.addEventListener('click', async () => {
      if (!activeRun) return;
      if (cancel.textContent === 'Cancel') {
        cancel.textContent = 'Confirm';
        return;
      }
      cancel.disabled = true;
      cancel.textContent = 'Cancelling…';
      try {
        await api(`/api/extract/${activeRun}/cancel`, { method: 'POST' });
      } catch (error) {
        toast(error.message, true);
        cancel.disabled = false;
        cancel.textContent = 'Cancel';
      }
    });
  }
}
