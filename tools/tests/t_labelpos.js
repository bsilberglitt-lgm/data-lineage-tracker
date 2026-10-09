// Where a data flow's label sits on the diagram.
//
// There is no browser in the assistant's sandbox, so this reads the geometry
// functions straight out of index.html and checks the arithmetic. What it pins
// down is the bug Ben reported: on a SHORT flow pointing UPWARD the label box
// was drawn over its own arrowhead, hiding the only indication of direction.
//
// The rule being enforced: the label box, which occupies [anchor-23, anchor-7],
// must clear the arrowhead, which reaches from the tip back to tip+10. Every
// other geometry must keep exactly the midpoint it had before — Ben was
// explicit that the other cases already read correctly, so a regression there
// would be a worse outcome than the original bug.
const fs = require('fs');
const src = fs.readFileSync('static/index.html', 'utf8');
const start = src.indexOf('function midpointOf');
const end = src.indexOf('// Closest point on a polyline');
eval(src.slice(start, end));

const BOX_ABOVE = 23, BOX_H = 16, ARROW = 10;
let P = 0; const F = [];
const ck = (l, c, d = '') => { if (c) { P++; console.log('  ok   ' + l); }
  else { F.push(l); console.log('  FAIL ' + l + (d ? '   [' + d + ']' : '')); } };

// Does the label box overlap the arrowhead, for an upward flow?
//
// Both axes matter. An earlier version of this check compared only the vertical
// extents and reported a false failure on a bent path, where the label sat 60px
// to the side of the arrowhead and overlapped nothing. A label is a rectangle,
// not a height.
//
// LABEL_W is deliberately generous: labelWidth() is max(44, len*6+14), so 80
// stands in for a fairly long display name and keeps the check pessimistic.
const LABEL_W = 80, ARROW_W = 10;
function overlaps(anchor, end) {
  const box = { top: anchor.y - BOX_ABOVE,
                left: anchor.x - LABEL_W / 2, right: anchor.x + LABEL_W / 2 };
  box.bottom = box.top + BOX_H;
  // The arrowhead points up, so it reaches from the tip back down to tip+10.
  const head = { top: end.y, bottom: end.y + ARROW,
                 left: end.x - ARROW_W / 2, right: end.x + ARROW_W / 2 };
  const vOverlap = box.top < head.bottom && box.bottom > head.top;
  const hOverlap = box.left < head.right && box.right > head.left;
  return vOverlap && hOverlap;
}

// The rule has three bands, and the lowest one is a deliberate non-goal.
const CLEARANCE = 36;

console.log('-- the reported bug: upward flows long enough to read --');
for (const len of [CLEARANCE, 40, 50, 60, 70, 90]) {
  const pts = [{x: 300, y: 400}, {x: 300, y: 400 - len}];   // straight up
  const a = labelPointOf(pts);
  ck(`${String(len).padStart(2)}px upward: label clears the arrowhead`,
     !overlaps(a, pts[1]),
     `anchor.y=${a.y.toFixed(1)} end.y=${pts[1].y} midpoint.y=${midpointOf(pts).y}`);
}

console.log('\n-- below the clearance, the old midpoint behaviour is kept --');
// Not an oversight. At these distances the gap between the two system boxes is
// smaller than the label itself, so every position collides with something. An
// earlier attempt moved the label below the anchor, which hid it behind the
// source box: dragging two systems together made the name disappear abruptly
// instead of being gradually covered, which is how every other flow behaves.
// Ben reported that as a regression, and he was right.
for (const len of [5, 15, 25, 35]) {
  const pts = [{x: 300, y: 400}, {x: 300, y: 400 - len}];
  const a = labelPointOf(pts), m = midpointOf(pts);
  ck(`${String(len).padStart(2)}px upward: unchanged from before`,
     Math.abs(a.x - m.x) < 0.001 && Math.abs(a.y - m.y) < 0.001 && a.below === undefined,
     `(${a.x},${a.y}) vs (${m.x},${m.y})`);
}

console.log('\n-- long upward flows are untouched --');
for (const len of [100, 200, 400]) {
  const pts = [{x: 300, y: 500}, {x: 300, y: 500 - len}];
  ck(`${len}px upward still uses the midpoint`,
     Math.abs(labelPointOf(pts).y - midpointOf(pts).y) < 0.001,
     `${labelPointOf(pts).y} vs ${midpointOf(pts).y}`);
}

console.log('\n-- every other direction is unchanged --');
const others = {
  'downward short':      [{x: 300, y: 100}, {x: 300, y: 140}],
  'downward long':       [{x: 300, y: 100}, {x: 300, y: 400}],
  'horizontal right':    [{x: 100, y: 300}, {x: 400, y: 300}],
  'horizontal left':     [{x: 400, y: 300}, {x: 100, y: 300}],
  'short horizontal':    [{x: 100, y: 300}, {x: 130, y: 300}],
  'diagonal up, mostly sideways': [{x: 100, y: 300}, {x: 400, y: 280}],
  'diagonal down':       [{x: 100, y: 100}, {x: 300, y: 300}],
};
for (const [name, pts] of Object.entries(others)) {
  const a = labelPointOf(pts), m = midpointOf(pts);
  ck(`${name}: midpoint`, Math.abs(a.x - m.x) < 0.001 && Math.abs(a.y - m.y) < 0.001,
     `(${a.x},${a.y}) vs (${m.x},${m.y})`);
}

console.log('\n-- bent paths --');
// Upward overall, but routed around an obstacle. The label must still end up
// on the path, not floating beside it.
const bent = [{x: 300, y: 400}, {x: 360, y: 370}, {x: 300, y: 340}];
const onBent = labelPointOf(bent);
const nearSegment = (p) => {
  for (let i = 1; i < bent.length; i++) {
    const a = bent[i-1], b = bent[i];
    const L = Math.hypot(b.x-a.x, b.y-a.y);
    const t = Math.max(0, Math.min(1, ((p.x-a.x)*(b.x-a.x) + (p.y-a.y)*(b.y-a.y)) / (L*L)));
    const d = Math.hypot(p.x - (a.x + (b.x-a.x)*t), p.y - (a.y + (b.y-a.y)*t));
    if (d < 0.01) return true;
  }
  return false;
};
ck('a short bent upward path keeps the label on the line', nearSegment(onBent),
   `(${onBent.x.toFixed(1)},${onBent.y.toFixed(1)})`);
ck('and clears the arrowhead', !overlaps(onBent, bent[bent.length-1]),
   `anchor.y=${onBent.y.toFixed(1)} end.y=${bent[bent.length-1].y}`);
ck('no path ever returns a `below` flag — that mode was removed',
   labelPointOf(bent).below === undefined);

console.log('\n-- degenerate input --');
ck('a zero-length path does not throw',
   (() => { try { labelPointOf([{x:5,y:5},{x:5,y:5}]); return true; } catch (e) { return false; } })());
ck('a single point does not throw',
   (() => { try { labelPointOf([{x:5,y:5}]); return true; } catch (e) { return false; } })());

console.log(F.length ? '\nFAILURES: ' + F.join(', ') : `\nall ${P} checks passed`);
process.exit(F.length ? 1 : 0);
