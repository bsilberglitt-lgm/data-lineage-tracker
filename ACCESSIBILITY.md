# Accessibility Report — Data Lineage Tracker

Covers the colour palette introduced in v0.2.1, the screen reader support added
alongside it, and the scenario view added in v0.3.0. Measured against
**WCAG 2.1 Level AA**.

---

## 0. Secondary text — a failure found and fixed in v0.3.0

The v0.2.1 report listed "contrast has not been verified for every small UI
element" as a known gap. Measuring it while choosing the scenario background
turned up a real failure, present since long before scenarios existed.

| Colour | Used for | On white | On page | Result |
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

## 1. Colour contrast

Every system type is drawn as a coloured node with its name inside. Two ratios
matter: the label against the node fill, and the node border against the canvas.

**Requirements:** 4.5:1 for normal text (1.4.3), 3:1 for graphical objects and
user-interface components (1.4.11).

| System type | Label on fill | Border on canvas | Result |
|---|---|---|---|
| **Source** (blue) | **9.27:1** | **4.58:1** | Pass |
| **Destination** (green) | **9.21:1** | **7.15:1** | Pass |
| **Data Store** (purple) | **7.72:1** | **6.63:1** | Pass |
| **Manual Entry** (orange) | **6.85:1** | **4.43:1** | Pass |

Spare colours available when a SuperAdmin adds a new type — all vetted the same
way, so a new type cannot arrive with an unreadable combination:

| Colour | Label on fill | Border on canvas | Result |
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

On `#fcf7e3`, every text colour in the app clears AA: body text 14.70:1,
headings 10.82:1, field labels 5.35:1, secondary text 4.96:1, primary blue
4.51:1, danger red 5.06:1.

**Colour is not the only cue**, as WCAG 1.4.1 requires. The header names the
scenario, the name carries a "(Scenario)" suffix, and a badge appears beside it.
Someone who cannot see the tint at all still knows exactly where they are.

**Proposed and removed records** are shown by pattern, not colour: a dashed
outline and a struck-through, faded row. Both survive every form of colour
blindness, and both are stated in text for screen readers.

---

## 2. Colour blindness

Source-blue and Destination-green were checked under simulated deuteranopia
(green-blind, the most common form), protanopia (red-blind), and tritanopia.

**Finding, stated plainly:** blue and green are distinguishable by *hue* to most
people, but they sit close together in *lightness*. Under deuteranopia the two
node fills differ by a contrast ratio of only 1.16:1. Someone with red-green
colour blindness should not be expected to tell them apart by colour alone.

Two things address this:

1. **Colour is never the only cue.** Every node prints its type name inside it,
   and the type is also shown in the details panel and in the Systems table.
   This satisfies **WCAG 1.4.1 (Use of Colour)** — the requirement is not that
   colours be distinguishable, but that colour is not the *only* way information
   is conveyed.
2. Of the palettes tested, the chosen one gives the **best lightness separation**
   available while keeping every combination above AA. Two earlier candidates
   measured 1.01:1 and 1.10:1 under the same simulation.

If stronger visual separation is ever wanted, the options are different shapes
per type, or a pattern fill — both larger changes than colour alone.

## 3. Screen reader support

Added in this release:

| Area | What was done |
|---|---|
| **Dialogs** | Modals carry `role="dialog"` with `aria-modal`, and confirmations use `role="alertdialog"`. Each is labelled by its own heading via `aria-labelledby`. |
| **Icon-only buttons** | Every `×` close button and the type-key information button have an `aria-label`; decorative icons are `aria-hidden`. |
| **Forms** | Labels are tied to inputs with `htmlFor`/`id`. Fields failing validation set `aria-invalid` and point at their message with `aria-describedby`. |
| **Validation summary** | The "please complete…" summary uses `role="alert"`, so it is announced as soon as it appears. |
| **Status messages** | The notice banner is `role="status"` with `aria-live="polite"`, so saves and errors are read without stealing focus. |
| **Sortable tables** | Headers expose `aria-sort` (`ascending`/`descending`/`none`), are keyboard focusable, and respond to Enter and Space. |
| **Navigation** | The sidebar is a `<nav>` landmark with an accessible name. |
| **The diagram** | The SVG is `role="img"` with a description of its contents, and points the user at the Systems and Data Flows tables, which carry the same information in a form a screen reader can navigate properly. |
| **Version badge** | Exposed as a button, keyboard focusable, labelled "Version 0.2.1 — open release notes". |

### Known gaps

Reported honestly rather than left for someone to discover:

- **The diagram itself is not navigable by screen reader.** It is an image with
  a summary. The equivalent information is fully available in the Systems and
  Data Flows tables, which is the standard approach for a complex visualisation,
  but it is not the same experience.
- **Focus is not trapped inside modals.** Tabbing can move behind an open
  dialog. Closing with Escape is also not yet wired up.
- **Focus is not moved to a dialog when it opens**, nor returned to the button
  that opened it on close.
- **No skip-to-content link** for keyboard users to bypass the sidebar.
- **Contrast has not been verified for every small UI element** — the grey
  secondary text on resources and disabled states were not individually
  measured in this pass. Badges added since are measured as they are
  introduced:

  | Badge | Colours | Ratio | AA small text (4.5:1) |
  |---|---|---|---|
  | `badge-manual` — a data flow with manual steps | `#8a4b00` on `#fff3e0` | 6.20:1 | passes |
  | `fd-off` — a field marked not used | `#8a4b00` on `#fff3e0` | 6.20:1 | passes |
  | Account chip, secondary line | `#4a5764` on `#fff` | 7.40:1 | passes |
  | Account chip, avatar initials | `#fff` on `#2E75B6` | 4.84:1 | passes |
  | Account chip, name | `#1B3A5C` on `#fff` | 11.63:1 | passes |
  | Account menu item, hovered | `#1B3A5C` on `#f1f5f9` | 10.61:1 | passes |

  The account chip moved out of the navy sidebar into the white toolbar in
  0.6.0, so none of its colours are inherited any more and all four were
  measured rather than assumed.
- **No testing with an actual screen reader.** The markup follows established
  patterns, but VoiceOver, NVDA, or JAWS have not been used against it. That
  testing is what would turn "should work" into "does work."

None of these block the release. If this needs to meet a formal accessibility
standard — likely if it becomes an official state system — the focus management
and a real screen reader pass are the next things to do.

## 4. How these numbers were produced

Contrast ratios use the WCAG 2.1 relative luminance formula, computed directly
from the hex values in `TYPE_PALETTE` (`app.py`). Colour-blindness figures use
standard linear transformation matrices for each type of dichromacy. The
canvas background is `#f5f8fb`.

Anyone can re-run these; the calculation is a dozen lines and depends on nothing
beyond the palette values themselves.
