# Release Checklist — Data Lineage Tracker

Everything needed to take a change from an idea to running in production, in
order, with the exact commands. Written so someone who has never released this
app can do it correctly.

**This file is the source of truth.** A Word copy exists for ticking off by hand,
but if the two ever disagree, believe this one.

> **Where it says "Ask Claude"**, the exact wording changes each release (version
> numbers, commit messages, release notes). Ask rather than guess — the wrong
> version number is awkward to undo once published.

---

## Environments

| Environment | Where | Database | Purpose |
|---|---|---|---|
| **Local** | Docker Desktop on your Mac | PostgreSQL in a Docker volume | Where all work happens |
| **Test** | *not built yet* | — | See "Test environment" below |
| **Production** | Render (auto-deploys from `main`) | Render PostgreSQL | What users see |

Production URL: `https://data-lineage-tracker-7fa6.onrender.com`
Repository: `github.com/bsilberglitt-lgm/data-lineage-tracker`

**Project folder** — every command below assumes you are in it:

```bash
cd ~/Desktop/Desktop\ Folder/Ben\'s\ Folder/Claude\ Cowork/data-lineage-tracker
```

*(A different machine will have a different path. Everything after this step is
identical.)*

---

## Part 1 — Before you start

- [ ] **Docker Desktop is running.** Look for the whale in the menu bar. If it
  won't start, see *Troubleshooting → Docker won't start*.

- [ ] **You have disk space.** Docker fills up quietly and then fails in
  confusing ways.

  ```bash
  df -h /System/Volumes/Data
  docker system df
  ```

  Under ~10 GB free is worth clearing before you begin:

  ```bash
  docker system prune -a
  ```

- [ ] **You have the latest code.**

  ```bash
  git pull
  ```

- [ ] **If the pull brought in a migration, restart so it runs.**

  ```bash
  docker compose restart app
  docker compose logs app | grep -i upgrade
  ```

  Migrations run at startup, so code that expects a new column will fail with a
  **500** until the app has been restarted. Expect a line like
  `Running upgrade 0005 -> 0006`. Nothing found and no migration in the pull is
  fine; nothing found *and* a new file in `alembic/versions/` is not.

- [ ] **Start the app.**

  ```bash
  docker compose up
  ```

  Wait for `Data Lineage Tracker running at http://localhost:8765`.

  > **This command does not return, and that is correct.** It stays in the
  > foreground streaming the app's logs, so the terminal will not accept
  > another command. That is the app running, not a hang.
  >
  > Every later step that needs a terminal — `selftest.py`, the JSX check, git —
  > needs a **second terminal tab**: press **Cmd + T**, then `cd` back to the
  > project folder. Or start the app detached instead and keep one terminal:
  >
  > ```bash
  > docker compose up -d
  > docker compose logs -f app     # Ctrl-C stops watching, not the app
  > ```
  >
  > Stop a detached app with `docker compose down`.

- [ ] **Sign in** at <http://localhost:8765> and confirm the app works *before*
  you change anything. Knowing it was healthy at the start saves a lot of
  confusion later.

---

## Part 2 — While developing

### Which command applies after which change

This trips people up constantly. Only the third one needs a rebuild.

| What you changed | What to run |
|---|---|
| `static/index.html` (frontend only) | Nothing — hard-refresh the browser: **Cmd + Shift + R** |
| Any `.py` file, or a migration | `docker compose restart app` |
| `requirements.txt` or `Dockerfile` | `docker compose up --build` |

**Why:** the project folder is mounted into the container, so the browser
re-reads `index.html` on every request. But Python loads `app.py` once at
startup, and migrations only run at startup.

> **Symptom of forgetting this:** the page looks new but the API returns
> **405 Method Not Allowed**, or a JSON parse error like
> `Unexpected token '<'`. That means the frontend is new and the backend is old.
> Confirm with <http://localhost:8765/api/version> — it must show the version
> you expect.

- [ ] **After any frontend edit**, check it still compiles:

  ```bash
  python3 tools/check_jsx.py
  ```

  A JSX syntax error produces a completely blank page with the reason hidden
  in the browser console, so this check is worth running often. It takes
  about a second.

---

## Part 3 — Pre-release verification

Run all of these. They are ordered cheapest-first.

- [ ] **1. JSX structural check**

  ```bash
  python3 tools/check_jsx.py
  ```

- [ ] **2. Real Babel compile** (authoritative — this is the compiler the
  browser uses)

  ```bash
  ./tools/check_jsx.sh
  ```

  First run pulls a Node image and takes a minute. Expect
  `OK — JSX compiles cleanly`.

- [ ] **3. Automated end-to-end test** against local

  ```bash
  python3 selftest.py --email <your email>
  ```

  It prompts for your password (typing is hidden — no shell-quoting
  problems). Expect **all checks passed**. Any failure blocks the release.

- [ ] **4. Manual click-through.** *This is the step that catches what the
  others cannot.* Every bug that has reached production so far was invisible
  to the automated tests. See **Smoke test** below.

- [ ] **5. Clean up Docker** after using the Babel check, which pulls an image
  you only need occasionally:

  ```bash
  docker system prune -a
  ```

---

## Smoke test (run every release)

Sign in as a **SuperAdmin** and confirm each:

- [ ] Sign-in page loads and the **version number** in the bottom-right corner
  is correct
- [ ] Lineage diagram renders; dragging a node from its **border** moves it;
  clicking the **middle** opens the details panel
- [ ] Systems, Data Flows, Data Elements, Contributors tables all load, and
  **column sorting** works
- [ ] Open one record, **edit and save** a field, confirm it persists after a
  refresh
- [ ] Schema drill-down opens on a system with a mapped schema; a table and a
  field both open their governance panels
- [ ] **Resources**: add a link and upload a document on one record; confirm the
  title is clickable, a document offers a download prompt, and a description shows
- [ ] **Audit Log** loads (shows "Loading…" then rows — *not* "no history"),
  filters work, CSV export downloads
- [ ] **Users**, **Preferences**, and **Release Notes** screens all load
- [ ] **System types**: the diagram key is alphabetical, its information button
  explains each type, and a SuperAdmin can edit them in Preferences
- [ ] With a details panel open, clicking the diagram closes it (and the first
  click does nothing else)
- [ ] **Required fields**, in all three places a record can be edited — the Add
  modals, a table row, and the details flyout: clear a required field, save, and
  confirm it is refused with a red highlight, a "this field is required"
  message, and the form scrolled back to the top so the summary is visible
- [ ] **Delete**: a data flow can be deleted from its details panel (red button,
  "are you sure" prompt), and a system still used by a flow refuses to delete
  with an explanation rather than an error
- [ ] Both deletions appear in the **Audit Log**
- [ ] **Scenarios** — the release's main feature:
  - Create one from Current Architecture; confirm it copies every system and
    the sidebar shows it as "… (Scenario)"
  - Inside it, the background is tinted and the header names the scenario
  - Add a system and a data flow; both default to **Proposed** and draw with a
    dashed outline
  - Edit a copied record and confirm it becomes **Modified**
  - Delete a copied record and choose **retire**; it stays, struck through
  - Select several rows and hide them; **Show hidden items** brings them back
  - Open the schema on a system copied from live — the field list should be
    there — then change a field's governance and confirm **Current
    Architecture's schema is unchanged**
  - **Then switch back to Current Architecture and confirm nothing changed**
- [ ] **Help**: the **?** beside the version number opens the panel; search
  finds an article; opening it on the diagram and inside a scenario suggests
  different articles first; a Viewer does not see the administration article
- [ ] **Diagram arrows**: click an arrow's **line** — it turns blue, connection
  points appear on both systems, and each end shows a grab cursor. **Drag an end
  onto a different point** and confirm it stays after a refresh; clicking a point
  should also work. Confirm the **systems cannot be dragged** while adjusting.
  Click the line again to add a bend and drag it; double-click the bend to remove
  it; **Escape** leaves adjustment. Click the arrow's **label** and confirm it
  opens the record instead. **Reset arrow** returns it to automatic. Two flows between
  the same pair should already be drawn apart, and crossing lines should hop
- [ ] **Display Name**: set one on a data flow (max 20 characters) and confirm
  the diagram shows it instead of the ID; clear it and the ID returns
- [ ] **Field List**: add one to a data flow, attach a document, then **Clear**
  it — the slot stays and the earlier version is still listed. **Remove** takes
  the slot away entirely on a data flow. On a Manual Entry system the slot can
  only be cleared, never removed
- [ ] **Data flow naming**: in Add Data Flow, pick a source and a destination
  and confirm the **Name fills in as "<source> to <destination>"**; change one
  system and it should follow; type your own name and it must then survive
  changing a system. Repeat in a flow's details panel
- [ ] **Release titles** are legible on the Releases screen (they were once dark
  blue on a dark blue bar)
- [ ] **A modal does not close on an outside click.** Open Add Data Flow, type
  something, click the dark area beside the window: it must stay open with the
  text intact. Confirm the **X** and **Cancel** both still close it. Check the
  same for Add System, Add Contributor, Add/Edit User, Add Scenario, Manage
  Scenario, Add Release and Add System Type
- [ ] **Dropdowns fit.** In a data flow's details panel, the Source and
  Destination dropdowns stay inside the panel with a long system name selected
- [ ] **Logo**: in Preferences → General, choose a PNG and confirm the preview
  and the real sidebar both show it. Try each of the four **positions**, and
  **Hide the title text**, saving preferences after each. Check the sidebar
  still looks right and the navigation has not shifted. Confirm the logo also
  appears on the **sign-in screen** (sign out to see it), along with the
  configured **title** rather than the built-in default. Confirm that choosing
  an image changes **nothing until Save preferences** is clicked. Try
  something that is not an image — rename a `.txt` to `.png` — and confirm it
  is refused straight away. Then **Remove** it, save, and confirm both screens
  go back to the title
- [ ] Sign out, and confirm you are returned to the sign-in page

Then, in a **private/incognito window**, sign in as a **Viewer** test account:

- [ ] No Edit or Delete buttons anywhere
- [ ] No Administration section in the sidebar
- [ ] Dragging a diagram node does nothing

Finally:

- [ ] **Test whatever changed in this release specifically.** Ask Claude for a
  list of what to exercise if you're unsure.

---

## Part 4 — Version number and release notes

- [ ] **Decide the version number.** Semantic versioning, staying on `0.x` while
  in beta:
  - **MINOR** (`0.2.0` → `0.3.0`) — new features
  - **PATCH** (`0.2.0` → `0.2.1`) — bug fixes only

- [ ] **Ask Claude** to bump `version.py` and draft the release notes into
  `SEED_RELEASES` in `app.py` **as a draft** (`is_published = 0`).

- [ ] **Read the draft notes and edit them.** They are written for users, not
  developers. This is your review step and it is deliberate — publishing is
  what shows the notes to everyone.

- [ ] **Approve the notes**, then ask Claude to set that release's
  `is_published` to `1` in `SEED_RELEASES`. It then goes live automatically
  when production starts up with the new code — no separate publishing step.

- [ ] Restart and confirm the new version appears:

  ```bash
  docker compose restart app
  ```

  Then check <http://localhost:8765/api/version>, and open the release notes to
  read them the way a user will see them.

> **Important:** release notes live in the **database**, not the code, and the
> two are kept in step by one rule:
>
> - While a release is a **draft**, the code owns it — the notes are re-seeded
>   from `SEED_RELEASES` every time the app starts, in every environment. So
>   **edit drafts in the code**; an edit typed into the app is overwritten at the
>   next restart.
> - Once a release is **published**, it is live and the code never touches it
>   again. Editing published notes in the app is safe and permanent.

---

## Part 5 — Deploy to production

- [ ] **Back up the production database.** Do this every time a release contains
  a migration, and when in doubt assume it does.

  Render dashboard → your **PostgreSQL instance** → **Recovery** →
  **Create export** → download the `.dir.tar.gz` when it appears.

  *(Paid Render Postgres also keeps continuous point-in-time recovery, but a
  downloaded copy is the one that survives anything happening to the
  account.)*

  > **The nightly S3 backup does not replace this step.** It runs at 07:00
  > UTC, so by mid-afternoon it is most of a day stale — and a migration that
  > goes wrong destroys exactly the hours since it ran. Take the export
  > immediately before deploying. See `backup/README.md`.

- [ ] **Check production environment variables.** Render → your **web service**
  → **Environment**:
  - `DATABASE_URL` — present, pointing at the Internal Database URL
  - `BOOTSTRAP_SUPERADMIN_EMAIL` — present

  > **Only if you used the emergency SuperAdmin reset since the last deploy:**
  > confirm `BOOTSTRAP_SUPERADMIN_RESET` has been deleted again. Left in place,
  > it resets that password on *every* deploy. It is not normally set, so
  > there is nothing to do here unless you set it yourself.

- [ ] **Commit and push.** Ask Claude for the commit message, or write one
  summarising the release.

  ```bash
  git add -A
  git status                    # read this — nothing unexpected should be listed
  git commit -m "<message>"
  git push
  ```

  `.env`, `lineage.db`, `node_modules` and generated spreadsheets are
  excluded by `.gitignore`. If `git status` shows any of them, stop and ask.

- [ ] **Watch the deploy.** Render → web service → **Logs**. You are looking
  for, in order:

  ```
  Running database migrations...
  INFO  [alembic.runtime.migration] Running upgrade ...
  Preferences: added N new setting(s)
  Release notes: seeded N version(s)
  Data Lineage Tracker running at http://localhost:10000
  ```

  A traceback here means the deploy failed — go to **Rollback**.

---

## Part 6 — Verify production

- [ ] **Confirm the new code is live:** open
  `https://<your-app>.onrender.com/api/version` and check the version
  matches what you just released.

- [ ] **Run the automated test against production:**

  ```bash
  python3 selftest.py https://<your-app>.onrender.com --email <your email>
  ```

  It creates a temporary Viewer account to prove roles are enforced, then
  deactivates it. Expect all checks passed.

- [ ] **Repeat the smoke test** against production. Migrations and real
  PostgreSQL behave differently from local in ways tests don't always catch.

- [ ] **Hard-refresh** the browser (**Cmd + Shift + R**) the first time. The app
  sends no-cache headers, but a copy cached before those headers existed can
  still linger.

---

## Part 7 — Confirm the release notes

If you set `is_published = 1` before deploying (Part 4), the notes are already
live — this part is confirmation, not work.

- [ ] In **production**, open **Release Notes** from the sidebar and check the
  new version is there, is **not** marked Draft, and reads correctly.
- [ ] Sign out and back in to confirm the what's-new modal appears.

Publishing is what triggers that modal for every user at their next sign-in.
Users who missed several releases see them all, oldest first.

If you deliberately left the release as a draft, publish it here instead:

- [ ] Find the new version, marked **Draft**, and click **Publish**.

  Don't edit a draft's wording in the app — drafts are re-seeded from the code
  at every startup, so the change would be lost. Change it in `SEED_RELEASES`
  and deploy. Once published, in-app edits are safe.

---

## Quarterly — prove a backup restores

Not part of a release, but it belongs on the same list because it is the step
everyone means to do and nobody does.

- [ ] **Restore the latest nightly backup into a scratch database and sign in
  to it.** The full procedure is Part 3 of `backup/README.md`; it takes about
  ten minutes.

  A backup nobody has restored is a file, not a backup. Counting rows is the
  minimum — a restore into an empty schema exits successfully and looks fine
  until you look at what arrived.

- [ ] **While you are there, confirm the backups are actually arriving:**

  ```bash
  aws s3 ls s3://YOUR-BUCKET/data-lineage-tracker/daily/ --human-readable
  ```

  Expect roughly 30 files, the newest from this morning, none of them tiny.
  A backup job that has silently stopped looks exactly like one that is
  working until you go and look.

---

## Part 8 — After the release

- [ ] Note anything that went wrong, and **ask Claude to update this checklist**
  so the next release doesn't repeat it.
- [ ] Stop the local stack if you're done: `docker compose down`
  *(use `docker compose down -v` only if you want to erase local data and
  start from seed).*

---

## Hotfix path (urgent production fix)

For a broken production feature where waiting isn't reasonable. Skips depth, not
safety.

- [ ] Confirm the problem is real in production, and capture the error —
  browser console, plus Render logs:

  ```
  Render → web service → Logs
  ```

- [ ] Fix locally, then run the **minimum** checks:

  ```bash
  python3 tools/check_jsx.py          # if the frontend changed
  docker compose restart app          # if any .py changed
  python3 selftest.py --email <your email>
  ```

- [ ] Click through **just the affected feature** plus sign-in.

- [ ] Bump the **PATCH** version (`0.2.0` → `0.2.1`). Ask Claude.

- [ ] **Back up the database anyway** if the fix touches a migration. Skipping
  this is the one shortcut that can't be undone.

- [ ] Commit, push, verify `/api/version`, and test the fixed feature in
  production.

- [ ] Write the release notes **afterwards** rather than before, and publish
  when convenient.

---

## Rollback — when a deploy goes wrong

Work down this list; the first that applies is usually enough.

### The app is broken but the data is fine

Render → web service → **Deploys** → find the last good deploy →
**Rollback to this deploy**. This reverts code only, in a couple of minutes.

> **Careful:** if the bad deploy ran a migration, rolling back the code does
> **not** roll back the database. Old code against a new schema usually still
> works (columns are added, rarely removed), but if it doesn't, restore the
> database as well.

### The data is wrong

Render → **PostgreSQL instance** → **Recovery**:

- **Point-in-time recovery** — restore to a moment before the deploy. Creates a
  *new* database instance, so you can inspect it before switching over. Then
  update `DATABASE_URL` on the web service to point at it.
- **Restore from your export** — the `.dir.tar.gz` you downloaded in Part 5.

### A schema upload went wrong

No database restore needed. In the app: open the system → **Database Schema** →
**Previous versions** → **Revert to this**. The current schema is saved first,
so the revert is itself reversible.

### A single field was changed by mistake

In the app: **Audit Log** → find the entry → **Revert**. Restores the values from
before that change, and records the revert as a new entry. Only works for
updates, and it warns you if the field has changed since.

### Nothing else worked

The downloaded export plus the code in git is always enough to rebuild from
scratch. Ask Claude to walk through it.

---

## Test environment *(not built yet)*

When a test environment is added, the flow becomes:
**local → test → production**, with Part 5 and Part 6 run twice.

Steps to fill in when it exists:

- [ ] How test gets deployed (branch? manual? auto?)
- [ ] Test environment URL
- [ ] Whether it uses a copy of production data or seed data
- [ ] Who signs off before promoting to production

Ask Claude to update this section — and the Word copy — when the environment is
set up.

---

## Appendix A — One-time setup on a new machine

Only needed once per computer.

- [ ] **Git identity** — without this, `git commit` fails with
  *"Author identity unknown"*:

  ```bash
  git config --global user.name "Your Name"
  git config --global user.email "you@example.gov"
  ```

- [ ] **Docker Desktop** — install from
  [docker.com](https://www.docker.com/products/docker-desktop/), choosing
  the build for your chip ( → About This Mac).

- [ ] **Cap Docker's disk usage** — Docker Desktop → **Settings** →
  **Resources** → set the disk image size to around **10 GB**. This project
  uses 1–2 GB; the cap stops runaway growth filling your drive.

- [ ] **Python certificates** — needed for `selftest.py` to reach an HTTPS URL,
  otherwise it fails with `CERTIFICATE_VERIFY_FAILED`:

  ```bash
  /Applications/Python\ 3.14/Install\ Certificates.command
  ```

  *(Adjust the version to the Python you have.)*

- [ ] **Create `.env`** in the project folder (it is gitignored, never
  committed):

  ```
  BOOTSTRAP_SUPERADMIN_EMAIL=you@example.gov
  BOOTSTRAP_SUPERADMIN_FIRST_NAME=Your
  BOOTSTRAP_SUPERADMIN_LAST_NAME=Name
  BOOTSTRAP_SUPERADMIN_PASSWORD=
  ```

  Leaving the password blank makes the app generate a strong one and print
  it to the logs once on first run.

- [ ] **First run**:

  ```bash
  docker compose up --build
  ```

  Watch for the `Initial SuperAdmin created` banner and copy the password.
  If it scrolls past:

  ```bash
  docker compose logs app | grep -A6 "Initial SuperAdmin"
  ```

---

## Appendix B — Troubleshooting

**Blank page in the browser**
JSX failed to compile. Run `python3 tools/check_jsx.py`, then
`./tools/check_jsx.sh`. Also check the browser console (**Cmd + Option + J**).
If it mentions `import`, the Babel version pin in `static/index.html` has been
disturbed — it must stay pinned.

**405 Method Not Allowed, or `Unexpected token '<'`**
The backend is running old code. `docker compose restart app`, then confirm at
`/api/version`.

**`these Docker images are configured to store database data in a format which is compatible with pg_ctlcluster` / `unused mount/volume`**
Postgres 18+ moved its data directory to a version-specific path and declares
its volume one level up. The mount must be `/var/lib/postgresql`, **not**
`/var/lib/postgresql/data`. Fix `docker-compose.yml`, then:

```bash
docker compose down -v
docker compose up
```

Mounting the old path does not just warn — the container exits, and had it
started, the data would have gone to an anonymous volume and disappeared on the
next re-create.

**`database files are incompatible with server` on `docker compose up`**
The local Postgres major version changed and the existing volume was written by
the old one. Postgres will not upgrade a data directory in place. Recreate it:

```bash
docker compose down -v
docker compose up
```

`-v` deletes the local database and it re-seeds from scratch, which is fine —
local data is seed data. This happened when the image moved from 16 to 18 to
match production.

**`pg_restore: error: unsupported version` during a restore drill**
`pg_restore` cannot read a dump produced by a newer server. The local Postgres
is older than production's. Check `docker-compose.yml` matches the Render
database's major version, then recreate the volume as above. The symptom looks
like a corrupt backup and is not one.

**`docker compose restart app` prints nothing**
That's normal — it doesn't stream logs. Use `docker compose logs --tail=20 app`,
or `docker compose logs -f app` to follow.

**`CERTIFICATE_VERIFY_FAILED` running selftest against HTTPS**
Run the Python certificate installer (Appendix A). As a stopgap, add
`--insecure` to the selftest command.

**`fatal: Author identity unknown`**
Set your git identity (Appendix A).

**`Unable to create '.git/index.lock': File exists`**
A stale lock from an interrupted git command. With no git process running:

```bash
rm -f .git/index.lock
```

**Terminal shows `>` and won't run your command**
The shell is waiting for you to finish a multi-line command — usually a stray
trailing `\` or an unclosed quote. Press **Ctrl + C** and retype.

**Docker won't start**
1. Check space: `df -h /System/Volumes/Data`
2. Check Docker's own usage: `du -sh ~/Library/Containers/com.docker.docker`
3. Force-quit stuck processes: `sudo pkill -9 -i -f docker`, then
   `open -a Docker`
4. Still stuck: `sudo rm -rf /Applications/Docker.app` and reinstall. Nothing in
   this project depends on Docker's internal state, so a reinstall is safe — it
   only costs the download.

**Local database is in a strange state**
Erase it and start from seed. Local data is disposable; production is unaffected:

```bash
docker compose down -v
docker compose up --build
```

---

## What each verification actually proves

Worth understanding, because it explains why the manual step can't be skipped.

| Check | Proves | Does **not** prove |
|---|---|---|
| `tools/check_jsx.py` | The JSX is structurally sound | That it compiles or renders |
| `tools/check_jsx.sh` | Babel compiles the page | That it renders correctly |
| `selftest.py` | API, permissions, audit trail all work | That any screen displays properly |
| Click-through | The app works for a person | Nothing beyond what you clicked |

Every bug that has reached production in this project got past the first three
and was caught by the fourth.
