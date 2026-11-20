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
`RELEASE_CHECKLIST.docx` is a Word copy Ben checks off by hand.

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

### 3. Check the knowledge base at every release

**Every release, without being asked, before the release notes go out:**

1. Read the release's changes against `help_articles.py` and decide, for each
   one, whether an existing article is now wrong or incomplete, and whether the
   release introduces something with no article at all.
2. **Tell Ben what you found** — which articles need changing and why, and
   which new ones you think are needed — as a short list, as part of the
   release. Say so explicitly when nothing needs changing; silence reads as
   not having looked.
3. Write the drafts only if he says go ahead.
4. **Publish only after he has reviewed them.** Same rule as the release
   notes, and for the same reason: an article is the thing a user reads when
   they are already confused, so it should not be the first draft of anything.

A release note says what changed; an article says how to use it. The notes are
read once and the article is read every time somebody new arrives, so an
article left behind by a release is the more expensive of the two to get
wrong. Articles live in **code** (`help_articles.py`), not the database, so
this is a normal edit with a normal review — there is no draft state to lean
on, which is why the review has to happen before the commit rather than after.

While you are in there: `SCREENS` maps a screen to the articles offered on it,
so a new screen with no entry silently offers nothing, and a new article that
names no screen can only be found by searching.

### 4. The commit message goes in a file, not into Terminal

Write the release's commit message to **`COMMIT_MSG.txt`** in the project root
and tell Ben to run:

```bash
git add -A && git commit -F COMMIT_MSG.txt && git push
```

**Include the `git add -A`.** `-F` says where the message comes from and
nothing about what is being committed, so without staging, `commit` finds an
empty index and stops — and what it prints is a status listing under "Changes
not staged for commit", which reads like *the work was already committed*
rather than *you forgot to stage*. Ben lost a few minutes to exactly that
reading, having been handed the command without the `add`. The `&&` then means
`push` never runs, so the branch really is up to date with the remote, and the
story is consistent all the way down. Nothing is damaged; the release simply
has not happened. Check `git log --oneline -1` against the message you wrote if
there is any doubt.

Not `-m`, and not a heredoc. A long message pasted into Terminal breaks on the
first quote, backtick, or line that looks like a shell word — Ben lost one to
exactly that, and the failure is a half-written commit plus a shell waiting at
a continuation prompt, which is an unpleasant place to be mid-release. `-F`
reads the bytes from the file and nothing interprets them.

`COMMIT_MSG.txt` is in `.gitignore`: it describes a commit rather than
belonging to one, and it is overwritten every release. Posting the message in
chat as well is fine and worth doing — Ben reviews it there — but the file is
what he actually commits from.

### 5. The app is written in American English

Ben asked for this in November 2026: color without a "u", "Check" rather than
"Tick", catalog, behavior, gray, center, organization, analyze, labeled.

It applies to **everything in the repository**, not only what a user sees —
the UI strings, the help articles, this file, `ACCESSIBILITY.md`,
`RELEASE_CHECKLIST.md`, the migration docstrings and the code comments. A
comment written in one dialect and a label in another is how a label gets
written the wrong way the next time somebody copies the wording from a comment
beside it.

Two things are deliberately exempt, because changing them would be a schema or
API change dressed up as a spelling fix: `aria-labelledby`, which is an HTML
attribute, and any identifier, column or endpoint that already exists. All of
the current ones happen to be American already (`field_catalog.py`,
`/api/field-catalog`), so this has not yet cost anything.

### 6. Move uploaded files to S3 when object storage exists

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

### 7. Backups: the job never deletes

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

**The CA store is pinned to the system one, on purpose.** This job is the only
thing in the project that makes outbound HTTPS — `aws s3 cp` — so its trust
store is the one that matters, and `AWS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt`
makes botocore verify against the store the image refreshes at build time
rather than any bundle it carries internally. That came out of Render's
eMudhra advisory (October 2026), and the reason it is worth an explicit
setting here rather than a rebuild and a shrug: if something could impersonate
S3 to this job, what it receives is a full database dump. Rebuild both images
after any CA advisory; for a Docker service nobody refreshes the trust store
on your behalf.

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
rather than a scan artifact, and `check_jsx.py` reports it correctly.

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

**Keyed on the column, never the label.** The same column is labeled
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
were absent from the catalog with no error anywhere, and could not be given a
definition. The generator now **fails** on any labeled component it does not
recognize rather than quietly producing a short list. Add new field components
to `EDIT_COMPONENTS` / `READ_COMPONENTS`.

**Modal fields declare their column; required-ness is read, not listed.**
Every optional field in the three Add windows used to be a hand-written
`form-group` whose column appeared only inside an onChange closure, so a modal
field had no identity anything else could use. The generator recovered the
column with a regex and found **40 of 80**: anything spread over several lines,
anything inside a `form-row`, and every checkbox was invisible to it, with no
error. `ModalField`, `ModalCheck` and `ReqField` now carry `col=`, so this is a
read rather than a guess, and `ReqField` is also where the catalog learns a
field is **required** — which is what makes it unhideable.

Four hand-written blocks remain, deliberately: a data flow's display name and
the three inputs for the flow created alongside a Manual Entry system are not
record fields, so there is nothing for hiding or a definition to resolve. They
carry no `col` and a comment saying why.

The generator **fails** if a modal's `useRequired` list names a catalog field
that is drawn with an optional component. Those are two statements of the same
fact, and a field that validates as required while the catalog thinks it
optional would be hideable — which would make that record type impossible to
create.

**Field hiding is per record type, and never deletes.** `field_hidden`
(migration 0012) is keyed on `(entity_type, column_name)` with **no shared
`"*"` scope**, unlike definitions: what a field *means* is usually the same
everywhere, whether you *use* it is not, and one check emptying three screens is
a worse surprise than three checks. Hiding never touches the stored value, so
the preference is safe to get wrong — the alternative is a mis-click that needs
a restore. Required fields are refused on write *and* filtered on read, because
a row could predate a field becoming required.

Note the asymmetry in how the two are looked up: **definitions are keyed on
every label**, because 81 call sites pass nothing else; **hiding is keyed on
the column**, with a label-to-column map sent alongside for the older
components. Keying hiding on text would mean a field relabeled in one view
quietly reappearing there.

**Shared definitions use `entity_type = "*"`.** A row stored that way applies
to every record type carrying the column, and a row for a specific record type
overrides it (`effective_definition`). Eleven columns appear on more than one
record type; that is what lets Data Steward be defined once for all three while
Description stays different per type. No schema change was needed — the primary
key already allowed it.

### A component a view renders can hide its fields from the generator

The generator reads the bodies of the components named in `VIEWS` —
`TaskEditView`, `AddTaskModal` and so on. So a field drawn by a *different*
component that one of those renders is invisible to it, and the catalog simply
comes up short: Preferences offers one field fewer than the app has, that field
cannot be given a definition or hidden, and nothing anywhere says so.

That is exactly the silent omission the generator exists to end, and it
happened in 0.8.0. `TaskExtras` holds the automated flag and the dependency
block, written once because the flyout and the Add window both need them and
two copies of a control that size is how one stops matching the other. Its
`ModalCheck` and `EditField` sat outside every view body, `unknown_field_components`
could not see it — that rule looks for a `label=` and `<TaskExtras />` carries
none — and `is_automated` was absent from the catalog with all four checks
green.

Two things close it:

- **`FIELD_GROUPS`** names the components whose bodies are read as part of the
  view that renders them, and `expand` substitutes each body **at the tag**,
  not at the end. A field's section comes from the nearest heading above it, so
  a body appended to the end lands its fields under whichever section happens
  to be last — `is_automated` first came out under Notes, three sections below
  where it is drawn.
- **`unscanned_field_groups` fails** on any other capitalized tag a view
  renders whose own body contains a field component. That is decidable without
  a JS parser and without a list of every out-of-scope screen: read the
  component, look for a field component in it. `OUT_OF_SCOPE_VIEWS` and the
  names in `VIEWS` are excluded, and they have to be listed because a
  component's boundary here is the span from one declaration to the next — so
  the last component declared inside `DetailPanel` swallows that panel's
  render, which names every view it can draw.

So: a new sub-component that draws fields fails the check rather than quietly
shortening the catalog. Add it to `FIELD_GROUPS`.

### The seed is a fictional district, and the harness builds from nothing

Until 0.9.0 two things carried Maine DOE's own data, and both were wrong the
moment the project was shared:

* **`seed_db()` in `app.py`** held the department's real architecture — eight
  systems, the real collection pipeline, 33 data elements from the state's
  interoperability profile. An adopter starting from an empty database would
  watch another state's systems appear on first run and have to work out which
  parts were examples.
* **`lineage.db`** was the harness seed. `tools/harness_setup.py` copied it to
  get migration 0001's tables *and* some rows to test against, which is why
  0001 was excluded from the generated DDL.

Now: `seed_db()` creates an invented district with **role-based names**
(`Statewide Integration Platform`, not any particular product), eight systems
in the same source-to-reporting shape, seven flows including one carrying no
PII, and twelve data elements chosen to cover every case the catalogs display.
`sample_schema.json` replaces the two real schema seeds with four tables and
three foreign keys. The shape is deliberately realistic because the point of a
seed is to demonstrate features; the content is deliberately invented.

**The harness now builds from the migrations alone**, 0001 included, starting
from an empty file. The module docstring always claimed the schema was
generated rather than hand-written; that is now true of all of it rather than
of everything after 0001. One consequence worth knowing when reading a suite:
**the harness database has no rows.** Anything needing a system creates its
own, and `seed_db()` supplies the sample district once the server has started.

`FIRST_MIGRATION` is emitted separately, before the hand-written 0002 block,
because that block renames `stakeholder` and the table has to exist first.
And `PRELUDE_0002` uses **single braces** while `PREAMBLE` uses doubled ones:
the preamble goes through `.format()` and the prelude is concatenated as-is, so
a doubled brace there reaches SQLite literally and fails with
`unrecognized token: "{"`.

**The history was rewritten in November 2026, so commit hashes before then do
not exist any more.** `git filter-repo` stripped `lineage.db`, the two real
schema seeds, `FedReporting_Tables.xlsx`, `.DS_Store` and
`migrate_sqlite_to_postgres.py` out of every commit, while the repository was
still private — doing it after publishing would have been a far weaker
guarantee, because an unreferenced commit stays reachable by SHA until GitHub
garbage-collects. A pre-rewrite mirror is archived locally and off any public
service; it is the only remaining copy of the department's pre-auth database.

#### The generator was dropping every REFERENCES clause

Generating 0001 exposed it. `gen_harness_schema.py` translated the column type,
nullability, defaults and primary keys, and **silently discarded
`sa.ForeignKey(...)`**. That cost nothing while 0001 was excluded, because
`lineage.db`'s tables had been created by real Alembic and carried their
constraints — so the harness had foreign keys without the generator ever
emitting one.

The moment 0001 was generated they vanished, and the harness enables
`PRAGMA foreign_keys=ON`, so a constraint production enforces stopped being
enforced in the only place the suites can see it. `t_052` caught it: a
free-text system name in a data element's Source of Truth was **stored instead
of refused**, which is a 500 in production and a silent success in the
harness. Exactly the harness-diverges-from-production shape this file keeps
recording, arriving by a route nobody was watching.

`REFERENCES` is emitted now. `ON DELETE` deliberately is not: nothing in the
schema declares one, because the application does its own cascades on purpose,
so emitting a guess would invent behavior the migrations do not describe.

#### A schema seed must agree about the system's NAME, not just its id

System ids are sequential — `SYS-001`, `SYS-002` — so a seed file naming
`SYS-003` collides with whatever happens to be third in any other deployment.
`sample_schema.json` names `SYS-003`, and in the Maine database `SYS-003` is
the real operational data store.

Matching on the id alone, the only thing between a fictional schema and a real
system was the "already has tables" guard. **Clearing a system's schema to
re-upload a fresh `.sql` is an ordinary maintenance action**, and the next
restart would have injected four invented tables into a real system, where
they would look mapped and could have governance attached to them. The guard
held in practice and the hazard was one routine action from firing — the worst
kind, because nothing is wrong until something ordinary happens.

`seed_schema()` now requires the file's `system_name` to match the stored name.
`sample_schema.json` says "Operational Data Store"; Maine's SYS-003 is "NEO
(State Operational Data Store)". A file with **no** `system_name` is refused
rather than trusted, because an unnamed file predates this check and cannot
have been verified against anything. The skip prints which name it found and
which it wanted, so a legitimate rename is diagnosable rather than silent.

The general lesson, and it is new in this file: **a seed is a write into
somebody else's database.** Every other guard here is about the harness
disagreeing with production. This one was about shipped example data reaching a
real system, which is a different failure and needs its own kind of check —
identity, not idempotency.

#### Schema seed files are globbed into the harness, not listed

`seed_schema()` globs `*_schema.json`, so `harness_setup.py` globs them too. A
hand-kept list there would mean the harness quietly having no schema to drill
into — which is what happened on the first attempt: `sample_schema.json` was
not in `APP_FILES`, `schema_table` came up empty, and `selftest`'s governance
inheritance checks had nothing to inherit through. The suites do not report
"no schema was seeded"; they report an assertion about governance failing.

`APP_FILES` itself is still a hand-kept list of modules, for the reason given
in its comment: that failure is loud, because the server exits on
`ModuleNotFoundError` before binding a port.

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

### Diagram geometry is pure functions, tested in Node

`alignSnap`, `snapAngle`, `diagramExtent` and `exportFilename` take numbers and
return numbers. No DOM, no React, no reading state. That is the only reason
`tools/tests/t_align.js` can exercise them at all — there is no browser in the
assistant's sandbox, so geometry that reaches into the DOM is geometry nobody
can check until Ben clicks on it.

```bash
./tools/run_geometry_tests.sh    # all of them, in Docker — no local Node
node tools/tests/t_align.js      # one of them, if Node is installed
```

**The Docker wrapper exists because Node is not a reasonable thing to require.**
Ben hit `node: command not found` at checklist step 1b-iii, and installing a
runtime on his Mac to run four dependency-free test files is the wrong trade
when Docker is already required for local development and for
`tools/check_jsx.sh`. `run_geometry_tests.sh` mounts the project read-only into
`node:20-alpine` and throws the container away; unlike `check_jsx.sh` there is
no npm install, so there is no registry to fail.

Its `SUITES` list is written out rather than globbed, deliberately: a new suite
has to be added there on purpose, because a suite that silently stops being run
is worse than one that does not exist — the checklist would claim it passed.
Add new geometry suites to that list.

Two properties the suite exists to protect:

- **Snapping is inert outside its tolerance.** A snap that moves things you did
  not ask it to move is worse than no snap, because you stop trusting the
  position of anything. Every multiple of 45 degrees is checked from both
  sides, and every angle 17 degrees off one is checked to be untouched.
- **A guide spans both boxes.** A dotted line that stops short of the box it
  refers to does not explain itself, which is the only reason to draw one.

**Angles are measured from horizontal, not between segments.** Measuring the
angle at a bend was the alternative and is harder to predict while dragging,
because moving one end changes the answer for two segments at once. The
difference from the nearest multiple has to be taken the short way round: -179
and 179 degrees are two degrees apart, not 358, and a naive subtraction refuses
to snap near 180.

**The alignment tolerance is divided by the zoom at the call site.** The
arithmetic is in diagram units, so a fixed six of them is a sixth of a
centimetre at 3x and half a centimetre at 0.3x — dividing keeps the stickiness
feeling the same.

**Shift defeats both**, deliberately one key for "leave my positioning alone"
rather than one per gesture.

**`alignSnap` reads `x_pos`/`y_pos`, because that is what a system row carries.**
The first version read `o.x`/`o.y` — properties a system does not have — so
every comparison was against `undefined` and no guide could ever appear. The
Node suite passed throughout, because its fixtures were written from the same
wrong assumption as the code. **A test built on the author's misunderstanding
confirms the misunderstanding**, which is the most expensive lesson in this
file: the suite now checks the production call site with a regex and asserts
that an `x`/`y`-shaped fixture snaps *nothing*.

**A right angle needs both segments, which is why `snapBend` exists.**
`snapAngle` alone rotates the incoming segment onto the nearest 45 degrees,
which makes a diagonal look deliberate — Ben said that felt right immediately
— but it cannot square a corner, because the outgoing segment keeps whatever
angle the destination imposes. That is the "45 works, 90 does nothing" he
reported. `snapBend` lines the dragged point up with either neighbor's x or y
when close, which is exact and makes both segments axis-aligned, and falls back
to the rotation for anything not near an axis. It also refuses to collapse a
bend onto the neighbor it is locking to, which would not be a bend at all.

### `const` does not escape an `eval`

`t_labelpos.js`, `t_autoname.js` and `t_align.js` all read geometry out of
`index.html` and `eval` it. **Function declarations leak into the enclosing
scope; `const` and `let` do not.** So the functions are callable but the
constants beside them are invisible, and a test that references one dies with
`ReferenceError`. `t_align.js` reads the values it needs out of the source with
a regex instead of hardcoding them, so the test cannot keep passing after
someone changes the value it assumes.

### Exporting the diagram: three fix-ups, all load-bearing

A copied `<svg>` does not carry the page's stylesheet, so `diagramStyles()`
inlines the rules it needs — read from `document.styleSheets` rather than
hand-maintained, or the export drifts out of agreement with the screen and
nobody notices until they open a file.

**Which rules is decided by the file's own classes, not by a prefix list.** The
first version matched selectors starting with a few guessed prefixes and missed
`.system-node rect { rx: 10; ry: 10 }` — the rule that rounds every box — so
exports came out with sharp corners. Nobody would think to put "check the
corners are round" on a checklist. `diagramStyles(root)` now collects the
classes present in the clone and keeps any rule mentioning one, plus the bare
element rules, so the list maintains itself.

**Three formats and a Print button** (`EXPORT_FORMATS`). SVG is written
directly; PNG and JPG are drawn through a canvas at `EXPORT_RASTER_SCALE`,
which is 2 so a projector and a high-resolution screen both look right. JPG
gets an explicit white fill because it has no transparency and an unpainted
canvas comes out black.

**PDF is deliberately not a download.** It has no browser API, so it would mean
jsPDF plus its SVG plugin — roughly 400KB on every page load and two more
pinned versions — for a button used rarely. `printDiagram` opens the browser's
print dialog instead, where "Save as PDF" is a destination: lossless vector
output, no dependency, and it covers actual paper too. GIF was asked for and
dropped: 256 colors, no browser encoder, and visibly worse than PNG for text
and thin lines. The viewBox is set from
`diagramExtent()` so the file holds the **whole** diagram rather than the
scrolled viewport. And the pan-and-zoom transform is removed, along with the
selection highlight and the editing handles, because those are states of the
app rather than facts about the architecture.

Everything operates on a **clone**. The live SVG is never touched, so an export
cannot disturb what is on screen.

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
filtered out, so the window can gray them and name the contributor that has
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

`read_labels` in the catalog generator took the *first* `data.X` in a read
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

Four narrow rules: every `setX(...)` call has a declaration, every bare
`something(` call names something declared, every `onClick={handler}` names
something declared, and no `\uXXXX` escape sits in JSX **text**.

The call rule was added in 0.6.1 after `endpointsOf` was invented out of thin
air and called from a drag handler: it compiled, and would have thrown the
first time anyone dragged a bend. Rules 1 and 2 could not see it, because it is
neither a setter nor an event-handler attribute. Requiring no space before the
parenthesis is what keeps prose out — JSX text says "something (see below)"
with a space, never "something(" — and `blank_template_text` blanks the literal
text of template literals while keeping `${...}` contents, because otherwise
SVG and CSS function syntax (`translate(`, `scale(`) and plain English
(`person(s)`) read as calls.

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

#### It only covered three record types

`SERVER_TUPLES` listed `system`, `flow` and `element` — and **not `task`**,
which is where this project has found the silently-unsaveable field three
times: Owner on a flow, `proposed` with no checkbox, `status` dropped by a
`continue`. The check written to catch that shape was not looking at the record
type that keeps producing it.

`is_automated` made it four. It was in `TASK_FIELDS`, drawn in the flyout and
in the Add window, and missing from one object literal — so it could be set
when creating a task and never changed afterwards — and `--check` passed.
`task` and `contributor` are in the list now. **When you add a record type,
add it here**, because the only thing worse than no check is one that reports
OK about a file it never read.

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

**A caption needs a gap below it, not just above.** `.gov-hint` carried
`margin-top: 2px` and no bottom margin, so a hint written as a *sibling* after a
field row sat 8px under the field it explains and flush against the next one —
reading as a caption for the wrong field. Ben reported it on the task dates and
status. `.field-row + .gov-hint` and `.edit-field-row + .gov-hint` pull it up
and push the next row away, so the gap above is smaller than the gap below, and
indent it to the value column: a hint about a value that starts under the
**label** looks like it belongs to the panel rather than to the field.

Keyed on **adjacency**, so it reaches only the hints that really are siblings. A
hint nested inside a row — the flow name, the display-name counter, the derived
status in the task edit view — is already aligned with its value and already
gets the row's bottom margin, and is untouched. `SchemaGovEditView` had solved
the same problem by hand years earlier with an inline `marginLeft: 140`; that is
gone, and the label width is now `--field-label-w`, which was written out three
times.

**A static value in an edit row needs the label's own padding.**
`.edit-field-row .field-label` carries `padding-top: 6px` so it sits level with
the text inside an input. The one place that shows a *value* rather than a
control — the derived status — had none, so it started 6px higher than its own
label. Invisible while the value had text in it, and obvious the moment it did
not: an unset derived status rendered `''`, and the hint below it rose to where
the value should have been, printing "Determined based on subtasks" above the
word "Status". `.edit-field-row .field-value` now matches the label's padding
and is `display: block` so the hint sits under it. And the value falls back to
an em dash, the way `ReadField` already does — **an absence shown as an
absence** is the same rule the catalog follows, and an empty string is not it.

**Both of these were verified by looking at them.** The rules were lifted out of
`index.html` into a throwaway page rendering before and after side by side,
which is the only way to judge "closer to the field below than the field above"
— and for the status, the fixture had to include the *empty* case, because that
is the only one where the bug shows. Two lessons from the exercise itself: the
first preview was too narrow and clipped the column that mattered, so a
screenshot is only evidence once it has actually been read; and a fixture that
only covers the populated case will not reproduce a bug that needs the empty
one.

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

### The Add buttons live inside the stats bar

`STATS_BAR_VIEWS` decides which views get the counts bar across the top, and
the Add buttons are rendered **inside** that bar — one cluster, because two
`margin-left: auto` siblings push each other apart. So a view missing from that
list loses its Add button as well as its counts, with no error anywhere.

That is what happened to Tasks in 0.7.0. `+ Add Task` was written with the
right condition and appeared on the diagram, which shares the bar with Add
System and Add Flow, and never appeared on the Tasks catalog at all. Ben found
it in a minute; nothing automated could, because the JSX is valid, the button
exists, and every suite passes. The list used to be written inline at the gate,
which is why the relationship was invisible — it is a named constant now, with
the invariant stated at both ends.

**When adding a record type, add its view to `STATS_BAR_VIEWS` first.** The
general lesson is the same one as the reused CSS classes: a control that is
correct in isolation can still never render, and only looking at the page finds
it.

### Tasks: one parent, one set of dates, and a status it does not own

A task (0.7.0, migration 0016) is the work a flow or a store depends on — the
validation steps before a load is accepted, the stored procedure somebody runs,
the file a person moves by hand. Five decisions are load-bearing.

**Exactly one parent, and it must be in the view being written.** `anchor_kind`
+ `anchor_id` rather than two nullable foreign keys, which would have allowed a
task attached to both or to neither. `check_task_anchor` also refuses a parent
belonging to a *different* view: a scenario task pointing at a live system is
the cross-view reference the leak tests exist for, and it would draw a circle
on a diagram where its parent does not exist.

**No recurrence.** The first sketch had several date pairs per task with a flag
marking the active one; that flag goes stale and the status light then quietly
lies, which is worse than no light. A quarterly job is four tasks, made with
the copy action, so March's outcome stays visible in June. That removed an
occurrences table and all the active-pair arithmetic with it.

**The copy carries everything except the dates and the progress.** Type,
parent, description, notes, the people and the whole checklist come across;
dates come up blank and every step resets to Not started. The dates are the one
thing that differs between occurrences, and a copy pre-filled with March's
would be wrong in a way nobody notices until it is overdue. The position is
dropped too — two circles at identical coordinates look like one, and the copy
would be undraggable under the original.

**A task with subtasks does not own its status.** `derived_task_status`
computes it, and `task_rows` resolves it on *read* as well as on write, so a
row edited directly in the database still reports something true. Only a task
with no subtasks carries a status set by hand, and the edit view shows it as a
value with a line saying why rather than as a disabled control — a missing
control looks like a bug.

**A status nobody has set is NULL, not "Not started"** (migration 0019). Those
are different facts, and a diagram where nothing has been assessed should not
look like one where everything has been assessed and found not to have begun.
Three consequences, each with a test: an unset subtask is ignored when
deriving; the circle's tooltip leaves the parentheses out rather than printing
"Name ()"; and the catalog shows a dash.

Two rules in the derivation are easy to get wrong, and I got both wrong once:

* **Once a task has subtasks, the answer is theirs — including "nothing".**
  Leaving the hand-set value showing through when the subtasks say nothing put
  a stale "Completed" under a field captioned *Determined based on subtasks*,
  which contradicts its own caption. `apply_task_status` therefore writes NULL
  in that case, and `status_from_steps` means "has subtasks", not "produced a
  value".
* **"All completed" is asked of EVERY subtask, not only the ones with a
  status.** Filtering the unset ones out of that question made one finished
  subtask out of three report the whole task Completed. An unset subtask is not
  a finished one. The "any started" test *is* over the ones that said
  something, which is the asymmetry to preserve.

**Subtasks are replaced wholesale, not diffed.** They are an ordered list a
person edits as a list — reordering, inserting in the middle, deleting — and
matching that up row by row produces either a diff algorithm or a pile of edge
cases. Their statuses and dates travel in the payload, so nothing is lost.

**"Subtask" in the UI, `task_step` in the database.** Ben renamed them after
0.7.0 was built, by which point 0016 had run. Renaming the table would have
meant editing a migration that has already run, which is the one thing this
file says never to do, so the schema keeps `task_step`, the API payload keeps
the `steps` key, and every word a user sees says subtask. The frontend
component is `TaskSubtasks`. If the schema is ever renamed it needs a new
migration and a sweep of both names — do not fix it by editing 0016.

**A subtask has its own expected start and end** (migration 0017), independent
of the task's. The task's pair is the outer window; a validation whose first
check happens the day a file lands and whose sign-off is a week later has two
dates that matter, and one range over the whole task hides which part is late.
Either end may be blank, so `dateRangeLabel` reads "from X", "due Y" or
"X to Y" rather than printing a dangling half-range. The copy action drops a
subtask's dates for the same reason it drops the task's.

`pos_main` / `pos_cross` are named for their role, not an axis, because the
meaning depends on the parent: a fraction along the path plus a perpendicular
offset on a flow, two offsets from the box origin on a system. Changing a
task's parent therefore clears both, server-side and in the panel, so the
default placement runs again.

**A task's documents go through the same Resources machinery.**
`RESOURCE_PARENTS` is the whole switch — adding `"task": ("task", "task_id")`
and widening one route pattern was the entire change, because
`require_parent_in_view` already works for any scoped table with an id. A
task's documents are the procedure somebody follows to do it: the checklist,
the spreadsheet template, the memo saying who signed off. They are deleted with
the task, and with the task's parent.

**Tasks have no `is_hidden`, deliberately.** Scenario focus hides systems and
flows to declutter the diagram; a task is drawn on its parent, so it disappears
and returns with it. The tasks GET is the one scoped read that does not pass
`respect_hidden` — a second flag would be another thing to keep in step, and a
task hidden while its system shows would be a circle nobody could find.

**A subtask's rows in the flyout are hand-written, not `ReadField`s.**
`ReadField` resolves field hiding by **label**, so a task field hidden in
Preferences called "Status" would blank every subtask's status as well — two
unrelated things that happen to share a word. The subtask block therefore draws
its own `field-row`s: a number, then each value labeled, with a rule between
subtasks. The first version crammed status, person and both dates into one gray
line under the description, which read as a jumble and made a value nobody had
entered indistinguishable from one that was not being displayed.

**Steps, assignments and the parent are not cataloged fields.** The parent is
two columns answered by one choice; the people and the steps are lists. None
has a single column for a definition or for hiding to resolve against, so all
three are hand-written, like the four blocks on a data flow. `t_tasks.py`
asserts their *absence* from the catalog, so the next person to add a field
component there finds out on purpose.

### Feature suites sign in through `tools/tests/_client.py`

Eleven suites each opened with the same copied block: log in as
a hardcoded real address with the sandbox bootstrap password, then
`change-password` to a literal out of the test file. Two faults, and the second
is the serious one.

**It only worked on a database nobody had used.** On a real local database the
login fails and the next line dies with `KeyError: 'csrf_token'`, which names
nothing. Ben hit exactly that at checklist step 3b.

**And had the login succeeded it would have rewritten his password.** A test
that changes the credentials of the account it borrows is a footgun; it stayed
harmless only because it could not get in.

So credentials come from `--email` plus a prompt (`--password` and
`TEST_PASSWORD` exist for the harness, which has no terminal), the sign-in is
**checked** rather than indexed into, and **nothing changes a password** — an
account whose password is still temporary is refused with the same message
`selftest.py` gives. The one-time bootstrap change is the harness's job, not a
suite's.

**And no suite may demand a pristine database.** `t_fieldhide` asserted
"nothing is hidden to begin with", which is true only of a database nobody has
used — so on Ben's own local app, where he had hidden a field in Preferences
while testing that very feature, his correct setting was reported as a test
failure. It now records what was already hidden, asserts the *invariant* the
original check was reaching for (the catalog and `/api/field-definitions`
agree about the hidden set, despite being keyed differently), picks a victim
field that is **not** already hidden, and **restores every field it touched to
the state it found** rather than to visible. A closing check proves it.

The general rule, the same one as the password: **a suite borrows the database,
it does not own it.** Leave settings as they were, and assert relationships
rather than a starting state you do not control.

Three more things to know when touching these:

* **`request` returns the real status code.** The old per-suite helper
  flattened every success to 200, so `ck(..., st == 200)` on a creation passed
  by accident. Those are `in (200, 201)` now. A new assertion should expect the
  real code.
* **Some suites keep their own `rq`** — `t_041` and `t_044` take an extra
  `sc=` scenario argument, `t_branding` returns three values. Those call
  `sign_in_with(S(), rq)` and borrow only the sign-in. Importing `request` over
  the top of one of those shadows it and breaks the suite at its first scoped
  call, which is how I broke `t_044` mid-conversion.
* **No suite may hardcode a path, or depend on the working directory.**
  `t_041` read `/tmp/h2/static/index.html`, so it could only ever run in the
  sandbox. `t_fieldhide` and `t_fielddefs` did `sys.path.insert(0, ".")` to
  find `field_catalog`, which works only when run from the project root. All
  three now search relative to the test file first, then the working
  directory, then the harness's flat copy.
* **No suite may import `app`.** `t_fielddefs` did, for one list —
  `SEED_FIELD_DEFINITIONS` — and importing `app` drags in `db`, which needs
  `psycopg_pool`. That package lives in the Docker image, not on a Mac, so the
  suite died with `ModuleNotFoundError` four checks in. It parses the list out
  of `app.py` with `ast.literal_eval` instead: no import, no dependencies,
  nothing executed. **A test that reads one constant should not need the whole
  application, its database driver and a connection pool.**

  `t_publish` is the one exception and is harness-only by design — it imports
  `app` and reads the SQLite file directly. Its docstring says so, and it is
  deliberately absent from the checklist.

### Proposed was accepted by the server and had no control

A task has carried `change_state` since 0016 and the update handler has always
honored `proposed` — the **checkbox was simply never drawn** in the task edit
view, so the state could be set when creating a task inside a scenario and
never changed afterwards.

That is the silently-unsaveable-field problem from the opposite direction, and
worth naming because `check_payloads.py` cannot see it: that check asks whether
the browser sends every field the server accepts, and the browser *was* sending
`proposed` — `saveEdit` adds it for every scenario edit. What was missing was a
way for a person to change the value it sends. **A payload check proves the
wiring, not that a control exists.**

The checkbox is first in the section and only inside a scenario, matching
`SystemEditView` and `FlowEditView`, and unchecking it writes `unchanged`
without touching `removed` — retiring has its own action. `t_tasks.py` toggles
it both ways, because "it can be turned on" says nothing about whether it can
be turned off.

### A "do something" line in a dropdown is styled on the SELECT

"Add Status" and "Add Contributor" are the empty option in their dropdowns,
phrased as invitations rather than as values, and they are drawn gray and
italic by a class on the **`<select>`** — `.select-hint` — not on the
`<option>`.

Styling the option is the obvious thing and it fails on macOS: the system draws
native select popups itself and ignores `option { color; font-style }`
entirely. So it looked right on one platform and did nothing on Ben's. The
option keeps the class too, for the browsers that do honor it, but nothing
depends on that.

The class is applied when the current value matches an option marked
`hint: true` (`selectHintClass`), so the italics disappear by themselves the
moment a real value is chosen rather than needing to be cleared.

### A status that cannot be recognized was dropped, not cleared

Before migration 0019 the task update handler read:

```python
if key == "status" and value not in TASK_STATUSES: continue
```

So choosing the empty option and saving sent `status: null`, the handler
**skipped the key entirely**, and the old status stayed — the control moved,
the save reported success, nothing changed. Precisely the
silently-unsaveable-field shape this project keeps finding, from a `continue`
that looked like input validation.

It now clears on `None`, `""` or `"none"` and ignores only genuinely
unrecognized words. `t_tasks.py` drives **the whole payload
`DetailPanel.saveEdit` builds**, not just the one field, because a partial
request proves the handler works and says nothing about what the browser
actually sends.

### "Other" is a constant, not a row in `task_type`

A task type is a controlled list a SuperAdmin owns, but somebody creating a
task should not be blocked because nothing fits and they cannot edit the list.
So **"Other" is always offered**, with `task.task_type_other` (migration 0018)
holding what they typed, and `task_type_label` reading the two as
"Other — Annual certification".

Two things it deliberately is not:

* **Not a row in `task_type`.** `ensure_task_types` seeds only an empty table,
  so a seeded row would appear on new installations and never on an existing
  one — a split nobody notices until a user asks why they cannot see it.
  `TASK_TYPE_OTHER` is a constant, `/api/task-types` reports it so the browser
  is not the second place that decides the word, and adding a type literally
  called "Other" is refused with a 409 saying why.
* **Not a value that lands in `task_type`.** Writing the typed wording there
  would make every Editor a type author by the back door, which is exactly the
  boundary `TaskTypesHandler` draws, and fifty one-off spellings would make the
  catalog's Type column useless.

`check_task_type` is one function because create, edit and copy all ask the
same question; a type accepted on create and refused on edit is the sort of
asymmetry that only surfaces when somebody tries to fix a typo. Changing the
type away from "Other" **clears** the wording, so it cannot linger and
reappear looking like something they typed.

### A 405 means a route that does not exist

`AppStaticFileHandler` is mounted at `r"/(.*)"` and, being a
`StaticFileHandler`, supports only GET and HEAD. So **any request that matches
no API route falls through to it, and a PUT or POST there returns 405 Method
Not Allowed** — not 404. That is what an un-restarted server looks like from
the browser.

It happened the moment tasks reached the diagram. `index.html` is deliberately
never cached (see `set_extra_headers`), so Ben's browser picked up the new
frontend immediately while the running Python process still had the old route
table: `PUT /api/tasks/<id>/position` did not exist, hit the static handler,
and returned 405. Three separate symptoms, one cause — and a fourth, because
the failed position call aborted `addTask` before its `refresh()`, so a second
task appeared not to be created at all.

**When a 405 appears on an endpoint that plainly exists in `app.py`, restart
before debugging.** Python route tables are read once at startup.

**The quieter version of this is a missing field, not a 405.** During 0.7.1 Ben
added dates to a task's subtasks, refreshed, and the task's own dates stayed
blank. Nothing was wrong: `task_rows` had gained
`effective_expected_start`, the browser had the new `index.html` within the
second, and the running Python still had the old `task_rows` — so the key was
simply absent from the payload, `data.expected_start || data.effective_...`
resolved to undefined, and the field rendered empty with no error anywhere. A
405 at least announces itself. **Any change to `app.py` needs a restart**, and
the symptom of skipping one is a new feature that appears to do nothing at
all.

The lasting fix is that creating a task no longer depends on placing it:
`placeTask` catches its own failure and says so, and the refresh and the
notification happen either way. A second request must never be able to make a
successful first one look like a failure.

### Tasks on the diagram: a circle, and where it goes

`pos_main` / `pos_cross` mean different things depending on the parent, so the
placement geometry is a set of **pure functions** — `pointAlong`, `taskPoint`,
`taskFlowSlot`, `taskSystemSlot`, `defaultTaskSlot`, `taskDropPosition` — and
`tools/tests/t_taskpos.js` exercises all of them in Node. The same reason as
the alignment geometry: there is no browser in the assistant's sandbox, so
placement that reached into the DOM would be placement nobody could check until
Ben clicked on it.

```bash
node tools/tests/t_taskpos.js
```

**`taskPoint` reads `x_pos` / `y_pos`.** This is the second time that mistake
was available to make — `alignSnap` read `o.x` / `o.y` and no guide ever
appeared, while the Node suite passed because its fixtures shared the code's
wrong assumption. So the suite asserts an `x`/`y`-shaped system places
**nothing**, and greps the production call site for `x_pos`.

**The circles are painted after the systems.** A task on a system sits *inside*
its box, so drawn earlier it is covered by exactly the box it belongs to — the
same trap as the anchor dots in 0.4.1, and it looks like a dead click handler
rather than a paint-order mistake. SVG has no z-index; keep them last.

**One gesture.** Pressing a circle starts a drag; releasing without having
moved opens the record. The `moved` flag is what makes that work — without it
every attempt to open a task would also save the position it already had, which
would be a pointless write on every click.

**Position goes to `/api/tasks/<id>/position`, not the task's PUT.** Same
reasoning as `FlowRouteHandler`: dragging a circle to tidy a diagram is layout,
so it is **not audited** and does not mark a scenario record as Modified.
Tidying a picture should not make a plan look like it changed. `t_tasks.py`
asserts the audit total does not move — counted, not searched for a field name,
because a task's *creation* records `pos_main` among its values and searching
finds that instead.

**The default slot is computed in the browser**, by `defaultTaskSlot`, because
only the browser knows where the arrows currently run: a flow's path depends on
resolved anchors and waypoints that the server never computes. On a flow the
first circle sits at the midpoint and a *label height* off the line — the
label box occupies about 23px above its anchor at the same midpoint, so the
circle goes clear of it — and a second task slides **along** the path rather
than further out, alternating either side of the middle. Drifting outward would
stop it looking attached to its line. In a system it is the first free slot
filling from the top-left, and once the box is full it stacks rather than
spilling outside, because a circle outside its own system is worse than two
overlapping.

**Color coding is still to come.** Every circle currently reads the same, with
the status in its tooltip, and the legend says only "Task". One neutral circle
now is better than a palette that has to be explained before it is finished.

The circle carried a letter "T" in the first version and does not now: a single
letter inside an 11px circle is illegible at the zoom levels people actually
use, and it is not a label in any language. The fill is `#dfe3e8` — the ring
reads 9.02:1 against it — so the circle looks like an object rather than a hole
in the diagram. The legend reuses `.task-ring`, sized to match `.legend-color`
at 14px, so the key cannot drift from the thing it explains, and
`.legend-divider` rules separate System Types from the task entry and from the
scenario entries. Without the rules, a circle in a list of colored squares
read as a ninth system type.

**Selection is an outer halo, not a recolor — and that is a contrast fix, not
a style choice.** The ring is `#1B3A5C`: 8.69:1 against the worst of the eight
`TYPE_PALETTE` fills (purple) and 11.63:1 on white, because a task attached to
a system is drawn *inside* its box. Recoloring the ring to the app's selected
orange was the obvious thing and it fails — `#D4772C` is **2.43:1** on that
purple, under the 3:1 WCAG 1.4.11 wants for a non-text indicator, so a selected
task inside a purple box would have been *less* visible than an unselected one.
The dark ring stays and the orange is drawn outside it. **I claimed 9.4:1 for
this before measuring it properly**: "the lightest fill" is not the worst
contrast, and this is the third time a contrast claim in this project has been
wrong on the first pass. Compute it, over every palette entry, before writing
the number down.

**The legend swatch had to be pinned to a size.** `.canvas-container svg`
sets `width: 100%; height: 100%` for the diagram, and the legend lives inside
that container — so a 14px swatch SVG was stretched to the size of the whole
canvas, and `width`/`height` attributes on the element do not win against a
stylesheet rule. `.legend svg` pins it to match `.legend-color`. The reused-CSS
lesson again, from a direction nobody would predict: the rule being inherited
was written for a completely different element.

**The circle has an invisible hit disc wider than itself.** Hovering and
grabbing should target the thing you are pointing at, not the thing that is
drawn: without it the tooltip appeared over only a few pixels and the circle
was hard to grab at low zoom. It is the first child so nothing drawn later
steals its events.

**On a flow, a circle sits ON the line — `pos_cross` is forced to zero.** This
took three goes. A free perpendicular offset let a circle be dragged anywhere
on the canvas, including inside an unrelated system box. Clamping it to a
distance fixed that and was still wrong: where two flows run close together, a
circle a little off one of them is ambiguous between the two, and which flow it
belongs to is the one thing the circle has to communicate. Centered on the path
there is nothing to misread, and the gesture is one-dimensional — up and down
the line, which is all anybody wanted. A non-zero value stored by an earlier
build is **ignored rather than migrated**, so those circles simply snap onto
their lines.

That is also why `taskFlowSlot` skips the midpoint: the flow's label is drawn
there, and sideways is no longer available, so the first circle starts one step
along the path instead. A system task is still clamped to its box.

**A dragged circle lands at the nearest point on its line, not under the
cursor.** Worth knowing when reading `t_taskpos.js`: the round-trip check is
that the stored fraction redraws to the nearest place on the path, which is not
the same assertion as "back under the pointer".

**The hover card is HTML, not a `<title>` and not `Tooltip`.** All three were
tried. A `<title>` is what every other thing on this canvas uses, and on
something as small as a circle it is unusable: the browser waits about a second
and restarts that timer on every pixel of movement — exactly the complaint
recorded under "Tooltips: not the `title` attribute", which the rest of the app
learned years earlier. `Tooltip` cannot be used here either, because it renders
through a DOM portal and a portal cannot be a child of an SVG node.

So `.task-card` is an absolutely-positioned div beside the `<svg>`, placed from
the circle's diagram coordinates through the same pan and zoom, appearing on
`mouseenter` with no delay. `pointer-events: none` matters: without it the card
comes between the cursor and the circle it describes and the hover flickers.
The group keeps an `aria-label` with the same text, because a visual card is
invisible to a screen reader.

### The harness had to learn `op.alter_column`, and read every migration

Two separate faults in `tools/gen_harness_schema.py`, both found by migration
0019 making `status` nullable, and both of the project's favorite shape — a
schema change that reaches production and silently misses the test harness.

**SQLite cannot alter a column**, so `op.alter_column(..., nullable=True)` had
no translation and was dropped in silence: the harness kept the NOT NULL that
0016's `CREATE TABLE` declared, and inserting a null status failed in the test
harness and nowhere else. The generator now collects nullability changes in a
first pass and emits the affected `CREATE TABLE` without that constraint, so
the harness ends up with the schema the migrations *describe* rather than the
schema the first one described.

**That first pass has to read the whole `alembic/versions` directory, not
`sys.argv`.** `harness_setup.py` runs the generator once per migration file, so
a pass over argv is a pass over one file — and the migration that relaxes a
column is almost never the one that created it. With argv alone the fix looked
like it worked and changed nothing.

A guard now exits on an `alter_column` that changes a type or adds a NOT NULL,
because neither can be translated. A **rename** is accepted only when the new
name appears in `harness_setup.py`'s preamble, which is where 0002's
`stakeholder -> contributor` is written out by hand — so a future rename that
nobody adds there fails here rather than six suites later. The first attempt
failed on every rename including that one, which is the same mistake as the
first `op.execute` guard: a blanket rule that does not know which cases are
already handled.

### The harness ignores raw DDL in `op.execute`

`tools/gen_harness_schema.py` translates `op.create_table` and `op.add_column`
and nothing else, so a migration that writes its DDL as
`op.execute("ALTER TABLE …")` is **silently absent from the harness schema**
and every suite touching the new column fails with *no such column* — which
reads as a bug in the feature. That is the same failure shape as 0012's missing
table and 0012's dropped default, so the generator now **exits** on an
`op.execute` carrying `ALTER TABLE` or `CREATE TABLE`.

One exception, and it is principled rather than a special case: a statement
with `IF NOT EXISTS` / `IF EXISTS` is a catch-up repairing a column onto a
database whose earlier `create_table` ran without it. A harness built from the
migrations in order already has the column, so skipping the statement is
correct. Migration 0006 is exactly that, and it is why a blanket failure was
wrong on the first attempt.

So: use `op.add_column` when you can. The idempotent form is for repairing a
migration that already ran, not a default to reach for — migration 0017 adds
two genuinely new columns and uses plain `add_column` for this reason.

### `_columns` had a dead branch, and tasks reached it

`scenarios._columns` falls back to the cursor's `description` for an empty
table. Nothing had ever run it: `system`, `data_flow` and `flow_element` are
never empty in a database worth cloning. A new installation with no tasks hit
it and every scenario creation 500'd with *'_Col' object is not subscriptable*.
**A description entry is not reliably a tuple** — psycopg exposes `.name`,
sqlite3's are plain tuples — so it reads the attribute first and indexes only
as a fallback. Worth remembering because the harness is SQLite and production
is PostgreSQL: a branch exercised by neither is a branch that is wrong.

### Dropdowns are `Combo`, and a `<datalist>` is why

Ben: "the lists are starting to get unwieldy and it can be hard to find the
item you want." Every dropdown over records grows with the department, and a
native `<select>` can only be scrolled or jumped through letter by letter.

**The bug that forced it was the datalist, not the option lists.** The task
parent picker was an `<input list=...>` with a `<datalist>`, chosen in 0.7.0
because the browser supplies the dropdown, the keyboard handling and the
accessibility for free. It does — and it also **filters the suggestions against
the text already in the box.** A control pre-filled with its current value
therefore matches only itself, and the list looks like it holds one item. That
is exactly Ben's report that a task's "Attached to" dropdown "only shows what
the task is currently attached to, so if you want to move it to a different
data flow or system you have to recreate it". Nothing was read-only and no
option was missing; the value already in the field was hiding all of them. No
amount of work on the option list fixes it from outside.

So `Combo` is built around one load-bearing property: **opening shows the whole
list, whatever the current value is.** The typed filter applies only to what
somebody types after opening. Seeding the query with the selected label would
reproduce the datalist bug in our own code, which is the one thing to watch for
when editing it.

Four more things that are not obvious:

* **The listbox renders through a portal**, for the same reason `Tooltip` does:
  the details flyout scrolls, so a list drawn inside it is clipped at the panel
  edge, and these controls live in the flyout *and* in modals. It is
  `position: fixed` at z-index 300 — above the modal overlay (100) and
  `Tooltip` (200). An `absolute` list inside the wrapper is the obvious thing
  and it is clipped.
* **`.combo-wrap input.combo-input`, not `.combo-input`.** Inside an Add window
  the control sits in a `.form-group`, and `.form-group input` sets
  `padding: 8px 10px` at higher specificity — it wins, and a long system name
  runs under the caret. The reused-CSS lesson again, and the rest of
  `.form-group input` *is* wanted here, which is why one property is overridden
  rather than the class opted out of.
* **`disabled` and `group` exist because a native `<select>` gave them for
  free.** The account picker lists already-linked accounts grayed rather than
  omitting them, and the 80-field definition picker used `<optgroup>`. Dropping
  either would have been a regression dressed up as a new feature. The arrow
  keys step *over* both: landing on a row that Enter ignores is a dead end the
  user has to diagnose.
* **Scroll and resize re-measure rather than close.** Closing is what `Tooltip`
  does, but opening a control near the foot of a panel can make the browser
  scroll it into view, which would close the list the user had just opened.

`EditField`, `ModalField`, `ReqField`, `SystemSelect`, `ContributorSelect`, the
task parent and people pickers, the subtask rows, the schema governance person
override, the flow endpoints, the account and field pickers, the view switcher
and the scenario owner transfer all go through it.

**What is still a native `<select>`, and why.** Yes/No and `(inherit)` pairs,
role lists, the preferences controls, the logo position, the audit filters, the
scenario status. Each is a fixed list of two to five choices, where there is
nothing to filter and a combobox is strictly worse — it trades the platform's
own popup and keyboard handling for ours to no benefit. This is a judgment
rather than a rule, so if the uniformity matters more than the trade, convert
them; nothing in `Combo` assumes a long list.

`selectHintClass` is gone: every select it styled is a `Combo`, which styles
its own rows. The `.select-hint` class and the note above it stay for the
native ones that remain.

### Rule 5 in `check_identifiers.py` is about scope, not spelling

`contributors={contributors}` inside `SchemaTableReadView`, which never took
`contributors` as a prop. It throws `ReferenceError` the instant the component
renders, React unmounts the tree, and the page goes blank — which is what Ben
reported as "flyout brings up blank screen when clicking on fields or tables in
a schema". It had been there since the schema drill-down shipped; nobody had
clicked a table while the Governance section existed.

Rules 1, 2 and 4 cannot see it: not a setter, not an event handler, not a call.
**And neither can a whole-file name check, because `contributors` IS declared —
in `App`.** The first attempt at this rule compared against the file-wide set
of declared names, found nothing, and produced three false positives from a
`const NODE_W = 200, NODE_H = 70, NODE_EDGE = 12` whose second and third names
`declared_names` was not collecting. Both of those are worth knowing: the fault
is one of **scope**, and the declaration scanner was only ever reading the first
name of a multiple declaration.

Being scope-aware in general needs a real JS parser — the 183-false-positive
problem. But one case is decidable without one: **a component declared at column
zero has no enclosing scope except the module**, so a bare name it forwards must
be its own prop, its own local, or module-level. Nested components — declared
indented, inside another — legitimately close over their parent and are skipped.
That is the whole rule, and it finds exactly the two real bugs with no false
positives.

What it does **not** catch, so do not read a pass as more than it is: a name a
nested component borrows wrongly, a prop forwarded inside a call rather than as
a bare identifier (`options={taskParentOptions(systems, flows)}` is invisible to
it), and anything in a component assigned to a `const`. Those still need the
click-through.

### Task colors: precedence, and what an unset status is not

`taskSignal(task, today)` returns one of five keys and the **order is the whole
design**, so it is written out in the source rather than left to a conditional
chain to imply. `TASK_SIGNALS` carries the key, the label and the fill, and both
the diagram and the legend read it, so the key cannot drift from what it
explains.

Three rules are easy to get wrong:

* **Completed beats every date.** A task finished after its end date is
  finished, not late. Coloring it red teaches people to ignore red.
* **A subtask's dates count, not just the task's own.** This was a bug: Ben set
  a subtask to In progress with an end date of yesterday and the circle stayed
  yellow, because both date rules read only `task.expected_end` and
  `task.expected_start`. A task with subtasks already does not own its
  *status* — `derived_task_status` computes it — and it was inconsistent for
  the dates to work the other way, since the subtask dates are exactly where
  slippage shows up first. Each date is judged against the status that owns it:
  a subtask past its end only counts if that subtask is not completed, and one
  past its start only if that subtask has not begun.
* **An unset status past its start date IS `late_start`.** Ben asked for this,
  and it reverses the call made when the feature was first built. The original
  argument was that "nobody has assessed this" and "somebody said it has not
  started" are different facts — the point of migration 0019 — and only the
  second can be late. That distinction is real and still drawn everywhere a
  status is *reported*: the catalog's dash, the hover card's parentheses, the
  derivation ignoring unset subtasks. It is the wrong distinction for a **date**
  rule, because what the start date tells you does not depend on whether
  anybody filled the field in — the day came and went and nothing says the work
  began. Treating that as nothing to see hides the slippage precisely where
  nobody is paying attention. `taskNotBegun` is the shared predicate, so the
  task and its subtasks cannot disagree about it.

**`active` asks the subtasks as well as the derived status.** `task_rows`
derives the status on read, so a task with one subtask in progress arrives with
`status: 'in_progress'` already — but asking the subtasks directly means the
browser does not depend on the server having got it right, and the two cannot
disagree about a color. A test fixture that omitted the derived status is what
exposed the dependency, which is the useful kind of unrealistic fixture.

**The color is never the only cue.** The five fills cannot be told apart by hue
alone — active yellow and `late_start` orange are 15 degrees apart, which is no
distinction for anyone with a color deficiency — so every state but `open`
draws a mark inside the ring, and `open` draws none because the absence of
information is shown as an absence. `late_start`'s mark is a hollow ring rather
than a dash, because a horizontal bar is what a retired task already draws
across itself.

**The ring does not change color.** It is what delineates the circle against
the eight system fills it may sit on (8.69:1 against the worst), and
recoloring it per status would put that measurement back in play five times
over. The status goes in the **fill**, where the only contrast that has to hold
is against the ring and the mark — at worst 6.54:1. Measured over every fill,
not inferred from the lightest, which is the mistake recorded above for the
selection halo.

**Today is computed locally, not with `toISOString()`.** That is UTC, so west of
Greenwich it reports tomorrow's date for part of every evening, which would turn
a task due today red several hours early. `todayISO()` is the one to use, and it
is recomputed per render because the app is left open overnight.
`exportFilename` still uses `toISOString()` for a filename — the same wart,
one day off in the evening, left alone only because `t_align.js` evals a slice
that `todayISO` is not in.

**No JSX in the geometry region.** `TaskMark` was first written beside
`taskSignal`, where it belongs by subject, and took down all three Node suites
that `eval` slices of that region with `SyntaxError: Unexpected token '<'`. It
lives with the other components now, with a note saying why.

**The legend grows in three steps, and the middle one is the point.** No tasks
in the view: no entry at all. Tasks but nothing color-coded: one gray circle
labeled "Task", which is the 0.7.0 legend. Something color-coded: the
five-state key. Ben's reasoning is that the complexity of five rows should only
arrive once somebody is using statuses or dates — until then the gray circle is
the whole truth about the diagram, and five rows explaining colors that appear
nowhere are noise to read past. `tasksUseSignals` decides it, and asks "is any
circle not gray" rather than "has anyone set a status", because the two
date-driven states color a circle without anyone having touched a status
field and a key that omitted them would leave an orange circle unexplained.

**The legend swatches were all gray, and the cause is already in this file.**
`.legend .task-ring` set `fill: #dfe3e8`, and a stylesheet rule beats a `fill`
attribute on the element — so every status swatch came out gray however it was
drawn. That is the same trap recorded two paragraphs below for the swatch's
*size*, where `width`/`height` attributes lose to `.canvas-container svg`. The
comment was in the file and I walked into it with the color. When a swatch is
meant to vary, the varying property cannot be in the stylesheet.

`tools/tests/t_tasksignal.js` drives every combination the precedence
distinguishes, and reads the fills out of the source rather than hardcoding
them, so it cannot keep passing after somebody changes a color. The two
assertions that encoded the old unset-status rule are left pointing at the new
one, so the reversal reads as deliberate rather than as drift.

### Automated tasks: a blue that replaces gray and nothing else

Ben: "when a task is automated, it should show up as a blue circle instead of
grey — this will allow a user to see in the diagram which tasks are automated
vs manual." (Quoted as he wrote it, which is why "grey" survives the American
English rule here: a quotation is a record of what somebody said, not our
prose.) The question behind it is the one Manual Steps on a data flow
already answers for flows: which of this depends on a person being at their
desk.

`is_automated` is an INTEGER 0/1 column on `task` (migration 0021), not a task
type. Automation cuts across every type — a validation can be automated or run
by hand, and so can a transfer — so folding it into the type would double a
list a SuperAdmin maintains and make the catalog's Type column answer two
questions badly. It defaults to 0, because relabeling existing history as
automated would be a claim nobody made.

**Instead of gray — so it replaces the resting state and competes with
nothing.** `taskSignal` returns `automated` where it would have returned
`open`, and every stage above it wins: a task past its end date is drawn
overdue whether or not a machine runs it, because urgency is what the color is
for and automation is not urgency. The blue therefore appears exactly where
there was nothing to report, which is most circles most of the time, and that
is what makes "which of these need a person" a scan rather than a count.
`TASK_RESTING` names the two resting states once, because three places ask "is
anything being reported here" and a list written out at each would have
drifted the first time a state was added — which adding this one would have
done.

**The contrast that matters is the mark, not the fill.** `#93bde4` is 5.89:1
against the ring, so the triangle inside it passes as text. Against the
resting gray it is **1.53:1**, which is no distinction at all — and that is
deliberate rather than an oversight: automated draws a triangle and *nothing
recorded yet* draws nothing, and a mark against no mark does not depend on
color vision. A darker blue would read as a stage. Measured over every fill,
per the rule in `ACCESSIBILITY.md`, not inferred from the lightest.

**The legend grows in three steps and the automated row is independent of
them.** `tasksUseAutomated` asks whether anything in the view is automated, and
the row appears on that basis whether or not the five-state key is showing —
so a blue circle on screen always has an entry explaining it, and a key never
explains a color that is not there.

**A copy carries the flag; a copy does not carry dependencies.** Whether a
person has to do the work is a fact about the work, so next quarter's copy of a
nightly load is still automated. A dependency is not: the task its predecessor
waited on was the *previous* occurrence of something upstream, so a carried
link would leave March's copy waiting on February's load — already satisfied
before the copy existed, which is worse than no link because it reads as a
checked assumption. Same judgment as the dates.

**Both controls live in `TaskExtras`, rendered by the flyout and the Add
window.** Ben asked for both in both places. The `modal` prop picks the
surrounding markup rather than there being two components: the flyout lays
fields out in a 140px label column (`.edit-field-row`), the Add window in
`.form-group` with the label above the control, and a control that brings the
wrong family with it looks like a bug even though it works — the reused-CSS
lesson met head on instead of walked into. Sharing one component is also what
made the field catalog miss `is_automated`; see the generator section above.

The pair sits **below** both dates rather than between them. Ben asked for the
automated checkbox directly above the dependency one, and the two of them
wedged between Expected Start and Expected End split a pair of dates that
reads as a pair. Below both, the reason the dependency block is here at all
still holds.

**Dependencies are deliberately not drawn on the diagram.** Ben decided this
directly. The circles already say what is blocked and the hover card names
what it is waiting on; lines between tasks would cross the flow arrows they
sit on and make the picture answer two questions at once.

### An end date cannot precede its start date

`check_task_dates` is one function because a task and each of its subtasks ask
the same question, and a rule enforced on create but not on edit is the
asymmetry that only surfaces when somebody tries to fix a typo — the same
reasoning as `check_task_type`. Equal dates are fine: a task that starts and
finishes in a day is ordinary.

Two things about it are load-bearing:

* **The update path merges with the stored row.** An update writes only the keys
  it is sent, so a payload carrying just one of the pair has to be checked
  against the date already on the record — otherwise moving the end date alone
  is never refused. Both directions are tested, including the case that must
  still be *allowed*: moving the start alone when no end date is stored crosses
  nothing, and a check that refused it would be over-eager.
* **The subtask sweep runs before `write_task_children`.** Subtasks are replaced
  wholesale, so a bad pair caught after the `DELETE` would mean a refused save
  had already destroyed the list. `t_tasks.py` asserts the existing subtasks
  survive a refusal.

The date inputs also carry `min`/`max`, which stops the native picker offering
an impossible date. That is a convenience and explicitly not the boundary: it
can be typed past, and it says nothing about a row that predates the rule.
`EditField` and `ModalField` both take the pair now.

### A task's window comes from its subtasks, and is not stored

Ben: entering dates on subtasks and then finding the task itself blank is
confusing. The task's pair **is** the outer window, so where nobody has said
otherwise the subtasks are the best statement of it —
`derived_task_dates(step_rows)` returns the earliest subtask start and the
latest subtask end.

**Nothing is written to the task's columns.** This is resolved on read, exactly
as a derived status is, and the reasoning is the same one that made that
choice right: a value copied into `task.expected_start` is correct at the
moment of the copy and silently wrong the moment a subtask moves. A stored span
would also have to be kept in step by every path that touches a subtask — the
update handler, the copy action, a scenario clone — and whichever one got missed
would leave a task claiming a window that does not contain its own subtasks. The
derived form cannot go stale and needed no migration. `t_tasks.py` replaces a
task's subtasks and asserts the window moves with them, which is the assertion a
stored span would fail.

**Carried as `effective_expected_start` / `effective_expected_end` alongside the
raw columns**, the way `effective_definition` and `effective_title` already work
here. That distinction is what makes the override possible, and it is load-
bearing in a way that is easy to undo:

* The **edit form binds to the raw columns.** `DetailPanel.saveEdit` sends
  `expected_start` on every save whether or not anybody touched it, so a box
  pre-filled with the derived date would be sent straight back and become a
  stored override — a task would stop tracking its subtasks the first time it
  was saved for an unrelated reason. Blank means derived; typing a date
  overrides; clearing it returns to derived. A hint under each box names the
  date it is using and says how to override it, because an empty box with
  invisible behavior is worse than no behavior.
* The **read view, the catalog and its sort keys use the effective value.**
  Sorting on the raw column while displaying the effective one would file a task
  whose dates come from its subtasks among the blanks.
* **Per end, not per pair.** A task may name its own start and leave the finish
  to its subtasks, so `start_from_steps` and `end_from_steps` are separate.
* **A subtask with no date takes no part.** A subtask with no start says nothing
  about when the work begins, and treating a missing date as "now" or "forever"
  are both inventions.

`taskSignal` does not read the effective dates and does not need to: it already
asks the subtasks directly, so the color was right before this existed.

### Dependencies: the start date is what gives them teeth

A task can wait on other tasks (0.8.0, migration 0020). The first spec had the
start date **override** the dependencies; Ben revised it so the start date is
what makes them matter, and the revision is better. The rule in full:

| Start date | Dependencies | State |
|---|---|---|
| set, not yet reached | any | `open` |
| set, passed | all complete | `late_start` |
| set, passed | any unfinished | `blocked` |
| none anywhere | any unfinished | `open` |
| none anywhere | all complete | `late_start` |

The sense of it: **before the date, waiting is what the plan says should be
happening** and there is nothing to report. After it, waiting is the reason
nothing has started, which is worth a color. And with no date at all the
dependencies stand in for one — the moment the last finishes, the work could
have begun.

Four things are load-bearing:

* **`taskDepsComplete` returns false for a task with NO dependencies.**
  "Nothing is holding this up" and "everything that was holding it up is done"
  are different facts, and only the second should start the clock. A truthy
  `every()` over an empty array would have made every dateless task in the
  database report late the moment this shipped.
* **The no-date clause asks whether a start date is SET, not whether one has
  passed.** Asking the latter lets a task with a *future* start date fall
  through to the dependency rule and report late before its own start. Both
  are tested.
* **A dependency's status is the DERIVED one.** `task_rows` resolves it the
  same way it resolves a task's own, so the comparison is against the status
  the dependency would *show*. Resolving it server-side is what keeps
  `taskSignal` a pure function the Node suite can drive.
* **A dependency nobody has assessed is not a finished one**, the same
  asymmetry the subtask derivation keeps.

**Three refusals, never silent drops** (`check_task_dependencies`): a task
cannot depend on itself, on a task in another view — the rule
`check_task_anchor` already applies to parents, for the same leak reasons —
or in a way that closes a loop. The cycle check walks the graph **as it would
be after the save**, which is why the proposed list is passed in rather than
read back from the table; and it follows the whole chain, so A→C→B→A is caught
even though no two of them are paired directly.

**The scenario copy needs a SECOND pass.** A dependency points at another
task, and on the first pass the target may not have been given its scenario id
yet — one pass silently drops every forward reference, which is half the links
depending on id order. `copied_tasks` is tracked separately from `id_map`
because that map also holds the systems and flows copied earlier. A dependency
whose target was not copied is dropped rather than left pointing across the
boundary, mirroring how a task whose parent was not copied is dropped.

**Deleting a task clears rows naming it in EITHER column.** A dangling
`depends_on_task_id` would leave a task waiting forever on something that no
longer exists, and the color rules would read that as unfinished and draw it
blocked with nothing in the panel to fix.

**Only at the task level, deliberately.** Subtasks are an ordered checklist
inside one piece of work; a subtask waiting on a task elsewhere would give the
derived status two masters.

### A state's label is not always the state's own label

`late_start` is reached two ways — a start date that has passed, and no start
date at all with every dependency finished — and Ben asked for the second to
read **"Not started, dependencies completed"** rather than "past its start
date". He is right: a task with no start date cannot be past one, and a label
that contradicts the record is worse than a vague one. Somebody reads "past
its start date", opens the task, finds the field empty, and stops trusting the
circle.

**It is a labeling function, not a sixth signal.** `taskSignalLabel(task,
today)` wraps `taskSignal` and substitutes the wording; the key, the fill and
the mark are unchanged, because it *is* the same state. Adding a signal would
have meant a seventh fill to measure, a seventh legend row and a seventh
branch in the precedence, to say the same thing in different words.

**`taskLateFromDeps` is exact rather than a guess.** `startPassed` requires a
start date to exist, so a `late_start` on a task with no start date anywhere
can only have come from the dateless-dependency clause — nothing else reaches
it. The case that makes it worth a test is a date on a **subtask**: the task's
own column is empty there, so a predicate reading only `task.expected_start`
would hand back the dependency wording for a task that genuinely has a date.

**The legend still says "Not started, past its start date".** The key explains
a color, and both routes to that color are the same color; wording it per
task would mean deciding what a key says based on which tasks happen to be in
view. If that mismatch ever reads wrong, the fix is a longer legend label
covering both, not a second row.

`t_tasksignal.js` drives the boundary from both sides, and greps the hover
card's call site for `taskSignalLabel` — a correct function nobody calls is the
shape of bug this project keeps finding.

One thing the suite cost on the way: it compares against labels read out of the
source with a regex, and the source writes two of them with `\uXXXX` escapes.
Read raw, the label comes back with a literal backslash and four digits, and a
label assertion fails for a reason that has nothing to do with labels. The
reader decodes them now.

### Email: settings in the database, the secret in the environment

Added in 0.9.0, for sharing the tracker with other states: a forgotten
password needed Ben to issue a temporary one by hand, which is the first thing
another department would hit.

**The split is the design, and it is a compromise between two true things.**
`CLAUDE.md` says config is environment variables only, and that is right for a
secret. But in a state agency the person who knows the mail relay is usually
not the person who can redeploy a container, so putting the host and port in
the environment means mail never gets configured at all. So: the host, port,
encryption, username, from address, from name and `app_base_url` are rows in
the existing `preference` table — SuperAdmin-only and audited, which that
table already was, so this needed **no migration for configuration**. The
password or API key comes from the environment and nothing else.

**From a variable or from a file, and the file form is the one that matters
off Render.** `SMTP_PASSWORD` / `RESEND_API_KEY` hold the secret directly;
`SMTP_PASSWORD_FILE` / `RESEND_API_KEY_FILE` hold the *path* of a file holding
it. The second form is not a convenience — it is how both platforms an adopter
is likely to use actually deliver a secret to a container. AWS ECS task
secrets, Azure Key Vault through the CSI driver and Docker secrets all mount
the value as a file, so an app that can only read a variable forces somebody to
copy the credential out of their secret store and paste it into deployment
configuration, which is the precise thing their security team is trying to
prevent. It also buys rotation: `read_secret` reads the file **per send** rather
than caching it, so replacing a mounted secret takes effect with no restart.

Two details in `read_secret` are load-bearing. The value is **stripped**,
because a mounted secret almost always ends in a newline and a trailing
newline in a password is an authentication failure nobody can see by looking.
And a `*_FILE` that is set but unreadable is **an error, not a reason to fall
back** to the plain variable: falling back would mean a deployment whose secret
mount had failed quietly authenticating with whatever stale value was left in
the environment — something broken looking like something working, which is the
one failure shape this file exists to record. `problems()` names the mount
failure distinctly from "no credential found", because the two need different
things done about them.

Why not store the credential too, encrypted? Because it would travel in all 45
retained nightly backups, and the key to decrypt it would have to be an
environment variable — so it would move the secret rather than protect it, and
add a rotation problem. `mailer.describe()` reports `secret_present` and the
*name* of the variable, never the value; `t_email.py` asserts the password
appears in no API response.

**Two transports, and SMTP is the one that matters.** Most agencies run a
relay and will not want an external vendor in the path for a password reset,
and SMTP is what nearly every destination speaks — so one code path covers
nearly everybody. Resend's HTTP API is there for hosts that will not allow an
outbound SMTP connection. `mailer.py` is **standard library only** (`smtplib`,
`urllib`), so adopting this adds no dependency to install, pin or audit.

That SMTP-first choice turned out better than the reasoning that produced it,
because **both cloud-native email services speak plain SMTP**: AWS **SES** has
an SMTP interface with its own credentials, and Azure **Communication Services
Email** authenticates at `smtp.azurecomm.net:587` with STARTTLS, where the
username encodes the ACS resource, Entra application id and tenant, and the
password is an Entra client secret. So an adopter on AWS or Azure uses their
platform's own mail service with no code change at all. An internal agency
relay is usually easier still — `security` `none` with no username, which is
what an IP-allowlisted relay wants.

#### Do not point this at Exchange Online with a username and password

The one destination our SMTP transport does **not** have a future with is
Microsoft 365 direct submission, and it is worth being precise about why, since
it is the first thing a state running M365 will reach for.

Exchange Online began **disabling Basic Authentication for SMTP AUTH by
default in December 2026**, with permanent removal to be announced for the
second half of 2027; an administrator can re-enable it in the interim. This
module authenticates with a password only — there is no XOAUTH2 and no Graph —
so a deployment submitting through `smtp.office365.com` has a relay that stops
working by default almost immediately and permanently not long after.

**That is a deliberate scope limit, not an oversight**, and the reason it is
acceptable is the paragraph above: every destination an adopter *should* be
using already works. The documented position is therefore to steer people to
SES, ACS or an internal relay rather than to build OAuth speculatively. If a
deployment genuinely must submit through Exchange Online, that needs OAuth
support adding first, and **Microsoft Graph `sendMail` is the better target
than XOAUTH2 SMTP** — both are reachable with `urllib` and a client-credentials
token fetch, so neither costs the standard-library-only property.

Scope the worry correctly when reading about this elsewhere: the deprecation
affects **M365 as the relay**. It does not touch SES, ACS, Resend, SendGrid,
Google Workspace or an internal relay.

#### The Resend HTTP path needs a User-Agent, and a 403 is not always auth

Two faults, found when Ben pressed **Send test message** on production with a
correct API key and a verified domain.

**`api.resend.com` is behind Cloudflare, and `urllib`'s default User-Agent is
banned.** `Python-urllib/3.x` trips the Browser Integrity Check, which answers
**403 with Cloudflare error 1010** — "banned your access based on your
browser's signature". The request never reaches Resend, so nothing about the
key, the domain or the payload is involved. `USER_AGENT` is sent now, as an
honest description of this application rather than a browser string:
impersonating Chrome would also work and is the wrong trade, because it makes
our traffic indistinguishable from a scraper to whoever debugs it next and is
a lie that then has to be maintained.

**And our error message blamed the credential.** The handler mapped any 401 or
403 to "Resend rejected the API key", so the one thing that was correct is
what Ben was sent to check. **A 403 from a CDN and a 403 from an API are the
same status code and different problems**, and the status alone cannot tell
them apart — so `_looks_like_edge_block` reads the **body**: Resend answers
with JSON, Cloudflare with an HTML page naming its own numeric code. It wants
both the vendor name and an error number, because Resend's own JSON could
legitimately mention Cloudflare.

The general lesson is the one this file keeps recording from new directions:
**an error message that names a cause confidently is worse than one that does
not, when the cause is wrong.** It does not merely fail to help — it spends
somebody's time proving a correct thing correct. The detail strings here are
read by an administrator who cannot see the code, so a confident wrong
sentence is the whole of what they have.

Worth knowing for the future: **SMTP avoids this path entirely.** Resend
speaks SMTP at `smtp.resend.com` with username `resend` and the API key as the
password, with no CDN in front of it, which is another small argument for the
SMTP-first shape of this module.

**`mailer.send` returns `(ok, detail)` and never raises.** Every caller has to
carry on either way: a reset must answer the browser without revealing whether
the address exists, and the test button needs the real reason to show. The
`detail` strings are written to be read by an administrator, and the SMTP auth
failure deliberately does *not* echo the server's text, because some relays
include the username and that string reaches a browser.

**Nothing logs a message body.** A reset email contains a working token for
somebody's account, so a log line carrying one is a credential at rest in
whatever aggregates the logs. The failure line logs the reason and not the
recipient, for the same reason.

#### The test sink, and why it announces itself

`EMAIL_SINK_PATH` makes every send append JSON to a file and report success.
It exists because the feature suites run against a real server process, so an
in-memory list would be invisible to them, and because **a test suite must
never be able to mail a real person**.

It is also a hole if it is ever set on a real deployment: reset would report
success and deliver nothing. So `Preferences → Email status` shows a row
saying so, and `t_email.py` refuses to run without it rather than risking
real mail.

**It takes two variables, and the app refuses to start on one.** The first
version only warned at startup, and a warning at startup is read once by
whoever deploys and never again — an environment file copied from a test
deployment carries `EMAIL_SINK_PATH` along with everything else, and the
symptom arrives weeks later as users who never received a reset and an
administrator with no reason to doubt the mail settings they can see. So
`EMAIL_SINK_CONFIRM=discard-all-mail` is required as well, `mailer.sink_path()`
returns None without it, and `mailer.sink_refusal()` gives `main()` a sentence
to print before `raise SystemExit(2)`.

The asymmetry is the whole design: **one variable is a mistake and crashes,
two are a decision and work.** A crash on the way up is the cheapest possible
place to find this, because nothing has silently swallowed a message yet.
`sink_refusal` returns the sentence rather than raising so the caller decides
what to do with it and a test can assert the wording without arranging to catch
an exit. `t_email.py` checks both variables itself, because the server's answer
to a missing confirmation is to not start — which from inside a suite is a
connection error, and a connection error names nothing.

#### Four decisions in the reset flow that are security, not polish

* **The link is built from `app_base_url`, never the request's Host header.**
  Reading the header is the standard way reset links get poisoned: an attacker
  sends a forgot-password request for somebody else's address with a Host of
  their choosing, and the victim receives a real token pointing at the
  attacker's server. `auth.reset_link` takes the base URL as an argument so
  there is no header in reach, and `t_email.py` sends a forged `Host` and
  `X-Forwarded-Host` and asserts the link ignores both.
* **The token is in the URL fragment, not the query string.** A fragment is
  never transmitted: the browser strips everything from the `#` onwards before
  sending the request. A query string is not, so `?token=...` is written into
  the access log of every reverse proxy, load balancer and CDN in the path —
  Render's today, an ALB or Application Gateway later — where it sits as a live
  credential in whatever aggregates those logs, readable by anyone with log
  access and kept for however long those logs are kept. Clearing it from the
  address bar with `history.replaceState`, which the browser also does,
  protects browser *history* and does nothing whatever about that. Nothing
  server-side reads it from the URL either way — the page is served for the
  path alone and the browser POSTs the token it found — so this costs nothing.
  `t_email.py` asserts the **absence** of `/reset-password?token=` as well as
  the presence of the `#` form, because a check that only looked for `token=`
  somewhere in the body would still pass if `reset_link` were reverted. The
  browser reads the query string as a fallback so that links issued by an
  earlier build still work; those expire within the hour, so that fallback can
  come out once no live token predates the change.
* **The answer never reveals whether an address exists.** Same status and same
  body for an address with an account, one without, and a deactivated one —
  asserted as equality of the whole response, not as "both succeeded". A
  forgot-password endpoint that distinguishes them is a free list of who works
  at the department, which for a state agency is a phishing target list. It is
  also why a *send failure* is not reported to the caller: "we could not send
  to that address" tells an attacker the address was real.
* **The token is stored as a SHA-256 hash** (migration 0022), so a reader of
  the database or of a backup holds nothing usable. SHA-256 rather than scrypt
  because 32 bytes of `token_urlsafe` has nothing to brute-force — the slow
  hash a password needs would buy nothing and cost a comparison per click.
* **Spending a token kills every other outstanding one for that account**, and
  the reset revokes **every** session with no exception for the one asking —
  unlike change-password, nobody is signed in here, so an unrevoked session
  would be somebody else's.

The rate limit is counted **per account, not per IP**: the thing being
prevented is somebody's inbox being flooded, and an attacker doing that will
not keep the same address. It is 5 an hour rather than 1, because a person
whose first mail was slow must not be stopped. The HTTP answer does not change
when the limit bites — only the mail stops — which `t_email.py` checks against
a **fresh** account, since run against one that has already spent its hour the
burst produces zero messages and "fewer than 8" passes while proving nothing.

#### Both switches, or the link is a trap

`reset_enabled()` requires the `enable_self_service_reset` preference AND a
working transport AND a base URL. The preference is the organization's
decision; the rest is whether that decision can be honored. Offering the link
with mail unconfigured sends somebody to a screen that promises an email and
delivers nothing, and they wait instead of asking an administrator. The
sign-in screen learns this as **one boolean** on `/api/branding/public` — the
host, port and username behind it are nobody's business before sign-in, and
`t_email.py` asserts no `smtp*` or `email_*` key is public.

`password_policy` **is** public now, and that reverses an assertion in
`t_branding.py`. The reset screen is anonymous and has to state the rules a
new password must meet, or somebody types one, is refused, and has to guess.
The rules are not a secret — every signed-in user sees them on the
forced-change screen. The old check is left pointing at the new behavior with
a note, so the reversal reads as deliberate rather than as drift.

#### `/reset-password` 404'd for all of 0.9.0, and nothing could see it

The route was written the obvious way and is wrong:

```python
(r"/(reset-password)", AppStaticFileHandler, {
    "path": str(STATIC_DIR), "default_filename": "index.html"}),
```

The capture group becomes the path `StaticFileHandler` is asked to serve, so it
went looking for `static/reset-password` — and **`default_filename` only
applies to a directory.** Neither a file nor a directory, so 404. Every
emailed reset link in the release landed on an error page.

`AppPageHandler` fixes it by serving a **constant** file: the path in this URL
is not a filename at all, which is why a cleverer route cannot help.
`set_extra_headers` still receives `index.html`, so the never-cache-the-app
rule keeps applying.

**The reason this survived is the lesson.** `t_email.py` exercises
`POST /api/auth/reset-password` — a different thing with a similar name — and
every other check in this project talks to the API. **Nothing loaded a page.**
So the suite proving the link is *built* correctly sat next to a route that
could not *serve* it, and both were green. A person following a real link from
a real message was the first thing to find it, at the end of 0.9.0's smoke
test.

Two guards now exist, and the second is the general one:

* `selftest.py` asks for `/reset-password` and asserts it returns the
  **application** — `text/babel` in the body — not merely that it is not a
  404. It also asserts an unknown `/api/...` path is still a 404, which is the
  property that makes this one explicit route rather than a catch-all.
* The release checklist smoke test has a reset item, with the warning that **no
  email arrives while the sink is active** — that absence being indistinguish-
  able from a broken feature is what made the step worth writing down.

#### `/reset-password` is the one non-root page

A reset link has to point at a path rather than at `/`, so the token is not
sitting in the address bar of the ordinary diagram. `AppStaticFileHandler`
would 404 it — there is no `reset-password.html`, the whole app is one
`index.html` — so there is an explicit route for exactly that path. Not a
catch-all: returning the app for every unknown path would turn a mistyped API
route into a page of HTML instead of an error.

The browser reads the token **once on mount** and immediately clears the query
string with `history.replaceState`, because a token in a URL otherwise lands
in browser history. It stays in React state for the one request that spends it.

#### `APP_FILES` in `harness_setup.py` is hand-kept

`mailer.py` was not in it, so the harness server exited on
`ModuleNotFoundError` before binding a port and every suite reported
"connection refused". Annoying, and nothing like the silent failures a stale
*migration* list used to cause — the server does not start at all — which is
why that list is still written out rather than globbed. **Add a new top-level
module to `APP_FILES`.**

### A suite borrows the database — including from the suite before it

`t_fielddefs` asserted "nothing is defined except what the app ships with",
which is the same mistake `t_fieldhide` was fixed for one release earlier, with
one extra turn: it broke not because a person had written a definition, but
because **`t_fieldhide`, run first, leaves one behind on purpose** — it writes a
definition to prove hiding and defining are independent. So the two suites
passed alone and one of them failed whenever both ran.

It now asserts that what the app ships with is present, prints anything else as
a note, and leaves the invariant the original check was reaching for to the rest
of the suite, which writes a definition, reads it back, overrides it, clears it
and checks the field is blank again — a relationship rather than a starting
state.

The rule, stated once more because it has now been broken three ways: **assert
relationships, not a starting state you do not control** — and "you do not
control it" includes the suite that ran before yours.

**And a fourth way: a test account created with a fixed address.** Ben ran
`t_tasks` twice on his local database and got nine failures, every one of them
a permission check. None of them was the fault. The suite did
`POST /api/users` with a fixed email and then signed in with the temporary
password — which works exactly once. On the second run the POST is a 409, the
stored password is whatever the first run changed it to, the login returns 401,
and every later request as that person fails. **A sign-in that never happened
is reported as the feature being broken**, which is the expensive part: the
failures point everywhere except at the cause.

`_client.ensure_account` creates the account **or resets it** — a fresh
temporary password from the SuperAdmin, active, with the right role — and
returns the password the account ends up holding, because a later re-login in
the same suite needs it. `t_tasks`, `t_052` and `t_060` all had this bug and
all three now share the one helper.

**Resetting a password here is not the footgun `_client` exists to prevent.**
That rule is about the account a suite *borrows* to sign in as — a real
person's. These are the suite's own: it creates them, names them for itself,
and nobody else uses them. Ownership is the whole distinction, and it is worth
stating because the two look identical in a diff.

**Some checks cannot be made repeatable by resetting, and need a new address
each run.** "A title given at creation is stored" and "an unlinked account is
offered in the picker" are both about an account nobody has touched, and the
previous run linked it — resetting a password unlinks nothing.
`_client.unique_email` stamps the local part with the clock. The cost is one
spare account per run on a database already full of test debris, which is the
right trade against a check that silently stops meaning anything.

Two things this cost that are worth remembering:

* **The branch only runs on the second run**, so a mistake inside it passes
  every fresh-database test. The first version called `.get` on `/api/users`,
  which returns a bare list — green on run one, `AttributeError` on run two.
* **Verify the claim, not the fix.** The checklist already said "run them in
  any order, and more than once". Fixing `t_tasks` and stopping there would
  have left that sentence false: running the whole set twice found `t_052` and
  `t_060` failing the same way. Run every suite twice before believing it.

#### Put the restore in a `finally`, and stop on a missing precondition

Two more ways `t_email` broke the borrows-the-database rule, both found when Ben
ran it with no `SMTP_PASSWORD` in the container.

**The restore has to be in a `finally`.** It was the last thing in `main()`, so
an exception skipped it — and one did: with mail unconfigured the sink stayed
empty, `token_in(sent[-1])` raised `IndexError`, and the suite exited leaving
this deployment's email preferences set to its own test values. The docstring
claimed it restored them, which made it worse than silence. A suite that
mutates shared state has to put it back **on the way out as well as on the way
through**, so the whole body is wrapped and the restore cannot be skipped by a
crash or by an early return.

**A missing precondition must stop the suite, not fail every check.** Mail
being configured was asserted with an ordinary `ck()` and the suite carried on,
so one absent variable produced nine failures, a traceback, and no indication
which of the ten was the cause. The suites already refuse clearly when the sink
is missing; this now does the same, printing `problems()` and the exact
`docker compose` line. **A precondition checked as an assertion reports the
cause everywhere except where it is** — the most expensive failure shape in
this file, arriving this time through a test rather than through the app.

Worth knowing while you are in there: **a credential is required even when the
sink is active**, because `mailer.send` evaluates `problems()` before it looks
at the sink. That ordering is deliberate — a test environment must not be able
to mask a misconfigured transport — so the fix is a throwaway value, not a
reordering.

### `selftest.py` accepts a bare port

`python3 selftest.py 8900` used to die inside urllib with
`ValueError: unknown url type: '8900/api/systems'`, which names neither the
argument nor the fix. The feature suites in `tools/tests` take a port
positionally and always have, so a bare port is the obvious thing to type.
A numeric positional is now expanded to `http://localhost:<port>`. The
checklist passes a whole URL and did not hit this.

## Accessibility

`ACCESSIBILITY.md` records the WCAG contrast measurements for every system-type
color and what screen reader support exists. **Colors for system types must
come from `TYPE_PALETTE` in `app.py`** — every entry has been verified against
AA, so an arbitrary hex value would bypass that guarantee. The known gaps
(focus trapping, no real screen reader testing) are listed there too.

## Not yet built

- Two-factor authentication. TOTP via an authenticator app needs no email at
  all, so this is no longer blocked on anything — the `require_2fa` preference
  is in place and locked off. Email-based codes would use the 0.9.0 mailer.
- Email notifications for tasks (assignment, overdue). The mailer is built and
  the only question left is the shape: per-event alerts raise who-opts-in and
  what-stops-a-mail-storm, so a scheduled digest is probably the better first
  version.
- Single sign-on via OIDC against a state identity provider
- Field-level lineage tracing (tracing a column back through its transformations)
- Agreements management screen
