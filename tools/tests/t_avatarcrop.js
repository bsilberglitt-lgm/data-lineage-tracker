// The avatar cropper's geometry: zoom, pan, and which square gets stored.
//
//     node tools/tests/t_avatarcrop.js
//
// There is no browser in the assistant's sandbox, so this reads the arithmetic
// straight out of index.html and drives it. It cannot tell you whether
// dragging the picture *feels* right, or whether the dimmed ring is legible
// over a pale photo — both are click-throughs. What it pins down is the one
// failure that would otherwise be found by somebody noticing their stored
// avatar has a transparent corner.
//
// Four properties this exists to protect:
//
//   * **At zoom 1 the shorter side exactly fills the stage.** That is the most
//     of the picture that can be shown without a gap, and it is what makes
//     zoom 1 the sensible floor on the slider.
//   * **The stage is always covered.** For any offset anybody could ask for,
//     at any zoom, the clamp has to leave the image overlapping the whole
//     stage. A gap is not a cosmetic problem: the saved square would carry a
//     transparent wedge, baked in, and the circle it is drawn in would show it
//     against whatever background the chip happens to have.
//   * **The source rectangle stays inside the image.** This is the same
//     property one level down, stated in the units `drawImage` actually takes.
//     Clamping the offset and then computing the rect wrongly would produce
//     exactly the same transparent edge, so both are checked.
//   * **Zooming does not move the picture out from under the circle.** The
//     point of the image under the middle of the stage has to stay there as
//     the slider moves, or repositioning becomes a fight against the zoom.
//
// A note on the shape of these checks, which is deliberate: three of the four
// are **swept over many inputs** rather than asserted at one or two. A single
// example would pass for an off-by-one that only shows on a portrait image at
// high zoom, and "it looked right on the one I tried" is how this project has
// found half its bugs.
const fs = require('fs');

const src = fs.readFileSync('static/index.html', 'utf8');
const start = src.indexOf('// ===== Cropper geometry =====');
const end = src.indexOf('function AvatarCropModal(');
if (start < 0 || end < 0 || end < start) {
  console.log('could not find the cropper geometry in static/index.html');
  process.exit(2);
}

// Function declarations leak out of an eval; `const` and `let` do not. These
// are all functions, so this works — but the stage size is a `const` declared
// outside the slice, so it is read out of the source with a regex rather than
// hardcoded here. A test that hardcodes a value the source owns keeps passing
// after somebody changes it.
eval(src.slice(start, end));

function readConst(name) {
  const m = src.match(new RegExp('const\\s+' + name + '\\s*=\\s*(\\d+)'));
  if (!m) {
    console.log(`could not read ${name} out of the source`);
    process.exit(2);
  }
  return Number(m[1]);
}
const STAGE = readConst('CROP_STAGE');
const OUTPUT = readConst('CROP_OUTPUT');
const MAX_ZOOM = readConst('CROP_MAX_ZOOM');

let passed = 0, failed = 0;
function ck(label, cond, detail) {
  if (cond) { passed++; console.log('  PASS  ' + label); }
  else {
    failed++;
    console.log('  FAIL  ' + label + (detail ? '\n          ' + detail : ''));
  }
}

// The stage and the CSS have to agree, or the circle frames a different square
// from the one that gets saved. The stylesheet is the other half of this
// constant, so it is checked rather than assumed.
const cssSide = (src.match(/\.crop-stage\s*\{[^}]*width:\s*(\d+)px/) || [])[1];
ck(`the stylesheet's stage is ${STAGE}px too`, Number(cssSide) === STAGE,
   `CSS says ${cssSide}, CROP_STAGE is ${STAGE}`);
const holeSide = (src.match(/\.crop-hole\s*\{[^}]*width:\s*(\d+)px/) || [])[1];
ck('the circular window is the full width of the stage',
   Number(holeSide) === STAGE, `hole ${holeSide}, stage ${STAGE}`);

console.log('\n1. At zoom 1 the shorter side fills the stage');
for (const [w, h] of [[800, 800], [1600, 900], [900, 1600], [4000, 3000],
                      [300, 300], [50, 90]]) {
  const sc = cropBaseScale(w, h, STAGE);
  const shortSide = Math.min(w, h) * sc;
  ck(`${w}x${h}: the short side is exactly the stage`,
     Math.abs(shortSide - STAGE) < 1e-9, `got ${shortSide}`);
  ck(`${w}x${h}: the long side is at least the stage`,
     Math.max(w, h) * sc >= STAGE - 1e-9);
}
// A degenerate image must not produce a scale of zero or Infinity, either of
// which would make every later division nonsense rather than merely wrong.
ck('a zero-sized image falls back to a usable scale',
   cropBaseScale(0, 0, STAGE) === 1);

console.log('\n2. The stage is always covered, whatever is asked for');
let gaps = 0, worst = null;
for (const [w, h] of [[1600, 900], [900, 1600], [800, 800], [4000, 3000],
                      [1000, 1001]]) {
  const base = cropBaseScale(w, h, STAGE);
  for (let z = 1; z <= MAX_ZOOM + 0.001; z += 0.25) {
    const scale = base * z;
    // Deliberately absurd requests, including far past either edge: the clamp
    // is the only thing between a drag and a gap, so it is asked for positions
    // no drag would produce as well as ones it would.
    for (const want of [{ x: 9999, y: 9999 }, { x: -9999, y: -9999 },
                        { x: 0, y: 0 }, { x: 40, y: -40 },
                        { x: -(w * scale), y: -(h * scale) }]) {
      const off = cropClamp(want, w, h, scale, STAGE);
      const covers = off.x <= 1e-9 && off.y <= 1e-9
        && off.x + w * scale >= STAGE - 1e-9
        && off.y + h * scale >= STAGE - 1e-9;
      if (!covers) { gaps++; worst = { w, h, z, want, off }; }
    }
  }
}
ck('no offset at any zoom leaves a gap', gaps === 0,
   gaps ? `${gaps} gaps, e.g. ${JSON.stringify(worst)}` : '');

console.log('\n3. The source rectangle stays inside the image');
let outside = 0, example = null;
for (const [w, h] of [[1600, 900], [900, 1600], [800, 800], [4000, 3000]]) {
  const base = cropBaseScale(w, h, STAGE);
  for (let z = 1; z <= MAX_ZOOM + 0.001; z += 0.5) {
    const scale = base * z;
    for (const want of [{ x: 9999, y: 9999 }, { x: -9999, y: -9999 },
                        { x: 0, y: 0 }, { x: -13, y: -77 }]) {
      const off = cropClamp(want, w, h, scale, STAGE);
      const r = cropSourceRect(off, scale, STAGE);
      const ok = r.sx >= -1e-6 && r.sy >= -1e-6
        && r.sx + r.side <= w + 1e-6 && r.sy + r.side <= h + 1e-6;
      if (!ok) { outside++; example = { w, h, z, off, r }; }
    }
  }
}
ck('the rectangle drawn is never outside the source', outside === 0,
   outside ? `${outside} bad rects, e.g. ${JSON.stringify(example)}` : '');

// The rect is square in source pixels, because the output canvas is square.
// A non-square rect stretched into a square canvas is a subtly squashed face,
// which is the one subject where that is unmistakable and the last thing
// anybody would suspect of the arithmetic.
const sq = cropSourceRect({ x: -10, y: -40 }, 0.5, STAGE);
ck('the rectangle is square', Math.abs(sq.side - STAGE / 0.5) < 1e-9);

console.log('\n4. A square image at zoom 1 maps the whole picture');
for (const n of [300, 800, 4000]) {
  const base = cropBaseScale(n, n, STAGE);
  const off = cropCenter(n, n, base, STAGE);
  const r = cropSourceRect(off, base, STAGE);
  ck(`${n}x${n}: the crop is the whole image`,
     Math.abs(r.sx) < 1e-9 && Math.abs(r.sy) < 1e-9
     && Math.abs(r.side - n) < 1e-6,
     JSON.stringify(r));
}

console.log('\n5. Centering centers, on the axis with room to spare');
{
  const w = 1600, h = 900;
  const base = cropBaseScale(w, h, STAGE);
  const off = cropCenter(w, h, base, STAGE);
  ck('a landscape image is centered horizontally and flush vertically',
     Math.abs(off.x - (STAGE - w * base) / 2) < 1e-9
     && Math.abs(off.y) < 1e-9, JSON.stringify(off));
  const t = cropCenter(900, 1600, cropBaseScale(900, 1600, STAGE), STAGE);
  ck('and a portrait image the other way round',
     Math.abs(t.x) < 1e-9 && t.y < 0, JSON.stringify(t));
}

console.log('\n6. Zooming keeps the middle of the stage where it was');
{
  const w = 1600, h = 900;
  const base = cropBaseScale(w, h, STAGE);
  let drift = 0, worstDrift = null;
  for (let z = 1; z <= MAX_ZOOM - 0.5; z += 0.5) {
    const from = base * z, to = base * (z + 0.5);
    // Start from a clamped, off-center position: the identity is trivially
    // true at the center, and the center is not where somebody is when they
    // reach for the slider.
    const off = cropClamp({ x: -37, y: -11 }, w, h, from, STAGE);
    const next = cropZoomAbout(off, from, to, STAGE);
    // The source point under the middle of the stage, before and after.
    const beforeMid = (STAGE / 2 - off.x) / from;
    const afterMid = (STAGE / 2 - next.x) / to;
    if (Math.abs(beforeMid - afterMid) > 1e-6) {
      drift++; worstDrift = { z, beforeMid, afterMid };
    }
  }
  ck('the same point stays under the circle as the slider moves', drift === 0,
     drift ? JSON.stringify(worstDrift) : '');
  // Zooming out to where it started must land back where it started, or
  // sliding up and down leaves the picture somewhere it was never put.
  const base1 = base * 1;
  const off0 = cropClamp({ x: -37, y: -11 }, w, h, base1, STAGE);
  const up = cropZoomAbout(off0, base1, base * 2.5, STAGE);
  const back = cropZoomAbout(up, base * 2.5, base1, STAGE);
  ck('zooming in and back out returns the original position',
     Math.abs(back.x - off0.x) < 1e-6 && Math.abs(back.y - off0.y) < 1e-6,
     `${JSON.stringify(off0)} -> ${JSON.stringify(back)}`);
}

console.log('\n7. What gets stored is square and a fixed size');
ck(`the output is ${OUTPUT}px square`, OUTPUT > 0 && Number.isInteger(OUTPUT));
// The stored square is drawn into a fixed canvas, so a crop taken at high zoom
// is upscaled rather than refused. That is deliberate — a small picture framed
// tightly is still the picture they chose — and it is worth stating so nobody
// "fixes" it by rejecting the crop.
ck('a crop smaller than the output is still a valid rectangle',
   cropSourceRect({ x: 0, y: 0 }, 8, STAGE).side === STAGE / 8);

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
