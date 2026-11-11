# Accessibility Report — Data Lineage Tracker

Covers the color palette introduced in v0.2.1, the screen reader support added
alongside it, and the scenario view added in v0.3.0. Measured against
**WCAG 2.1 Level AA**.

---

## 0. Secondary text — a failure found and fixed in v0.3.0

The v0.2.1 report listed "contrast has not been verified for every small UI
element" as a known gap. Measuring it while choosing the scenario background
turned up a real failure, present since long before scenarios existed.

| Color | Used for | On white | On page | Result |
|---|---|---|---|---|
| `#888` | secondary text, 24 places | 3.54 | 3.21 | **Failed AA** |
| `#999` | hints, empty states, dates, 8 places | 2.85 | 2.58 | **Failed AA** |
| `#6b6b6b` | the replacement | **5.33** | **4.82** | Pass |

All 32 uses now use `#6b6b6b`, which clears AA on white panels, on the page
background, and on the scenario tint. Nothing else about the design changed.

**Still outstanding:** `#bbb` is used twice — the breadcrumb separator and the
"none" tag — at roughly 1.9:1. Both are arguably decorative rather than
informational, and darkening them changes how the interface looks rather than
just how it measures, so they are left as a deliberate decision rather than
silently restyled.

---

## 1. Color contrast

Every system type is drawn as a colored node with its name inside. Two ratios
matter: the label against the node fill, and the node border against the canvas.

**Requirements:** 4.5:1 for normal text (1.4.3), 3:1 for graphical objects and
user-interface components (1.4.11).

| System type | Label on fill | Border on canvas | Result |
|---|---|---|---|
| **Source** (blue) | **9.27:1** | **4.58:1** | Pass |
| **Destination** (green) | **9.21:1** | **7.15:1** | Pass |
| **Data Store** (purple) | **7.72:1** | **6.63:1** | Pass |
| **Manual Entry** (orange) | **6.85:1** | **4.43:1** | Pass |

Spare colors available when a SuperAdmin adds a new type — all vetted the same
way, so a new type cannot arrive with an unreadable combination:

| Color | Label on fill | Border on canvas | Result |
|---|---|---|---|
| Teal | 8.33:1 | 5.63:1 | Pass |
| Slate | 8.58:1 | 6.94:1 | Pass |
| Magenta | 8.79:1 | 6.60:1 | Pass |
| Olive | 7.78:1 | 5.34:1 | Pass |

Every combination clears AA for normal text, and most clear **AAA** (7:1) as
well. The weakest measurement anywhere is the Manual Entry border at 4.43:1,
comfortably above the 3:1 required for a graphical object.

## 1a. The scenario background

A scenario is tinted so it is obvious you are not looking at the live diagram.
Choosing the shade is a trade-off: too pale and it does nothing, too strong and
it starts eating into text contrast.

| Candidate | Difference from the page background (CIE Lab dE) | Worst text contrast on it |
|---|---|---|
| `#fdfaef` | 8.3 — easy to miss | 4.63 |
| **`#fcf7e3` (chosen)** | **12.7 — clearly noticeable** | **4.51** |
| `#fbf3d5` | 17.9 | 4.35 |
| `#f7ecc0` | 25.4 — heavy | 4.09 **fails AA** |

Contrast ratio is the wrong tool for comparing two pale backgrounds — every
candidate scores about 1.0 against the page — so perceptual difference (CIE Lab)
was used to judge visibility, and contrast ratio only to check the text on top.

On `#fcf7e3`, every text color in the app clears AA: body text 14.70:1,
headings 10.82:1, field labels 5.35:1, secondary text 4.96:1, primary blue
4.51:1, danger red 5.06:1.

**Color is not the only cue**, as WCAG 1.4.1 requires. The header names the
scenario, the name carries a "(Scenario)" suffix, and a badge appears beside it.
Someone who cannot see the tint at all still knows exactly where they are.

**Proposed and removed records** are shown by pattern, not color: a dashed
outline and a struck-through, faded row. Both survive every form of color
blindness, and both are stated in text for screen readers.

---

## 1b. A task's circle on the diagram (v0.7.0)

A task is drawn as a small circle, and one attached to a system sits **inside**
that system's box — so the ring has to read against every fill in
`TYPE_PALETTE`, not just against the canvas.

| Ring color | Worst palette fill | Ratio | On white |
|---|---|---|---|
| **`#1B3A5C` (chosen)** | purple `#e8daef` | **8.69:1** | 11.63:1 |
| `#D4772C` (the app's selected orange) | purple `#e8daef` | **2.43:1 — fails** | 3.24:1 |

The second row is why **selection is an outer halo rather than a recolor**.
Recoloring the ring to the selected orange was the first attempt and it is
worse than useless: at 2.43:1 it is under the 3:1 WCAG 1.4.11 requires for a
non-text indicator, so a selected task inside a purple box would have been
*less* visible than an unselected one. The dark ring stays and the orange is
drawn as a concentric ring outside it, where its neighbors are the dark ring
(3.58:1) and the fill.

Checked against all eight palette fills, not a chosen one: purple 8.69, green
8.71, blue 9.46, orange 9.54, teal 9.61, plus white 11.63 and the scenario
tint 10.82. The worst case is what matters, and it is not the lightest fill —
an earlier note in CLAUDE.md said 9.4:1 on the basis of that wrong assumption.

The circle's **fill** carries the status (see 1c below). The fill is
decorative as far as the *shape* goes: against some box fills it is nearly the
same luminance (1.01:1 on magenta), and it is the **ring** that delineates the
circle, which is what 1.4.11 asks of a non-text indicator. That is also why the
ring keeps one color across all five statuses — recoloring it per status
would put the measurement above back in play five times over.

The circle carried a letter "T" in the first version and does not now — a
single letter inside an 11px circle was illegible at the zoom levels people
actually use, and it is not a label in any language.

**Color is not the only cue.** Selection also opens the details flyout. A
proposed task is drawn with a **dashed** ring and a retired one is struck
through, matching systems and flows — patterns, not colors. The status is in
the circle's hover card as words, and the whole task list is available as a
table in the Tasks catalog, which is what a screen reader user will use.

**Known gap:** the circle is a drag target with no keyboard equivalent, the
same gap the system boxes and arrow handles already have. Positions are
layout-only, so nothing is unreachable — a task is fully readable and editable
from the Tasks catalog and its flyout — but repositioning one needs a mouse.

---

## 1c. Task status colors (v0.7.1, extended in 0.8.0)

The circle's fill says where the work stands. Seven states, and the only
contrast that has to hold is against the ring and the mark drawn inside it —
both `#1B3A5C`, as above.

| State | Fill | Ring and mark against it |
|---|---|---|
| Completed | `#b2dcb6` | 7.64:1 |
| Past its end date | `#f2b3ab` | 6.54:1 |
| Blocked | `#e8938a` | **4.97:1 (worst case)** |
| In progress | `#f6e08f` | 8.85:1 |
| Not started, past its start date | `#f8c489` | 7.33:1 |
| Automated, nothing recorded yet | `#93bde4` | 5.89:1 |
| Nothing recorded yet | `#dfe3e8` | 9.02:1 |

Every one clears 4.5:1, so the mark passes as text as well as the 3:1 that
1.4.11 asks of a non-text indicator. Measured over all seven fills rather than
inferred from the lightest — the mistake made twice before in this project and
recorded in section 1b.

**The two resting states are 1.53:1 apart, and that is on purpose rather than
an oversight.** `#93bde4` against `#dfe3e8` is nowhere near a contrast anybody
could rely on, so the blue is not what tells an automated task from a manual
one: the mark is. Automated draws a triangle, *nothing recorded yet* draws
nothing, and the gap between a mark and no mark does not depend on color
vision at all. The blue exists to make "which of these need a person" a scan
rather than a count, for the people who can use it — which is exactly the role
color plays everywhere else in this section. Raising the contrast between the
two would mean a darker blue, which would then be competing with the five
stage colors for attention it does not deserve: automation is not urgency.

**Blocked and Past its end date are deliberately both red**, which Ben asked
for and which is defensible only because the mark carries the distinction:
red means "needs attention" and the mark says which kind. They are not the
same red — 1.32:1 apart in luminance, so they are distinguishable side by side
— but anyone relying on hue alone will read both as "a problem", which is
true of both. The cross and the exclamation mark are what separate them, and
that is the same reliance on the mark that the rest of this section rests on.

**Each state also draws a mark inside the ring, and that is a requirement
rather than a flourish.** The stage fills cannot be separated by hue: *in
progress* yellow and *not started, past its start date* orange are 15 degrees
apart, which is no distinction at all under deuteranopia or protanopia, and
both sit beside a red at 7 degrees. Hue separation across the set runs from 15
to 179 degrees, and luminance ratios between pairs from 1.02 to 1.38:1 — so
anybody relying on lightness alone cannot separate them either. Color here is
a fast scan for people who can use it; the mark is the actual distinction, and
WCAG 1.4.1 is satisfied by the mark, not by the palette.

| State | Mark |
|---|---|
| Completed | a check |
| Past its end date | an exclamation mark |
| In progress | a half-filled disc |
| Blocked | a cross |
| Not started, past its start date | a hollow inner ring |
| Automated, nothing recorded yet | a triangle |
| Nothing recorded yet | **none** |

*Nothing recorded yet* deliberately has no mark: the absence of information is
shown as an absence, the same way the Tasks catalog shows a dash. And the
hollow ring is a ring rather than a dash because a horizontal bar is already
what a retired task draws across itself — two marks that look alike on one
canvas is worse than one that needs a glance at the key.

The diagram key lists each state in use with the real circles — the same component the
diagram draws, so the key cannot drift from what it explains. It appears only
once something on that diagram is actually color-coded; with tasks present but
nothing recorded, the key is a single gray circle labeled "Task", and with no
tasks at all there is no entry. The automated row follows the same rule
independently: it appears whenever something on the diagram is automated, and
is left out of the full key when nothing is, so the key never explains a color
that is not on screen. A key that explains colors appearing nowhere on
screen is noise, and noise in a key is read past. The hover card
names the state in words whenever the color says something the status does
not, which is the two date-driven states.

The cross is two strokes, so it reads at the size the exclamation mark does
and cannot be mistaken for it — which matters more here than elsewhere,
because those two share a color family.

**Known gap:** the marks have not been checked at small zoom by anyone but the
author. They are drawn inside an 11px-radius circle, and the "T" that used to
sit there was removed for being illegible at the zoom levels people actually
use. If a mark cannot be read at 0.5x the answer is a larger circle, not a
cleverer glyph.

## 2. Color blindness

Source-blue and Destination-green were checked under simulated deuteranopia
(green-blind, the most common form), protanopia (red-blind), and tritanopia.

**Finding, stated plainly:** blue and green are distinguishable by *hue* to most
people, but they sit close together in *lightness*. Under deuteranopia the two
node fills differ by a contrast ratio of only 1.16:1. Someone with red-green
color blindness should not be expected to tell them apart by color alone.

Two things address this:

1. **Color is never the only cue.** Every node prints its type name inside it,
   and the type is also shown in the details panel and in the Systems table.
   This satisfies **WCAG 1.4.1 (Use of Color)** — the requirement is not that
   colors be distinguishable, but that color is not the *only* way information
   is conveyed.
2. Of the palettes tested, the chosen one gives the **best lightness separation**
   available while keeping every combination above AA. Two earlier candidates
   measured 1.01:1 and 1.10:1 under the same simulation.

If stronger visual separation is ever wanted, the options are different shapes
per type, or a pattern fill — both larger changes than color alone.

## 3. Screen reader support

Added in this release:

| Area | What was done |
|---|---|
| **Dialogs** | Modals carry `role="dialog"` with `aria-modal`, and confirmations use `role="alertdialog"`. Each is labeled by its own heading via `aria-labelledby`. |
| **Icon-only buttons** | Every `×` close button and the type-key information button have an `aria-label`; decorative icons are `aria-hidden`. |
| **Forms** | Labels are tied to inputs with `htmlFor`/`id`. Fields failing validation set `aria-invalid` and point at their message with `aria-describedby`. |
| **Validation summary** | The "please complete…" summary uses `role="alert"`, so it is announced as soon as it appears. |
| **Status messages** | The notice banner is `role="status"` with `aria-live="polite"`, so saves and errors are read without stealing focus. |
| **Sortable tables** | Headers expose `aria-sort` (`ascending`/`descending`/`none`), are keyboard focusable, and respond to Enter and Space. |
| **Navigation** | The sidebar is a `<nav>` landmark with an accessible name. |
| **The diagram** | The SVG is `role="img"` with a description of its contents, and points the user at the Systems and Data Flows tables, which carry the same information in a form a screen reader can navigate properly. |
| **Version badge** | Exposed as a button, keyboard focusable, labeled "Version 0.2.1 — open release notes". |

### Known gaps

Reported honestly rather than left for someone to discover:

- **The diagram itself is not navigable by screen reader.** It is an image with
  a summary. The equivalent information is fully available in the Systems, Data
  Flows and Tasks tables, which is the standard approach for a complex
  visualization, but it is not the same experience.
- **A task's circle cannot be repositioned from the keyboard**, the same gap
  the system boxes and the arrow handles already have. Nothing is unreachable
  — position is layout only, and a task is fully readable and editable from the
  Tasks catalog — but moving one needs a mouse.
- **Focus is not trapped inside modals.** Tabbing can move behind an open
  dialog. Closing with Escape is also not yet wired up.
- **Focus is not moved to a dialog when it opens**, nor returned to the button
  that opened it on close.
- **No skip-to-content link** for keyboard users to bypass the sidebar.
- **Contrast has not been verified for every small UI element** — the gray
  secondary text on resources and disabled states were not individually
  measured in this pass. Badges added since are measured as they are
  introduced:

  | Badge | Colors | Ratio | AA small text (4.5:1) |
  |---|---|---|---|
  | `badge-manual` — a data flow with manual steps | `#8a4b00` on `#fff3e0` | 6.20:1 | passes |
  | `fd-off` — a field marked not used | `#8a4b00` on `#fff3e0` | 6.20:1 | passes |
  | Account chip, secondary line | `#4a5764` on `#fff` | 7.40:1 | passes |
  | Account chip, avatar initials | `#fff` on `#2E75B6` | 4.84:1 | passes |
  | Account chip, name | `#1B3A5C` on `#fff` | 11.63:1 | passes |
  | Account menu item, hovered | `#1B3A5C` on `#f1f5f9` | 10.61:1 | passes |
  | Disabled input value | `#5a6472` on `#e4e9ef` | 4.91:1 | passes |

  The account chip moved out of the navy sidebar into the white toolbar in
  0.6.0, so none of its colors are inherited any more and all four were
  measured rather than assumed.
- **No testing with an actual screen reader.** The markup follows established
  patterns, but VoiceOver, NVDA, or JAWS have not been used against it. That
  testing is what would turn "should work" into "does work."

None of these block the release. If this needs to meet a formal accessibility
standard — likely if it becomes an official state system — the focus management
and a real screen reader pass are the next things to do.

## 4. How these numbers were produced

Contrast ratios use the WCAG 2.1 relative luminance formula, computed directly
from the hex values in `TYPE_PALETTE` (`app.py`). Color-blindness figures use
standard linear transformation matrices for each type of dichromacy. The
canvas background is `#f5f8fb`.

Anyone can re-run these; the calculation is a dozen lines and depends on nothing
beyond the palette values themselves.
