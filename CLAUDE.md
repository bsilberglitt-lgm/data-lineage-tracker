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

**How to write them.** Short. The 0.5.1 notes ran to 520 words across nineteen
bullets; the same release rewritten as 0.5.2 was 234 words across ten, and that
is the target shape. The rules, agreed with Ben in October 2026:

- **One line per change.** A second sentence only where leaving it out would
  let a reasonable person assume the opposite — that deactivating a contributor
  clears their assignments, say. If a bullet needs three clauses, check whether
  it is really two changes.
- **State the benefit, not the mechanism.** "Email address now appears for
  Owners and Data Stewards" — not an account of the column that used to hold an
  identifier. Nobody reading the what's-new modal needs to know how it works.
- **End with "so …" where the why is not obvious**, and leave it off where it
  is. "Manual Steps records that a flow depends on a person, so the places
  where timing can slip are easy to find" earns it; "PII Elements removed from
  the bar across the top" does not.
- **No internal detail at all.** Migrations, generators, test suites, contrast
  measurements and the checks that stop a bug returning are real work and
  belong in this file and in the commit message. They are not product changes.
  In particular, do not reassure users that a guard now exists: it describes our
  process, and it invites the question of what else is silently broken.
- **Lead with fixes**, since those are what someone will want to confirm first.

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

`check_jsx.py` decides whether a `<` begins JSX by looking at the character
before it. During 0.5.1 a comment placed between `return (` and the root
element of `App` broke that: the last character of the prose was a full stop,
which reads as a comparison, so the opening tag was never recorded and its
closing tag was reported as stray 200 lines away. `expression_position` now
walks back over comments as well as whitespace. If it ever reports a stray
closing tag for an element that is plainly opened, suspect the few characters
before the opening tag rather than the tag itself.

A `{/* ... */}` comment cannot sit outside the root element of a `return`: it
is a JSX expression container, and there is no element for it to belong to.
Use a `//` comment above the `return` instead. This is a genuine syntax error
rather than a scan artefact, and `check_jsx.py` reports it correctly.

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

Generated rather than hand-written because a hand-kept list of 80 fields drifts
the first time someone adds a field and forgets it — and the failure is silent:
the new field simply cannot be given a definition, with no error anywhere.
Every field already states its own database column at its call site, so the
list is derived from what is actually rendered.

**Keyed on the column, never the label.** The same column is labelled
differently depending on where it is drawn:

    system.encryption_at_rest    edit "Encrypt at Rest" / read "Encryption at Rest"
    data_flow.transfer_method    edit "Method" / modal "Transfer Method"

Seventeen of the 80 have more than one spelling. A definition keyed on text
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

**Modal fields declare their column; required-ness is read, not listed.**
Every optional field in the three Add windows used to be a hand-written
`form-group` whose column appeared only inside an onChange closure, so a modal
field had no identity anything else could use. The generator recovered the
column with a regex and found **40 of 80**: anything spread over several lines,
anything inside a `form-row`, and every checkbox was invisible to it, with no
error. `ModalField`, `ModalCheck` and `ReqField` now carry `col=`, so this is a
read rather than a guess, and `ReqField` is also where the catalogue learns a
field is **required** — which is what makes it unhideable.

Four hand-written blocks remain, deliberately: a data flow's display name and
the three inputs for the flow created alongside a Manual Entry system are not
record fields, so there is nothing for hiding or a definition to resolve. They
carry no `col` and a comment saying why.

The generator **fails** if a modal's `useRequired` list names a catalogue field
that is drawn with an optional component. Those are two statements of the same
fact, and a field that validates as required while the catalogue thinks it
optional would be hideable — which would make that record type impossible to
create.

**Field hiding is per record type, and never deletes.** `field_hidden`
(migration 0012) is keyed on `(entity_type, column_name)` with **no shared
`"*"` scope**, unlike definitions: what a field *means* is usually the same
everywhere, whether you *use* it is not, and one tick emptying three screens is
a worse surprise than three ticks. Hiding never touches the stored value, so
the preference is safe to get wrong — the alternative is a mis-click that needs
a restore. Required fields are refused on write *and* filtered on read, because
a row could predate a field becoming required.

Note the asymmetry in how the two are looked up: **definitions are keyed on
every label**, because 81 call sites pass nothing else; **hiding is keyed on
the column**, with a label-to-column map sent alongside for the older
components. Keying hiding on text would mean a field relabelled in one view
quietly reappearing there.

**Shared definitions use `entity_type = "*"`.** A row stored that way applies
to every record type carrying the column, and a row for a specific record type
overrides it (`effective_definition`). Eleven columns appear on more than one
record type; that is what lets Data Steward be defined once for all three while
Description stays different per type. No schema change was needed — the primary
key already allowed it.

### The harness applies every migration in `alembic/versions/`

`gen_harness_schema.py` understood `server_default=sa.text("0")` but not a bare
`server_default="0"`, and silently dropped the latter. Harmless in a
`CREATE TABLE`, where SQLite allows `NOT NULL` with no default — fatal in an
`ALTER TABLE ADD COLUMN`, which fails with *Cannot add a NOT NULL column with
default value NULL* and takes the whole harness schema down. Migration 0013
found it; 0012 had been quietly losing its default since the release before.
Both forms are handled now.

`tools/harness_setup.py` used to carry a hand-written list of migration
filenames. When 0012 arrived the harness simply did not have the new table, and
every suite touching it failed with *no such table* — which reads like a bug in
the feature rather than a stale list. The list is now derived from the
directory, so a new migration is picked up by existing it. Keep the `NNNN_`
naming: the order is the filename order.

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

### Self-service is a separate endpoint, not a relaxed admin one

`/api/auth/profile` (0.6.0) lets any signed-in user change their own
`first_name`, `last_name` and `title`. It is deliberately **not** a widened
`/api/users/<id>`: that handler can change a role, issue a temporary password,
deactivate an account, and refuses when the actor cannot manage the target. If
self-service went through it with a narrower field list, the protection would
live in a list of allowed keys rather than in the role check — and the next
field added to that handler would be editable by everyone by default.

Anything outside the three fields is **refused with a 403 naming the field**,
not ignored. Silently dropping `role` would make a privilege-escalation attempt
look like a success, and this project has found enough silently-dropped fields
already. A mixed request is refused whole: no partial application.

**Email is not self-service.** It is the sign-in identity *and* the name
against every audit entry, so changing it changes both how someone signs in and
how their history reads.

Passwords still go through `/api/auth/change-password`, which already verified
the current password, enforced the policy, revoked the other sessions and
audited the change — the profile window only needed a UI.

**A title lives on the account** (migration 0015). A contributor with a linked,
*active* account shows that account's title; a contributor with no account
keeps their own `contributor.title`, which is the only place that column is
still read. A deactivated account stops supplying one, because its title is no
longer current. No data migration was needed: Ben cleared every contributor
title in production first, so there was nothing to copy up — had there been,
the migration would have had to copy each linked contributor's title onto its
account or the titles would simply have stopped being displayed.

**A contributor created from an account copies the name only.** Add Contributor
can pick an existing `app_user`, which fills the name and links the two in one
transaction. The email and title are deliberately *not* copied: both resolve
from the account wherever a contributor is displayed, so a copy here would be a
snapshot that goes stale the moment either changes — the same disagreement this
release removed for titles. The window shows them, says where they come from,
and hides the corresponding form fields.

`/api/users/pickable` exists because creating a contributor is an **Editor's**
job while `/api/users` is Administrator-only. It returns the minimum that job
needs — name, email, title, and whether they are already linked — and nothing
about anyone's access. Already-linked accounts are returned rather than
filtered out, so the window can grey them and name the contributor that has
them; omitting them would leave someone hunting for a colleague who is simply
absent. The server refuses a double link with a 409 as well, because the list
is a snapshot and two people can be adding contributors at once.

### Deactivating an account revokes its sessions immediately

Not at next sign-in: `UPDATE user_session SET revoked_at` runs as part of the
deactivation, and `load_session` also refuses an inactive user on every
request. Worth knowing when writing tests — a suite that deactivates an
account to prove something, reactivates it, and then reuses the old session
gets a 401 where it expected a 403. Sign in again instead.

### A read field may mention more than one `data.` value

`read_labels` in the catalogue generator took the *first* `data.X` in a read
field's attributes. That broke on a resolved value with a fallback:

```jsx
<ReadField label="Title" value={data.effective_title || data.title} />
```

It attributed the row to `effective_title`, which is not a column, so
`contributor.title` silently lost its read label and a definition written for
Title stopped appearing in the read view. The generator now prefers the first
name the **edit view** already established as a real column, falling back to
the first match. Keep the edit view parsed first; that ordering is what makes
the distinction possible.

### A blank page after sign-in is usually an undeclared identifier

`check_jsx.py` proves the JSX is *structurally* sound and `check_payloads.py`
proves the API contract holds. Neither notices a name that is used and declared
nowhere — that compiles perfectly and throws at render.

It happened in 0.5.2. One edit added a button calling `loadImpact`, a dialog
reading `impact`, and a `useEffect` calling `setImpact(null)`; the edit that was
supposed to declare them never wrote its result. The sign-in screen rendered,
and everything after it was blank. Every server-side suite passed, because the
fault was entirely in the browser.

```bash
python3 tools/check_identifiers.py --check    # run before commit
```

Three narrow rules: every `setX(...)` call has a declaration, every
`onClick={handler}` names something declared, and no `\uXXXX` escape sits in
JSX **text**.

That third one reached Ben in 0.6.0: `<option>No \u2014 enter details</option>`
rendered the backslash and the digits. Inside a quoted string or a template
literal the escape is interpreted; between tags it is four literal characters.
Use the character itself or an HTML entity (`&mdash;`). The rule is checked
against a copy with template literals blanked as well, since a backtick string
is exactly where such an escape belongs — seven of the eight escapes in
`index.html` are legitimate for that reason.

A first attempt at the first two rules tried to find *every* undeclared
identifier and produced 183 false positives, because JSX text content reads as
identifiers and separating prose from code needs a real JSX parser. All three
rules as written need unambiguous syntax, so prose cannot trigger them, and
each sits exactly where its failure shows up. npm is blocked in the assistant's
sandbox, so ESLint is not available to the thing making most of these edits;
this is the substitute.

### Hand-written payloads must name every field the server accepts

`DetailPanel.saveEdit` builds each record type's PUT body as an object literal,
and the update handlers write **only the keys they are sent**
(`for key in self.FLOW_FIELDS: if key in data`). A field the server accepts but
the browser never sends is therefore **silently unsaveable**: the control
appears, the dropdown works, the save returns 200, and the value never changes.

That is what happened to **Owner on a data flow**. It was in the Add window, in
the edit view and in the read view, and missing from one object literal — so an
Owner could be set when creating a flow and never changed afterwards. **Source
of Truth on a data element** had the same hole, and fixing it exposed a second
bug: it is a foreign key to `system.system_id` but was drawn as a free-text box,
so sending it at all turned a silent no-op into a 500 for anyone typing a
system's name. It is a picker now.

```bash
python3 tools/check_payloads.py            # report
python3 tools/check_payloads.py --check    # exit 1 on a gap (run before commit)
```

The check is deliberately one-directional. A gap is a bug; the reverse is not —
the browser sends `proposed`, which no field tuple contains because the server
handles it separately. Fields that genuinely should not be editable from the
flyout go in `DORMANT` in that file, each with a reason.

This is the `field_catalog.py` lesson in a different place: a hand-kept list
that has to track another hand-kept list drifts, and the failure is silent.

### Deactivation, not deletion — and assignments survive it

Contributors follow user accounts: `is_active` INTEGER 0/1 (migration 0014),
never a delete. The audit trail refers to contributors by identifier, so
deleting one leaves entries pointing at a record that no longer exists.

**Deactivating never clears an assignment.** A system that named someone as its
Data Steward goes on naming them, shown as deactivated, and `contributorLabel`
appends "— deactivated" so the gap is visible. Clearing those references would
leave the system with no steward and no record that it ever had one outside the
audit log, which is the data loss deactivation exists to avoid. The linked user
account and the link are untouched as well — deactivating a contributor is not
deactivating a sign-in.

`assignable()` is what keeps a deactivated person out of the pickers **except**
on a record that already names them, where removing the current value from its
own dropdown would make the assignment impossible to see or change.

Writing `is_active` is SuperAdmin-only and **refused** rather than ignored for
anyone else (`CONTRIBUTOR_SUPERADMIN_FIELDS`), which is why the browser only
sends it when the viewer is a SuperAdmin — sending it unconditionally would
make every Editor's save fail.

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
