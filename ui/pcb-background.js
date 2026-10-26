// Mail-Scope welcome-screen circuit-board background · creator: Bálint Szabolcs
"use strict";

/**
 * Build the tilted, animated printed-circuit-board SVG of the welcome screen.
 *
 * The layout is generated from a fixed seed, so the board is identical on every
 * start. All styling lives in styles.css; per-pulse timing is set through the
 * CSSOM, which the Content-Security-Policy allows (no style attributes).
 */
const MailScopePcb = (() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const GREEN = "#4ADE80";
  const AMBER = "#F2B544";
  const PERIODS = [600, 1000, 1400, 2000];

  /**
   * Generate traces, vias, pins, chips, buses and pulses for the board.
   */
  function generateBoard() {
    let seed = 20261026;
    const rnd = () => {
      seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    const R = (a, b) => a + rnd() * (b - a);
    const r1 = (v) => Math.round(v * 10) / 10;
    const S2 = Math.SQRT2;

    // Route a 45° PCB trace: a straight stub, one diagonal, then a straight run.
    const route = (x1, y1, x2, y2, axis, stub) => {
      x2 = r1(x2);
      y2 = r1(y2);
      if (axis === "h") {
        const dir = Math.sign(x2 - x1) || 1;
        const sx = x1 + dir * stub;
        const rx = Math.abs(x2 - sx);
        const ry = Math.abs(y2 - y1);
        if (rx >= ry) {
          return { d: `M${x1} ${y1}H${sx}L${r1(sx + dir * ry)} ${y2}H${x2}`, len: stub + ry * S2 + rx - ry, x2, y2 };
        }
        const sy = Math.sign(y2 - y1);
        return { d: `M${x1} ${y1}H${sx}L${x2} ${r1(y1 + sy * rx)}V${y2}`, len: stub + rx * S2 + ry - rx, x2, y2 };
      }
      const dir = Math.sign(y2 - y1) || 1;
      const sy0 = y1 + dir * stub;
      const ry = Math.abs(y2 - sy0);
      const rx = Math.abs(x2 - x1);
      if (ry >= rx) {
        return { d: `M${x1} ${y1}V${sy0}L${x2} ${r1(sy0 + dir * rx)}V${y2}`, len: stub + rx * S2 + ry - rx, x2, y2 };
      }
      const sx = Math.sign(x2 - x1);
      return { d: `M${x1} ${y1}V${sy0}L${r1(x1 + sx * ry)} ${y2}H${x2}`, len: stub + ry * S2 + rx - ry, x2, y2 };
    };

    const traces = [];
    const pulses = [];
    const vias = [];
    const pins = [];
    const addPulse = (d, len, color, inbound) => {
      const period = PERIODS.find((p) => p >= len + 220) || 2000;
      const speed = inbound ? R(230, 320) : R(130, 230);
      const duration = period / speed;
      pulses.push({
        d,
        color,
        period,
        duration: `${duration.toFixed(2)}s`,
        delay: `${(-R(0, duration)).toFixed(2)}s`,
      });
    };
    const add = (r, options) => {
      traces.push(r.d);
      if (options.via) vias.push({ x: r.x2, y: r.y2 });
      const pulse = options.inbound || rnd() < (options.chance ?? 0.45);
      if (pulse) {
        addPulse(r.d, r.len, options.color || (rnd() < 0.7 ? GREEN : AMBER), Boolean(options.inbound));
      }
    };

    const C = { x: 520, y: 330, w: 160, h: 140 };
    const pinY = (i) => 342 + i * 12;
    const pinX = (i) => 534 + i * 12;
    for (let i = 0; i < 11; i += 1) {
      pins.push({ x: C.x - 6, y: pinY(i) - 2, w: 6, h: 4 });
      pins.push({ x: C.x + C.w, y: pinY(i) - 2, w: 6, h: 4 });
    }
    for (let i = 0; i < 12; i += 1) {
      pins.push({ x: pinX(i) - 2, y: C.y - 6, w: 4, h: 6 });
      pins.push({ x: pinX(i) - 2, y: C.y + C.h, w: 4, h: 6 });
    }

    const sources = [
      { x: 150, y: 200, w: 150, h: 100, label: "PST", sub: "OUTLOOK", left: true, top: true, pins: [0, 1, 2, 3, 4], color: GREEN },
      { x: 150, y: 520, w: 150, h: 100, label: "MBOX", sub: "THUNDERBIRD", left: true, top: false, pins: [6, 7, 8, 9, 10], color: AMBER },
      { x: 900, y: 200, w: 150, h: 100, label: "OST", sub: "EXCHANGE", left: false, top: true, pins: [0, 1, 2, 3, 4], color: GREEN },
      { x: 900, y: 520, w: 150, h: 100, label: "EML", sub: "MAIL EXTRACTOR", left: false, top: false, pins: [6, 7, 8, 9, 10], color: AMBER },
    ];
    const chips = [];
    sources.forEach((s) => {
      const innerX = s.left ? s.x + s.w : s.x;
      const outerX = s.left ? s.x : s.x + s.w;
      const edgeY = s.top ? s.y : s.y + s.h;
      for (let k = 0; k < 5; k += 1) {
        const y = s.y + 18 + k * 16;
        pins.push({ x: innerX - (s.left ? 0 : 6), y: y - 2, w: 6, h: 4 });
        pins.push({ x: outerX - (s.left ? 6 : 0), y: y - 2, w: 6, h: 4 });
        add(route(s.left ? C.x : C.x + C.w, pinY(s.pins[k]), innerX, y, "h", 22), { inbound: true, color: s.color });
        add(route(outerX, y, s.left ? R(-30, -10) : R(1210, 1230), s.y - 70 + k * 60, "h", 14), { chance: 0.4 });
      }
      for (let k = 0; k < 8; k += 1) {
        const x = s.x + 15 + k * 17;
        pins.push({ x: x - 2, y: s.top ? s.y - 6 : s.y + s.h, w: 4, h: 6 });
        pins.push({ x: x - 2, y: s.top ? s.y + s.h : s.y - 6, w: 4, h: 6 });
        add(route(x, edgeY, s.x - 50 + k * 36, s.top ? R(-30, -10) : R(810, 830), "v", 14), { chance: 0.4 });
      }
      chips.push(s);
    });

    for (let i = 0; i < 12; i += 1) {
      add(route(pinX(i), C.y, 380 + i * 40 + R(-5, 5), R(40, 120), "v", 18), { via: true, chance: 0.5 });
      add(route(pinX(i), C.y + C.h, 380 + i * 40 + R(-5, 5), R(680, 760), "v", 18), { via: true, chance: 0.5 });
    }
    add(route(C.x, pinY(5), 470, pinY(5), "h", 10), { via: true, chance: 0.3 });
    add(route(C.x + C.w, pinY(5), 730, pinY(5), "h", 10), { via: true, chance: 0.3 });

    const buses = [];
    for (let k = 0; k < 6; k += 1) {
      const y = 64 + k * 8;
      const top = `M-40 ${y}H${300 - k * 8}L${340 - k * 8} ${y + 40}H1240`;
      buses.push(top);
      const y2 = 728 + k * 8;
      const bottom = `M-40 ${y2}H${860 + k * 8}L${900 + k * 8} ${y2 - 40}H1240`;
      buses.push(bottom);
      if (k === 1 || k === 4) {
        addPulse(top, 1400, GREEN, false);
        addPulse(bottom, 1400, AMBER, false);
      }
    }
    return { traces, pulses, vias, pins, chips, buses, center: C };
  }

  /**
   * Create one SVG element with presentation attributes.
   */
  function svg(name, attributes = {}, className = "") {
    const node = document.createElementNS(SVG_NS, name);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
    if (className) node.setAttribute("class", className);
    return node;
  }

  /**
   * Group many paths with one shared presentation.
   */
  function pathGroup(paths, attributes) {
    const group = svg("g", { fill: "none", ...attributes });
    paths.forEach((d) => group.append(svg("path", { d })));
    return group;
  }

  /**
   * Draw the Mail-Scope magnifier-and-envelope mark at the board centre.
   */
  function logoMark() {
    const group = svg("g", { transform: "translate(568 352)" });
    group.append(
      svg("circle", { cx: 28, cy: 28, r: 19, fill: "none", stroke: GREEN, "stroke-width": 3 }),
      svg("path", { d: "M28 4v9M28 43v9M4 28h9M43 28h9", stroke: GREEN, "stroke-width": 3, "stroke-linecap": "round" }),
      svg("path", { d: "M42 42L57 57", stroke: GREEN, "stroke-width": 5.5, "stroke-linecap": "round" }),
      svg("rect", { x: 18.5, y: 21.5, width: 19, height: 13, rx: 1.8, fill: "none", stroke: "#DCE7E0", "stroke-width": 2.4 }),
      svg("path", { d: "M19 23L28 29.5L37 23", fill: "none", stroke: "#DCE7E0", "stroke-width": 2.4, "stroke-linejoin": "round" }),
    );
    return group;
  }

  /**
   * Build the complete board SVG, layered from the substrate up to the pulses.
   */
  function buildSvg() {
    const board = generateBoard();
    const root = svg("svg", { viewBox: "0 0 1200 800", width: 2400, height: 1600, focusable: "false" }, "pcb-svg");

    const defs = svg("defs");
    const pattern = svg("pattern", { id: "msDots", width: 10, height: 10, patternUnits: "userSpaceOnUse" });
    pattern.append(svg("circle", { cx: 5, cy: 5, r: 0.7, fill: "#10251A" }));
    const filter = svg("filter", { id: "msBlur", x: "-60%", y: "-60%", width: "220%", height: "220%" });
    filter.append(svg("feGaussianBlur", { stdDeviation: 14 }));
    defs.append(pattern, filter);
    root.append(defs);

    // The substrate extends far beyond the traces, so that the tilted plane's
    // far edge stays out of view even on 2560 px wide screens.
    root.append(
      svg("rect", { x: -1200, y: -1400, width: 3600, height: 2900, fill: "#06100B" }),
      svg("rect", { x: -1200, y: -1400, width: 3600, height: 2900, fill: "url(#msDots)" }),
      pathGroup(board.buses, { stroke: "#0E2219", "stroke-width": 1.6 }),
      pathGroup(board.traces, { stroke: "#020604", "stroke-width": 3.2, "stroke-linejoin": "round", transform: "translate(1.4 2.4)" }),
      pathGroup(board.traces, { stroke: "#1A3E2D", "stroke-width": 2.4, "stroke-linejoin": "round" }),
    );

    const vias = svg("g");
    board.vias.forEach((via) => vias.append(
      svg("circle", { cx: via.x, cy: via.y, r: 4.2, fill: "#06100B", stroke: "#3A5E4A", "stroke-width": 1.8 }),
    ));
    const pins = svg("g", { fill: "#2C4A3A" });
    board.pins.forEach((pin) => pins.append(svg("rect", { x: pin.x, y: pin.y, width: pin.w, height: pin.h })));
    root.append(vias, pins);

    board.chips.forEach((chip) => {
      const group = svg("g");
      const centreX = chip.x + chip.w / 2;
      group.append(
        svg("rect", { x: chip.x - 14, y: chip.y - 14, width: chip.w + 28, height: chip.h + 28, fill: "none", stroke: "#1C3328", "stroke-width": 1, "stroke-dasharray": "4 4" }),
        svg("rect", { x: chip.x + 4, y: chip.y + 7, width: chip.w, height: chip.h, rx: 4, fill: "#010302" }),
        svg("rect", { x: chip.x, y: chip.y, width: chip.w, height: chip.h, rx: 4, fill: "#0B1611", stroke: "#2A4636", "stroke-width": 1.5 }),
        svg("circle", { cx: chip.x + 12, cy: chip.y + 12, r: 3, fill: "#2A4636" }),
      );
      const label = svg("text", { x: centreX, y: chip.y + chip.h / 2 + 4, "text-anchor": "middle", "font-size": 22, "font-weight": 500, fill: "#7FA08C", "letter-spacing": 2 }, "pcb-text");
      label.textContent = chip.label;
      const sub = svg("text", { x: centreX, y: chip.y + chip.h / 2 + 24, "text-anchor": "middle", "font-size": 10, fill: "#4C6A5A", "letter-spacing": 1 }, "pcb-text");
      sub.textContent = chip.sub;
      group.append(label, sub);
      root.append(group);
    });

    const C = board.center;
    root.append(
      svg("rect", { x: 510, y: 320, width: 180, height: 160, rx: 10, fill: GREEN, filter: "url(#msBlur)" }, "ms-breath"),
      svg("rect", { x: C.x + 5, y: C.y + 8, width: C.w, height: C.h, rx: 5, fill: "#010302" }),
      svg("rect", { x: C.x, y: C.y, width: C.w, height: C.h, rx: 5, fill: "#0A1510", stroke: "#3E8A5C", "stroke-width": 2 }),
      logoMark(),
    );
    const name = svg("text", { x: 600, y: 452, "text-anchor": "middle", "font-size": 11, fill: "#7FA08C", "letter-spacing": 3 }, "pcb-text");
    name.textContent = "MAIL-SCOPE";
    root.append(name);

    // Pulses: a wide faint halo under a thin bright core; no blur filters here.
    [["8", "ms-pulse-halo"], ["2.6", "ms-pulse-core"]].forEach(([width, layer]) => {
      const group = svg("g");
      board.pulses.forEach((pulse) => {
        const path = svg("path", { d: pulse.d, stroke: pulse.color, "stroke-width": width }, `ms-pulse ${layer} f${pulse.period / 100}`);
        path.style.animationDuration = pulse.duration;
        path.style.animationDelay = pulse.delay;
        group.append(path);
      });
      root.append(group);
    });
    return root;
  }

  let mounted = null;
  const onVisibility = () => {
    if (mounted) mounted.classList.toggle("pcb-paused", document.hidden);
  };

  return {
    /**
     * Render the board into a container; repeated calls keep one instance.
     */
    mount(container) {
      if (mounted && mounted.parentElement === container) {
        mounted.classList.toggle("pcb-paused", document.hidden);
        return;
      }
      this.unmount();
      const stage = document.createElement("div");
      stage.className = "pcb-stage";
      const plane = document.createElement("div");
      plane.className = "pcb-plane";
      plane.append(buildSvg());
      stage.append(plane);
      container.append(stage);
      mounted = stage;
      onVisibility();
      document.addEventListener("visibilitychange", onVisibility);
    },

    /**
     * Stop and remove the board, so no animation runs behind the workspace.
     */
    unmount() {
      document.removeEventListener("visibilitychange", onVisibility);
      if (mounted) {
        mounted.classList.add("pcb-paused");
        mounted.remove();
        mounted = null;
      }
    },

    /** Count animated paths; used to keep the board within its budget. */
    animatedPathCount() {
      return mounted ? mounted.querySelectorAll(".ms-pulse, .ms-breath").length : 0;
    },
  };
})();

window.MailScopePcb = MailScopePcb;
