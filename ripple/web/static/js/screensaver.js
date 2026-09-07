/* The lake screensaver.
   A full-tab idle takeover: the viewport becomes a dark lake, stones drop,
   and as each wavefront passes a point a production entity surfaces there.
   It is decorative. It reads no script, writes nothing, and calls no model.

   Two things it must not do: keep a backgrounded tab burning frames, and
   interrupt work. Extraction and agent turns run behind it untouched, and
   the idle timer is suspended whenever the page is holding something the
   user has not resolved. */

const SAVER = {
  speed: 205,          // px/s, the wavefront
  rings: 7,            // trailing rings per drop
  ringGap: 46,         // px between rings
  entityLife: 2600,    // ms an entity is on screen
  slot: [190, 76],     // px reserved per entity, so none ever overlap
  maxNodes: 90,        // hard cap; a throttled tab would otherwise leak DOM
  autoMin: 3400,
  autoMax: 6600,
};

// The department hues, as the graph uses them. Nothing on the lake is data,
// so a chip draws one of these at random rather than the one its type would
// have; with colour off it takes the water's own grey instead. The square
// behind each glyph is mixed from the hue rather than given its own value,
// so a token change carries here without a second edit.
const SAVER_HUES = [
  '--cast', '--transport', '--props', '--set', '--wardrobe', '--sound',
  '--ac', '--red',
];

/* Three looks. Dark is the lake with the lights down, light is the same
   lake on warm paper, and water is the grey-blue one. The rings are canvas
   rather than CSS, so their colours live here and the chrome's live in the
   stylesheet. */
const SAVER_WATER = {
  dark: {
    lead: '111,203,224',
    glow: '47,168,199',
    trail: '163,190,198',
    splash: '214,240,246',
    pebble: ['#3A4046', '#6E7A82', '#565F67'],
  },
  light: {
    lead: '27,125,151',
    glow: '47,168,199',
    trail: '124,119,110',
    splash: '92,88,80',
    pebble: ['#C9C4BC', '#5C5850', '#EDE9E3'],
  },
  water: {
    lead: '23,117,142',
    glow: '31,140,168',
    trail: '250,253,255',
    splash: '255,255,255',
    pebble: ['#8C9AA4', '#2A3238', '#B4C0C8'],
  },
};

// 24-unit Lucide-shaped glyphs, stroked not filled. Inlined rather than
// loaded: Ripple runs offline and pulls no asset from a CDN.
const SAVER_GLYPH = {
  cast: '<circle cx="12" cy="8" r="4"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>',
  prop: '<path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5z"/><path d="M3 7.5 12 12l9-4.5M12 12v9"/>',
  wardrobe: '<path d="M8.5 3 12 6l3.5-3L21 6.5l-2.5 3.5V21h-13V10L3 6.5z"/>',
  location: '<path d="M12 21c4-4.5 6-7.6 6-10a6 6 0 1 0-12 0c0 2.4 2 5.5 6 10z"/>'
    + '<circle cx="12" cy="11" r="2.2"/>',
  set_design: '<path d="M4 20v-6a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v6"/>'
    + '<path d="M6 11V6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v5M4 17h16"/>',
  makeup: '<path d="M14 4 20 10 10 20H4v-6z"/><path d="M12 6 18 12"/>',
  transportation: '<path d="M4 16v-3l2-5h12l2 5v3"/><path d="M3 16h18v3h-3v-3H6v3H3z"/>'
    + '<circle cx="7.5" cy="16" r="1.4"/><circle cx="16.5" cy="16" r="1.4"/>',
  vfx: '<path d="M12 3l1.8 4.7L18.5 9l-4.7 1.8L12 15.5l-1.8-4.7L5.5 9l4.7-1.3z"/>'
    + '<path d="M18 16l.9 2.1L21 19l-2.1.9L18 22l-.9-2.1L15 19l2.1-.9z"/>',
  stunt: '<path d="M12 3c3 3.5 5 6 5 9a5 5 0 0 1-10 0c0-1.6.8-3 2-4.5.4 1.4 1.1 2 2 2 '
    + '0-2.4.3-4.4 1-6.5z"/>',
  sound: '<path d="M5 9v6M9 6.5v11M12 4v16M15 6.5v11M19 9v6"/>',
};

/* 40 entities: the people and the things a production has to move when a
   line changes. A static list, drawn from a shuffled bag so nothing repeats
   until the bag empties. */
const SAVER_ENTITIES = [
  ['Mara', 'cast'], ['Ray Voss', 'cast'], ['Det. Halsey', 'cast'],
  ['Luz', 'cast'], ['Tommy Breen', 'cast'], ['Esther Kim', 'cast'],
  ['The courier', 'cast'], ['Young Mara', 'cast'], ['Bar patrons', 'cast'],
  ['Wool coat', 'wardrobe'], ['Rain slicker', 'wardrobe'],
  ['Uniform blues', 'wardrobe'], ['Wedding ring', 'wardrobe'],
  ['Harbour dock', 'location'], ['Motel 9', 'location'],
  ['Precinct lobby', 'location'], ['Lock-up garage', 'location'],
  ['Blue sedan', 'transportation'], ['Grip truck', 'transportation'],
  ['Night bus', 'transportation'], ['Patrol car', 'transportation'],
  ['Motel key', 'prop'], ['Police scanner', 'prop'], ['Brass padlock', 'prop'],
  ['Duffel bag', 'prop'], ['Manila folder', 'prop'], ['Burner phone', 'prop'],
  ['Waiting-room chairs', 'set_design'], ['Neon sign', 'set_design'],
  ['Concrete floor', 'set_design'], ['Set dressing', 'set_design'],
  ['Rain machine', 'vfx'], ['Muzzle flash', 'vfx'], ['Arc weld', 'vfx'],
  ['Stunt burn', 'stunt'], ['Chase route', 'stunt'], ['Stair fall', 'stunt'],
  ['Split lip', 'makeup'], ['Night shoot', 'sound'], ['Foghorn', 'sound'],
];

/* A 26px pebble, so the cursor over the lake is the thing being thrown.
   Hotspot centred, with the system pointer as the fallback. */
function saverCursor(theme) {
  const [fill, stroke, sheen] = SAVER_WATER[theme].pebble;
  return 'data:image/svg+xml;utf8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26">'
    + `<ellipse cx="13" cy="13" rx="8.5" ry="7" fill="${fill}" stroke="${stroke}"`
    + ' stroke-width="1.2"/><ellipse cx="10.5" cy="11" rx="3" ry="2.1"'
    + ` fill="${sheen}"/></svg>`);
}

function saverShuffled() {
  const bag = SAVER_ENTITIES.slice();
  for (let i = bag.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [bag[i], bag[j]] = [bag[j], bag[i]];
  }
  return bag;
}

/* A measurement below 2px means the element is not laid out yet. Accepting
   it leaves a 0x0 backing store and the whole effect silently does nothing,
   so the last good size is kept until a real one arrives. */
function saverSize(canvas, last) {
  const box = canvas.getBoundingClientRect();
  const width = box.width || window.innerWidth
    || document.documentElement.clientWidth || 0;
  const height = box.height || window.innerHeight
    || document.documentElement.clientHeight || 0;
  return {
    width: width >= 2 ? width : last.width,
    height: height >= 2 ? height : last.height,
  };
}

class Screensaver {
  constructor(settings) {
    this.settings = settings;
    this.open = false;
    this.root = null;
    this.bag = [];
    this.drops = [];
    this.nodes = [];
    this.taken = [];
    this.lastThrow = 0;
    this.lastFrame = 0;
    this.reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  }

  nextEntity() {
    if (!this.bag.length) this.bag = saverShuffled();
    return this.bag.pop();
  }

  /* The settings were read when the page loaded, and a page open in another
     tab while they changed still holds the old ones. Re-reading them here
     costs one request per opening and means the lake always runs on what
     Settings currently says. */
  refresh() {
    fetch('/api/settings/screensaver')
      .then((response) => (response.ok ? response.json() : null))
      .then((settings) => { if (settings) Object.assign(this.settings, settings); })
      .catch(() => { /* Keep the settings in hand. */ });
  }

  show() {
    if (this.open || !this.settings.enabled) return;
    this.refresh();
    this.open = true;
    this.returnFocus = document.activeElement;
    this.lastThrow = 0;
    this.drops = [];
    this.nodes = [];
    this.taken = [];

    const root = document.createElement('div');
    this.look = SAVER_WATER[this.settings.theme] ? this.settings.theme : 'dark';
    root.className = `saver saver-${this.look}`;
    root.tabIndex = -1;
    root.setAttribute('aria-hidden', 'true');
    root.innerHTML = `
      <canvas class="saver-lake"></canvas>
      <div class="saver-entities"></div>
      <div class="saver-mark">
        <span class="saver-rings"><i></i><i></i><i></i></span>
        <span class="saver-word">Ripple</span>
      </div>
      <div class="saver-hint">
        <span>Click to drop a stone</span><span><kbd>Esc</kbd> Exit</span>
      </div>`;
    root.style.cursor = `url("${saverCursor(this.look)}") 13 13, default`;
    document.body.appendChild(root);

    // The overlay itself is hidden from the accessibility tree, so the one
    // thing a screen reader needs to hear lives outside it.
    const spoken = document.createElement('div');
    spoken.className = 'saver-said';
    spoken.setAttribute('role', 'status');
    spoken.textContent = 'Screensaver. Press any key to return.';
    document.body.appendChild(spoken);
    this.spoken = spoken;

    this.root = root;
    this.canvas = root.querySelector('.saver-lake');
    this.layer = root.querySelector('.saver-entities');
    this.context = this.canvas.getContext('2d');
    this.box = { width: 0, height: 0 };
    this.measure();

    this.observer = new ResizeObserver(() => this.measure());
    this.observer.observe(this.canvas);
    this.onResize = () => this.measure();
    window.addEventListener('resize', this.onResize);
    window.addEventListener('load', this.onResize);

    this.onLeave = (event) => this.maybeLeave(event);
    window.addEventListener('keydown', this.onLeave, true);
    root.focus({ preventScroll: true });
    ripple.trace('screensaver.opened', { reduced: this.reduced.matches });

    if (this.reduced.matches) {
      this.paintStill();
      return;
    }
    root.addEventListener('pointerdown', (event) =>
      this.throwStone(event.clientX, event.clientY), true);
    this.start = performance.now();
    this.firstDrop = false;
    this.nextAuto = this.start + 700;
    this.onVisibility = () => (document.hidden ? this.pause() : this.resume());
    document.addEventListener('visibilitychange', this.onVisibility);
    window.addEventListener('blur', () => this.pause());
    window.addEventListener('focus', () => this.resume());
    this.resume();
  }

  /* Four ways out, and no others: Escape, Space, Enter, or the shortcut that
     opened it. A click throws a stone instead, and every other key is left
     alone, so leaning on the keyboard or brushing the mouse in a screening
     room does not take the lake down mid-drop. */
  maybeLeave(event) {
    if (!this.open) return;
    const mods = ['ctrl', 'alt', 'shift', 'meta']
      .filter((one) => event[`${one}Key`]);
    const chord = [...mods, event.code].join('+');
    const way = ['Escape', 'Space', 'Enter', 'NumpadEnter'].includes(event.code)
      && !mods.length;
    if (!way && chord !== this.settings.shortcut) return;
    event.preventDefault();
    event.stopPropagation();
    this.hide();
  }

  hide() {
    if (!this.open) return;
    this.open = false;
    this.pause();
    if (this.observer) this.observer.disconnect();
    window.removeEventListener('resize', this.onResize);
    window.removeEventListener('load', this.onResize);
    window.removeEventListener('keydown', this.onLeave, true);
    if (this.onVisibility) {
      document.removeEventListener('visibilitychange', this.onVisibility);
    }
    this.nodes.forEach((node) => clearTimeout(node.timer));
    this.nodes = [];
    if (this.root) this.root.remove();
    if (this.spoken) this.spoken.remove();
    this.root = null;
    ripple.trace('screensaver.closed', {});
    if (this.returnFocus && this.returnFocus.focus) {
      this.returnFocus.focus({ preventScroll: true });
    }
  }

  measure() {
    if (!this.canvas) return;
    const size = saverSize(this.canvas, this.box);
    if (size.width < 2 || size.height < 2) return;
    this.box = size;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.width = Math.round(size.width * ratio);
    this.canvas.height = Math.round(size.height * ratio);
    this.context.setTransform(ratio, 0, 0, ratio, 0, 0);
    if (this.reduced.matches) this.paintStill();
  }

  /* Under reduced motion the lake holds still: the vignette, the wordmark,
     the hint, and one ring set that never moves. */
  paintStill() {
    const { width, height } = this.box;
    if (width < 2) return;
    const water = SAVER_WATER[this.look || 'dark'];
    const context = this.context;
    context.clearRect(0, 0, width, height);
    const x = width / 2;
    const y = height / 2;
    for (let k = 0; k < 4; k += 1) {
      const r = 120 + k * SAVER.ringGap * 1.6;
      context.beginPath();
      context.arc(x, y, r, 0, Math.PI * 2);
      context.lineWidth = k === 0 ? 1.5 : 1.1;
      context.strokeStyle =
        `rgba(${water.lead},${0.3 * Math.exp(-0.3 * k)})`;
      context.stroke();
    }
  }

  pause() {
    if (this.frame) cancelAnimationFrame(this.frame);
    if (this.watchdog) clearInterval(this.watchdog);
    this.frame = null;
    this.watchdog = null;
  }

  /* rAF plus a watchdog. A backgrounded or throttled tab can stop ticking
     rAF entirely, and without the interval the lake freezes mid-ripple and
     stays frozen when the tab comes back. */
  resume() {
    if (!this.open || this.reduced.matches || this.frame) return;
    // Opened into a tab that is already in the background: the loop waits
    // for the tab to come forward rather than drawing frames nobody sees.
    if (document.hidden) return;
    const step = () => {
      this.frame = requestAnimationFrame(step);
      this.draw();
    };
    this.frame = requestAnimationFrame(step);
    this.watchdog = setInterval(() => {
      if (performance.now() - this.lastFrame > 90) this.draw();
    }, 33);
  }

  /* A stone thrown by hand, gated by the configured minimum interval. A
     throw inside the window is ignored outright: no drop, no queue, no
     acknowledgement, and the timer does not reset, so holding the pointer
     down cannot crowd the lake. The automatic schedule is untouched by it. */
  throwStone(x, y) {
    const now = performance.now();
    const gate = this.settings.throttle_seconds * 1000;
    if (this.lastThrow && now - this.lastThrow < gate) return;
    this.lastThrow = now;
    this.drop(x, y, now);
  }

  drop(x, y, now) {
    const { width, height } = this.box;
    const corner = Math.max(
      Math.hypot(x, y), Math.hypot(width - x, y),
      Math.hypot(x, height - y), Math.hypot(width - x, height - y),
    );
    const maxR = corner + 80;
    this.drops.push({
      x, y, at: now, maxR, life: (maxR / SAVER.speed) * 1000 + 1400,
    });
    this.emit(x, y, maxR, now);
  }

  /* Emission is directional: a drop in a corner sends its entities inward
     across the open water, a drop in the middle sends them everywhere. */
  emit(x, y, maxR, now) {
    const { width, height } = this.box;
    const centreX = width / 2;
    const centreY = height / 2;
    const toCentre = Math.atan2(centreY - y, centreX - x);
    const offCentre = Math.min(1, Math.hypot(centreX - x, centreY - y)
      / Math.hypot(centreX, centreY));
    const spread = (1 - offCentre * 0.72) * Math.PI + 0.55;
    const wanted = 5 + Math.floor(Math.random() * 4);

    for (let i = 0; i < wanted; i += 1) {
      let placed = null;
      for (let attempt = 0; attempt < 14 && !placed; attempt += 1) {
        const angle = toCentre
          + (Math.random() + Math.random() - 1) * spread;
        const dist = 140 + Math.random() * (maxR * 0.78 - 140);
        if (dist <= 140) continue;
        const at = {
          x: x + Math.cos(angle) * dist,
          y: y + Math.sin(angle) * dist,
          angle,
          dist,
        };
        if (this.free(at)) placed = at;
      }
      if (placed) this.reveal(placed, now);
    }
  }

  /* No entity ever overlaps another, the wordmark, or the hint strip. A
     candidate that cannot be placed is abandoned rather than nudged, so the
     realised count is lower near the edges. That is correct. */
  free(at) {
    const [slotW, slotH] = SAVER.slot;
    const { width, height } = this.box;
    const half = { x: slotW / 2, y: slotH / 2 };
    if (at.x - half.x < 24 || at.x + half.x > width - 24) return false;
    if (at.y - half.y < 24 || at.y + half.y > height - 70) return false;

    const hits = (box, padX, padY) =>
      Math.abs(at.x - (box.left + box.width / 2)) < half.x + box.width / 2 + padX
      && Math.abs(at.y - (box.top + box.height / 2)) < half.y + box.height / 2 + padY;

    const mark = this.root.querySelector('.saver-mark').getBoundingClientRect();
    if (hits(mark, 44, 34)) return false;
    const hint = this.root.querySelector('.saver-hint').getBoundingClientRect();
    if (hits(hint, 0, 0)) return false;

    return !this.taken.some((slot) =>
      Math.abs(at.x - slot.x) < slotW && Math.abs(at.y - slot.y) < slotH);
  }

  /* The reveal is tied to the wavefront, not to a clock: an entity surfaces
     when the leading ring reaches it, and never before. */
  reveal(at, now) {
    this.taken.push({ x: at.x, y: at.y });
    const arrival = (at.dist / SAVER.speed) * 1000;
    const [name, type] = this.nextEntity();
    const hue = this.settings.colour === 'none'
      ? 'var(--saver-plain)'
      : `var(${SAVER_HUES[Math.floor(Math.random() * SAVER_HUES.length)]})`;

    setTimeout(() => {
      if (!this.open || !this.layer) return;
      const node = document.createElement('div');
      node.className = 'saver-entity';
      node.style.left = `${at.x}px`;
      node.style.top = `${at.y}px`;
      node.style.setProperty('--dx', `${Math.cos(at.angle) * 7}px`);
      node.style.setProperty('--dy', `${Math.sin(at.angle) * 7}px`);
      node.style.setProperty('--hue', hue);
      node.innerHTML =
        `<span class="saver-chip"><svg viewBox="0 0 24 24" fill="none"
           stroke="currentColor" stroke-width="1.5" stroke-linecap="round"
           stroke-linejoin="round" aria-hidden="true">${
          SAVER_GLYPH[type] || SAVER_GLYPH.prop}</svg></span>
         <span class="saver-name">${esc(name)}</span>`;
      this.layer.appendChild(node);

      // Two removals, because one is not enough: the loop's sweep stops in a
      // backgrounded tab, and a node left behind is a leak.
      const entry = { node, timer: setTimeout(() => {
        node.remove();
        this.nodes = this.nodes.filter((one) => one !== entry);
        this.taken = this.taken.filter((slot) =>
          slot.x !== at.x || slot.y !== at.y);
      }, SAVER.entityLife + 800) };
      this.nodes.push(entry);
      while (this.nodes.length > SAVER.maxNodes) {
        const oldest = this.nodes.shift();
        clearTimeout(oldest.timer);
        oldest.node.remove();
      }
    }, arrival + Math.max(0, now - performance.now()));
  }

  draw() {
    const now = performance.now();
    // rAF and the watchdog can both land in the same instant; drawing twice
    // costs a frame and shows nothing new.
    if (now - this.lastFrame < 11) return;
    this.lastFrame = now;
    const { width, height } = this.box;
    if (width < 2 || !this.context) return;

    if (now >= this.nextAuto) {
      const inset = 0.16;
      const x = this.firstDrop
        ? width * (inset + Math.random() * (1 - inset * 2))
        : width * 0.5;
      const y = this.firstDrop
        ? height * (inset + Math.random() * (1 - inset * 2))
        : height * 0.32;
      this.firstDrop = true;
      this.drop(x, y, now);
      this.nextAuto = now + SAVER.autoMin
        + Math.random() * (SAVER.autoMax - SAVER.autoMin);
    }

    const context = this.context;
    const water = SAVER_WATER[this.look || 'dark'];
    context.clearRect(0, 0, width, height);
    this.drops = this.drops.filter((drop) => now - drop.at < drop.life);

    for (const drop of this.drops) {
      const age = now - drop.at;
      const r = (age / 1000) * SAVER.speed;

      for (let k = 0; k < SAVER.rings; k += 1) {
        const rk = r - SAVER.ringGap * k;
        if (rk <= 0) continue;
        const damp = Math.exp(-rk / 720) * Math.exp(-0.3 * k)
          * (1 - Math.min(1, rk / drop.maxR) * 0.45);
        if (damp < 0.012) continue;
        context.beginPath();
        context.arc(drop.x, drop.y, rk, 0, Math.PI * 2);
        if (k === 0) {
          context.lineWidth = 1.5;
          context.strokeStyle = `rgba(${water.lead},${damp * 0.95})`;
          context.stroke();
          // A second, quieter ring just behind the front, so the leading
          // edge reads as water rather than as a drawn circle.
          if (rk > 3.5) {
            context.beginPath();
            context.arc(drop.x, drop.y, rk - 3.5, 0, Math.PI * 2);
            context.lineWidth = 1;
            context.strokeStyle = `rgba(${water.glow},${damp * 0.34})`;
            context.stroke();
          }
        } else {
          context.lineWidth = 1.1;
          context.strokeStyle = k < 2
            ? `rgba(${water.lead},${damp * 0.5})`
            : `rgba(${water.trail},${damp * 0.5})`;
          context.stroke();
        }
      }

      // The stone breaking the surface.
      if (age < 500) {
        const f = 1 - age / 500;
        context.beginPath();
        context.arc(drop.x, drop.y, 2 + 3 * f, 0, Math.PI * 2);
        context.fillStyle = `rgba(${water.splash},${0.72 * f})`;
        context.fill();
      }
    }
  }
}

/* Wiring: the idle timer, the shortcut, and the conditions that suspend
   both. Nothing here runs until the settings arrive, and a settings request
   that fails leaves the page exactly as it was. */
function setUpScreensaver(settings) {
  const saver = new Screensaver(settings);
  window.ripple.screensaver = saver;
  let idleAt = Date.now();

  // What the page is holding. Taking the tab over mid-dialog, mid-turn, or
  // over unsaved text would lose the thread the user is in the middle of.
  const HOLDING = [
    '.confirm-veil',            // a confirmation waiting on an answer
    '.overlay:not(.hide)',      // the ripple preview, awaiting Accept
    '.keys-box',                // the shortcuts sheet
    '.u.edited',                // a line edited and not yet applied
    '.chip.running',            // an agent turn mid-flight
    '.build-progress',          // an import or extraction running
  ].join(',');

  function busy() {
    return document.querySelector(HOLDING) !== null;
  }

  // A change saved in Settings reaches the lake without a reload, in this
  // page and in the app's other tabs.
  ripple.onSetting('screensaver', (saved) => Object.assign(settings, saved));

  ['keydown', 'pointermove', 'pointerdown', 'wheel', 'scroll'].forEach((name) =>
    window.addEventListener(name, () => { idleAt = Date.now(); },
      { passive: true, capture: true }));

  setInterval(() => {
    if (saver.open || !settings.enabled) return;
    if (settings.idle_minutes === 'never') return;
    if (busy()) { idleAt = Date.now(); return; }
    const after = Number(settings.idle_minutes) * 60000;
    if (Date.now() - idleAt >= after) saver.show();
  }, 1000);

  window.addEventListener('keydown', (event) => {
    if (!settings.enabled || saver.open) return;
    const mods = ['ctrl', 'alt', 'shift', 'meta']
      .filter((one) => event[`${one}Key`]);
    if (`${mods.join('+')}+${event.code}` !== settings.shortcut) return;
    event.preventDefault();
    saver.show();
  }, true);
}

document.addEventListener('DOMContentLoaded', () => {
  fetch('/api/settings/screensaver')
    .then((response) => (response.ok ? response.json() : null))
    .then((settings) => { if (settings) setUpScreensaver(settings); })
    .catch(() => { /* Decorative. A page without it is a working page. */ });
});
