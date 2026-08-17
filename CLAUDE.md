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
4. Publish only after he has confirmed.

### 2. Move uploaded files to S3 when object storage exists

Uploaded documents (the Resources feature) and schema `.sql` files are currently
stored **in PostgreSQL** as binary columns. That was a deliberate interim choice:
it needs no extra vendor, and the files are covered by the database's existing
backups and point-in-time recovery.

Ben plans to add an S3 bucket when he sets up scheduled database backups.
**When that happens, remind him to migrate file storage to S3 as well** — the
two jobs belong together. The relevant code is `resource_version.file_data` and
`schema_snapshot.source_sql` in `app.py`, plus the `ResourceFileHandler`.

Until then, uploads are capped at 10 MB (`MAX_UPLOAD_BYTES`).

## Architecture

- **Backend:** Python + Tornado, PostgreSQL via `db.py` (a thin shim that keeps
  sqlite-style `?` placeholders and row access working).
- **Frontend:** a single `static/index.html` — React compiled in the browser by
  Babel. React and Babel are **pinned** by version in the script tags; do not
  un-pin them. An unpinned Babel once switched to the automatic JSX runtime,
  emitted an `import` statement, and produced a blank page.
- **Schema:** Alembic migrations in `alembic/versions/`. Never edit tables by
  hand; add a migration.
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

## Not yet built

- Two-factor authentication and self-service password reset (both need email;
  Resend is the intended provider, and a domain with DNS access is required)
- Single sign-on via OIDC against a state identity provider
- Field-level lineage tracing (tracing a column back through its transformations)
- Agreements management screen
