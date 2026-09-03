// Replay endpointSetter's rules exactly as written in index.html.
const fs = require('fs');
const src = fs.readFileSync('static/index.html', 'utf8');
const start = src.indexOf('function autoFlowName');
const end = src.indexOf('// Views that belong to a particular scenario');
eval(src.slice(start, end));

const systems = [
  { system_id: 'SYS-001', name: 'NEO' },
  { system_id: 'SYS-002', name: 'Federal Reporting' },
  { system_id: 'SYS-003', name: 'Staging' },
];
let P = 0, F = [];
const ck = (l, c, d='') => { if (c) { P++; console.log('  ok   ' + l); }
  else { F.push(l); console.log('  FAIL ' + l + (d ? '   [' + d + ']' : '')); } };

function harness(initial) {
  let form = { ...initial };
  const set = (k, v) => { form = { ...form, [k]: v }; };
  return {
    change(key, value) { endpointSetter(form, set, systems)(key, value); return form; },
    get form() { return form; },
  };
}

console.log('-- the add modal, starting empty --');
let h = harness({ name: '', source_system_id: '', destination_system_id: '' });
h.change('source_system_id', 'SYS-001');
ck('one system alone does not name it', h.form.name === '', h.form.name);
h.change('destination_system_id', 'SYS-002');
ck('both chosen fills the name in', h.form.name === 'NEO to Federal Reporting', h.form.name);
h.change('destination_system_id', 'SYS-003');
ck('changing one keeps the name in step', h.form.name === 'NEO to Staging', h.form.name);
h.change('source_system_id', 'SYS-002');
ck('changing the other too', h.form.name === 'Federal Reporting to Staging', h.form.name);

console.log('\n-- a name the user typed is theirs --');
h = harness({ name: '', source_system_id: '', destination_system_id: '' });
h.change('source_system_id', 'SYS-001');
h.change('destination_system_id', 'SYS-002');
h.form.name = 'Nightly enrolment extract';           // as if typed
let typed = harness({ ...h.form });
typed.change('destination_system_id', 'SYS-003');
ck('changing a system does not overwrite it',
   typed.form.name === 'Nightly enrolment extract', typed.form.name);
ck('...but the system did change', typed.form.destination_system_id === 'SYS-003');

console.log('\n-- the flyout, opening an existing flow --');
let saved = harness({ name: 'NEO to Federal Reporting',
                      source_system_id: 'SYS-001', destination_system_id: 'SYS-002' });
saved.change('destination_system_id', 'SYS-003');
ck('a still-generated name follows the change', saved.form.name === 'NEO to Staging', saved.form.name);

let custom = harness({ name: 'Enrolment feed',
                       source_system_id: 'SYS-001', destination_system_id: 'SYS-002' });
custom.change('destination_system_id', 'SYS-003');
ck('a custom name survives the change', custom.form.name === 'Enrolment feed', custom.form.name);

console.log('\n-- edge cases --');
let cleared = harness({ name: '', source_system_id: 'SYS-001', destination_system_id: 'SYS-002' });
cleared.change('source_system_id', 'SYS-003');
ck('an emptied name is refilled', cleared.form.name === 'Staging to Federal Reporting', cleared.form.name);
let same = harness({ name: '', source_system_id: '', destination_system_id: '' });
same.change('source_system_id', 'SYS-001');
same.change('destination_system_id', 'SYS-001');
ck('a system to itself still names it', same.form.name === 'NEO to NEO', same.form.name);
let unknown = harness({ name: 'NEO to Federal Reporting',
                        source_system_id: 'SYS-001', destination_system_id: 'SYS-002' });
unknown.change('destination_system_id', 'SYS-999');
ck('an unknown system leaves the name alone rather than blanking it',
   unknown.form.name === 'NEO to Federal Reporting', unknown.form.name);
ck('autoFlowName is empty when a system is missing',
   autoFlowName(systems, 'SYS-001', '') === '', autoFlowName(systems, 'SYS-001', ''));

console.log(F.length ? '\nFAILURES: ' + F.join(', ') : `\nall ${P} checks passed`);
process.exit(F.length ? 1 : 0);
