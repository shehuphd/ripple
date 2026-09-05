/* Ask Ripple: one chat over one script. A question is answered from the
   graph on the grounded path; a change request runs the agent, which reads,
   plans, drafts, and previews, and stops at a card the user presses. Nothing
   in this file can apply a change; Confirm posts to the one endpoint that
   accepts, and the agent has no tool that reaches it. */
const chatwrap = document.getElementById('chatwrap');
if (chatwrap) {
  const chat = document.getElementById('chat');
  const input = document.getElementById('q');
  const send = document.getElementById('ask');
  const intro = document.getElementById('chat-intro');
  const scriptId = chatwrap.dataset.script;
  const scriptTitle = chatwrap.dataset.title || '';

  // One turn in flight at a time: a second Enter while the first runs would
  // bill a second set of model calls for the same request.
  let running = false;
  let thread = null;

  function syncSend() {
    const empty = !input.value.trim();
    send.disabled = empty || running;
    send.dataset.tip = empty ? 'Type a message to send' : 'Send to Ripple';
  }
  input.addEventListener('input', syncSend);
  syncSend();

  function scrollDown() {
    chat.scrollTop = chat.scrollHeight;
  }

  function turn(role, label, inner) {
    if (intro) intro.classList.add('hide');
    const wrap = document.createElement('div');
    wrap.className = `turn ${role}`;
    wrap.innerHTML = `
      <div class="avatar" aria-hidden="true">${role === 'you' ? '☺' : '≋'}</div>
      <div class="turnbody"><div class="who">${esc(label)}</div>${inner}</div>`;
    chat.append(wrap);
    scrollDown();
    return wrap;
  }

  function addUser(text) {
    return turn('you', 'You', `<div class="said">${esc(text)}</div>`);
  }

  function working(text) {
    return turn('ripple', 'Ripple', `
      <div class="chips toolchips"><span class="chip running">
        <i class="dot"></i>${esc(text)}</span></div>`);
  }

  /* Rendering a turn ------------------------------------------------------ */

  function toolChips(tools) {
    if (!tools || !tools.length) return '';
    const chips = tools.map((t) => `<span class="chip${t.ok ? '' : ' bad'}">`
      + `${t.ok ? '✓' : '!'} ${esc(t.summary)}</span>`).join(' ');
    return `<div class="chips toolchips">${chips}</div>`;
  }

  function planCard(rows) {
    if (!rows || !rows.length) return '';
    const body = rows.map((row) => `
      <tr${row.needs_change === false ? ' class="nochange"' : ''}>
        <td class="num">${esc(row.scene)}</td>
        <td>${esc(row.change)}</td>
        <td class="cite">${esc((row.citation || '').toUpperCase())}</td>
      </tr>`).join('');
    const changing = rows.filter((row) => row.needs_change !== false).length;
    return `
      <div class="card chatcard">
        <div class="hd"><h2>Coverage list</h2>
          <span class="meta">${rows.length} scenes, ${changing} changing</span>
          <span class="tag set_design">Computed from the graph</span></div>
        <div class="tscroll">
          <table class="scripts listtable plantable">
            <colgroup><col style="width:80px"><col><col style="width:150px">
            </colgroup>
            <thead><tr><th>Scene</th><th>Planned change</th>
              <th>Citation</th></tr></thead>
            <tbody>${body}</tbody>
          </table>
        </div>
      </div>`;
  }

  function findingRows(findings) {
    return (findings || []).map((f) => `
      <div class="finding">
        <span class="tag ${esc(f.severity)}">${esc(f.severity)}</span>
        <span>${esc(f.message)}</span>
      </div>`).join('');
  }

  function rippleCard(payload, held) {
    if (!payload) return '';
    const heldNote = (held && held.length)
      ? `<div class="tiny muted held">${held.length} proposed fact(s) scored
          below the confidence floor and were left out of this proposal:
          ${held.map((h) => esc(`${h.subject} ${h.predicate} ${h.object}`
            + ` (${h.confidence})`)).join('; ')}.</div>`
      : '';
    return `
      <div class="card chatcard">
        <div class="hd"><h2>Ripple</h2>
          <span class="meta">${payload.operations || 0} graph operations</span>
          <span class="tag ${esc(payload.severity || '')}">
            ${esc(payload.severity || '')}</span></div>
        <div class="answer">${esc(payload.summary || '')}</div>
        ${findingRows(payload.findings)}
        ${heldNote}
      </div>`;
  }

  function omissionCard(payload) {
    if (!payload) return '';
    return `
      <div class="card chatcard">
        <div class="hd"><h2>If this scene is cut</h2>
          <span class="meta">${(payload.orphans || []).length} orphaned
            reference(s)</span></div>
        ${findingRows(payload.orphans)}
      </div>`;
  }

  function spendLine(payload) {
    const bits = [`${payload.model_calls} model call(s)`,
      `${(payload.tokens || 0).toLocaleString()} tokens`];
    if (payload.cost) bits.push(payload.cost);
    return `<span class="spend tiny muted">${esc(bits.join(' · '))}</span>`;
  }

  function actionRow(payload) {
    if (payload.stage === 'plan') {
      return `<div class="cardactions">
        <button class="btn pri" data-act="go">✓ Go ahead</button>
        <button class="btn" data-act="adjust">↺ Adjust the plan</button>
        ${spendLine(payload)}</div>`;
    }
    if (payload.change_set_id) {
      return `<div class="cardactions">
        <button class="btn pri" data-act="confirm">✓ Confirm</button>
        <button class="btn" data-act="revise">↺ Revise</button>
        <button class="btn" data-act="cancel">✕ Cancel</button>
        ${spendLine(payload)}</div>`;
    }
    return `<div class="cardactions">${spendLine(payload)}</div>`;
  }

  function addRipple(payload) {
    const wrap = turn('ripple', 'Ripple', `
      ${toolChips(payload.tools)}
      <div class="answer">${esc(payload.reply || '')}</div>
      ${planCard(payload.plan)}
      ${omissionCard(payload.omission)}
      ${rippleCard(payload.ripple, payload.held_back)}
      ${actionRow(payload)}`);
    wireActions(wrap, payload);
    return wrap;
  }

  function addApplied(summary) {
    const rows = (summary.rows || []).map((row) => `
      <tr><td class="num">${esc(row.scene)}</td>
        <td>${esc(row.applied)}</td>
        <td class="cite">${esc(row.predicate)}</td></tr>`).join('');
    const open = (summary.findings || []).length
      ? `<div class="card chatcard">
           <div class="hd"><h2>Still open</h2>
             <span class="meta">${summary.findings.length} continuity
               finding(s)</span>
             <a class="btn" href="/reader?script=${encodeURIComponent(scriptId)}">
               Review in the reader</a></div>
           ${findingRows(summary.findings)}</div>`
      : '';
    turn('ripple', 'Ripple', `
      <div class="banner ok">✓ Change set accepted, base
        v${summary.version_from} → v${summary.version_to}
        <a class="btn" href="/reader?script=${encodeURIComponent(scriptId)}">
          ↺ Undo in the reader</a></div>
      <div class="answer">${esc(summary.text || '')}</div>
      <div class="card chatcard">
        <div class="hd"><h2>What changed</h2>
          <span class="meta">${summary.operations} graph operations across
            ${summary.units} unit(s)</span></div>
        <div class="tscroll">
          <table class="scripts listtable plantable">
            <colgroup><col style="width:80px"><col><col style="width:150px">
            </colgroup>
            <thead><tr><th>Scene</th><th>Applied</th><th>Predicate</th></tr>
            </thead><tbody>${rows}</tbody>
          </table>
        </div>
      </div>
      ${open}
      <div class="cardactions"><span class="spend tiny muted">
        Logged as one grouped action in Settings, Spend</span></div>`);
  }

  /* A grounded answer, rendered as a Ripple turn. Same evidence the ask path
     has always returned; the chat is a new surface for it, not a new source. */
  function addAnswer(body) {
    const cited = (body.cited_units || []).map((u) => `
      <div class="cited"><span class="no">${esc(u.scene ?? '')}</span>
        <span class="bd">${esc(u.text)}</span></div>`).join('');
    const chips = (body.entities || [])
      .map((e) => `<span class="tag">${esc(e)}</span>`).join(' ');
    turn('ripple', 'Ripple', `
      <div class="answer">${esc(body.answer)}</div>
      ${chips ? `<div class="chips">${chips}</div>` : ''}
      <div class="card chatcard">
        <div class="hd"><h2>Evidence</h2>
          <span class="meta">grounded in ${body.grounded_in} assertion(s)
            across ${body.total_units} unit(s), mean confidence
            ${body.mean_confidence}</span></div>
        ${cited || '<div class="empty">None.</div>'}
      </div>`);
    if (body.query_id) prependQuestion(body.query_id, body.question || '');
  }

  /* Card actions ---------------------------------------------------------- */

  function retire(wrap) {
    const actions = wrap.querySelector('.cardactions');
    if (actions) actions.remove();
  }

  function wireActions(wrap, payload) {
    wrap.querySelectorAll('[data-act]').forEach((button) => {
      button.addEventListener('click', async () => {
        const act = button.dataset.act;
        if (act === 'adjust') {
          input.value = '';
          input.placeholder = 'Say what to change about the plan';
          input.focus();
          return;
        }
        if (act === 'go') {
          retire(wrap);
          await run('Go ahead.', 'draft');
          return;
        }
        if (act === 'revise') {
          input.placeholder = 'Say what to change, and Ripple redrafts';
          input.focus();
          wrap.dataset.revising = payload.change_set_id;
          return;
        }
        if (act === 'cancel') {
          button.disabled = true;
          try {
            await api(`/api/changes/${payload.change_set_id}/reject`, {
              method: 'POST', body: form({ reason: 'Cancelled in Ask Ripple' }),
            });
            retire(wrap);
            toast('Proposal cancelled. Nothing was applied.');
          } catch (error) { toast(error.message, true); }
          return;
        }
        if (act === 'confirm') {
          button.disabled = true;
          button.textContent = 'Applying…';
          try {
            const summary = await api(
              `/api/conversations/${thread}/confirm`,
              { method: 'POST',
                body: form({ change_set_id: payload.change_set_id }) },
            );
            retire(wrap);
            addApplied(summary);
            const sub = document.getElementById('ask-sub');
            if (sub) {
              sub.innerHTML = `${esc(scriptTitle)}, base `
                + `<span class="num">v${summary.version_to}</span>`;
            }
          } catch (error) {
            toast(error.message, true);
            button.disabled = false;
            button.textContent = '✓ Confirm';
          }
        }
      });
    });
  }

  /* Sending --------------------------------------------------------------- */

  async function run(text, stage) {
    if (running) return;
    const message = (text || input.value).trim();
    if (!message) return;
    running = true;
    syncSend();
    if (!text) input.value = '';
    input.placeholder = 'Reply to Ripple';
    addUser(message);
    const pending = working(stage === 'draft' ? 'Drafting' : 'Reading the graph');
    // A revision rejects the proposal it supersedes, so the preview cache
    // cannot replay a suggestion the user has already moved past.
    const revising = chat.querySelector('[data-revising]');
    if (revising && !stage) {
      try {
        await api(`/api/changes/${revising.dataset.revising}/reject`, {
          method: 'POST', body: form({ reason: 'Revised in Ask Ripple' }),
        });
      } catch (error) { /* already gone; the redraft stands on its own */ }
      retire(revising);
      revising.removeAttribute('data-revising');
      stage = 'draft';
    }
    try {
      const fields = { message };
      if (thread) fields.conversation_id = thread;
      if (stage) fields.stage = stage;
      const body = await api(`/api/scripts/${scriptId}/ripple`, {
        method: 'POST', body: form(fields),
      });
      pending.remove();
      if (body.kind === 'answer') {
        body.question = message;
        addAnswer(body);
      } else {
        if (body.conversation) {
          const opened = !thread;
          thread = body.conversation.id;
          if (opened) prependThread(thread, body.conversation.title);
        }
        addRipple(body);
      }
    } catch (error) {
      pending.remove();
      turn('ripple', 'Ripple', `<div class="answer">${esc(error.message)}</div>`);
    } finally {
      running = false;
      syncSend();
      input.focus();
    }
  }

  send.addEventListener('click', () => run());
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') run();
  });
  document.querySelectorAll('[data-example]').forEach((button) => {
    button.addEventListener('click', () => {
      input.value = button.dataset.example;
      syncSend();
      run();
    });
  });

  /* The sidebar: threads and questions, both replayed from storage at no
     cost. Opening either clears the chat, so one screen shows one thread. */
  function sidebarRow(listId, dataset, label) {
    const list = document.getElementById(listId);
    if (!list) return;
    const row = document.createElement('button');
    row.className = 'srow qrow';
    Object.entries(dataset).forEach(([key, value]) => {
      row.dataset[key] = value;
    });
    row.title = label;
    row.innerHTML = '<span class="ic">≋</span>';
    const text = document.createElement('span');
    text.className = 'lb';
    text.textContent = label;
    row.append(text);
    list.prepend(row);
    wireRow(row);
    const counter = document.getElementById(
      listId === 'thread-list' ? 'thread-count' : 'history-count',
    );
    if (counter) counter.textContent = list.querySelectorAll('.qrow').length;
    const empty = document.getElementById(
      listId === 'thread-list' ? 'thread-empty' : 'history-empty',
    );
    if (empty) empty.classList.add('hide');
  }

  function prependThread(id, title) {
    sidebarRow('thread-list', { thread: id }, title);
  }

  function prependQuestion(id, question) {
    sidebarRow('ask-history', { query: id }, question);
  }

  async function openThread(id) {
    chat.innerHTML = '';
    thread = id;
    try {
      const body = await api(`/api/conversations/${id}`);
      // A proposal that was decided keeps its card but not its buttons: an
      // applied turn later in the thread means Confirm already happened.
      const decided = new Set(
        body.turns
          .filter((entry) => entry.role === 'applied' && entry.change_set_id)
          .map((entry) => entry.change_set_id),
      );
      body.turns.forEach((entry) => {
        if (entry.role === 'user') addUser(entry.text);
        else if (entry.role === 'applied') addApplied(entry.payload);
        else {
          const wrap = addRipple(entry.payload);
          if (entry.change_set_id && decided.has(entry.change_set_id)) {
            retire(wrap);
          }
        }
      });
    } catch (error) { toast(error.message, true); }
  }

  async function openQuestion(id) {
    chat.innerHTML = '';
    thread = null;
    try {
      const body = await api(`/api/queries/${id}`);
      addUser(body.question);
      addAnswer(body);
    } catch (error) { toast(error.message, true); }
  }

  function wireRow(row) {
    row.addEventListener('click', () => {
      document.querySelectorAll('.qrow.on').forEach((other) => {
        other.classList.remove('on');
      });
      row.classList.add('on');
      if (row.dataset.thread) openThread(row.dataset.thread);
      else openQuestion(row.dataset.query);
    });
  }

  document.querySelectorAll('#thread-list .qrow, #ask-history .qrow')
    .forEach(wireRow);
}

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
