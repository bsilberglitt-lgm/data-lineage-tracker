# Working on this project

Notes for whoever picks this up next — human or AI assistant. Read this before
making changes.

## Standing reminders

### 1. Release notes need Ben's review before they go live

**Every release, without being asked:**

1. Bump `VERSION` in `version.py` (semantic versioning; stay on `0.x` while in beta —
   MINOR for new features, PATCH for fixes only).
2. Draft the release notes and add them to `SEED_RELEASES` in `app.py`, or create
   the release in the app, **as a draft** (`is_published = 0`).
3. **Show Ben a preview of the draft notes and explicitly ask for edits before
   publishing.** Publishing is what triggers the what's-new modal for every user,
   so notes should not go out unreviewed.
4. Publish only after he has confirmed — flip the seed's `is_published` to `1`
   (or publish in the app).

How the seed and the database relate, since it is easy to get wrong:

- Release notes live in the **database**. `SEED_RELEASES` is what populates it.
- A release that is still a **draft** is owned by the code: `ensure_releases()`
  re-seeds its title, notes, and published flag from `SEED_RELEASES` on every
  start. So edit drafts in the code, not in the app — an in-app edit to a draft
  is overwritten at the next restart.
- A release that is **published** is live and is never overwritten. An
  administrator can edit published notes in the app and those edits survive
  restarts and deploys.

### 2. Keep the release checklist current — in BOTH formats

`RELEASE_CHECKLIST.md` is the source of truth for how a release is done, and
`RELEASE_CHECKLIST.docx` is a Word copy Ben ticks off by hand.

**Whenever anything changes that affects the release process** — a new command,
a new environment, a changed verification step, a new one-time setup item, a
newly discovered failure mode worth adding to Troubleshooting — you must:

1. Update `RELEASE_CHECKLIST.md`, and
2. Regenerate the Word copy:

   ```bash
   pandoc RELEASE_CHECKLIST.md -o RELEASE_CHECKLIST.docx --toc --toc-depth=2
   ```

3. Tell Ben the Word version has been regenerated, and share it, so he replaces
   whatever copy he is working from.

Two formatting notes for the markdown, learned the hard way: keep list-item
continuation content indented **2 spaces** (6 spaces makes pandoc render fenced
code blocks as literal ``` characters), and check the result by converting to
PDF and looking at the pages before declaring it done.

### 3. Move uploaded files to S3 when object storage exists

Uploaded documents (the Resources feature) and schema `.sql` files are currently
stored **in PostgreSQL** as binary columns. That was a deliberate interim choice:
it needs no extra vendor, and the files are covered by the database's existing
backups and point-in-time recovery.

**The bucket now exists** (September 2026) — it was created for the nightly
database backups, so the precondition this reminder was waiting on is met. The
migration itself has not been done. The relevant code is
`resource_version.file_data`, `schema_snapshot.source_sql` and
`app_branding.logo_data` in `app.py`, plus `ResourceFileHandler` and
`BrandingLogoHandler`.

Two things make it worth doing rather than leaving, and the first is now
measured rather than assumed: **uploaded documents are the largest thing in the
database.** As of September 2026 `resource_version` is 3.2 MB against 808 KB
for `schema_column` and 560 KB for the entire audit trail, and every byte of it
is carried in each of the 45 retained nightly backups. Second, the app's memory
footprint scales with the largest file anyone attaches, because a download
reads the whole column into Python.

Neither is urgent: at this size the storage costs cents a year, so the decision
to wait for a Maine DOE AWS account rather than migrate into a personal one is
about avoiding a production dependency, not about cost.

**The backup job's credential must not be reused for this.** It deliberately
holds `s3:PutObject` on one prefix and nothing else — no read, no delete — so
that a leaked key cannot destroy the backup history. The app needs read, write
*and* delete on a different prefix. Two IAM users, two prefixes. See
`backup/README.md`.

Until then, uploads are capped at 10 MB (`MAX_UPLOAD_BYTES`). The logo has its
own, much lower caps — `MAX_LOGO_BYTES` and `MAX_LOGO_DIMENSION` — because it is
rendered about 32px tall and because a byte cap alone does not stop a
decompression bomb.

**Uploaded images are identified by their magic bytes, never by extension or by
the browser's content type** (`inspect_image`). The type that comes back is what
gets stored and what the endpoint serves, so the two can never disagree. Do not
add SVG to the accepted list: an SVG can carry script, and this one is served
from our own origin.

### 4. Backups: the job never deletes

`backup/` holds a Render Cron Job that dumps the database nightly to S3 —
30 daily, 12 monthly, 3 years of annual snapshots. Two properties are load-
bearing and easy to undo by accident:

- **Retention is done by S3 lifecycle rules, not by the script.** The IAM user
  has `s3:PutObject` only. A credential that cannot delete is one that
  ransomware cannot use to destroy the backup history. Do not "simplify" this
  by having the script prune old files.
- **A dump smaller than 10 KB is refused.** `pg_dump | gzip` reports *gzip's*
  exit status, so an unreachable database yields a valid empty archive and a
  script that believes it succeeded. Without that guard the job would upload a
  stub every night and look healthy until someone needed it.

`sh backup/test_backup.sh` exercises both, plus the date logic, with no
database, AWS account or Docker. The restore procedure is Part 3 of
`backup/README.md`, and the release checklist carries a quarterly reminder to
actually run it.

## Architecture

- **Backend:** Python + Tornado, PostgreSQL via `db.py` (a thin shim that keeps
  sqlite-style `?` placeholders and row access working).
- **Frontend:** a single `static/index.html` — React compiled in the browser by
  Babel. React and Babel are **pinned** by version in the script tags; do not
  un-pin them. An unpinned Babel once switched to the automatic JSX runtime,
  emitted an `import` statement, and produced a blank page.
- **Schema:** Alembic migrations in `alembic/versions/`. Never edit tables by
  hand; add a migration.

  **Never edit a migration that has already run — anywhere, including one
  laptop.** Alembic records it as applied and will not revisit it, so the
  change reaches new databases only, and the environment that already ran it
  diverges from the code with no warning. The symptom is a 500 complaining
  about a column that plainly exists in the migration file. This happened
  during 0.3.0: `is_hidden` was added to 0005 after 0005 had run locally, and
  0006 exists only to repair it. Always add a new migration, and make catch-up
  migrations idempotent (`ADD COLUMN IF NOT EXISTS`) so they are safe on
  databases built either way.
- **Config:** environment variables only, so the same container runs locally, on
  Render, and later on AWS or Azure.

## Conventions worth preserving

- **Boolean-ish columns are `INTEGER` (0/1)**, not `BOOLEAN`. The API and the
  frontend both depend on this.
- **The audit log is append-only.** Nothing in the application edits or deletes
  an audit row. Reverting a change writes a *new* event.
- **Bulk operations get one summary audit event**, never one per row. A schema
  upload touches thousands of columns; auditing each one would bury real
  activity and bloat the table.
- **Permissions are enforced server-side on every request** (`permissions.py`,
  and `READ_ROLE` / `WRITE_ROLE` on each handler). Hiding a control in the UI is
  a convenience, not a security boundary.
- **Governance survives schema re-uploads** by matching on
  `schema.table.column` name. Don't break that in `schema_import.py`.

## Testing

There is no browser or PostgreSQL in the assistant's sandbox, so:

```bash
docker compose up
python3 selftest.py --email you@example.gov      # prompts for the password
```

`selftest.py` covers authentication, role enforcement, the governance
inheritance chain, and the audit trail. **It cannot catch rendering bugs** —
click through the UI after frontend changes.

### Checking the frontend compiles

The whole UI is one inline `<script type="text/babel">` block compiled in the
browser, so a syntax error there yields a blank page with the reason buried in
the console. Two checks, in increasing authority:

```bash
python3 tools/check_jsx.py    # fast structural scan; no network or Docker needed
./tools/check_jsx.sh          # real Babel compile in Docker — authoritative
```

Run the Python one after every frontend edit (it works in the assistant's
sandbox, where npm and PyPI are blocked). Run the shell one before committing.
Neither proves the page *renders* — only that it compiles.

`check_jsx.py` is a scan, not a parser, and during 0.4.1 it **passed a region
that had orphaned closing tags** left behind by a block that was moved. If you
have reordered or spliced JSX rather than edited it in place, do not trust the
scan alone: count braces, parens and `<g>` openers against closers over the
region you touched, and run the Docker check before handing it over.

### `field_catalog.py` is generated — do not edit it

`tools/gen_field_catalog.py` parses `static/index.html` and writes
`field_catalog.py`: every field a record has, its section, and the label each
surface uses. Regenerate it after adding, removing or relabelling a field:

```bash
python3 tools/gen_field_catalog.py            # write it
python3 tools/gen_field_catalog.py --check    # fail if stale (run before commit)
```

Generated rather than hand-written because a hand-kept list of 77 fields drifts
the first time someone adds a field and forgets it — and the failure is silent:
the new field simply cannot be given a definition, with no error anywhere.
Every field already states its own database column at its call site, so the
list is derived from what is actually rendered.

**Keyed on the column, never the label.** The same column is labelled
differently depending on where it is drawn:

    system.encryption_at_rest    edit "Encrypt at Rest" / read "Encryption at Rest"
    data_flow.transfer_method    edit "Method" / modal "Transfer Method"

Seventeen of the 77 have more than one spelling. A definition keyed on text
would attach to one view and be missing from the others, which looks like the
feature half-working. The server resolves every spelling to one column and
emits the definition under each, so a component can look itself up knowing only
the label it was handed — which is what makes this work without editing all 81
field call sites.

**Not every field is an `EditField`.** `ContributorSelect` and
`ContributorField` draw the person pickers, and the first version of the
generator knew only about `EditField` — so six fields, Data Steward among them,
were absent from the catalogue with no error anywhere, and could not be given a
definition. The generator now **fails** on any labelled component it does not
recognise rather than quietly producing a short list. Add new field components
to `EDIT_COMPONENTS` / `READ_COMPONENTS`.

**Shared definitions use `entity_type = "*"`.** A row stored that way applies
to every record type carrying the column, and a row for a specific record type
overrides it (`effective_definition`). Eleven columns appear on more than one
record type; that is what lets Data Steward be defined once for all three while
Description stays different per type. No schema change was needed — the primary
key already allowed it.

### `{0 && <div/>}` renders a zero

Boolean-ish columns are `INTEGER` 0/1 by convention (see above), and JSX renders
the number `0` rather than nothing. So this puts a stray "0" on the page:

```jsx
{record.label_locked && <div className="gov-hint">…</div>}
```

Write `{!!record.label_locked && …}` or a ternary. This reached Ben in 0.4.4,
under the resource edit form's label field. The INTEGER convention makes it a
standing hazard rather than a one-off, and it is invisible to
`tools/check_jsx.py` — the JSX is perfectly valid.

### Reused CSS classes carry their original layout assumptions

`.form-group input` sets `width: 100%` with padding and a border, and
`.form-group label` sets `display: block`. Both are more specific than
`.check-inline`, so a checkbox row dropped into a `form-group` gets stretched
across the full width and its caption wraps to the next line — the box ends up
looking stranded at the right-hand end of the line above its own text. This
reached Ben in 0.5.0, on the "Use one definition everywhere this field appears"
toggle.

`.form-group label.check-inline` and `.check-inline input[type="checkbox"]`
now out-specify both, so the pattern is safe wherever it is used.

The same release produced the same bug from the other direction: the
"Already defined" list reused `.res-title-link`, which is `flex: 1` because a
resource row wants the title to take the slack and push its buttons to the
right edge. In a row that ends with an information marker and a badge, that
stretch flung both to the far side of the panel. `.fd-row .res-title-link`
resets it to `flex: 0 1 auto`.

The lesson for both: a class carries the layout assumptions of the place it
was written for. Before reusing one, read what it does in a flex context, not
just what it looks like. Nothing automated catches a bug of this shape — the
JSX is valid, the API is correct, and every suite passes. Look at the page.

### Tooltips: not the `title` attribute

Explanatory text uses the `Tooltip` component, not `title`. Three reasons, all
found the hard way:

- **The browser owns the delay** — about a second — and the timer restarts on
  every cursor movement, so a `title` feels like it needs one exact spot and a
  steady hand.
- **It is invisible to keyboard users.** `ACCESSIBILITY.md` lists that gap;
  `Tooltip` shows on focus as well as hover and sets `aria-describedby`.
- **A conditional `title` silently disappears.** The Remove button on a
  resource had `title={locked ? '…' : undefined}`, so for ordinary documents
  there was no tooltip at all and nothing to discover.

`Tooltip` renders through a **portal**, deliberately. The details flyout
scrolls, so anything drawn inside it is clipped at the panel edge — which is
where field definitions will live. It sits at `z-index: 200`, above the modal
overlay at 100.

### SVG has no z-index

Paint order *is* stacking order: whatever is written later in the document is
drawn on top and receives the clicks. This cost two rounds in 0.4.1. The
connection points and grab handles for a data-flow arrow sit **on** a system's
border, so while they were emitted inside the flow group — before
`{systems.map(...)}` — the system box covered exactly the pixels you needed to
hit, and nothing was wrong with the handler at all.

The adjustment overlay is therefore rendered **after** the systems, in a
deliberate order: anchor dots first, then grab handles on top, because the two
share coordinates at the arrow's current end and the handle must be the one
that receives the drag. Keep it there. A bug of this shape looks exactly like a
dead event handler, so check the render order before debugging the handler.

## Accessibility

`ACCESSIBILITY.md` records the WCAG contrast measurements for every system-type
colour and what screen reader support exists. **Colours for system types must
come from `TYPE_PALETTE` in `app.py`** — every entry has been verified against
AA, so an arbitrary hex value would bypass that guarantee. The known gaps
(focus trapping, no real screen reader testing) are listed there too.

## Not yet built

- Two-factor authentication and self-service password reset (both need email;
  Resend is the intended provider, and a domain with DNS access is required)
- Single sign-on via OIDC against a state identity provider
- Field-level lineage tracing (tracing a column back through its transformations)
- Agreements management screen
