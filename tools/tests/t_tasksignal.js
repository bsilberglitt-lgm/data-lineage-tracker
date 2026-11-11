// What a task's circle says about where it stands.
//
//     node tools/tests/t_tasksignal.js
//
// `taskSignal` is five rules in strict precedence, and precedence is exactly
// the kind of thing that looks right while being wrong for one combination
// nobody tries by hand. There is no browser in the assistant's sandbox, so
// this reads the function out of index.html and drives every pair of
// (status, dates) that the precedence distinguishes.
//
// Four properties it exists to protect, each of which was a decision rather
// than an accident:
//
//   * **Completed beats every date.** A task finished after its end date is
//     finished, not late. Getting this wrong turns the diagram red for work
//     that is done, which teaches people to ignore red.
//   * **An unset status past its start date IS late_start.** Ben asked for
//     this, reversing the original call. What the start date tells you does
//     not depend on whether anyone filled the status in: the day came and went
//     and nothing says the work began. The 0019 distinction still holds
//     everywhere a status is *reported* — the catalog's dash, the hover card's
//     parentheses, the derivation ignoring unset subtasks — and those are
//     asserted elsewhere.
//   * **A subtask's dates count.** Ben set a subtask to In progress with an
//     end date of yesterday and the circle stayed yellow, because both date
//     rules read only the task's own columns. A task with subtasks does not own
//     its status; it should not own its dates either, and the subtask dates are
//     where slippage shows up first. A regression here is invisible: the circle
//     is simply the wrong color, with nothing in any log.
//   * **In progress beats late_start.** A task that has begun cannot also be
//     failing to begin.
//   * **A missing date decides nothing.** Most tasks have no dates at all, and
//     a rule that reads a null as "long ago" would make the whole diagram red.
//
// And one guard of the kind this project keeps needing: the colors are read
// out of the source rather than written down here, so the suite cannot keep
// passing after somebody changes a fill. A test built on the author's
// assumptions confirms the assumptions — the most expensive lesson in
// CLAUDE.md.
const fs = require('fs');
const src = fs.readFileSync('static/index.html', 'utf8');
const start = src.indexOf("// ===== What a task's circle says about where it stands =====");
const end = src.indexOf("// ===== Where a task's circle sits =====");
if (start < 0 || end < 0) {
  console.log('could not find the task signal block');
  process.exit(2);
}
// `const` does not escape an `eval`, so TASK_SIGNALS is invisible even though
// it is in the slice. Read it out of the source instead.
eval(src.slice(start, end));

// `\uXXXX` escapes are interpreted inside the quoted string the source
// actually contains, so they have to be decoded here too — read raw, the label
// comes back with a literal backslash and four digits, and a label comparison
// fails for a reason that has nothing to do with labels.
const unesc = (t) => t.replace(/\\u([0-9a-fA-F]{4})/g,
  (_m, h) => String.fromCharCode(parseInt(h, 16)));

const SIGNALS = [...src.slice(start, end).matchAll(
  /\{\s*key:\s*'([a-z_]+)',\s*label:\s*'([^']*)',\s*fill:\s*'(#[0-9a-fA-F]{6})'/g)]
  .map(m => ({ key: m[1], label: unesc(m[2]), fill: m[3] }));

let pass = 0; const fails = [];
const ck = (what, ok) => { if (ok) pass++; else fails.push(what); };
const sig = (t, today) => taskSignal(t, today);
const T = '2026-11-06';
const eq = (what, got, want) =>
  ck(`${what} — expected ${want}, got ${got}`, got === want);

// ---- the source really does define these states, and only these ----
eq('seven states are defined', String(SIGNALS.length), '7');
eq('the states are in precedence order',
   SIGNALS.map(s => s.key).join(','),
   'complete,overdue,blocked,active,late_start,automated,open');
ck('every state has a distinct fill',
   new Set(SIGNALS.map(s => s.fill)).size === SIGNALS.length);
ck('every fill is a six-digit hex',
   SIGNALS.every(s => /^#[0-9a-f]{6}$/i.test(s.fill)));

// ---- nothing recorded ----
eq('no status and no dates', sig({}, T), 'open');
eq('a null task', sig(null, T), 'open');
eq('status unset, start long past', sig({ expected_start: '2020-01-01' }, T),
   'late_start');
eq('status unset, end long past', sig({ expected_end: '2020-01-01' }, T), 'overdue');

// ---- completed beats every date ----
eq('completed', sig({ status: 'completed' }, T), 'complete');
eq('completed after its end date',
   sig({ status: 'completed', expected_end: '2020-01-01' }, T), 'complete');
eq('completed after its start date',
   sig({ status: 'completed', expected_start: '2020-01-01' }, T), 'complete');
eq('completed with both dates past',
   sig({ status: 'completed', expected_start: '2020-01-01',
         expected_end: '2020-02-01' }, T), 'complete');

// ---- overdue ----
eq('end date yesterday, not started',
   sig({ status: 'not_started', expected_end: '2026-11-05' }, T), 'overdue');
eq('end date yesterday, in progress',
   sig({ status: 'in_progress', expected_end: '2026-11-05' }, T), 'overdue');
eq('end date today is NOT overdue',
   sig({ status: 'in_progress', expected_end: T }, T), 'active');
eq('end date today, nothing said, is NOT overdue',
   sig({ expected_end: T }, T), 'open');
eq('end date tomorrow',
   sig({ status: 'in_progress', expected_end: '2026-11-07' }, T), 'active');

// ---- in progress beats late_start ----
eq('in progress, start long past',
   sig({ status: 'in_progress', expected_start: '2020-01-01' }, T), 'active');
eq('in progress, no dates', sig({ status: 'in_progress' }, T), 'active');

// ---- late_start needs the explicit statement ----
eq('not started, start yesterday',
   sig({ status: 'not_started', expected_start: '2026-11-05' }, T), 'late_start');
eq('not started, start today is not yet late',
   sig({ status: 'not_started', expected_start: T }, T), 'open');
eq('not started, start tomorrow',
   sig({ status: 'not_started', expected_start: '2026-11-07' }, T), 'open');
eq('not started, no start date', sig({ status: 'not_started' }, T), 'open');
// These two asserted the opposite until Ben asked for the change, and they are
// left pointing at it so the reversal is deliberate rather than drift.
eq('UNSET status with a past start date IS late_start',
   sig({ status: null, expected_start: '2020-01-01' }, T), 'late_start');
eq('empty-string status with a past start date IS late_start',
   sig({ status: '', expected_start: '2020-01-01' }, T), 'late_start');

// ---- a missing "today" decides nothing ----
eq('no today, past end', sig({ expected_end: '2020-01-01' }, null), 'open');
eq('no today, not started past start',
   sig({ status: 'not_started', expected_start: '2020-01-01' }, null), 'open');

// ---- a subtask's dates drive the task, which is the bug Ben found ----
// His exact case: one subtask, In progress, end date yesterday. The task's own
// dates are empty, so before the fix rule 2 saw nothing and rule 3 returned
// 'active'.
eq("Ben's case: subtask in progress, ended yesterday",
   sig({ steps: [{ status: 'in_progress', expected_end: '2026-11-05' }] }, T),
   'overdue');
eq('subtask past its end, status unset',
   sig({ steps: [{ expected_end: '2026-11-05' }] }, T), 'overdue');
eq('subtask past its end but completed is not overdue',
   sig({ status: 'completed',
         steps: [{ status: 'completed', expected_end: '2026-11-05' }] }, T),
   'complete');
eq('a completed subtask past its end, among others, is not overdue',
   sig({ status: 'in_progress',
         steps: [{ status: 'completed', expected_end: '2026-11-05' },
                 { status: 'in_progress' }] }, T),
   'active');
eq('one overdue subtask among many wins',
   sig({ status: 'in_progress',
         steps: [{ status: 'completed' },
                 { status: 'in_progress', expected_end: '2026-11-05' }] }, T),
   'overdue');
eq('subtask end date today is not overdue',
   sig({ steps: [{ status: 'in_progress', expected_end: T }] }, T), 'active');
// The same case with the status the server would have derived, to prove the
// two routes to 'active' agree. The fixture WITHOUT it is the one that caught
// taskSignal depending on the server's derivation — a subtask saying "in
// progress" is enough on its own now.
eq('and identically with the derived status present',
   sig({ status: 'in_progress',
         steps: [{ status: 'in_progress', expected_end: T }] }, T), 'active');
eq('a subtask in progress alone reads active',
   sig({ steps: [{ status: 'in_progress' }] }, T), 'active');
eq('subtask past its start, not started',
   sig({ steps: [{ status: 'not_started', expected_start: '2026-11-05' }] }, T),
   'late_start');
eq('subtask past its start, status unset',
   sig({ steps: [{ expected_start: '2026-11-05' }] }, T), 'late_start');
eq('subtask past its start but in progress is not late',
   sig({ status: 'in_progress',
         steps: [{ status: 'in_progress', expected_start: '2026-11-05' }] }, T),
   'active');
eq('subtask past its start but completed is not late',
   sig({ steps: [{ status: 'completed', expected_start: '2026-11-05' }] }, T),
   'open');
eq('the task overdue beats a subtask merely late to start',
   sig({ expected_end: '2026-11-05',
         steps: [{ status: 'not_started', expected_start: '2026-11-04' }] }, T),
   'overdue');
eq('an empty steps list changes nothing', sig({ steps: [] }, T), 'open');

// ---- taskNotBegun is the shared rule, so state it directly ----
ck('unset has not begun', taskNotBegun(null) === true);
ck('empty string has not begun', taskNotBegun('') === true);
ck('not_started has not begun', taskNotBegun('not_started') === true);
ck('in_progress HAS begun', taskNotBegun('in_progress') === false);
ck('completed HAS begun', taskNotBegun('completed') === false);

// ---- tasksUseSignals decides how much legend to draw ----
ck('no tasks: nothing is color-coded',
   tasksUseSignals([], T) === false);
ck('tasks with nothing recorded: not color-coded',
   tasksUseSignals([{}, { status: null }, { steps: [] }], T) === false);
ck('a not_started task with no dates is still not color-coded',
   tasksUseSignals([{ status: 'not_started' }], T) === false);
ck('one task in progress is enough',
   tasksUseSignals([{}, { status: 'in_progress' }], T) === true);
ck('a date-driven state counts even with no status anywhere',
   tasksUseSignals([{ expected_end: '2026-11-05' }], T) === true);
ck('a subtask-driven state counts too',
   tasksUseSignals([{ steps: [{ expected_end: '2026-11-05' }] }], T) === true);
ck('null list is safe', tasksUseSignals(null, T) === false);

// ---- dependencies: the blocked state, and what gives it teeth ----
// Ben's rule, which reverses the first draft: the start date does not override
// the dependencies, it is what makes them matter. Before the date, waiting is
// just the plan. After it, waiting is the reason nothing has happened.
const dep = (status) => ({ task_id: 'TSK-X', name: 'Upstream', status });
const DONE = [dep('completed')];
const WAIT = [dep('in_progress')];
const UNSET = [dep(null)];

eq('start date passed, a dependency unfinished: BLOCKED',
   sig({ expected_start: '2026-11-05', dependencies: WAIT }, T), 'blocked');
eq('start date passed, all dependencies done: the ordinary late start',
   sig({ expected_start: '2026-11-05', dependencies: DONE }, T), 'late_start');
eq('start date NOT yet reached, dependency unfinished: nothing to report',
   sig({ expected_start: '2026-11-20', dependencies: WAIT }, T), 'open');
eq('start date NOT yet reached, dependencies done: still nothing to report',
   sig({ expected_start: '2026-11-20', dependencies: DONE }, T), 'open');
eq('no date anywhere, dependency unfinished: nothing to report',
   sig({ dependencies: WAIT }, T), 'open');
eq('no date anywhere, all dependencies done: late start',
   sig({ dependencies: DONE }, T), 'late_start');

// A dependency nobody has assessed is not a finished one — the same asymmetry
// the subtask derivation keeps.
eq('a dependency with NO status does not count as done',
   sig({ expected_start: '2026-11-05', dependencies: UNSET }, T), 'blocked');
eq('and with no date either, it does not start the clock',
   sig({ dependencies: UNSET }, T), 'open');
eq('one unfinished among several is enough to block',
   sig({ expected_start: '2026-11-05',
         dependencies: [dep('completed'), dep('completed'), dep('in_progress')] },
       T), 'blocked');
eq('all of several done reads as late, not blocked',
   sig({ expected_start: '2026-11-05',
         dependencies: [dep('completed'), dep('completed')] }, T), 'late_start');

// A SUBTASK's start date counts as the task's, the way it does for the
// existing rules — so a task with no date of its own is still blocked.
eq('a subtask start date arms the blocked rule too',
   sig({ steps: [{ status: 'not_started', expected_start: '2026-11-05' }],
         dependencies: WAIT }, T), 'blocked');
eq('and a subtask start date in the future does not',
   sig({ steps: [{ status: 'not_started', expected_start: '2026-11-20' }],
         dependencies: WAIT }, T), 'open');
// The no-date clause must ask whether a date is SET, not whether one passed.
// Asking "has one passed" would let a task with a future start date fall
// through to the dependency rule and report late before its own start.
eq('a future start date suppresses the dependency clock',
   sig({ expected_start: '2026-11-20', dependencies: DONE }, T), 'open');
eq('a future SUBTASK start date does too',
   sig({ steps: [{ expected_start: '2026-11-20' }], dependencies: DONE }, T),
   'open');

// Precedence above blocked.
eq('completed beats blocked',
   sig({ status: 'completed', expected_start: '2026-11-05',
         dependencies: WAIT }, T), 'complete');
eq('past its end date beats blocked',
   sig({ expected_start: '2026-11-01', expected_end: '2026-11-05',
         dependencies: WAIT }, T), 'overdue');
eq('a task somebody has STARTED is not blocked',
   sig({ status: 'in_progress', expected_start: '2026-11-05',
         dependencies: WAIT }, T), 'active');

eq('no dependencies at all: unchanged behavior',
   sig({ expected_start: '2026-11-05' }, T), 'late_start');
eq('an empty dependency list is not "all done"',
   sig({ dependencies: [] }, T), 'open');

// ---- taskDepsComplete states the distinction on its own ----
ck('no dependencies is not complete',
   taskDepsComplete({ dependencies: [] }) === false);
ck('missing dependencies key is not complete',
   taskDepsComplete({}) === false);
ck('all completed is complete',
   taskDepsComplete({ dependencies: DONE }) === true);
ck('one unfinished is not complete',
   taskDepsComplete({ dependencies: [dep('completed'), dep(null)] }) === false);

ck('a blocked task is color-coded, so the legend must appear',
   tasksUseSignals([{ expected_start: '2026-11-05', dependencies: WAIT }], T)
   === true);

// ---- every status the app can store maps somewhere ----
for (const st of ['not_started', 'in_progress', 'completed']) {
  ck(`status ${st} maps to a defined state`,
     SIGNALS.some(s => s.key === sig({ status: st }, T)));
}

// ---- automated stands in for open, and loses to every real state ----
// Ben: "when a task is automated, it should show up as a blue circle instead
// of gray". Instead of GRAY — so it replaces the resting state and nothing
// else. A task that is late is drawn late whether or not a machine runs it,
// because urgency is what the color is for; the blue appears where there was
// nothing to report, which is where the question "does this need a person"
// is actually being asked.
eq('automated with nothing recorded',
   sig({ is_automated: 1 }, T), 'automated');
eq('and the integer convention, not a boolean',
   sig({ is_automated: true }, T), 'automated');
eq('0 is not automated', sig({ is_automated: 0 }, T), 'open');
eq('automated and completed reads completed',
   sig({ is_automated: 1, status: 'completed' }, T), 'complete');
eq('automated and past its end date reads overdue',
   sig({ is_automated: 1, status: 'in_progress',
         expected_end: '2026-11-01' }, T), 'overdue');
eq('automated and in progress reads in progress',
   sig({ is_automated: 1, status: 'in_progress' }, T), 'active');
eq('automated and past its start date reads late',
   sig({ is_automated: 1, expected_start: '2026-11-01' }, T), 'late_start');
eq('automated and blocked reads blocked',
   sig({ is_automated: 1, expected_start: '2026-11-01',
         dependencies: WAIT }, T), 'blocked');
// A future start date is the one case where there is nothing to report AND a
// date is set, so the blue has to survive it.
eq('automated with a start date still to come',
   sig({ is_automated: 1, expected_start: '2026-11-20' }, T), 'automated');

// Both resting states have to be named in one place: three callers ask "is
// there anything to report", and a list written out at each would drift the
// first time a state is added — which is exactly what adding this one would
// have done.
ck('the resting states are named once in the source',
   /const TASK_RESTING = \['open', 'automated'\];/.test(src));
ck('and the legend gate reads that constant, not a literal',
   /TASK_RESTING\.indexOf\(taskSignal\(t, today\)\) < 0/.test(src));

// ---- the wording, which is not always the state's own label ----
// Ben: a task with no start date whose dependencies have all finished should
// read "Not started, dependencies completed". Same color, same mark, same
// state — it is reached a different way, and a label claiming it is past a
// start date it does not have is worse than a vague one, because the field is
// empty when you open the task.
//
// The whole point of these is the BOUNDARY: every case that reaches
// `late_start` by way of a date keeps the date wording, including the ones
// that also have finished dependencies.
const DEPS_LABEL = 'Not started, dependencies completed';
const DATE_LABEL = SIGNALS.find(x => x.key === 'late_start').label;
const lbl = (t, today) => taskSignalLabel(t, today);

eq('no date anywhere, dependencies done',
   lbl({ dependencies: DONE }, T), DEPS_LABEL);
eq("the task's own start date has passed",
   lbl({ expected_start: '2026-11-01' }, T), DATE_LABEL);
eq('a start date that has passed wins the wording, deps or not',
   lbl({ expected_start: '2026-11-01', dependencies: DONE }, T), DATE_LABEL);
// The one that would be easy to get wrong: the date lives on a SUBTASK, so
// the task's own column is empty and a check reading only that column would
// hand back the dependency wording for a task that genuinely has a date.
eq("a subtask's start date counts as having one",
   lbl({ steps: [{ expected_start: '2026-11-01' }], dependencies: DONE }, T),
   DATE_LABEL);
// A future start date means the dependency clause is never reached at all, so
// there is no late state to word either way.
eq('a start date still to come is not late at all',
   sig({ expected_start: '2026-11-20', dependencies: DONE }, T), 'open');
eq('overdue keeps its own label',
   lbl({ expected_end: '2026-11-01' }, T), SIGNALS.find(x => x.key === 'overdue').label);
// `TASK_SIGNAL_BY_KEY` is a `const` and so invisible out here — the trap
// recorded in CLAUDE.md — which is why this asks the labeling function
// instead of reading the map. Fixtures that reach each state, so the label
// comes back through the production path.
const AT = [
  ['complete',   { status: 'completed' }],
  ['overdue',    { expected_end: '2026-11-01' }],
  ['blocked',    { expected_start: '2026-11-01', dependencies: WAIT }],
  ['active',     { status: 'in_progress' }],
  ['automated',  { is_automated: 1 }],
  ['open',       {}],
];
for (const [key, fixture] of AT) {
  eq(`${key} reaches its own state`, sig(fixture, T), key);
  eq(`${key} keeps its own label`, lbl(fixture, T),
     SIGNALS.find(x => x.key === key).label);
}

// The predicate on its own, because the label function is two decisions and
// only one of them is this.
ck('taskLateFromDeps is false with no dependencies',
   taskLateFromDeps({}) === false);
ck('taskLateFromDeps is false when a dependency is unfinished',
   taskLateFromDeps({ dependencies: WAIT }) === false);
ck('taskLateFromDeps is false when a start date exists',
   taskLateFromDeps({ expected_start: '2026-11-01',
                      dependencies: DONE }) === false);
ck('taskLateFromDeps is true with no date and everything done',
   taskLateFromDeps({ dependencies: DONE }) === true);
ck('a null task is not late from dependencies',
   taskLateFromDeps(null) === false);

// The hover card is what Ben sees, so check the production call site reads the
// labeling function rather than the state's own label — the same kind of
// source check as t_taskpos.js greps for x_pos. A correct function nobody
// calls is the shape of bug this project keeps finding.
ck('the hover card uses taskSignalLabel',
   /lines\.push\(taskSignalLabel\(task, today\)\);/.test(src));

// ---- todayISO is local, not UTC ----
// `toISOString()` is UTC, so west of Greenwich it reports tomorrow for part of
// every evening — which would turn a task due today red several hours early.
// Asserted against the local date rather than a fixed string, so it holds
// wherever this runs.
const d = new Date();
const p = (n) => String(n).padStart(2, '0');
eq('todayISO is the local date',
   todayISO(), `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`);
ck('todayISO is YYYY-MM-DD', /^\d{4}-\d{2}-\d{2}$/.test(todayISO()));

// ---- the production call site passes a date ----
// `taskSignal(task)` with no second argument silently never reports overdue or
// late_start, which looks like the feature doing nothing rather than a missing
// argument. Checked in the source, the same way t_taskpos.js greps for x_pos.
ck('the diagram passes today to taskSignal',
   /taskSignal\(task,\s*today\)/.test(src));
ck('the diagram computes today locally',
   /const today = todayISO\(\);/.test(src));

console.log(`\n${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log('  FAILED:', f);
process.exit(fails.length ? 1 : 0);
