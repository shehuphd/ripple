/* The graph canvas.
   Positions come from the server, which computes them deterministically, so
   this file only draws. Edges are SVG paths with horizontal labels; nodes are
   absolutely positioned HTML so their text uses the same font stack as the
   rest of the app and stays selectable. */

const DEPARTMENT_VAR = {
  cast: '--cast', transportation: '--transport', prop: '--props',
  set_design: '--set', wardrobe: '--wardrobe', makeup: '--wardrobe',
  sound: '--sound', vfx: '--set', stunt: '--red', location: '--ac',
};

function colourFor(entityType) {
  const name = DEPARTMENT_VAR[entityType];
  return name ? `var(${name})` : 'var(--t3)';
}

/* Predicates group into families and each family gets one line treatment, so
   the eye learns five patterns instead of nine. Type is carried by the dash
   pattern and the printed predicate, never by colour alone; direction is
   carried by a mid-edge arrowhead, with the flowing dashes as reinforcement
   that switches off under prefers-reduced-motion. */
const EDGE_FAMILY = {
  appears_in: 'presence', occurs_at: 'presence',
  wears: 'handling', carries: 'handling', uses: 'handling', travels_by: 'handling',
  requires: 'dependency',
  establishes: 'establishes',
  interacts_with: 'interaction',
};
// interacts_with is symmetric and presence edges are the quiet backbone, so
// neither gets an arrow.
const DIRECTED_FAMILIES = new Set(['handling', 'dependency', 'establishes']);

function edgeFamily(link) {
  if (link.removed) return 'removed';
  return EDGE_FAMILY[link.predicate] || 'presence';
}

/* Zoom and pan. draw() lays out every node and edge inside a `.gzoom` layer
   in the canvas's own untransformed pixel box; zooming is one CSS transform
   on that layer, so the x*width / y*height layout math never has to know
   about scale. State lives on the canvas element itself, keyed per instance,
   so it survives a redraw (department filter, selection) without a reset. */
const ZOOM_MIN = 0.4;
const ZOOM_MAX = 2.5;
const ZOOM_STEP = 0.2;

function zoomState(canvas) {
  if (!canvas._zoom) canvas._zoom = { scale: 1, tx: 0, ty: 0 };
  return canvas._zoom;
}

function applyZoom(canvas) {
  const state = zoomState(canvas);
  const layer = canvas.querySelector('.gzoom');
  if (layer) {
    layer.style.transform = `translate(${state.tx}px, ${state.ty}px) scale(${state.scale})`;
  }
  const label = canvas.querySelector('.znum');
  if (label) label.textContent = `${Math.round(state.scale * 100)}%`;
}

function setZoom(canvas, scale, anchor) {
  const state = zoomState(canvas);
  const clamped = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, scale));
  if (anchor) {
    // Solve for the translate that leaves the anchor's on-screen position
    // unmoved as the scale changes, so zooming feels centred on the cursor
    // (or the canvas centre, for a button/keyboard zoom) rather than always
    // pulling toward the top-left origin.
    const rect = canvas.getBoundingClientRect();
    const anchorX = anchor.x - rect.left;
    const anchorY = anchor.y - rect.top;
    state.tx = anchorX - ((anchorX - state.tx) / state.scale) * clamped;
    state.ty = anchorY - ((anchorY - state.ty) / state.scale) * clamped;
  }
  state.scale = clamped;
  applyZoom(canvas);
}

function panBy(canvas, dx, dy) {
  const state = zoomState(canvas);
  state.tx += dx;
  state.ty += dy;
  applyZoom(canvas);
}

function resetZoom(canvas) {
  const state = zoomState(canvas);
  state.scale = 1;
  state.tx = 0;
  state.ty = 0;
  applyZoom(canvas);
}

/* Centre the viewport on a point given in the canvas's own untransformed
   pixel space (node.x * canvas.clientWidth, for instance). */
function centerOn(canvas, x, y) {
  const state = zoomState(canvas);
  state.tx = canvas.clientWidth / 2 - x * state.scale;
  state.ty = canvas.clientHeight / 2 - y * state.scale;
  applyZoom(canvas);
}

function centerAnchor(canvas) {
  const rect = canvas.getBoundingClientRect();
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
}

function wireZoom(canvas) {
  canvas.setAttribute('tabindex', '0');
  const zoomIn = canvas.querySelector('#zoom-in');
  const zoomOut = canvas.querySelector('#zoom-out');
  const zoomReset = canvas.querySelector('#zoom-reset');
  if (zoomIn) {
    zoomIn.addEventListener('click', () =>
      setZoom(canvas, zoomState(canvas).scale + ZOOM_STEP, centerAnchor(canvas)));
  }
  if (zoomOut) {
    zoomOut.addEventListener('click', () =>
      setZoom(canvas, zoomState(canvas).scale - ZOOM_STEP, centerAnchor(canvas)));
  }
  if (zoomReset) zoomReset.addEventListener('click', () => resetZoom(canvas));

  // The reader's mini graph lives inside a scrolling page and offers no zoom
  // controls, so capturing wheel there would trap page scrolling over it.
  if (!canvas.classList.contains('mini')) {
    canvas.addEventListener('wheel', (event) => {
      event.preventDefault();
      const direction = event.deltaY > 0 ? -1 : 1;
      setZoom(canvas, zoomState(canvas).scale + direction * 0.1,
        { x: event.clientX, y: event.clientY });
    }, { passive: false });
  }

  let dragging = null;
  canvas.addEventListener('pointerdown', (event) => {
    if (event.target.closest('.gnode, button, input, a')) return;
    dragging = { x: event.clientX, y: event.clientY };
    canvas.classList.add('grabbing');
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', (event) => {
    if (!dragging) return;
    panBy(canvas, event.clientX - dragging.x, event.clientY - dragging.y);
    dragging = { x: event.clientX, y: event.clientY };
  });
  canvas.addEventListener('pointerup', () => {
    dragging = null;
    canvas.classList.remove('grabbing');
  });
  canvas.addEventListener('pointercancel', () => {
    dragging = null;
    canvas.classList.remove('grabbing');
  });

  canvas.addEventListener('keydown', (event) => {
    const step = 40;
    if (event.key === 'ArrowUp') { panBy(canvas, 0, step); event.preventDefault(); }
    else if (event.key === 'ArrowDown') { panBy(canvas, 0, -step); event.preventDefault(); }
    else if (event.key === 'ArrowLeft') { panBy(canvas, step, 0); event.preventDefault(); }
    else if (event.key === 'ArrowRight') { panBy(canvas, -step, 0); event.preventDefault(); }
    else if (event.key === '+' || event.key === '=') {
      setZoom(canvas, zoomState(canvas).scale + ZOOM_STEP, centerAnchor(canvas));
      event.preventDefault();
    } else if (event.key === '-') {
      setZoom(canvas, zoomState(canvas).scale - ZOOM_STEP, centerAnchor(canvas));
      event.preventDefault();
    } else if (event.key === '0') {
      resetZoom(canvas);
      event.preventDefault();
    }
  });

  applyZoom(canvas);
}

/* A search box over the same node list the graph already drew. Picking a
   result reuses the click-selection path (select()), so it dims the
   unrelated graph and fills the detail pane the same way a click does, then
   pans/zooms the picked node into view. */
function wireSearch(input, resultsBox, canvas, getData, select, getSelectedId) {
  let active = -1;

  function matches(query) {
    const data = getData();
    if (!data || !query.trim()) return [];
    const q = query.trim().toLowerCase();
    return data.nodes.filter((n) => n.label.toLowerCase().includes(q)).slice(0, 8);
  }

  function render(list) {
    resultsBox.innerHTML = list.map((n, i) => `
      <div class="sres ${i === active ? 'active' : ''}" role="option"
           id="sres-${i}" data-id="${esc(n.id)}"
           aria-selected="${i === active}">
        <span class="mono tiny">${esc(n.kind === 'scene' ? 'scene'
          : (n.entity_type || '').replace(/_/g, ' '))}</span>
        <span>${esc(n.label)}</span>
      </div>`).join('');
    resultsBox.hidden = list.length === 0;
    input.setAttribute('aria-expanded', String(list.length > 0));
    if (active >= 0 && active < list.length) {
      input.setAttribute('aria-activedescendant', `sres-${active}`);
    } else {
      input.removeAttribute('aria-activedescendant');
    }
  }

  function close() {
    resultsBox.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    active = -1;
  }

  function pick(node) {
    close();
    input.value = '';
    if (getSelectedId() !== node.id) select(node);
    setZoom(canvas, Math.max(zoomState(canvas).scale, 1), centerAnchor(canvas));
    centerOn(canvas, node.x * canvas.clientWidth, node.y * canvas.clientHeight);
    canvas.focus();
  }

  input.addEventListener('input', () => {
    active = -1;
    render(matches(input.value));
  });
  input.addEventListener('keydown', (event) => {
    const list = matches(input.value);
    if (event.key === 'ArrowDown' && list.length) {
      active = (active + 1) % list.length;
      render(list);
      event.preventDefault();
    } else if (event.key === 'ArrowUp' && list.length) {
      active = (active - 1 + list.length) % list.length;
      render(list);
      event.preventDefault();
    } else if (event.key === 'Enter') {
      const chosen = list[active >= 0 ? active : 0];
      if (chosen) pick(chosen);
      event.preventDefault();
    } else if (event.key === 'Escape' && !resultsBox.hidden) {
      // Close the dropdown first; a second Escape then falls through to the
      // graph's own deselect handler instead of doing both at once.
      close();
      event.stopPropagation();
    }
  });
  resultsBox.addEventListener('click', (event) => {
    const row = event.target.closest('.sres');
    if (!row) return;
    const data = getData();
    const node = data.nodes.find((n) => n.id === row.dataset.id);
    if (node) pick(node);
  });
  document.addEventListener('click', (event) => {
    if (!event.target.closest('#graph-search')) close();
  });
}

function draw(canvas, data, onSelect, linkFilter, onDrawn, attempt) {
  const { clientWidth: width, clientHeight: height } = canvas;
  // A canvas with no measured size places every node at the origin. Defer
  // until layout has run rather than drawing a pile. The deferred retry
  // still carries onDrawn through, so a caller that pairs draw() with
  // something order-dependent (dimUnrelated, keyed on the freshly drawn
  // .gnode elements) fires against the draw that ran, not the one that
  // bailed. The retries are bounded: a canvas inside a collapsed pane never
  // gains a size, and requeueing forever pins a core; the resize fired by
  // reopening the pane draws it then.
  if (width < 40 || height < 40) {
    const tries = attempt || 0;
    if (tries < 30) {
      requestAnimationFrame(() =>
        draw(canvas, data, onSelect, linkFilter, onDrawn, tries + 1));
    }
    return;
  }
  let zoomLayer = canvas.querySelector('.gzoom');
  if (!zoomLayer) {
    zoomLayer = document.createElement('div');
    zoomLayer.className = 'gzoom';
    // First child, so the zoom controls, count, and legend, all later in
    // the template's DOM order, paint above it and stay clickable rather
    // than being covered by the full-size pannable layer.
    canvas.insertBefore(zoomLayer, canvas.firstChild);
    wireZoom(canvas);
  }
  zoomLayer.querySelectorAll('.gnode, svg').forEach((n) => n.remove());

  const at = (node) => ({ x: node.x * width, y: node.y * height });
  const byId = Object.fromEntries(data.nodes.map((n) => [n.id, n]));
  const drawable = linkFilter ? data.links.filter(linkFilter) : data.links;

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');

  // Two nodes can be joined by more than one edge: a scene both requires a
  // prop and has it appear in it. Drawn straight, those edges share a midpoint
  // and their labels stack illegibly, so parallel edges fan out.
  const lanes = {};
  for (const link of drawable) {
    const key = [link.source, link.target].sort().join('|');
    (lanes[key] = lanes[key] || []).push(link);
  }

  // A hub with many edges puts every label at its own curve's midpoint, and
  // those midpoints converge near the hub, so the labels pile up. Each
  // singleton edge takes its label position from its place in the busier
  // endpoint's fan, cycling through offsets around the midpoint so
  // neighbouring labels rest at different depths along their curves.
  const degree = {};
  for (const link of drawable) {
    degree[link.source] = (degree[link.source] || 0) + 1;
    degree[link.target] = (degree[link.target] || 0) + 1;
  }
  const FAN_OFFSETS = [0, -0.14, 0.14, -0.28, 0.28];
  const fanned = {};

  for (const parallel of Object.values(lanes)) {
    parallel.forEach((link, index) => {
      // Draw every edge in a lane from the same end. Parallel edges are
      // stored in opposite directions (a scene establishes a prop, and the
      // prop appears in the scene), so measuring t from each edge's own source
      // puts t=0.4 and t=0.6 on the same physical point.
      const [firstId, secondId] = [link.source, link.target].sort();
      const from = byId[firstId];
      const to = byId[secondId];
      if (!from || !to) return;
      const a = at(from);
      const b = at(to);

      // Fan around the straight line, and when a pair carries several edges
      // start off-centre so no edge lies on the chord where its label
      // would collide with the node labels at either end.
      const lane = parallel.length === 1
        ? 0
        : (index - (parallel.length - 1) / 2) * 2;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const length = Math.hypot(dx, dy) || 1;
      // Wide enough apart that a label fits between the arcs without
      // touching either: each lane bows 24px from the chord, so a pair of
      // curves clears a 12px label row with margin on both sides.
      const offset = lane * 24;
      const nx = (-dy / length) * offset;
      const ny = (dx / length) * offset;
      const mx = (a.x + b.x) / 2 + nx;
      const my = (a.y + b.y) / 2 + ny;
      // Labels are separated along the edge, not across it. A perpendicular
      // offset large enough to clear a predicate would push the label off its
      // own curve. The stagger is sized from the widest label on this pair and
      // the edge's length, because a fixed fraction is too small on a short
      // edge and wastefully large on a long one.
      const widest = Math.max(...parallel.map((l) => l.predicate.length)) * 6.2;
      const spread = Math.min(0.34, Math.max(0.14, (widest + 10) / length));
      let t;
      if (parallel.length === 1) {
        const hub = (degree[link.source] || 0) >= (degree[link.target] || 0)
          ? link.source : link.target;
        const rank = fanned[hub] || 0;
        fanned[hub] = rank + 1;
        t = 0.5 + FAN_OFFSETS[rank % FAN_OFFSETS.length];
      } else {
        t = Math.min(0.86, Math.max(0.14,
          0.5 + (index - (parallel.length - 1) / 2) * spread));
      }
      const point = (p0, p1, p2) =>
        (1 - t) * (1 - t) * p0 + 2 * (1 - t) * t * p1 + t * t * p2;
      const cx = point(a.x, mx, b.x);
      const cy = point(a.y, my, b.y);

      const family = edgeFamily(link);
      const d = `M ${a.x} ${a.y} Q ${mx} ${my} ${b.x} ${b.y}`;
      // The path is always drawn sorted-first to sorted-second, so the flow
      // and the arrow must reverse when the assertion's subject is the
      // other endpoint.
      const reversed = link.source !== firstId;

      if (family === 'establishes') {
        // A soft wide underlay behind the establishing edge: introductions
        // are the continuity-critical moments, so they read from across the
        // canvas.
        const glow = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        glow.setAttribute('d', d);
        glow.setAttribute('class', 'eglow');
        svg.appendChild(glow);
      }

      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', d);
      path.setAttribute('class', `epath ${family}${reversed ? ' rev' : ''}`);
      svg.appendChild(path);

      if (DIRECTED_FAMILIES.has(family)) {
        // The nodes render above the SVG, so an endpoint arrowhead would be
        // hidden under them. The arrow rides the curve just before the
        // label, pointing along the tangent toward the assertion's object.
        const ta = Math.max(0.1, t - 0.16);
        const q = (p0, p1, p2) =>
          (1 - ta) * (1 - ta) * p0 + 2 * (1 - ta) * ta * p1 + ta * ta * p2;
        const dq = (p0, p1, p2) =>
          2 * (1 - ta) * (p1 - p0) + 2 * ta * (p2 - p1);
        let tx = dq(a.x, mx, b.x);
        let ty = dq(a.y, my, b.y);
        if (reversed) { tx = -tx; ty = -ty; }
        const angle = Math.atan2(ty, tx) * 180 / Math.PI;
        const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        arrow.setAttribute('d', 'M4.5,0 L-3,3.4 L-3,-3.4 Z');
        arrow.setAttribute('class', `earrow ${family}`);
        arrow.setAttribute('transform',
          `translate(${q(a.x, mx, b.x)} ${q(a.y, my, b.y)}) rotate(${angle})`);
        svg.appendChild(arrow);
      }

      // The predicate is the content, so it is drawn horizontally rather than
      // rotated along the edge, on the curve's own midpoint.
      const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      label.setAttribute('x', cx);
      label.setAttribute('y', cy - 3);
      label.setAttribute('text-anchor', 'middle');
      label.setAttribute('class', `elabel ${family}`);
      label.textContent = link.predicate.replace(/_/g, ' ');
      svg.appendChild(label);
    });
  }
  zoomLayer.appendChild(svg);

  for (const node of data.nodes) {
    const point = at(node);
    const element = document.createElement('div');
    element.className = `gnode ${node.kind}${node.removed ? ' removed' : ''}`;
    element.dataset.id = node.id;
    element.style.left = `${point.x}px`;
    element.style.top = `${point.y}px`;
    element.style.color = node.id === data.focus ? 'var(--ac)' : colourFor(node.entity_type);
    if (node.id === data.focus) element.classList.add('on');
    element.textContent = node.label;
    // Nodes are as reachable by keyboard as by mouse. The label names the
    // kind too, since position and colour are visual carriers.
    element.tabIndex = 0;
    element.setAttribute('role', 'button');
    element.setAttribute('aria-label',
      `${node.kind === 'scene' ? 'Scene'
        : (node.entity_type || 'entity').replace(/_/g, ' ')}: ${node.label}`);
    element.addEventListener('click', () => onSelect(node));
    element.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        onSelect(node);
      }
    });
    zoomLayer.appendChild(element);
  }

  nudgeLabelsOffNodes(canvas);

  const count = canvas.querySelector('#gcount');
  if (count) {
    count.textContent =
      `${data.nodes.length} nodes · ${data.links.length} edges` +
      (data.hidden_below_threshold
        ? ` · ${data.hidden_below_threshold} below threshold hidden` : '');
  }
  if (onDrawn) onDrawn();
}

/* With a node selected, everything neither selected nor connected to it drops
   back, so the selection and its edges carry the view. Opacity is the carrier
   here rather than colour alone: the selected node also has the accent colour
   and its edges drawn, so the emphasis survives colourblindness. */
function dimUnrelated(canvas, data, selectedId) {
  const nodes = canvas.querySelectorAll('.gnode');
  if (!selectedId) {
    nodes.forEach((n) => n.classList.remove('dim'));
    return;
  }
  const connected = new Set([selectedId]);
  for (const link of data.links) {
    if (link.source === selectedId) connected.add(link.target);
    if (link.target === selectedId) connected.add(link.source);
  }
  nodes.forEach((n) => n.classList.toggle('dim', !connected.has(n.dataset.id)));
}

/* An edge that passes under a node leaves its label on top of that node's
   text, and two edges converging on a hub leave their labels on top of each
   other. Both are the same problem: a label overlapping something already
   placed. Labels resolve in document order against the node boxes and every
   label placed before them, moving along the vertical until clear.
   Deterministic and bounded: same input, same nudge. */
function nudgeLabelsOffNodes(canvas) {
  const obstacles = [...canvas.querySelectorAll('.gnode')].map((n) =>
    n.getBoundingClientRect());
  const hit = (a, b) => !(a.right < b.left || b.right < a.left ||
                          a.bottom < b.top || b.bottom < a.top);

  for (const label of canvas.querySelectorAll('svg text')) {
    // The escape direction is chosen once, at the first clash, and held.
    // Re-choosing per step lets a label caught between two obstacles bounce
    // up and down without ever leaving either.
    let direction = 0;
    for (let step = 1; step <= 8; step += 1) {
      const box = label.getBoundingClientRect();
      const clash = obstacles.find((n) => hit(box, n));
      if (!clash) break;
      if (!direction) {
        // Away from the obstacle's centre, so a label under it goes down
        // and one above it goes up rather than crossing it.
        direction = box.top + box.height / 2 < clash.top + clash.height / 2
          ? -1 : 1;
      }
      label.setAttribute(
        'y', Number(label.getAttribute('y')) + direction * 13);
    }
    obstacles.push(label.getBoundingClientRect());
  }
}

/* Expanded view */
const canvas = document.getElementById('canvas');
if (canvas) {
  const detail = document.getElementById('detail');
  const state = {
    depth: 1, confidence: 0, removed: false, data: null, selected: null,
  };

  function departments() {
    const boxes = [...document.querySelectorAll('.dept')];
    const on = boxes.filter((b) => b.checked).map((b) => b.value);
    return on.length === boxes.length ? null : on.join(',');
  }

  // Selection highlight and dim are reapplied after every draw: draw()
  // recreates the .gnode elements, so state set on the old ones is gone.
  function applySelection() {
    document.querySelectorAll('.gnode.on').forEach((n) => {
      if (n.dataset.id !== state.selected) n.classList.remove('on');
    });
    if (state.selected) {
      const chosen = canvas.querySelector(`.gnode[data-id="${state.selected}"]`);
      if (chosen) chosen.classList.add('on');
    }
    dimUnrelated(canvas, state.data, state.selected);
  }

  function redraw() {
    if (!state.data) return;
    draw(canvas, state.data, select, null, applySelection);
  }

  async function refresh() {
    const params = new URLSearchParams({
      depth: state.depth,
      min_confidence: state.confidence,
      include_removed: state.removed,
    });
    const chosen = departments();
    if (chosen !== null) params.set('departments', chosen);

    try {
      state.data = await api(`/api/units/${canvas.dataset.unit}/graph?${params}`);
    } catch (error) {
      ripple.trace('graph.load_failed', { error: error.message });
      toast(error.message, true);
      return;
    }
    redraw();
    document.getElementById('fit-info').textContent =
      `${state.data.nodes.length} nodes`;
  }

  function deselect() {
    state.selected = null;
    applySelection();
    detail.innerHTML = '<div class="empty">Select a node to see its edges.</div>';
  }

  function select(node) {
    // Clicking the selected node again is the deselect gesture.
    if (state.selected === node.id) {
      deselect();
      return;
    }
    state.selected = node.id;
    applySelection();
    const edges = state.data.links.filter(
      (l) => l.source === node.id || l.target === node.id);
    const byId = Object.fromEntries(state.data.nodes.map((n) => [n.id, n]));
    const rows = edges.map((l) => {
      const other = l.source === node.id ? byId[l.target] : byId[l.source];
      return `<div class="kv"><span class="mono tiny">${esc(l.predicate).replace(/_/g, ' ')}</span>
        <span>${esc(other ? other.label : '?')} · ${esc(l.confidence)}</span></div>`;
    }).join('');
    detail.innerHTML = `
      <h3>${esc(node.label)}</h3>
      <div class="tiny muted" style="margin-bottom:12px">
        ${esc(node.entity_type ? node.entity_type.replace(/_/g, ' ') : node.kind)}</div>
      <div class="kv"><span>Edges</span><span class="num">${edges.length}</span></div>
      ${rows || '<div class="empty">No edges.</div>'}`;
  }

  document.getElementById('depth-up').addEventListener('click', () => {
    state.depth = Math.min(3, state.depth + 1);
    document.getElementById('depth').textContent = state.depth;
    refresh();
  });
  document.getElementById('depth-down').addEventListener('click', () => {
    state.depth = Math.max(1, state.depth - 1);
    document.getElementById('depth').textContent = state.depth;
    refresh();
  });
  document.getElementById('conf').addEventListener('input', (event) => {
    state.confidence = Number(event.target.value);
    document.getElementById('conf-value').textContent = state.confidence.toFixed(2);
  });
  document.getElementById('conf').addEventListener('change', refresh);
  document.getElementById('removed').addEventListener('change', (event) => {
    state.removed = event.target.checked;
    refresh();
  });
  document.querySelectorAll('.dept').forEach((box) =>
    box.addEventListener('change', refresh));

  window.addEventListener('resize', redraw);
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && state.selected) deselect();
  });
  const searchInput = document.getElementById('search-input');
  const searchResults = document.getElementById('search-results');
  if (searchInput && searchResults) {
    wireSearch(searchInput, searchResults, canvas, () => state.data, select,
      () => state.selected);
  }
  refresh();
}

/* Script-level view: every scene on the spine, every entity in its wedge.
   Edges draw on selection only: all of them at once is a hairball, and the
   full link set is already in the page so selecting costs no request. */
const scriptCanvas = document.getElementById('script-canvas');
if (scriptCanvas) {
  const detail = document.getElementById('detail');
  const state = { confidence: 0, data: null, selected: null };

  // The graph can be the landing view, and landing on it is opening the
  // script. Same contract as the reader: a POST from the page, so a
  // prefetch cannot reorder Recently opened.
  api(`/api/scripts/${scriptCanvas.dataset.script}/opened`, { method: 'POST' })
    .catch(() => {});

  function departments() {
    const boxes = [...document.querySelectorAll('.dept')];
    const on = boxes.filter((b) => b.checked).map((b) => b.value);
    return on.length === boxes.length ? null : on.join(',');
  }

  function linkFilter(link) {
    if (!state.selected) return false;
    return link.source === state.selected || link.target === state.selected;
  }

  function redraw() {
    if (!state.data) return;
    state.data.focus = state.selected;
    // dimUnrelated rides as draw()'s onDrawn callback rather than a
    // separate statement: draw() defers itself via requestAnimationFrame
    // when the canvas is momentarily unsized (right after navigation, a
    // pane toggle), and a dimUnrelated call sitting after a deferred draw()
    // would run against zero .gnode elements while the drawn ones show up
    // later with no dim state at all.
    draw(scriptCanvas, state.data, select, linkFilter,
      () => dimUnrelated(scriptCanvas, state.data, state.selected));
  }

  async function refresh() {
    const params = new URLSearchParams({ min_confidence: state.confidence });
    const chosen = departments();
    if (chosen !== null) params.set('departments', chosen);
    try {
      state.data = await api(
        `/api/scripts/${scriptCanvas.dataset.script}/graph?${params}`);
    } catch (error) {
      ripple.trace('graph.load_failed', { error: error.message });
      toast(error.message, true);
      return;
    }
    ripple.trace('graph.loaded', {
      script: scriptCanvas.dataset.script,
      nodes: state.data.nodes.length,
      links: state.data.links.length,
    });
    document.getElementById('fit-info').textContent =
      `${state.data.nodes.length} nodes · ${state.data.links.length} edges`;
    redraw();
  }

  function deselect(nodeId) {
    state.selected = null;
    redraw();
    detail.innerHTML = '<div class="empty">Select an entity or a scene to '
      + 'see its attributes, evidence, and everything it connects to.</div>';
    ripple.trace('graph.deselected', { node: nodeId });
  }

  async function select(node) {
    // Clicking the selected node again is the deselect gesture.
    if (state.selected === node.id) {
      deselect(node.id);
      return;
    }
    state.selected = node.id;
    redraw();
    if (node.kind === 'entity') {
      let card;
      try {
        card = await api(`/api/entities/${node.id}/detail`);
      } catch (error) {
        ripple.trace('graph.detail_failed', { entity: node.id, error: error.message });
        detail.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
        return;
      }
      ripple.trace('graph.entity_selected', {
        entity: node.id, assertions: card.assertions.length,
      });
      renderEntity(card);
    } else {
      renderScene(node);
    }
  }

  function renderEntity(card) {
    const attributes = card.attributes.map((a) => `
      <div class="kv"><span class="mono tiny">${esc(a.key)}</span>
        <span data-tip="${esc(a.evidence)}">${esc(a.value)}</span></div>`)
      .join('');
    const aliases = card.aliases.length
      ? `<div class="kv"><span>Also called</span>
         <span>${esc(card.aliases.join(', '))}</span></div>`
      : '';
    const rows = card.assertions.map((a) => `
      <div class="kv" data-tip="${esc(a.evidence)}">
        <span class="mono tiny">${esc(a.predicate).replace(/_/g, ' ')}</span>
        <span>${esc(a.subject)} → ${esc(a.object)} · ${a.confidence.toFixed(2)}</span>
      </div>`).join('');
    detail.innerHTML = `
      <h3>${esc(card.name)}</h3>
      <div class="tiny muted" style="margin-bottom:12px">
        ${esc(card.type.replace(/_/g, ' '))}
        ${card.scenes.length ? ` · scenes ${esc(card.scenes.join(', '))}` : ''}</div>
      ${card.description
        ? `<p class="tiny" style="margin-bottom:12px">${esc(card.description)}</p>`
        : ''}
      ${attributes
        ? `<div class="sgroup" style="padding-left:0">Attributes</div>${attributes}`
        : ''}
      ${aliases}
      <div class="sgroup" style="padding-left:0">Assertions
        <span class="num" style="float:right">${card.assertions.length}</span></div>
      ${rows || '<div class="empty">Nothing cites this entity yet.</div>'}`;
  }

  function renderScene(node) {
    const edges = state.data.links.filter(
      (l) => l.source === node.id || l.target === node.id);
    const byId = Object.fromEntries(state.data.nodes.map((n) => [n.id, n]));
    const rows = edges.map((l) => {
      const other = l.source === node.id ? byId[l.target] : byId[l.source];
      return `<div class="kv"><span class="mono tiny">${esc(l.predicate).replace(/_/g, ' ')}</span>
        <span>${esc(other ? other.label : '?')} · ${esc(l.confidence)}</span></div>`;
    }).join('');
    detail.innerHTML = `
      <h3>${esc(node.label)}</h3>
      <div class="tiny muted" style="margin-bottom:12px">scene</div>
      <div class="kv"><span>Edges</span><span class="num">${edges.length}</span></div>
      ${rows || '<div class="empty">Nothing extracted for this scene yet.</div>'}`;
  }

  document.getElementById('conf').addEventListener('input', (event) => {
    state.confidence = Number(event.target.value);
    document.getElementById('conf-value').textContent = state.confidence.toFixed(2);
  });
  document.getElementById('conf').addEventListener('change', refresh);
  document.querySelectorAll('.dept').forEach((box) =>
    box.addEventListener('change', refresh));
  window.addEventListener('resize', redraw);
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && state.selected) deselect(state.selected);
  });
  const searchInput = document.getElementById('search-input');
  const searchResults = document.getElementById('search-results');
  if (searchInput && searchResults) {
    wireSearch(searchInput, searchResults, scriptCanvas, () => state.data, select,
      () => state.selected);
  }
  refresh();
}
