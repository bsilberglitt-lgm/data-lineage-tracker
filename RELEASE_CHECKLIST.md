# Release Checklist — Data Lineage Tracker

Everything needed to take a change from an idea to running in production, in
order, with the exact commands. Written so someone who has never released this
app can do it correctly.

**This file is the source of truth.** A Word copy exists for checking off by hand,
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

Run all of these, in order. They are grouped by what each one needs, which is
also cheapest-first:

- **1a–1c** need nothing running. Seconds each.
- **2** needs Docker.
- **3a–3b** need the app running: `docker compose up` in another terminal.
- **4** needs your eyes, and catches what none of the above can.

A new step belongs in the group whose prerequisites it shares — numbering it
into the wrong group is how one ended up running after step 2.

- [ ] **1a. JSX structural check**

  ```bash
  python3 tools/check_jsx.py
  ```

- [ ] **1b. Field catalog is current** — it is generated from the UI, so a
  field added or relabeled since the last run leaves it stale, and a stale
  catalog means that field cannot be given a definition:

  ```bash
  python3 tools/gen_field_catalog.py --check
  ```

  If it reports being out of date, run it without `--check` and commit the
  result.

- [ ] **1b-ii. Things that compile and are still wrong** — a lost `useState`
  pair or handler throws at render, showing up as a **blank page after
  sign-in** with the reason only in the browser console; and a `\uXXXX`
  escape in JSX text renders as literal characters on the page:

  ```bash
  python3 tools/check_identifiers.py --check
  ```

- [ ] **1b-iii. Diagram geometry** — alignment snapping, angle snapping, the
  export extent, where a task's circle sits and which color it takes are pure
  functions, so they can be checked without a browser. Nothing here proves the
  dragging *feels* right or that the status marks are legible; those are the
  click-through:

  ```bash
  ./tools/run_geometry_tests.sh
  ```

  Runs all five geometry suites in Docker, which you already have for local
  development, so **Node does not need to be installed on your Mac**. Expect
  five `== tools/tests/...` blocks and `All geometry suites passed.`

  If you do happen to have Node, `node tools/tests/t_align.js` and friends run
  the same files directly and are quicker. `node: command not found` just means
  you do not — use the script.

- [ ] **1c. Every field the server accepts is actually sent** — the details
  panel builds its save payloads by hand, and the update handlers write only
  the keys they are sent, so a field missing from a payload is silently
  unsaveable: the control works, the save succeeds, the value never changes:

  ```bash
  python3 tools/check_payloads.py --check
  ```

  This is how Owner on a data flow was unchangeable for months. If it reports
  a gap, add the field to the matching `saveData` in `DetailPanel`, or to
  `DORMANT` in the checker with a reason.

- [ ] **2. Real Babel compile** (authoritative — this is the compiler the
  browser uses)

  ```bash
  ./tools/check_jsx.sh
  ```

  First run pulls a Node image and takes a minute. Expect
  `OK — JSX compiles cleanly`.

- [ ] **3a. Automated end-to-end test** against local

  ```bash
  python3 selftest.py --email <your email>
  ```

  It prompts for your password (typing is hidden — no shell-quoting
  problems). Expect **all checks passed**. Any failure blocks the release.

  Add a port only if the app is not on 8765: `python3 selftest.py 8765
  --email …`. A whole URL works too, which is what Part 6 uses.

- [ ] **3b. Feature suites** — each release's own checks, against the same
  running app. They are kept rather than thrown away so a later release cannot
  quietly undo an earlier one:

  ```bash
  EMAIL_SINK_PATH=.mail-test.jsonl EMAIL_SINK_CONFIRM=discard-all-mail \
    python3 tools/tests/t_email.py --email <your email>    # 0.9.0 — email, reset
  python3 tools/tests/t_tasks.py --email <your email>      # 0.7.0 — tasks
  python3 tools/tests/t_060.py --email <your email>        # 0.6.0 — self-service profile
  python3 tools/tests/t_052.py --email <your email>        # 0.5.2 — payloads, contacts, deactivation
  python3 tools/tests/t_fieldhide.py --email <your email>  # 0.5.1 — field hiding
  python3 tools/tests/t_fielddefs.py --email <your email>  # 0.5.0 — field definitions
  ```

  The variables are **on the command above** rather than only described here,
  because a command in a block gets pasted and a paragraph underneath it does
  not. Running the bare line is how this step fails.

  **The app must have been started with the same two**, which the command above
  cannot do for you. Check before you blame the suite:

  ```bash
  docker compose exec app printenv | grep -E 'EMAIL_SINK|SMTP_PASSWORD|RESEND'
  ```

  **What the variables do.** `EMAIL_SINK_PATH` makes the app write mail to a
  file instead of sending any. The suite reads that file to find the reset
  token in a message it never receives — and it cannot mail a real person by
  accident, which is the point.

  `EMAIL_SINK_CONFIRM=discard-all-mail` is required as well, and **the app
  refuses to start on the path alone.** One variable that makes password reset
  report success and deliver nothing is something an environment file carries
  along by accident; two mean one of them is a decision.

  **It also needs a mail credential, even though nothing can leave.**
  `mailer.send` checks that the transport is configured *before* it looks at
  the sink, deliberately, so a test environment cannot hide a misconfigured
  relay. Any value will do, because the sink intercepts before a socket is
  opened. Without it the suite stops with one message naming what is missing.

  The path must be **inside `/app`**, because Docker Compose mounts your
  checkout there: a path anywhere else is written inside the container and the
  suite, running on your Mac, cannot see it.

  ```bash
  EMAIL_SINK_PATH=/app/.mail-test.jsonl EMAIL_SINK_CONFIRM=discard-all-mail \
    SMTP_PASSWORD=not-a-real-password docker compose up -d
  EMAIL_SINK_PATH=.mail-test.jsonl EMAIL_SINK_CONFIRM=discard-all-mail \
    python3 tools/tests/t_email.py --email <your email>
  ```

  The container's environment is fixed when it is created, so after changing
  any of these, `docker compose down` and bring it up again — a restart keeps
  the old environment.

  Note the two different paths for the same file — `/app/...` is where the
  container writes it, the bare name is where you read it. `.mail-test.jsonl`
  is gitignored.

  If the app exits at startup saying it is refusing to start, you set the path
  without the confirmation. That is the guard working, not a broken build.

  **Restart the app without those variables when you are done.** The app prints
  a loud warning at every startup while the sink is active, and Preferences →
  Email status shows a row saying so, because a deployment with it set would
  report password resets as sent and deliver nothing.

  Each prompts for your password, like `selftest.py`. Add a port only if the
  app is not on 8765: `t_tasks.py 8765 --email …`.

  Expect **0 failed** from each. They create records as they go — contributors,
  scenarios, tasks, test user accounts — and clean up very little, so run them
  against a **local** database you do not mind filling with debris, never
  against production. They do leave your *settings* alone: field definitions
  and hidden fields are put back exactly as they were found.

  Run them in any order, and more than once — the whole set is verified that
  way, not just one at a time. No suite assumes a database nobody has used, one
  that an earlier suite has not touched, or one it has not itself run against
  before.

  > If you ever see a block of failures that are **all permission checks**
  > (`a Viewer cannot …`, `an Editor can …`), suspect the suite's own test
  > account rather than the feature. That is what a sign-in returning 401
  > looks like from the assertions downstream of it, and it is what a second
  > run against the same database used to cause.

  > These used to carry hardcoded sandbox credentials, which failed on any real
  > database with `KeyError: 'csrf_token'` and would have **changed the
  > password** of the account they borrowed if the login had succeeded. They
  > now prompt, and never change a password.

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

> **If a PUT or POST returns `405 Method Not Allowed` on an endpoint that is
> plainly in `app.py`, the server was not restarted.** Unmatched requests fall
> through to the static file handler, which serves only GET and HEAD. The page
> itself is never cached, so a browser can be running new frontend code against
> an old route table. Restart `docker compose up` before debugging anything
> else.
>
> **And if a new feature simply does nothing — no error, a field that stays
> blank — suspect the same thing first.** A browser refresh picks up the new
> frontend immediately; it cannot reload the Python. A value the new server
> would have sent is just absent, and absent renders as empty. Restart, then
> look again.


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
- [ ] **Searchable dropdowns** (0.7.1) — every dropdown over a list of records
  is now a box you can type into. Check a few, in the flyout *and* in an Add
  window, since they sit in different layouts:
  - Clicking one shows the **whole list**, even when it already has a value.
    This is the fix for a task's **Attached to** showing only its current
    parent, so check that one specifically: open a task, click Attached to, and
    confirm every system and data flow is listed
  - Typing a few letters narrows it; Escape closes it; arrow keys and Enter
    choose without the mouse
  - The list is **not clipped** at the edge of the details panel, and appears
    above a modal rather than behind it
  - A long system name does not run underneath the little caret on the right
  - In the account picker on a contributor, accounts already linked to somebody
    are **grayed and unselectable**, naming who has them
- [ ] **Task colors** (0.7.1) — on the diagram, with the key open:
  - **The key is not all gray.** Each of the five swatches shows its own
    color, matching the circles on the diagram. A stylesheet rule beats a
    `fill` attribute, so this is the thing to look at first
  - Each circle's color matches the key, and each has its **mark** inside it
    (a check, an exclamation mark, a half-filled disc, a hollow ring, or nothing
    for a task with no status recorded)
  - **Check the marks are legible at 0.5x zoom.** They sit inside an 11px
    circle, and nothing automated can tell you whether they read — if one
    cannot be made out, say so; the answer is a bigger circle
  - A task whose end date has passed is red and its hover card says *Past its
    end date*, even when its status says In progress
  - **A task whose only dates are on a subtask still colors.** Set a subtask
    to In progress with an end date of yesterday, leave the task's own dates
    empty, and confirm the circle goes red rather than staying yellow
  - A task with no status and a start date in the past is **orange**
  - A completed task with an end date in the past is **green**, not red
- [ ] **The key earns its complexity** (0.7.1) — three states to check, which
  means looking at a view with no tasks as well as one with them:
  - A view with **no tasks**: no task entry in the key at all
  - Tasks present but nothing color-coded (no statuses, no dates past): a
    single **gray circle labeled "Task"**, not the five-row key
  - Set one status or let one date pass: the five-row key appears
- [ ] **A task's window from its subtasks** (0.7.1) — the confusion this
  removes is subtask dates entered while the task itself still reads blank:
  - Create a task with **no dates** and give its subtasks dates. The task's
    Expected Start and End show the **earliest subtask start** and the
    **latest subtask end**, in the flyout and in the catalog, each with a line
    saying where it came from
  - Change a subtask's dates and confirm the task's window **moves with
    them** — it is worked out on the spot, not copied once
  - Type a date into the task's own Expected Start: it **wins**, and the note
    disappears. The End is still derived — the override is per date, not both
  - **Save the task again without touching the dates.** The derived End must
    still be derived afterwards; if saving silently turns it into a fixed date
    the override is leaking
  - Clear the task's own date and confirm it goes **back to derived**
- [ ] **Dates in order** (0.7.1) — on a task and on a subtask:
  - Having set a start date, the date picker will not offer an **earlier** end
    date, and vice versa
  - A reversed pair is refused with a message naming both dates, and for a
    subtask the message says **which** subtask
  - After a refused save, the subtasks that were already there are **still
    there** — they are rewritten wholesale, so the check has to run first
  - Same-day start and end is accepted
- [ ] **Add Task window** (0.7.1) — in a scenario, the **Proposed** checkbox is
  above an **Overview** heading, matching Add System and Add Flow
- [ ] **Dependencies** (0.8.0) — open a task, check **Add Dependencies** under
  the expected start date:
  - The picker lists every other task and can be typed into; the task itself
    is not offered
  - Add two, then remove one. Uncheck the box with dependencies still set and
    confirm it **asks before discarding them**
  - Set a start date in the **past** and leave a dependency unfinished: the
    circle turns red with a **cross**, and the hover card names what it is
    waiting on
  - Complete that dependency: the circle becomes the orange hollow ring
  - Set the start date in the **future** instead: the circle goes gray
    whatever the dependencies say
  - Clear every date on the task *and* its subtasks: gray until the last
    dependency completes, then orange
  - Try to make a loop (A waits on B, then B waits on A). It is refused with
    a message, and the task is left as it was
- [ ] **Dependencies in a scenario** (0.8.0) — with two linked tasks in the
  live view, create a scenario. The copied task still has its dependency, and
  it points at the scenario's **own** copy rather than the live task
- [ ] **Un-hide everything** (0.8.0) — in a scenario, the button asks for
  confirmation, and the message afterwards says how many items came back
- [ ] **Help panel** (0.8.0) — on the Tasks screen, "For this screen" lists
  the tasks article, and it opens
- [ ] **Tasks** (0.7.0):
  - The sidebar shows a **Tasks** count; the catalog lists them with what each
    is attached to and, where there are steps, how many are done
  - **+ Add Task is visible on BOTH the lineage diagram and the Tasks
    catalog**, including when there are no tasks yet. The Add buttons render
    inside the counts bar, so a view left out of `STATS_BAR_VIEWS` has no Add
    button at all — check every Add button is where it should be after adding
    a record type
  - **+ Add Task**: it refuses to submit without a name, a type and a parent,
    and the parent dropdown offers systems *and* data flows in one list
  - Choose **Other** as the type: a box appears, is required, and the record
    then reads "Other — <what you typed>" in the catalog and the flyout
  - **Attached to** filters as you type, and the dates open the browser's
    calendar rather than taking free text
  - A new task has **no status** until one is chosen, and the dropdown's first
    line reads "Add Status" rather than a value
  - Add two subtasks, assign one to somebody, give one a date range, and save;
    the flyout shows the status as **determined based on subtasks**, not as a
    control, and each subtask's fields are labeled under its number
  - Check one of two subtasks: the task reads **In progress**, not Completed
  - Check every subtask; the task reads **Completed**. Uncheck one; it reads
    **In progress**
  - **Copy task**: everything comes across except the dates and the progress,
    and the copy opens so the dates can be set
  - Delete the copy; the confirmation says its subtasks go with it
  - Delete a data flow that has a task; the confirmation **counts the task**
  - **Preferences → Task Types**: add one, rename one that is in use (it says
    how many tasks move), and confirm one in use refuses to be removed
  - **Attach a document and a link** to a task, as you would to a data flow
  - The flyout names what the task is **attached to**, under its title
  - On the **lineage diagram**: a circle appears on the flow or inside the
    system box; hovering names the task and its status; clicking opens the
    flyout; dragging moves it and it stays put after a refresh
  - A second task on the same flow does **not** land on the first, and neither
    sits on top of the flow's label
  - Dragging a circle does **not** appear in the Audit Log, and does not mark a
    scenario record as Modified
  - Move a system box and reroute its arrow — the circles follow
  - The key shows a **Task** entry at the same size as the system swatches,
    below a dividing rule, and only once a task exists
  - Hovering a circle names the task with its status in parentheses — and with
    no parentheses at all when it has no status
  - A circle **cannot** be dragged off its flow line or out of its box — on a
    flow it only slides along the line
  - The hover card appears **immediately**, not after a pause
  - Clear a status back to "Add Status" and save: it stays cleared after a
    refresh
  - "Add Status" and "Add Contributor" show gray and italic in the closed
    dropdown
  - **Export** the diagram and confirm the circles are in the file
  - Inside a scenario: the tasks are the scenario's own copies, editing one
    marks it **Modified**, and Current Architecture is untouched
  - A task's **Proposed** checkbox appears in a scenario, checks and unchecks,
    and draws the circle with a dashed ring when checked
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
- [ ] **Retire in a scenario**: open a system copied from Current Architecture,
  choose **Retire in this scenario**, and confirm it stays on the diagram faded
  and struck through. **Not retired after all** should undo it. On something the
  scenario itself proposed, Retire should be absent. Check Current Architecture
  is unaffected, and that saving the record with the **Proposed** box changed
  does not quietly un-retire it
- [ ] **Edit a resource**: correct the title, description and — on a link — the
  web address, and confirm the version history survives. Check **Clear** and
  **Remove** both explain themselves on hover
- [ ] **Tooltips** appear promptly, from anywhere on the button rather than one
  spot, and appear when you reach a control by **Tab** as well as by mouse
- [ ] **Short upward data flow**: place two systems almost on top of each other
  with a flow from the lower to the upper, and confirm the label does not cover
  the arrowhead. Check a long flow and a sideways flow still look as they did
- [ ] **Invite the same people**: branch a scenario that has collaborators, check
  the box, and confirm they arrive with the roles they had. Try it with someone
  both carried over and separately invited — it should not error
- [ ] **Status** no longer offers Deprecated, for systems or data flows
- [ ] **Field definitions**: in Preferences → General, pick a field, write a
  definition and save. Open a record of that type and confirm an information
  marker appears beside the field in **both** the read and the edit view, and
  that pointing at it and reaching it with **Tab** both show the text. Pick a
  field whose label differs by view — *Classification* on a system — and check
  the marker appears in both places from the single definition. Then clear it
  and confirm the marker disappears
- [ ] **Shared definitions**: pick **Data Steward**, check *use one definition
  everywhere this field appears*, and save. Confirm the marker shows the same
  text on a system, a data flow and a data element. Then write a different
  definition for the data flow alone and confirm it overrides there while the
  others keep the shared wording; remove that override and the shared wording
  should reappear
- [ ] **Person-picker fields appear in the list**: *Data Steward* under System →
  Data Management, and *Owner* and *Data Steward* under Data Flow → Ownership.
  Those are drawn by a different component and were missing from the catalog
  at first
- [ ] Sign out, and confirm you are returned to the sign-in page

Then, in a **private/incognito window**, sign in as a **Viewer** test account:

- [ ] No Edit or Delete buttons anywhere
- [ ] No Administration section in the sidebar
- [ ] Dragging a diagram node does nothing

**Password reset, if email is configured on this environment** — the only place
a person exercises the emailed link, which no suite can do:

- [ ] Sign out. **Forgot your password?** appears on the sign-in screen if
  email is working, and is absent if it is not. Both are correct answers;
  which one you should see depends on the environment
- [ ] Request a reset for a test account. **No email will arrive, and that is
  correct** — while the sink is active the message is written to a file and
  nothing is sent. Waiting for an inbox here is waiting for something that
  cannot happen. Read the link out of the file instead:

  ```bash
  python3 -c "
  import json
  print(json.loads([l for l in open('.mail-test.jsonl') if l.strip()][-1])['body'])"
  ```

  If the body's link points at the production hostname, that is `app_base_url`
  doing its job. Swap the host for `http://localhost:8765` and keep the
  `#token=...` exactly as it is — the fragment is the part being tested.

  Two different things look the same here. If the app is running **without**
  the sink and the SMTP settings are fake, the send really failed and the
  endpoint deliberately did not say so, because "we could not send to that
  address" would confirm the address exists. `docker compose exec app printenv
  | grep EMAIL_SINK` tells you which case you are in; the app log carries the
  real reason for a genuine failure.
- [ ] The **Set a new password** screen appears — not the sign-in screen — and
  the token disappears from the address bar at once
- [ ] A new password is accepted, and the rules are stated on the screen before
  you type rather than after you are refused
- [ ] Following the same link a second time is refused
- [ ] Any other session for that account is signed out

If the link opens the sign-in screen instead, the token is not being read.
Nothing automated catches that: the server-side suite proves the link is built
correctly and says nothing about the browser reading it.

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

- [ ] **Knowledge base review.** Claude should offer this without being asked;
  if it has not, ask. What you want is a short list: which existing help
  articles this release makes wrong or incomplete, and anything new in the
  release that has no article at all. "Nothing needs changing" is a valid
  answer and should be said out loud rather than left silent. Say go ahead on
  the ones you want and Claude drafts them. An article is what somebody reads
  when they are already confused, which is why it is worth more care than the
  release notes it accompanies.

- [ ] **Read the help article drafts before they ship.** Help articles live in
  the code, not the database, so there is no draft state to fall back on —
  once the commit goes out, the article is what users read. This review is the
  only one there is.

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

- [ ] **Commit and push.** Claude writes the message to `COMMIT_MSG.txt` in the
  project folder; these three lines are the whole thing to paste.

  ```bash
  git add -A
  git status                    # read this — nothing unexpected should be listed
  git commit -F COMMIT_MSG.txt && git push
  ```

  `-F` reads the message from the file, which is the only reliable way to get a
  long one in. A message pasted straight into Terminal breaks on the first
  quote, backtick or line that looks like a shell word, and `-m` strings or
  heredocs both hit that — Ben lost a message to it. `COMMIT_MSG.txt` is
  untracked, so it never lands in the commit it describes, and it is rewritten
  every release.

  `.env`, `lineage.db`, `node_modules`, generated spreadsheets and
  `COMMIT_MSG.txt` are excluded by `.gitignore`. If `git status` shows any of
  them, stop and ask.

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

## After a certificate or runtime advisory

Both services are **Docker**, which means Render refreshes nothing on your
behalf: the image decides the trust store. When a CA compromise or a runtime
CVE is announced:

- [ ] **Push the change.** Both services build from this repo, so a push to
  the connected branch rebuilds both on its own. The two pages are not the
  same, which is easy to trip over: a web service has **Deploys**, a cron job
  has **Builds** and **Runs**, and a cron job has no Manual Deploy menu at
  all. Watch each one's page until the build succeeds.
- [ ] **Do not rely on a redeploy alone to refresh certificates.** A deploy
  that changes nothing can reuse cached layers and leave the trust store
  exactly as it was. Both Dockerfiles install the certificate package
  explicitly for this reason — that fetches from the distribution's archive at
  build time, so the store is current whatever the cache did. If a CA advisory
  ever arrives *without* a Dockerfile change, force a fresh build instead.
- [ ] The backup job is the one that makes outbound HTTPS. Run it by hand
  afterwards — its **Runs** page, **Trigger Run** — and confirm the upload
  succeeds. A CA change that breaks verification shows up as a failed upload,
  and the next scheduled run is a night away.
- [ ] Node and PHP advisories do not apply to anything deployed here. The app
  is Python and Tornado; the only Node is `node:22-alpine` in
  `tools/run_geometry_tests.sh`, a local test container that is never deployed
  and does no networking.

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

### `check_jsx.sh` prints nothing for minutes

**It is working.** The npm install inside the container runs at
`--loglevel=error`, so a *successful* install prints nothing at all — and the
first run also pulls a Node image before that. One to three minutes of silence
is normal, and it is indistinguishable from a hang, which is the problem.

To tell the difference, in a second terminal:

```bash
docker ps
docker stats --no-stream
```

A `node:20-alpine` container listed with `NET I/O` climbing between two runs
means it is downloading. If `docker ps` **itself** hangs, the problem is not
this script: the Docker daemon is unresponsive. See below.

This script is the only thing in the project that needs the npm registry, so on
a filtered network check `docker run --rm node:20-alpine npm ping` before
blaming the app.

### `docker ps` hangs, or Compose does nothing

`docker ps` is a local call to the Docker daemon and touches no network, so if
it hangs the daemon is wedged rather than anything being slow. Check the whale
in the menu bar for a dialog waiting on you — a pending update or a new service
agreement will stop it serving requests, and the dialog is easy to miss behind
other windows. Then quit Docker Desktop fully and relaunch. Check `df -h /` as
well: a nearly-full host disk wedges it in exactly this way, by not answering
rather than by erroring.

### The database container exits 1 with "bogus data in lock file"

```
FATAL:  bogus data in lock file "postmaster.pid": ""
```

**The data is fine.** This is the lock file, not the database. Postgres writes
`postmaster.pid` once at startup and was killed before finishing — a force-quit
of Docker Desktop does it — so the file exists with a corrupt first line and
Postgres cannot tell whether another server is still running. It refuses to
start rather than risk two servers on one data directory.

Do **not** reach for `docker compose down -v`; it would destroy the local
database for no reason. Delete the stale file instead, with nothing running:

```bash
docker compose down
docker compose run --rm --no-deps --entrypoint sh db \
  -c 'find /var/lib/postgresql -name postmaster.pid -delete -print'
docker compose up -d
docker compose logs db | tail -8
```

Overriding the entrypoint with `sh` means no Postgres starts — the container
only mounts the data. Expect a line or two about recovery as the write-ahead
log is replayed on the way up; that is the mechanism that makes this safe.

### A dropdown shows only its current value

Fixed in 0.7.1 and recorded here because the symptom is so misleading. The
pickers were `<input list=…>` with a `<datalist>`, and a browser filters a
datalist's suggestions against the text already in the box — so a control
pre-filled with its current value matched only itself and looked read-only.
Nothing was missing from the list. If this ever reappears, the thing to check
is that `Combo` still opens with an **empty** query rather than seeding it from
the selected label.

**Blank page in the browser**
JSX failed to compile. Run `python3 tools/check_jsx.py`, then
`./tools/check_jsx.sh`. Also check the browser console (**Cmd + Option + J**).
If it mentions `import`, the Babel version pin in `static/index.html` has been
disturbed — it must stay pinned.

**Blank page after signing in, when the sign-in screen itself worked**
Something threw while rendering the authenticated part of the app — most often
an identifier that was never declared, which compiles fine. Run
`python3 tools/check_identifiers.py`, and open the browser console
(**Cmd + Option + J**) for the actual error. A blank sign-in screen as well
means a compile failure instead; see "Blank page in the browser" above.

**`check_jsx.py` reports a stray closing tag for an element that is plainly opened**
Look at the few characters immediately *before* the opening tag, not at the tag.
The scan decides whether a `<` starts JSX from the character preceding it, so a
comment ending in a full stop, or anything else that reads as the left-hand side
of a comparison, meant the opening tag was never recorded — and the error is
then reported at the *closing* tag, which can be hundreds of lines away. It now
walks back over comments; if you see this again, that is the shape of it.

**`Cannot add a NOT NULL column with default value NULL` when resetting the harness**
The harness schema generator reads `server_default` out of the migration. Both
`server_default="0"` and `server_default=sa.text("0")` are handled now; if a
future migration uses some other form, the default is dropped and an
`ALTER TABLE ADD COLUMN ... NOT NULL` fails, taking the whole schema with it.
Check what `python3 tools/gen_harness_schema.py alembic/versions/<file>.py`
prints for the new column.

**`no such table` in a test suite, right after adding a migration**
The harness builds its schema from `alembic/versions/`, so a new migration is
picked up automatically — but only after `python3 tools/harness_setup.py` is
run again. Rebuild the harness, then `cd /tmp/h2 && python3 reset_db.py`.

**405 Method Not Allowed, or `Unexpected token '<'`**
The backend is running old code. `docker compose restart app`, then confirm at
`/api/version`.

**`aws s3 cp` hangs with no output**
The AWS CLI has no credentials configured and is waiting on the EC2
instance-metadata service looking for a role, which does not exist on a laptop.
Confirm with `aws sts get-caller-identity`. Download from the S3 console in a
browser instead, or run `aws configure` with a key for your own IAM user — the
backup writer's key is write-only and cannot read backups back.

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

## Appendix C — Turning email on in production (one time)

The release checklist proves the reset *mechanism* against a test sink, which
by design can never deliver a message. This is the separate, one-time job of
making real mail work. Nothing here is part of a release, and none of it should
be done on the same pass as a deploy.

**Read this before starting, because the order matters.** Self-service reset
needs three things together — a working transport, a public address, and the
preference switched on — and it stays hidden until all three hold. So the
switch goes on last, after a test message has actually arrived.

### 1. Choose where mail will come from

| Option | When it fits |
|---|---|
| Your own relay (SMTP) | The organization runs one and will give you a host, port and credential. Usually no username: it allows servers by address. |
| **Resend** | No relay available, or an outbound SMTP connection is awkward. Needs a domain you control. |
| Amazon SES | You are already on AWS. Ordinary SMTP with its own credentials. |

**Do not use Microsoft 365 as the relay.** Microsoft is withdrawing
password-based sign-in for sending through Exchange Online — off by default
from December 2026 — and this application signs in with a password. See the
note in `mailer.py`.

### 2. Verify a sending domain

For Resend: add the domain, then add the DKIM and SPF records it gives you at
your DNS provider. Add a DMARC record while you are there.

**Use the same domain the application is served from.** Mail from one domain
carrying a link to another is the pattern that reads as phishing to both a spam
filter and a careful reader.

### 3. Put the credential in the environment

Either the value directly, or the path of a file holding it:

| Transport | Variable | Or |
|---|---|---|
| SMTP | `SMTP_PASSWORD` | `SMTP_PASSWORD_FILE` |
| Resend | `RESEND_API_KEY` | `RESEND_API_KEY_FILE` |

Set it where the app runs — on Render, the service's Environment page — then
**restart**. The file form is read per message, so rotating a mounted secret
later needs no restart; the first setup still does.

**Check that no sink variable is set in production.** `EMAIL_SINK_PATH` makes
mail report success and deliver nothing. The app refuses to start if it is set
without `EMAIL_SINK_CONFIRM`, so a half-copied environment fails loudly — but
both together would be silent, and neither belongs here.

### 4. Fill in Preferences → Email

- [ ] **Email delivery** — SMTP or Resend
- [ ] **From address** on the verified domain, e.g. `noreply@example.org`
- [ ] **From name**
- [ ] **Public address of this app** — the full `https://` address people use.
      Reset links are built from this and never from the incoming request, so a
      stale value here sends working links to the wrong hostname
- [ ] SMTP only: **host**, **port**, **encryption**, **username**
- [ ] Save

### 5. Send a test message, and read it

**Email status** → **Send test message**. It goes to your own address and
reports what the mail server said, including the reason for a refusal.

- [ ] Email status says the credential was found, and names where
- [ ] The test message arrives
- [ ] It is not in spam — if it is, that is a DNS records problem, not a
      configuration one

Nothing else will tell you this. A reset request deliberately reveals nothing
about whether it worked, so a wrong port is invisible from the sign-in screen.

### 6. Only now, turn it on

- [ ] **Allow self-service password reset** → On
- [ ] Sign out and confirm **Forgot your password?** appears
- [ ] Request a reset for your own account and follow the link through to a new
      password
- [ ] Confirm the link does not work a second time

### 7. Tell people

- [ ] Say the address reset mail will come from, before anyone needs it
- [ ] Say that requesting a reset answers the same way whether or not the
      address has an account, so "nothing arrived" may just be a typo

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
