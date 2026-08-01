/* The graph canvas.
   Positions come from the server, which computes them deterministically, so
   this file only draws. Edges are SVG lines with horizontal labels; nodes are
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

function draw(canvas, data, onSelect) {
  const { clientWidth: width, clientHeight: height } = canvas;
  // A canvas with no measured size places every node at the origin. Defer
  // until layout has run rather than drawing a pile.
  if (width < 40 || height < 40) {
    requestAnimationFrame(() => draw(canvas, data, onSelect));
    return;
  }
  canvas.querySelectorAll('.gnode, svg').forEach((n) => n.remove());

  const at = (node) => ({ x: node.x * width, y: node.y * height });
  const byId = Object.fromEntries(data.nodes.map((n) => [n.id, n]));

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');

  // Two nodes can be joined by more than one edge: a scene both requires a
  // prop and has it appear in it. Drawn straight, those edges share a midpoint
  // and their labels stack illegibly, so parallel edges fan out.
  const lanes = {};
  for (const link of data.links) {
    const key = [link.source, link.target].sort().join('|');
    (lanes[key] = lanes[key] || []).push(link);
  }

  for (const parallel of Object.values(lanes)) {
    parallel.forEach((link, index) => {
      // Draw every edge in a lane from the same end. Parallel edges are
      // stored in opposite directions — a scene establishes a prop, and the
      // prop appears in the scene — so measuring t from each edge's own source
      // puts t=0.4 and t=0.6 on the same physical point.
      const [firstId, secondId] = [link.source, link.target].sort();
      const from = byId[firstId];
      const to = byId[secondId];
      if (!from || !to) return;
      const a = at(from);
      const b = at(to);

      // Fan around the straight line, and when a pair carries several edges
      // start off-centre so no edge sits exactly on the chord where its label
      // would collide with the node labels at either end.
      const lane = parallel.length === 1
        ? 0
        : (index - (parallel.length - 1) / 2) * 2;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const length = Math.hypot(dx, dy) || 1;
      const offset = lane * 14;
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
      const t = parallel.length === 1
        ? 0.5
        : Math.min(0.86, Math.max(0.14,
            0.5 + (index - (parallel.length - 1) / 2) * spread));
      const point = (p0, p1, p2) =>
        (1 - t) * (1 - t) * p0 + 2 * (1 - t) * t * p1 + t * t * p2;
      const cx = point(a.x, mx, b.x);
      const cy = point(a.y, my, b.y);

      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', `M ${a.x} ${a.y} Q ${mx} ${my} ${b.x} ${b.y}`);
      path.setAttribute('fill', 'none');
      path.setAttribute('stroke', link.removed ? 'var(--red)' : 'var(--bd2)');
      path.setAttribute('stroke-width', link.removed ? 1.2 : 1);
      if (link.removed) path.setAttribute('stroke-dasharray', '4 3');
      svg.appendChild(path);

      // The predicate is the content, so it is drawn horizontally rather than
      // rotated along the edge, on the curve's own midpoint.
      const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      label.setAttribute('x', cx);
      label.setAttribute('y', cy - 3);
      label.setAttribute('text-anchor', 'middle');
      if (link.removed) label.setAttribute('fill', 'var(--red)');
      label.textContent = link.predicate;
      svg.appendChild(label);
    });
  }
  canvas.appendChild(svg);

  for (const node of data.nodes) {
    const point = at(node);
    const element = document.createElement('div');
    element.className = `gnode ${node.kind}${node.removed ? ' removed' : ''}`;
    element.style.left = `${point.x}px`;
    element.style.top = `${point.y}px`;
    element.style.color = node.id === data.focus ? 'var(--ac)' : colourFor(node.entity_type);
    if (node.id === data.focus) element.classList.add('on');
    element.textContent = node.label;
    element.addEventListener('click', () => onSelect(node));
    canvas.appendChild(element);
  }

  nudgeLabelsOffNodes(canvas);

  const count = canvas.querySelector('#gcount');
  if (count) {
    count.textContent =
      `${data.nodes.length} nodes · ${data.links.length} edges` +
      (data.hidden_below_threshold
        ? ` · ${data.hidden_below_threshold} below threshold hidden` : '');
  }
}

/* An edge that passes under a node leaves its label on top of that node's
   text. Shifting the label perpendicular is not enough when the edge runs
   through the centre, so it is moved along the vertical until it is clear.
   Deterministic and bounded: same input, same nudge. */
function nudgeLabelsOffNodes(canvas) {
  const nodes = [...canvas.querySelectorAll('.gnode')].map((n) =>
    n.getBoundingClientRect());
  const hit = (a, b) => !(a.right < b.left || b.right < a.left ||
                          a.bottom < b.top || b.bottom < a.top);

  for (const label of canvas.querySelectorAll('svg text')) {
    for (let step = 1; step <= 6; step += 1) {
      const box = label.getBoundingClientRect();
      const clash = nodes.find((n) => hit(box, n));
      if (!clash) break;
      // Move away from the node's centre, so a label under a node goes down
      // and one above it goes up rather than crossing the node.
      const direction = box.top + box.height / 2 < clash.top + clash.height / 2
        ? -1 : 1;
      label.setAttribute(
        'y', Number(label.getAttribute('y')) + direction * 13);
    }
  }
}

/* Expanded view */
const canvas = document.getElementById('canvas');
if (canvas) {
  const detail = document.getElementById('detail');
  const state = { depth: 1, confidence: 0, removed: false, data: null };

  function departments() {
    const boxes = [...document.querySelectorAll('.dept')];
    const on = boxes.filter((b) => b.checked).map((b) => b.value);
    return on.length === boxes.length ? null : on.join(',');
  }

  async function refresh() {
    const params = new URLSearchParams({
      depth: state.depth,
      min_confidence: state.confidence,
      include_removed: state.removed,
    });
    const chosen = departments();
    if (chosen !== null) params.set('departments', chosen);

    state.data = await api(`/api/units/${canvas.dataset.unit}/graph?${params}`);
    draw(canvas, state.data, select);
    document.getElementById('fit-info').textContent =
      `${state.data.nodes.length} nodes`;
  }

  function select(node) {
    document.querySelectorAll('.gnode.on').forEach((n) => {
      if (n.textContent !== node.label) n.classList.remove('on');
    });
    const edges = state.data.links.filter(
      (l) => l.source === node.id || l.target === node.id);
    const byId = Object.fromEntries(state.data.nodes.map((n) => [n.id, n]));
    const rows = edges.map((l) => {
      const other = l.source === node.id ? byId[l.target] : byId[l.source];
      return `<div class="kv"><span class="mono tiny">${l.predicate}</span>
        <span>${other ? other.label : '?'} · ${l.confidence}</span></div>`;
    }).join('');
    detail.innerHTML = `
      <h3>${node.label}</h3>
      <div class="tiny muted" style="margin-bottom:12px">
        ${node.entity_type ? node.entity_type.replace(/_/g, ' ') : node.kind}</div>
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

  window.addEventListener('resize', () => state.data && draw(canvas, state.data, select));
  refresh();
}
