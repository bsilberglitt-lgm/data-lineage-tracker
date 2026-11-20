// `scopedUrl` — which view an API request is about.
//
//     node tools/tests/t_scopedurl.js
//
// This exists because the scenario focus list was fixed once and came back.
// `AddScenarioModal` needs the systems belonging to whatever you chose to
// start from, not the view you are standing in, and the first fix was a call
// to `request` with a comment claiming it bypassed view scoping. `request` IS
// the scoping helper, so nothing was bypassed, and creating a scenario from
// inside another one went on offering the open scenario's systems.
//
// Two shapes of that bug, and the second is the nastier one:
//
//   * "Current Architecture" asked for `/api/systems` and got
//     `?scenario=<open>`.
//   * Naming a source asked for `?scenario=5` and got
//     `?scenario=5&scenario=<open>` — a duplicated parameter, where Tornado's
//     `get_argument` returns the LAST value. The URL plainly contained the
//     right answer and the server plainly read the wrong one.
//
// `scopedUrl` is a pure function of a string and two module-level variables,
// which is the only reason this can be tested at all: there is no browser in
// the assistant's sandbox. It is extracted from `index.html` and its globals
// are passed in as parameters, because `let` at module level does not escape
// an `eval` (see CLAUDE.md) — and because reading the real source means this
// cannot keep passing after somebody edits the function.

const fs = require('fs');
const path = require('path');

function findIndexHtml() {
  // Relative to this file first, then the working directory, then the
  // harness's flat copy. No suite may hardcode a path (see CLAUDE.md).
  const candidates = [
    path.join(__dirname, '..', '..', 'static', 'index.html'),
    path.join(process.cwd(), 'static', 'index.html'),
    path.join(process.cwd(), 'index.html'),
  ];
  for (const p of candidates) if (fs.existsSync(p)) return p;
  console.error('Could not find static/index.html from ' + __dirname);
  process.exit(2);
}

const src = fs.readFileSync(findIndexHtml(), 'utf8');

const fnSrc = (src.match(/function scopedUrl\(url\) \{[\s\S]*?\n\}/) || [])[0];
const listSrc = (src.match(/const UNSCOPED_ENDPOINTS = \[[\s\S]*?\n\];/) || [])[0];
if (!fnSrc || !listSrc) {
  console.error('Could not extract scopedUrl or UNSCOPED_ENDPOINTS from index.html.');
  process.exit(2);
}

// Globals as parameters, so each case controls the view independently.
const build = new Function('viewScenario', 'viewShowHidden',
  listSrc + '\n' + fnSrc + '\nreturn scopedUrl;');

let pass = 0, fail = 0;
function ck(label, cond, detail) {
  if (cond) { pass++; console.log('  PASS  ' + label); }
  else { fail++; console.log('  FAIL  ' + label + (detail ? '   [' + detail + ']' : '')); }
}
function eq(label, got, want) { ck(label, got === want, got + ' != ' + want); }

console.log('\n-- on the live diagram, nothing is appended --');
const live = build(null, false);
eq('a plain path is untouched', live('/api/systems'), '/api/systems');
eq('a path that names a scenario is untouched',
   live('/api/systems?scenario=5'), '/api/systems?scenario=5');

console.log('\n-- inside a scenario, ordinary reads are scoped to it --');
const inScn = build(12, false);
eq('a plain path gains the open scenario',
   inScn('/api/systems'), '/api/systems?scenario=12');
eq('an existing query string gets an ampersand',
   inScn('/api/systems?q=x'), '/api/systems?q=x&scenario=12');

console.log('\n-- a URL that already names a scenario is left alone --');
// The regression. Appending here produced ?scenario=5&scenario=12, and the
// server reads the last value, so the caller's choice was silently discarded.
eq('no second scenario is appended',
   inScn('/api/systems?scenario=5'), '/api/systems?scenario=5');
ck('and the result names exactly one scenario',
   (inScn('/api/systems?scenario=5').match(/scenario=/g) || []).length === 1,
   inScn('/api/systems?scenario=5'));
eq('including when other parameters are present',
   inScn('/api/systems?scenario=5&q=x'), '/api/systems?scenario=5&q=x');

console.log('\n-- include_hidden follows the same rule --');
const hidden = build(null, true);
eq('appended when absent', hidden('/api/systems'), '/api/systems?include_hidden=1');
eq('not duplicated when present',
   hidden('/api/systems?include_hidden=1'), '/api/systems?include_hidden=1');
const both = build(12, true);
eq('both appended together',
   both('/api/systems'), '/api/systems?scenario=12&include_hidden=1');
eq('only the missing one is appended',
   both('/api/systems?scenario=5'), '/api/systems?scenario=5&include_hidden=1');

console.log('\n-- unscoped endpoints are never scoped --');
eq('/api/preferences', inScn('/api/preferences'), '/api/preferences');
eq('a path under an unscoped prefix',
   inScn('/api/users/4'), '/api/users/4');
eq('/api/task-types is configuration, not records',
   inScn('/api/task-types'), '/api/task-types');
// The deny-list is why this is the safe default: records are scoped unless
// somebody says otherwise.
eq('/api/tasks is NOT on that list, so it stays scoped',
   inScn('/api/tasks'), '/api/tasks?scenario=12');
eq('a non-API url is untouched', inScn('/reset-password'), '/reset-password');

console.log('\n-- the modal asks for its list unscoped, which is the actual fix --');
// A correct `scopedUrl` is not enough on its own: the live branch has no
// scenario parameter to protect, so only the explicit opt-out saves it. Greped
// rather than called, because the call site is what regressed last time.
const call = (src.match(/request\('GET', url, undefined, \{ unscoped: true \}\)/) || [])[0];
ck('AddScenarioModal passes { unscoped: true }', !!call,
   'the focus list will be scoped to the open scenario again');
ck('request accepts the flag',
   /async function request\(method, url, data, \{ unscoped = false \} = \{\}\)/.test(src),
   'the flag is passed but request ignores it');
ck('and honors it',
   /url = unscoped \? url : scopedUrl\(url\)/.test(src),
   'request still scopes unconditionally');

console.log('\n' + (fail ? 'FAILED' : 'OK') + ' — ' + pass + ' passed, ' + fail + ' failed\n');
process.exit(fail ? 1 : 0);
