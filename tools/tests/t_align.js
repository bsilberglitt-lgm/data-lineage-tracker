// Alignment guides, and snapping an arrow segment to 45 degrees.
//
// There is no browser in the assistant's sandbox, so this reads the geometry
// straight out of index.html and checks the arithmetic. It cannot tell you
// whether the snapping *feels* right while dragging — that is a click-through
// — but it can pin down every case where the maths would be wrong, and those
// are the ones that would otherwise be found by eye, one at a time.
//
// The two properties worth protecting:
//
//   * Snapping must be **inert outside its tolerance.** A snap that quietly
//     moves things you did not ask it to move is worse than no snap, because
//     you stop trusting the position of anything.
//   * A guide must **span both boxes.** A dotted line that stops short of the
//     box it refers to does not explain itself, which is the whole point of
//     drawing one.
const fs = require('fs');
const src = fs.readFileSync('static/index.html', 'utf8');
const start = src.indexOf('// ===== Exporting the diagram =====');
const end = src.indexOf('const LABEL_CLEARANCE');
if (start < 0 || end < 0) { console.log('could not find the geometry'); process.exit(2); }
eval(src.slice(start, end));

// `function` declarations inside an eval leak into this scope; `const` ones do
// not. So the geometry functions are callable above, but the constants beside
// them are invisible — read them out of the source instead of hardcoding, or
// the test would keep passing after someone changed the value it assumes.
const num = (name) => {
  const m = new RegExp('const ' + name + ' = (-?[0-9.]+)').exec(src);
  if (!m) { console.log('could not find ' + name); process.exit(2); }
  return Number(m[1]);
};
const EXPORT_TITLE_SPACE = num('EXPORT_TITLE_SPACE');
const ALIGN_TOLERANCE = num('ALIGN_TOLERANCE');
const ANGLE_SNAP_TOLERANCE = num('ANGLE_SNAP_TOLERANCE');
const EXPORT_RASTER_SCALE = num('EXPORT_RASTER_SCALE');
// An array rather than a number, so it is read with its own small parser.
const EXPORT_FORMATS = (() => {
  const m = /const EXPORT_FORMATS = \[([\s\S]*?)\n\];/.exec(src);
  if (!m) { console.log('could not find EXPORT_FORMATS'); process.exit(2); }
  return m[1].split('{').slice(1).map(chunk => ({
    id: (/id: '([^']+)'/.exec(chunk) || [])[1],
    label: (/label: '([^']+)'/.exec(chunk) || [])[1],
    ext: (/ext: '([^']+)'/.exec(chunk) || [])[1],
    print: /print: true/.test(chunk),
  }));
})();

let P = 0; const F = [];
const ck = (l, c, d = '') => { if (c) { P++; console.log('  ok   ' + l); }
  else { F.push(l); console.log('  FAIL ' + l + (d ? '   [' + d + ']' : '')); } };
const near = (a, b, eps) => Math.abs(a - b) < (eps === undefined ? 0.001 : eps);

const W = 200, H = 70;

console.log('-- alignment: the fixtures use the shape the app passes --');
{
  // The bug this guards: alignSnap read `o.x`/`o.y`, systems carry
  // `x_pos`/`y_pos`, so nothing ever matched — and the suite passed throughout
  // because its fixtures were written from the same wrong assumption as the
  // code. A test built on the author's misunderstanding confirms the
  // misunderstanding, so the shape is now checked against the real call site
  // rather than assumed.
  const callsite = /alignSnap\(rawX, rawY, NODE_W, NODE_H, (\w+),/.exec(src);
  ck('the call site passes a plain list of systems',
     !!callsite && callsite[1] === 'others', callsite ? callsite[1] : 'not found');
  const built = /const others = systems\.filter\([^)]*\);/.test(src);
  ck('and builds it from `systems` without remapping the keys', built,
     'if this changes, the fixtures below have to change with it');
  // Belt and braces: a fixture using the OLD shape must now produce nothing,
  // which is what proves the function reads the real field names.
  const wrongShape = alignSnap(103, 400, W, H, [{ x: 100, y: 200 }], 6);
  ck('an x/y-shaped fixture no longer snaps anything',
     near(wrongShape.x, 103) && wrongShape.guides.length === 0,
     JSON.stringify(wrongShape));
}

console.log('-- alignment: left edges --');
{
  // Another box at x=100. Dragging to 103 is within tolerance, so it snaps.
  const r = alignSnap(103, 400, W, H, [{ x_pos: 100, y_pos: 200 }], 6);
  ck('a near-left-edge snaps to it', near(r.x, 100), String(r.x));
  ck('and reports a vertical guide',
     r.guides.length === 1 && r.guides[0].orient === 'v', JSON.stringify(r.guides));
  ck('the guide sits on the shared edge', near(r.guides[0].at, 100),
     String(r.guides[0].at));
  // The line has to reach from the other box's top to the dragged box's
  // bottom, or it does not explain what it is pointing at.
  ck('the guide spans both boxes',
     near(r.guides[0].from, 200) && near(r.guides[0].to, 470),
     JSON.stringify(r.guides[0]));
  ck('the untouched axis is left exactly alone', near(r.y, 400), String(r.y));
}

console.log('-- alignment: inert outside tolerance --');
{
  const r = alignSnap(110, 400, W, H, [{ x_pos: 100, y_pos: 200 }], 6);
  ck('ten pixels away does not snap', near(r.x, 110), String(r.x));
  ck('and draws no guide', r.guides.length === 0, JSON.stringify(r.guides));
}
{
  const r = alignSnap(103, 400, W, H, [{ x_pos: 100, y_pos: 200 }], 0);
  ck('a zero tolerance snaps nothing', near(r.x, 103) && r.guides.length === 0,
     JSON.stringify(r));
}
{
  const r = alignSnap(300, 400, W, H, [], 6);
  ck('with nothing to align to, nothing moves',
     near(r.x, 300) && near(r.y, 400) && r.guides.length === 0,
     JSON.stringify(r));
}

console.log('-- alignment: centres and right edges --');
{
  // Centre-to-centre. Both boxes are 200 wide here, so a centre match and a
  // left match coincide; offset the other box to tell them apart.
  const other = { x_pos: 100, y_pos: 200 };            // centre 200, right 300
  const r = alignSnap(198, 400, W, H, [other], 6);
  ck('a centre match snaps', near(r.x, 200) || near(r.x, 198 + 2),
     String(r.x));
  // Right edge of the dragged box onto the left edge of the other: dragged
  // right edge = x + 200, so x = -100 puts it at 100.
  const r2 = alignSnap(-97, 400, W, H, [other], 6);
  ck('a right-to-left edge match snaps', near(r2.x, -100), String(r2.x));
  ck('and the guide sits at the shared coordinate',
     r2.guides.length === 1 && near(r2.guides[0].at, 100),
     JSON.stringify(r2.guides));
}

console.log('-- alignment: the nearest match wins --');
{
  // Two candidates, 2px and 5px away. The closer one must win, or snapping
  // feels arbitrary.
  const r = alignSnap(100, 400, W, H, [{ x_pos: 102, y_pos: 200 }, { x_pos: 105, y_pos: 600 }], 6);
  ck('the closer candidate is chosen', near(r.x, 102), String(r.x));
}
{
  // Three boxes already share x=100. The guide should span all of them plus
  // the dragged one, not just the first found.
  const others = [{ x_pos: 100, y_pos: 100 }, { x_pos: 100, y_pos: 800 }, { x_pos: 500, y_pos: 300 }];
  const r = alignSnap(102, 400, W, H, others, 6);
  ck('a guide spans every box sharing the coordinate',
     near(r.guides[0].from, 100) && near(r.guides[0].to, 870),
     JSON.stringify(r.guides[0]));
}

console.log('-- alignment: both axes at once --');
{
  const r = alignSnap(103, 203, W, H, [{ x_pos: 100, y_pos: 200 }], 6);
  ck('both axes snap', near(r.x, 100) && near(r.y, 200), JSON.stringify(r));
  ck('and both guides are reported', r.guides.length === 2,
     JSON.stringify(r.guides));
  const orients = r.guides.map(g => g.orient).sort();
  ck('one vertical and one horizontal',
     orients[0] === 'h' && orients[1] === 'v', String(orients));
  // The horizontal guide's span is computed from the snapped x, so it should
  // start at the other box's left edge and end at the dragged box's right.
  const horiz = r.guides.filter(g => g.orient === 'h')[0];
  ck('the horizontal guide uses the snapped position',
     near(horiz.from, 100) && near(horiz.to, 300), JSON.stringify(horiz));
}

console.log('-- angle snapping --');
const at = (x, y) => ({ x: x, y: y });
{
  // Three degrees off horizontal: within tolerance, so it flattens, and the
  // length is kept rather than the endpoint being projected onto the axis.
  const a = at(0, 0), p = at(100, 5.24);        // ~3 degrees
  const r = snapAngle(a, p, 5);
  ck('a near-horizontal segment flattens', near(r.y, 0, 0.01), String(r.y));
  const len0 = Math.sqrt(p.x * p.x + p.y * p.y);
  const len1 = Math.sqrt(r.x * r.x + r.y * r.y);
  ck('and keeps its length', near(len0, len1, 0.001), len0 + ' vs ' + len1);
}
{
  const r = snapAngle(at(0, 0), at(5.24, 100), 5);
  ck('a near-vertical segment straightens', near(r.x, 0, 0.01), String(r.x));
}
{
  // 47 degrees: three degrees past the 45 line, so within tolerance.
  const deg = 47, len = 100;
  const p = at(Math.cos(deg * Math.PI / 180) * len,
               Math.sin(deg * Math.PI / 180) * len);
  const r = snapAngle(at(0, 0), p, 5);
  ck('47 degrees snaps to 45', near(r.x, r.y, 0.01),
     r.x + ',' + r.y);
}
{
  // 22 degrees: midway between 0 and 45, outside tolerance of either.
  const deg = 22, len = 100;
  const p = at(Math.cos(deg * Math.PI / 180) * len,
               Math.sin(deg * Math.PI / 180) * len);
  const r = snapAngle(at(0, 0), p, 5);
  ck('22 degrees is left alone', near(r.x, p.x) && near(r.y, p.y),
     JSON.stringify(r));
}
{
  const r = snapAngle(at(0, 0), at(0, 0), 5);
  ck('a zero-length segment is returned unchanged',
     near(r.x, 0) && near(r.y, 0), JSON.stringify(r));
}
{
  // The wrap-around case: 178 degrees is two degrees from 180, which is a
  // multiple of 45. Measuring the difference the long way round would read it
  // as 358 and refuse to snap.
  const deg = 178, len = 100;
  const p = at(Math.cos(deg * Math.PI / 180) * len,
               Math.sin(deg * Math.PI / 180) * len);
  const r = snapAngle(at(0, 0), p, 5);
  ck('178 degrees snaps to 180 across the wrap',
     near(r.y, 0, 0.01) && r.x < 0, JSON.stringify(r));
}
{
  // Every multiple of 45, approached from both sides, must land exactly.
  let allEight = true, worst = '';
  for (let k = -4; k <= 4; k++) {
    const target = k * 45;
    for (const nudge of [-3, 3]) {
      const deg = target + nudge, len = 80;
      const p = at(Math.cos(deg * Math.PI / 180) * len,
                   Math.sin(deg * Math.PI / 180) * len);
      const r = snapAngle(at(0, 0), p, 5);
      const got = Math.atan2(r.y, r.x) * 180 / Math.PI;
      let diff = got - target;
      while (diff > 180) diff -= 360;
      while (diff < -180) diff += 360;
      if (!near(diff, 0, 0.01)) { allEight = false; worst = target + '+' + nudge + ' -> ' + got; }
    }
  }
  ck('every multiple of 45 snaps exactly, from both sides', allEight, worst);
}
{
  // And nothing between them does. 22.5 is the midpoint, so +-17 degrees from
  // a multiple is the far side of the gap.
  let anyMoved = false, which = '';
  for (let k = -4; k <= 4; k++) {
    for (const nudge of [-17, 17]) {
      const deg = k * 45 + nudge, len = 80;
      const p = at(Math.cos(deg * Math.PI / 180) * len,
                   Math.sin(deg * Math.PI / 180) * len);
      const r = snapAngle(at(0, 0), p, 5);
      if (!near(r.x, p.x, 0.001) || !near(r.y, p.y, 0.001)) {
        anyMoved = true; which = String(deg);
      }
    }
  }
  ck('and an angle 17 degrees off is never moved', !anyMoved, which);
}
{
  // The anchor is not always the origin.
  const r = snapAngle(at(500, 300), at(600, 305), 5);
  ck('snapping is relative to the anchor, not the origin',
     near(r.y, 300, 0.01) && r.x > 500, JSON.stringify(r));
}

console.log('-- right-angle corners --');
{
  // The reported bug: a diagonal straightened but a square corner never
  // squared up, because only the segment arriving at the bend was adjusted.
  const prev = at(100, 100), next = at(400, 500);
  // Dragging the bend to roughly above `prev`: its x should lock to prev.x,
  // which makes that segment exactly vertical.
  const r = snapBend(at(104, 300), prev, next, 8, 5);
  ck('a bend near a neighbour\'s x locks to it', near(r.x, 100), String(r.x));
  ck('and keeps the y it was dragged to', near(r.y, 300), String(r.y));
}
{
  const prev = at(100, 100), next = at(400, 500);
  const r = snapBend(at(300, 104), prev, next, 8, 5);
  ck('a bend near a neighbour\'s y locks to it', near(r.y, 100), String(r.y));
}
{
  // The other neighbour counts too, which is the half that was missing.
  const prev = at(100, 100), next = at(400, 500);
  const r = snapBend(at(396, 300), prev, next, 8, 5);
  ck('the following point can be the one it lines up with',
     near(r.x, 400), String(r.x));
}
{
  // Both axes within tolerance of the SAME neighbour would collapse the bend
  // onto that point, which is not a bend. The nearer axis wins and the other
  // floats.
  const prev = at(100, 100);
  const r = snapBend(at(104, 103), prev, null, 8, 5);
  const collapsed = near(r.x, 100) && near(r.y, 100);
  ck('a bend is never collapsed onto its neighbour', !collapsed,
     JSON.stringify(r));
}
{
  // Away from both axes, the 45-degree rotation still applies — this is the
  // behaviour Ben said felt right, so it has to survive the fix.
  const prev = at(0, 0), next = at(1000, 1000);
  const p = at(Math.cos(47 * Math.PI / 180) * 100,
               Math.sin(47 * Math.PI / 180) * 100);
  const r = snapBend(p, prev, next, 8, 5);
  ck('a diagonal still rotates onto 45 degrees', near(r.x, r.y, 0.01),
     r.x + ',' + r.y);
}
{
  const prev = at(0, 0), next = at(1000, 1000);
  const p = at(Math.cos(22 * Math.PI / 180) * 300,
               Math.sin(22 * Math.PI / 180) * 300);
  const r = snapBend(p, prev, next, 8, 5);
  ck('and an angle between the multiples is still left alone',
     near(r.x, p.x) && near(r.y, p.y), JSON.stringify(r));
}
{
  const r = snapBend(at(50, 60), null, null, 8, 5);
  ck('with no neighbours at all, nothing moves',
     near(r.x, 50) && near(r.y, 60), JSON.stringify(r));
}

console.log('-- export geometry --');
{
  // The export has to cover the whole diagram, which is the bug a
  // viewport-sized export would have: whatever is scrolled off-screen simply
  // would not be in the file.
  const sys = [{ x_pos: 100, y_pos: 50 }, { x_pos: 900, y_pos: 600 }];
  const e = diagramExtent(sys, W, H, 40);
  ck('the extent starts left of the leftmost box', near(e.x, 60), String(e.x));
  ck('and leaves room above for the title',
     near(e.y, 50 - 40 - EXPORT_TITLE_SPACE), String(e.y));
  ck('it is wide enough for the rightmost box and its padding',
     near(e.width, (900 + W) - 100 + 80), String(e.width));
  ck('and tall enough for the lowest box, the padding and the title',
     near(e.height, (600 + H) - 50 + 80 + EXPORT_TITLE_SPACE), String(e.height));
  // The right edge of the last box must be inside the viewBox.
  ck('the rightmost box fits inside', e.x + e.width >= 900 + W,
     (e.x + e.width) + ' vs ' + (900 + W));
  ck('the lowest box fits inside', e.y + e.height >= 600 + H,
     (e.y + e.height) + ' vs ' + (600 + H));
}
{
  const e = diagramExtent([], W, H, 40);
  ck('an empty diagram still has a usable size',
     e.width > 0 && e.height > 0, JSON.stringify(e));
}
{
  // One box: width is the box plus padding on both sides, not zero.
  const e = diagramExtent([{ x_pos: 500, y_pos: 500 }], W, H, 40);
  ck('a single box is not exported as a zero-width file',
     near(e.width, W + 80) && near(e.height, H + 80 + EXPORT_TITLE_SPACE),
     JSON.stringify(e));
}

console.log('-- export filename --');
{
  // No extension: the dialog shows this as an editable stem and appends the
  // one belonging to whichever format is picked, so switching format cannot
  // leave a name ending in the wrong thing.
  const f = exportFilename('Maine DOE Data Lineage', 'Current Architecture');
  ck('it names the view, the scenario and the date, with no extension',
     /^Maine DOE Data Lineage \u2014 Current Architecture \u2014 \d{4}-\d{2}-\d{2}$/.test(f),
     f);
  // A scenario name is free text, so it can contain anything a person types.
  const risky = exportFilename('Lineage', 'Q1/Q2 plan: "draft" <v2>');
  ck('characters a filesystem objects to are removed',
     !/[\\/:*?"<>|]/.test(risky), risky);
  ck('and the rest of the name survives', risky.indexOf('Q1Q2 plan') >= 0, risky);
  const bare = exportFilename('', '');
  ck('an unset title and scenario still produce a sensible name',
     bare.indexOf('Data Lineage') === 0 && bare.indexOf('Current Architecture') > 0,
     bare);
}

console.log('-- the diagram heading --');
{
  // One helper feeds both the dialog title and the text drawn into the file,
  // so what someone reads before exporting is what they get afterwards.
  ck('it names the view', exportHeading('Current Architecture')
     === 'Lineage Diagram \u2014 Current Architecture',
     exportHeading('Current Architecture'));
  ck('a scenario view carries its own label',
     exportHeading('Q1 consolidation (Scenario)')
     === 'Lineage Diagram \u2014 Q1 consolidation (Scenario)',
     exportHeading('Q1 consolidation (Scenario)'));
  ck('with no view name it still says something sensible',
     exportHeading('') === 'Lineage Diagram \u2014 Current Architecture',
     exportHeading(''));
  // The dialog title is this with "Export " in front, which is the only place
  // the two could drift apart.
  const src2 = src.slice(src.indexOf('function ExportModal'));
  ck('the dialog builds its title from the same helper',
     /'Export ' \+ exportHeading\(viewName\)/.test(src2),
     'the dialog and the file must not word this differently');
  ck('and the file draws it from the same helper',
     /heading\.textContent = exportHeading\(/.test(src),
     'not found');
}

console.log('-- the download name, per format --');
{
  const stem = 'Lineage \u2014 Current \u2014 2026-10-29';
  ck('svg gets .svg', exportDownloadName(stem, 'svg') === stem + '.svg',
     exportDownloadName(stem, 'svg'));
  ck('png gets .png', exportDownloadName(stem, 'png') === stem + '.png',
     exportDownloadName(stem, 'png'));
  ck('jpg gets .jpg', exportDownloadName(stem, 'jpg') === stem + '.jpg',
     exportDownloadName(stem, 'jpg'));
  ck('an unknown format falls back to the first one',
     exportDownloadName(stem, 'tiff') === stem + '.svg',
     exportDownloadName(stem, 'tiff'));
  // The stem now comes from a text box, so it is cleaned again here rather
  // than trusting whatever exportFilename produced earlier.
  const typed = exportDownloadName('my/diagram: "v2"', 'png');
  ck('a name typed in the dialog is cleaned too',
     !/[\\/:*?"<>|]/.test(typed), typed);
  ck('an emptied name still produces a file',
     exportDownloadName('   ', 'png') === 'Data Lineage.png',
     exportDownloadName('   ', 'png'));
}

console.log('-- the formats offered --');
{
  ck('four formats are offered', EXPORT_FORMATS.length === 4,
     String(EXPORT_FORMATS.length));
  ck('each has an id, a label and an extension',
     EXPORT_FORMATS.every(f => f.id && f.label && /^\.\w+$/.test(f.ext)),
     JSON.stringify(EXPORT_FORMATS.map(f => f.id)));
  // PDF is listed where people look for it, but it is not a download: the
  // flag is what sends it to the browser's print dialog instead.
  const pdf = EXPORT_FORMATS.filter(f => f.id === 'pdf')[0];
  ck('PDF is offered', !!pdf, JSON.stringify(EXPORT_FORMATS.map(f => f.id)));
  ck('and is marked as going through Print rather than downloading',
     !!(pdf && pdf.print), JSON.stringify(pdf));
  ck('the three real downloads are not marked that way',
     EXPORT_FORMATS.filter(f => !f.print).map(f => f.id).join(',') === 'svg,png,jpg',
     EXPORT_FORMATS.filter(f => !f.print).map(f => f.id).join(','));
  ck('rasters are exported at twice the diagram size',
     EXPORT_RASTER_SCALE === 2, String(EXPORT_RASTER_SCALE));
}

console.log('\n  ' + (F.length ? F.length + ' failed, ' : '') + P + ' checks passed');
process.exit(F.length ? 1 : 0);
