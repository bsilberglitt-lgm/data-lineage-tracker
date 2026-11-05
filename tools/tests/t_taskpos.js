// Where a task's circle sits on the diagram.
//
//     node tools/tests/t_taskpos.js
//
// There is no browser in the assistant's sandbox, so this reads the placement
// geometry straight out of index.html and checks the arithmetic. It cannot
// tell you whether dragging a circle *feels* right — that is a click-through —
// but it pins down every case where the maths would be wrong, and those are
// the ones otherwise found by eye, one at a time.
//
// Four properties this exists to protect:
//
//   * **A stored position means different things on a flow and on a system.**
//     A fraction along the path plus a perpendicular offset, versus two
//     offsets from the box origin. Confusing the two puts a circle in open
//     space, and the symptom is a circle that looks almost right.
//   * **A new task does not land on an existing one.** Two circles at the same
//     coordinates read as one circle, and the one underneath cannot be
//     clicked or dragged.
//   * **The default on a flow clears the flow's label.** The label box sits
//     above the line at the midpoint, which is exactly where the first circle
//     would otherwise go.
//   * **A drag is the inverse of a draw.** Dropping a circle somewhere and
//     re-rendering has to leave it under the cursor, not near it.
//
// And one guard that exists because of a bug this project has already had:
// `taskPoint` reads `x_pos` / `y_pos`, because that is what a system row
// carries. `alignSnap` once read `o.x` / `o.y` — properties a system does not
// have — and the Node suite passed throughout because its fixtures were
// written from the same wrong assumption as the code. So an `x`/`y`-shaped
// system is asserted to place *nothing*.
const fs = require('fs');
const src = fs.readFileSync('static/index.html', 'utf8');
const start = src.indexOf("// ===== Where a task's circle sits =====");
const end = src.indexOf('// Closest point on a polyline to (px,py)');
if (start < 0 || end < 0) { console.log('could not find the task geometry'); process.exit(2); }
// The functions take `W = DIAGRAM_NODE_W` as a default, and that constant is
// declared outside the slice — a `const` does not escape an eval, so the
// default would throw the moment a caller omitted the size. Read both out of
// the source and put them where the defaults can see them, rather than
// hardcoding 200x70 and letting this suite keep passing after a change.
global.DIAGRAM_NODE_W = Number(/const DIAGRAM_NODE_W = (\d+)/.exec(src)[1]);
global.DIAGRAM_NODE_H = Number(/DIAGRAM_NODE_H = (\d+)/.exec(src)[1]);
eval(src.slice(start, end));

// `function` declarations inside an eval leak into this scope; `const` ones do
// not. So read the constants out of the source rather than hardcoding them —
// otherwise this suite would keep passing after someone changed a value it
// assumes.
const num = (name) => {
  const m = new RegExp('const ' + name + ' = ([^;\\n]+)').exec(src);
  if (!m) { console.log('could not find ' + name); process.exit(2); }
  // TASK_SLOT is written as an expression (TASK_R * 2 + 6), so evaluate it
  // with TASK_R already known rather than demanding a literal.
  return Number(eval(m[1].replace(/TASK_R/g, String(TASK_R_VALUE))));
};
const TASK_R_VALUE = Number(/const TASK_R = (\d+)/.exec(src)[1]);
const TASK_R = TASK_R_VALUE;
const TASK_SLOT = num('TASK_SLOT');
const TASK_FLOW_STEP = num('TASK_FLOW_STEP');
const TASK_FLOW_OFFSET = num('TASK_FLOW_OFFSET');
const NODE_W = global.DIAGRAM_NODE_W, NODE_H = global.DIAGRAM_NODE_H;
const sysRowFar = { system_id: 'SYS-009', x_pos: 400, y_pos: 300 };

let pass = 0; const fails = [];
function ck(label, cond, detail) {
  if (cond) { pass++; console.log('  PASS  ' + label); }
  else { fails.push(label); console.log('  FAIL  ' + label + (detail ? '   [' + detail + ']' : '')); }
}
const near = (a, b, tol) => Math.abs(a - b) <= (tol === undefined ? 0.5 : tol);

// A horizontal flow 400 long, and one with a bend, so nothing can pass by
// assuming a single straight segment.
const straight = [{ x: 100, y: 200 }, { x: 500, y: 200 }];
const bent = [{ x: 100, y: 100 }, { x: 100, y: 300 }, { x: 400, y: 300 }];

console.log('-- a point a fraction along a path --');
let at = pointAlong(straight, 0.5);
ck('the midpoint of a straight run', near(at.x, 300) && near(at.y, 200),
   `${at.x},${at.y}`);
ck('with the tangent pointing along it', near(at.tx, 1) && near(at.ty, 0),
   `${at.tx},${at.ty}`);
ck('and a normal perpendicular to that',
   near(at.tx * at.nx + at.ty * at.ny, 0), `${at.nx},${at.ny}`);
ck('0 is the start', (() => { const p = pointAlong(straight, 0); return near(p.x, 100); })());
ck('1 is the end', (() => { const p = pointAlong(straight, 1); return near(p.x, 500); })());
ck('out of range is clamped rather than extrapolated',
   (() => { const p = pointAlong(straight, 9); return near(p.x, 500); })());
ck('a negative fraction is clamped too',
   (() => { const p = pointAlong(straight, -4); return near(p.x, 100); })());

// Total length 200 + 300 = 500, so halfway is 250: 200 down the first leg and
// 50 along the second.
at = pointAlong(bent, 0.5);
ck('a bent path measures by length, not by segment count',
   near(at.x, 150) && near(at.y, 300), `${at.x},${at.y}`);
ck('and the tangent follows the segment it landed on',
   near(at.tx, 1) && near(at.ty, 0), `${at.tx},${at.ty}`);

ck('a degenerate path places nothing rather than throwing',
   pointAlong([{ x: 1, y: 1 }], 0.5).x === 0);
// A repeated point is a zero-length segment: stepping over it rather than
// returning the joint is what keeps the tangent meaningful.
const dup = [{ x: 0, y: 0 }, { x: 0, y: 0 }, { x: 100, y: 0 }];
ck('a zero-length segment is stepped over',
   near(pointAlong(dup, 0.5).x, 50), String(pointAlong(dup, 0.5).x));

console.log('\n-- a circle on a flow sits ON the line, clear of the label --');
let slot = taskFlowSlot([]);
// The whole point of centring on the line: where two flows run close
// together, a circle a little off one of them is ambiguous between the two,
// and which flow it belongs to is the only thing the circle has to say.
ck('the offset from the line is zero', slot.pos_cross === 0,
   String(slot.pos_cross));
// The label is drawn at the midpoint, so the midpoint is the one place a
// circle must not default to now that it cannot step sideways.
ck('the first task avoids the midpoint, where the label is',
   Math.abs(slot.pos_main - 0.5) >= TASK_FLOW_STEP * 0.9, String(slot.pos_main));
ck('but stays near it', Math.abs(slot.pos_main - 0.5) <= TASK_FLOW_STEP * 1.1,
   String(slot.pos_main));
ck('a drag cannot move it off the line either',
   taskDropPosition('data_flow', straight, 300, 120, NODE_W, NODE_H).pos_cross === 0);
ck('and a stored offset from an older build is ignored, not drawn',
   near(taskPoint({ anchor_kind: 'data_flow', pos_main: 0.5, pos_cross: 40 },
                  straight, NODE_W, NODE_H).y, 200));

const first = taskFlowSlot([]).pos_main;
const second = taskFlowSlot([first]).pos_main;
const third = taskFlowSlot([first, second]).pos_main;
ck('a second task slides along the path',
   second !== first && Math.abs(second - first) >= TASK_FLOW_STEP * 0.75,
   `${first} then ${second}`);
ck('and straddles the midpoint rather than trailing off one end',
   (first - 0.5) * (second - 0.5) < 0, `${first} and ${second}`);
ck('three tasks are all distinct',
   new Set([first, second, third]).size === 3, `${first} ${second} ${third}`);
// Ten tasks on one flow is not a real diagram, but it must not produce a
// fraction outside the path.
const many = [];
for (let i = 0; i < 10; i++) many.push(taskFlowSlot(many).pos_main);
ck('ten tasks all stay on the path',
   many.every(t => t >= 0 && t <= 1), JSON.stringify(many));
ck('nulls among the taken slots are ignored, not treated as 0',
   near(taskFlowSlot([null, undefined]).pos_main, slot.pos_main, 1e-9),
   String(taskFlowSlot([null, undefined]).pos_main));

console.log('\n-- the default slot in a system fills from the top-left --');
let s1 = taskSystemSlot([], NODE_W, NODE_H);
ck('the first slot is near the top-left corner',
   s1.pos_main <= TASK_R + 6 && s1.pos_cross <= TASK_R + 6,
   `${s1.pos_main},${s1.pos_cross}`);
let s2 = taskSystemSlot([s1], NODE_W, NODE_H);
ck('the second is to its right, on the same row',
   s2.pos_main > s1.pos_main && s2.pos_cross === s1.pos_cross,
   `${s2.pos_main},${s2.pos_cross}`);
ck('one slot apart', near(s2.pos_main - s1.pos_main, TASK_SLOT),
   String(s2.pos_main - s1.pos_main));

// Fill the first row and check it wraps rather than running off the edge.
const filled = [];
for (let i = 0; i < 20; i++) filled.push(taskSystemSlot(filled, NODE_W, NODE_H));
ck('every slot stays inside the box',
   filled.every(p => p.pos_main >= TASK_R && p.pos_main <= NODE_W - TASK_R &&
                     p.pos_cross >= TASK_R && p.pos_cross <= NODE_H - TASK_R),
   JSON.stringify(filled.slice(0, 3)));
ck('a second row is used once the first is full',
   new Set(filled.map(p => p.pos_cross)).size > 1,
   String(new Set(filled.map(p => p.pos_cross)).size));
// Once the box is full it stacks rather than spilling out: a circle outside
// its own system would be worse than two overlapping.
ck('and it stacks rather than spilling outside when full',
   filled.every(p => p.pos_main <= NODE_W - TASK_R),
   JSON.stringify(filled.slice(-2)));

console.log('\n-- drawing a circle --');
const flowTask = { anchor_kind: 'data_flow', pos_main: 0.5, pos_cross: 0 };
let p = taskPoint(flowTask, straight, NODE_W, NODE_H);
ck('a zero offset puts the circle on the line',
   near(p.x, 300) && near(p.y, 200), `${p.x},${p.y}`);
p = taskPoint({ anchor_kind: 'data_flow', pos_main: 0.5, pos_cross: 20 },
              straight, NODE_W, NODE_H);
ck('a stored offset does not move it off the line', near(p.y, 200), String(p.y));
ck('a task with no stored position still draws',
   !!taskPoint({ anchor_kind: 'data_flow', pos_main: null, pos_cross: null },
               straight, NODE_W, NODE_H));

const sys = { system_id: 'SYS-001', x_pos: 400, y_pos: 300 };
p = taskPoint({ anchor_kind: 'system', pos_main: 15, pos_cross: 15 }, sys, NODE_W, NODE_H);
ck('a system task is placed relative to its box',
   near(p.x, 415) && near(p.y, 315), `${p.x},${p.y}`);
p = taskPoint({ anchor_kind: 'system', pos_main: null, pos_cross: null },
              sys, NODE_W, NODE_H);
ck('with a default inside the box near the corner',
   p.x > 400 && p.x < 400 + TASK_SLOT && p.y > 300 && p.y < 300 + TASK_SLOT,
   `${p.x},${p.y}`);
p = taskPoint({ anchor_kind: 'system', pos_main: 9999, pos_cross: 9999 },
              sys, NODE_W, NODE_H);
ck('a position outside the box is pulled back inside',
   p.x <= 400 + NODE_W - TASK_R && p.y <= 300 + NODE_H - TASK_R, `${p.x},${p.y}`);

// The guard that `alignSnap` taught this project the hard way.
ck('an x/y-shaped system places NOTHING, because a system carries x_pos/y_pos',
   taskPoint({ anchor_kind: 'system', pos_main: 10, pos_cross: 10 },
             { system_id: 'SYS-001', x: 400, y: 300 }, NODE_W, NODE_H) === null);
ck('and the production code reads x_pos, not .x',
   /sys\.x_pos \+ slot\.x/.test(src), 'taskPoint must read x_pos/y_pos');
ck('a missing parent places nothing',
   taskPoint({ anchor_kind: 'system', pos_main: 1, pos_cross: 1 }, null) === null);
ck('a flow with no resolved endpoints places nothing',
   taskPoint({ anchor_kind: 'data_flow', pos_main: 0.5 }, []) === null);

console.log('\n-- a circle cannot be dragged away from its parent --');
// The property: a task is drawn ON its flow or INSIDE its box, so a drag has
// to be bounded both ways. The perpendicular offset on a flow was unbounded
// in the first version and a circle could be dragged anywhere on the canvas,
// including inside an unrelated system box — where it reads as belonging to
// that box.
const FAR = 100000;
let far = taskDropPosition('data_flow', straight, 300, -FAR, NODE_W, NODE_H);
ck('dragging far off a flow leaves the circle on the line',
   far.pos_cross === 0, String(far.pos_cross));
far = taskDropPosition('data_flow', straight, -FAR, 200, NODE_W, NODE_H);
ck('and along the path it stays on the path',
   far.pos_main >= 0 && far.pos_main <= 1, String(far.pos_main));
let drawn;

far = taskDropPosition('system', sysRowFar, 99999, 99999, NODE_W, NODE_H);
ck('dragging out of a system box is clamped to the box',
   far.pos_main <= NODE_W - TASK_R && far.pos_cross <= NODE_H - TASK_R,
   `${far.pos_main},${far.pos_cross}`);
far = taskDropPosition('system', sysRowFar, -99999, -99999, NODE_W, NODE_H);
ck('on the top-left side too',
   far.pos_main >= TASK_R && far.pos_cross >= TASK_R,
   `${far.pos_main},${far.pos_cross}`);
drawn = taskPoint({ anchor_kind: 'system', pos_main: -500, pos_cross: -500 },
                  sysRowFar, NODE_W, NODE_H);
ck('and a wild stored offset still draws inside the box',
   drawn.x >= sysRowFar.x_pos && drawn.x <= sysRowFar.x_pos + NODE_W &&
   drawn.y >= sysRowFar.y_pos && drawn.y <= sysRowFar.y_pos + NODE_H,
   `${drawn.x},${drawn.y}`);

console.log('\n-- the default slot for a new task --');
const flowRow = { flow_id: 'FLOW-001' };
const sysRow = { system_id: 'SYS-001', x_pos: 400, y_pos: 300 };
const existing = [
  // Sitting in the first default slot on purpose, so "avoids the one already
  // there" is a real test rather than one the defaults pass by accident.
  { task_id: 'TSK-001', anchor_kind: 'data_flow', anchor_id: 'FLOW-001',
    pos_main: taskFlowSlot([]).pos_main, pos_cross: 0 },
  { task_id: 'TSK-002', anchor_kind: 'system', anchor_id: 'SYS-001',
    pos_main: TASK_R + 4, pos_cross: TASK_R + 4 },
];
let got = defaultTaskSlot(
  { task_id: 'NEW', anchor_kind: 'data_flow', anchor_id: 'FLOW-001' },
  existing, [flowRow], [sysRow]);
ck('a new flow task avoids the one already there',
   got && Math.abs(got.pos_main - 0.5) >= TASK_FLOW_STEP * 0.75,
   JSON.stringify(got));
got = defaultTaskSlot(
  { task_id: 'NEW', anchor_kind: 'system', anchor_id: 'SYS-001' },
  existing, [flowRow], [sysRow]);
ck('a new system task takes the next slot, not the occupied one',
   got && got.pos_main > TASK_R + 4, JSON.stringify(got));
// Only tasks on the SAME parent crowd a slot. Counting every task would push
// the first circle on an empty flow away from the midpoint for no reason.
got = defaultTaskSlot(
  { task_id: 'NEW', anchor_kind: 'data_flow', anchor_id: 'FLOW-002' },
  existing, [{ flow_id: 'FLOW-002' }], [sysRow]);
ck('a task on a different parent starts at the first default slot',
   got && near(got.pos_main, taskFlowSlot([]).pos_main, 1e-9), JSON.stringify(got));
ck('a task whose own id is in the list does not avoid itself',
   (() => {
     const self = { task_id: 'TSK-001', anchor_kind: 'data_flow',
                    anchor_id: 'FLOW-001' };
     const r = defaultTaskSlot(self, existing, [flowRow], [sysRow]);
     return r && near(r.pos_main, taskFlowSlot([]).pos_main, 1e-9);
   })());
ck('a parent that is not on this diagram yields nothing to store',
   defaultTaskSlot({ task_id: 'NEW', anchor_kind: 'data_flow',
                     anchor_id: 'FLOW-999' }, existing, [flowRow], [sysRow]) === null);
ck('and so does a missing system',
   defaultTaskSlot({ task_id: 'NEW', anchor_kind: 'system',
                     anchor_id: 'SYS-999' }, existing, [flowRow], [sysRow]) === null);
ck('no task at all yields nothing', defaultTaskSlot(null, [], [], []) === null);

console.log('\n-- a drag is the inverse of a draw --');
// On a flow the gesture is one-dimensional, so the round trip is about the
// position ALONG the path: dropping near a point puts the circle at the
// nearest place on the line, not under the cursor.
let drop = taskDropPosition('data_flow', straight, 380, 175, NODE_W, NODE_H);
let back = taskPoint({ anchor_kind: 'data_flow', ...drop }, straight, NODE_W, NODE_H);
ck('dropping near a flow puts the circle at the nearest point on the line',
   near(back.x, 380, 2) && near(back.y, 200), `${back.x},${back.y}`);
ck('the stored fraction is where it was dropped along the path',
   near(drop.pos_main, 0.7, 0.01), String(drop.pos_main));

drop = taskDropPosition('data_flow', bent, 120, 290, NODE_W, NODE_H);
back = taskPoint({ anchor_kind: 'data_flow', ...drop }, bent, NODE_W, NODE_H);
ck('a bent path too, still on the line',
   near(back.x, 120, 3) && near(back.y, 300), `${back.x},${back.y}`);

drop = taskDropPosition('system', sys, 430, 330, NODE_W, NODE_H);
back = taskPoint({ anchor_kind: 'system', ...drop }, sys, NODE_W, NODE_H);
ck('dropping inside a system lands back under the cursor',
   near(back.x, 430) && near(back.y, 330), `${back.x},${back.y}`);
drop = taskDropPosition('system', sys, 10, 10, NODE_W, NODE_H);
ck('dropping outside a system is pulled to its edge, not stored as-is',
   drop.pos_main >= TASK_R && drop.pos_cross >= TASK_R,
   `${drop.pos_main},${drop.pos_cross}`);
ck('dropping with no parent stores nothing',
   taskDropPosition('system', null, 1, 1) === null &&
   taskDropPosition('data_flow', [], 1, 1) === null);
ck('and an x/y-shaped system stores nothing either',
   taskDropPosition('system', { x: 400, y: 300 }, 430, 330) === null);

console.log(`\n  ${pass} checks passed` + (fails.length ? `, ${fails.length} FAILED` : ''));
fails.forEach(f => console.log('  FAILED: ' + f));
process.exit(fails.length ? 1 : 0);
