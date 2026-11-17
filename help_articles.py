"""Help articles for the in-app knowledge base.

Articles live here, in the repository, rather than in the database. That is a
deliberate trade for now:

* They are versioned with the code they describe, so an article and the change
  that invalidated it can be reviewed in the same commit.
* Screenshots sit beside them in ``static/help/`` (served at ``/help/``) and are covered by the same
  history. A stale screenshot shows up in a diff; one uploaded into a database
  quietly rots.
* Nothing new is added to PostgreSQL ahead of the planned move to S3.

The cost is that changing an article needs a deploy.

**Designed to move.** ``list_articles()`` and ``get_article()`` are the only
things the API calls. Replacing this module with a database table later means
rewriting those two functions; the endpoints and the help panel do not change.

Each article carries:

    id        stable slug, used in the URL and for linking between articles
    title     shown in the index and as the heading
    summary   one line under the title in the index, and searched
    screens   which views this is relevant to, for "For this screen"
    min_role  the lowest role that sees it at all
    keywords  extra search terms that do not appear in the text
    body      Markdown; images must be under /help/ (served from static/help/)

Bodies are hard-wrapped for readability. The renderer joins wrapped lines into
paragraphs, as Markdown does — blocks are separated by blank lines, not by line
endings. It did not always: each source line became its own paragraph, which
broke sentences apart in the panel and stranded the last word on its own line.
Keep blank lines between blocks, and indent lines that continue a bullet.
"""

import permissions

# Screens an article can be tagged with. These match the frontend's view names.
SCREENS = ["diagram", "schema", "systems", "flows", "elements", "contributors",
           "tasks",
           "audit", "users", "preferences", "releases", "scenario"]


ARTICLES = [
    # ---------------------------------------------------------------- basics
    # "About this software" comes first deliberately: it is the introduction to
    # the project — who it is for, how it is run, and why it is worth the
    # effort. "Application overview", immediately after, covers what the
    # application tracks and how the screens relate. They are kept separate
    # because they answer different questions for different readers.
    {
        "id": "about-this-software",
        "title": "About this software",
        "summary": "What the tracker is, who it is being built for, how it is "
                   "run, and what it makes possible.",
        # Always at the top of the index, whatever screen you are on. It is not
        # tagged to any screen: it is about the project, not about a view.
        "pinned": True,
        "screens": [],
        "min_role": permissions.VIEWER,
        "keywords": ["about", "purpose", "why", "introduction", "open source",
                     "license", "license", "backup", "hosting", "architecture",
                     "portable", "vendor", "contract", "procurement", "CEDS",
                     "SLDS", "EDUcore", "standards", "documentation",
                     "Maine", "Department of Education"],
        "body": """\
The Data Lineage Tracker answers three questions about the department's data:
where it comes from, where it goes, and who is responsible for it along the way.

It keeps four kinds of records: the systems that hold or move data, the data
flows that connect them, the data elements that travel along those flows, and
the contributors accountable for each. You can read all of it as an interactive
diagram or as searchable catalogs, drill into a mapped database schema field by
field, attach supporting documents, and model a proposed change privately in a
scenario before anything is built.

It was originally developed by the Maine Department of Education. It has been
released under an open source license, so that other states and agencies can
run their own copy.

## How it works

It is a web application: you sign in with a browser, and there is nothing to
install. Behind that sits a Python application and a PostgreSQL database,
packaged as a container.

All of its configuration comes from environment variables rather than being
baked in, making it portable.

The database is backed up automatically every night to separate, encrypted
off-site storage, with thirty days of daily copies kept alongside monthly and
annual ones.

## What it makes possible

**One shared record instead of many private ones.** Most of this knowledge
already exists, but in people's heads, in a spreadsheet on somebody's desktop,
or in a document written for a project that ended a while ago. When several
people each maintain their own copy, all of them drift. A single shared record
means one thing to keep current and one thing to trust.

**Explaining where a number came from.** When a superintendent asks how a figure
in a report was produced, the diagram traces it back through the flows and
systems that fed it. The answer stops depending on which staff member happens to
be available.

**Knowing who to ask.** If something looks wrong, the record names the data
owner and data steward, with their contact details. A question can go to the
right person immediately instead of circulating until it finds them.

**Keeping documentation from falling behind.** People are busy and want to build
things and solve problems; going back afterwards to write a data dictionary is
rarely the most urgent task, and often never happens at all. Making the gaps
visible changes that; it is much easier to keep documentation current as you
build when everyone can see what is missing.

**Clearer conversations with vendors.** When a contract is being written for a
new system, this is where the integration requirements come from: which data
must move, in what form, by what method, under what authority. Being specific up
front about both the data mapping and the technology sets expectations about
scope on both sides, and surfaces gaps while they are still cheap to fix.

**Planning a change before committing to it.** A scenario is a private copy of
the diagram where you can map out a migration or a new integration in advance —
adding proposed systems and flows, retiring old ones — without touching the live
picture. The point is to confirm before the work starts that the intended
outputs still hold: that every field a given report needs is still accounted for
once the new architecture is in place.

**Checking new data against your standards.** Before bringing a new source into
the ecosystem (e.g., another department's data into the SLDS) scenario planning
alongside a standards mapping tool like EDUcore lets you consider how that
source maps to the standard you have adopted, such as CEDS. That shows which
transformations will be needed, and which gaps will remain, before anyone builds
the pipeline.

## Where to go next

**Application overview** describes what the application tracks and how the
diagram and the catalogs relate to each other. **Scenario planning: the basics**
is the quickest way into the planning features described above.
""",
    },
    {
        "id": "what-this-is",
        "title": "Application overview",
        "summary": "The four things it tracks, and how the diagram and the "
                   "catalogs relate to each other.",
        "screens": ["diagram", "systems"],
        "min_role": permissions.VIEWER,
        "keywords": ["overview", "start", "introduction", "glossary", "basics"],
        "body": """\
This application records **where data comes from, where it goes, and who is
responsible for it**. It tracks four things.

**Systems** are the places data lives or passes through — a student information
system, a data store, a reporting platform. Each has a type, an owner, and
governance information such as its data classification and retention schedule.

**Data flows** connect two systems. A flow records how data moves: how often,
by what method, in what format, under what legal authority, and who owns it.
When you add one you choose the two systems first, and the name is offered
ready-made as "<source> to <destination>" — overwrite it if you want something
else.

**Data elements** are the individual pieces of information that travel along
those flows — a student identifier, an enrollment date. They are defined once
and referenced everywhere, so a definition does not drift between systems.

**Contributors** are the people and units responsible for all of the above.

## Two ways to look at the same thing

The **lineage diagram** shows systems as boxes and flows as arrows between
them. It is the fastest way to answer "what feeds this?"

The **catalogs** — Systems, Data Flows, Data Elements, Contributors — are the
same records as sortable tables, with every field visible. Use the diagram to
understand shape, the catalogs to find detail.

Clicking anything in either place opens the same details panel on the right.
""",
    },
    {
        "id": "reading-the-diagram",
        "title": "Lineage diagram: the basics",
        "summary": "Node colors, arrows, dragging versus clicking, zooming, "
                   "and opening a database schema.",
        "screens": ["diagram"],
        "min_role": permissions.VIEWER,
        "keywords": ["map", "picture", "nodes", "arrows", "zoom", "drag", "key",
                     "legend", "colors", "colors", "bend", "connect",
                     "connection point", "anchor", "route", "overlap",
                     "bidirectional", "crossing"],
        "body": """\
Each box is a system. Its color shows its **type** — Source, Destination, Data
Store, Manual Entry — and the key in the corner lists them. The information
button beside the key explains what each type means. Color is never the only
signal: the type is also printed inside the box and shown in the details panel.

Arrows are data flows, drawn from source to destination. Each is labeled with
its identifier, or with a short **display name** if the flow has one. Click the
label to open that flow.

## Dragging versus clicking

A system box has two zones, and this catches people out:

* **The outer border** is the handle. Drag there to move the box.
* **The middle** opens the details panel.

Positions are shared, so moving a box moves it for everyone. You need edit
rights to move anything.

## Zooming

Use the **magnifier buttons** at the right of the bar showing the counts. Each
click steps in or out; the percentage between them returns you to 100%.
Trackpad pinch and scroll-to-zoom still work and change the same value.

## Arranging the arrows

Arrows route themselves, and several flows between the same two systems are
drawn apart from each other so a bidirectional pair can be read as two separate
flows. Where two lines cross, one hops over the other.

An arrow has two click targets, and they do different things:

* **Its label** — the identifier, or the display name if one is set — opens the
  data flow's record.
* **The line itself** puts the arrow into adjustment, drawn in blue with its
  connection points shown.

While an arrow is being adjusted:

* **The two ends show a grab handle.** Drag one to any connection point on the
  system it joins. Points appear on both systems as small circles; the one the
  arrow currently uses is filled in.
* **Or click a connection point** to attach that end to it without dragging.
* Points only appear on the two systems the flow joins — changing *which*
  systems it connects is an edit, made in the details panel.
* **Click the line again** to add a bend where you clicked, then drag it.
  Double-click a bend to remove it, or drag it back onto the straight line.
* **The systems themselves cannot be moved** while an arrow is being adjusted.
  The connection points sit on the same border you would drag the box by, so
  one has to give way.
* **Escape**, or a click on empty space, leaves adjustment.

**Reset arrow** in the flow's details puts it back to automatic.

Arrangement is shared, like the position of a box, and is not recorded in the
audit log — it changes how the diagram reads, not what the data flow is.

## Database schemas

A system with a mapped database schema shows a small **SCHEMA** tag. Click it,
or double-click the box, to open the schema drill-down, where you can see every
table and field and set governance at either level.
""",
    },
    {
        "id": "tasks",
        "title": "Tasks: the work a flow depends on",
        "summary": "Attaching work to a flow or a data store, subtasks, the "
                   "colored circles on the diagram, automated tasks, and "
                   "dependencies between tasks.",
        "screens": ["tasks", "diagram"],
        "min_role": permissions.VIEWER,
        "keywords": ["task", "tasks", "subtask", "subtasks", "step", "steps",
                     "dependency", "dependencies", "depends", "blocked",
                     "waiting", "due", "overdue", "late", "start date",
                     "end date", "status", "circle", "color", "legend",
                     "checklist", "manual", "validation", "schedule",
                     "person responsible", "owner", "automated", "automation",
                     "automatic", "scheduled job", "by hand", "recurring",
                     "copy"],
        "body": """\
A **task** is the work that a data flow or a data store depends on: validation
steps, stored procedures, file downloads and uploads, etc. Every task is
attached to exactly one data flow or one system.

## How to Add

**+ Add Task** sits beside the counts at the top of the Tasks catalog and on
the lineage diagram. You need edit rights.

A task needs a name, a **type**, and something to attach to. The **Attached
to** box lists every system and data flow — start typing to narrow the list.

Task types are a controlled list that a SuperAdmin keeps in Preferences (note
that **Other** is always available).

## Subtasks

A task can hold **subtasks**: an ordered list, each with its own description,
person responsible, status and dates.

**Once a task has subtasks, it no longer sets its own status.** The task's
status is worked out from them: all completed means completed, any started
means in progress, and none of them set means the task is not set either.

## Dates

A task has an expected start and an expected end, and so does each subtask. If
you leave the task's dates blank, it shows the span its subtasks cover — the
earliest subtask start and the latest subtask end, with a note saying so. Fill
a date in and it takes precedence.

## The circles on the diagram

A task appears on the diagram as a small circle: on the line for a task
attached to a data flow, inside the box for one attached to a system. Hover for
its name, status and dates. Press and drag to move it; press and release
without moving to open it. A circle on a flow stays **on** the line and slides
along it.

## Color coding

Each circle is colored based on where its work stands, and every color also
carries a mark inside the circle so the diagram can be read in black and white,
printed, or by someone who does not distinguish those hues.

* **Completed** — green and a check.
* **Past its end date** — red and an exclamation mark. The end date has gone by
  and nothing says the work is finished.
* **Blocked** — red and an "x". It is past its start date and something it
  waits on has not finished. See dependencies below.
* **In progress** — yellow and a half-filled circle.
* **Not started, past its start date** — orange and an "o".
* **Automated, nothing recorded yet** — blue and a triangle. See automated
  tasks below.
* **Nothing recorded yet** — gray with no mark at all.

A subtask's dates and status count towards the color as well as the task's own,
so a task running late shows it even when the task itself has no dates. A task
finished after its end date still says completed (not late).

The key in the corner of the diagram only lists the colors once something on
that diagram is actually color-coded. Until then it shows a single gray circle
labeled Task.

## Automated tasks

Check **Automated task** on any task that a machine does — a scheduled load, a
stored procedure that runs on a timer or trigger, a transfer that does not
require manual intervention. The circle turns **blue**, so the diagram answers
"which of these tasks needs a person" at a glance. Automated tasks with start
and/or end dates set will still change color & symbol based on status and
dates.

If automation is the only thing recorded on a diagram, the key lists just Task
and Automated task — so the automated/manual distinction can be looked at on
its own, without the status colors.

## Dependencies

A task can depend on other tasks. Check **Add Dependencies** — in the Add Task
window, or on an existing task below its dates — and choose the task it waits
on. Use **Add another** for more than one. (Unchecking the box removes the
dependencies.)

Dependencies change what the colors mean:

* **Before the start date**, nothing is reported. Waiting is what the plan says
  should be happening, so the circle stays gray.
* **After the start date**, if anything it waits on is unfinished, the task is
  **Blocked** — red, with an "x".
* **After the start date**, if everything it waits on is finished, the task is
  **Not started, past its start date**.
* **With no start date anywhere** — none on the task, none on any subtask — the
  dependencies stand in for one. The task stays gray until the last thing it
  waits on is completed, and then takes the same orange "o" with the hover card
  reading **Not started, dependencies completed**, since there is no start date
  for it to be past.

A task cannot depend on itself, it cannot depend on a task in a different view,
and it cannot be given a dependency that would close a loop (so A waiting on B
waiting on C waiting on A is not allowed).

## Recurring tasks

There is no recurrence setting. A quarterly job would be set as four tasks,
which can be created more easily with **Copy**. Copy brings across the type,
the parent, the description, the notes, the people and the whole checklist, and
leaves the dates blank with every subtask reset to Not started.

## Resources

A task can hold documents and links, in the same way a system or a data flow
can.

## Tasks in scenarios

A scenario gets its own copy of every task, including the dependencies between
them — remapped to point at the scenario's own tasks, never back at the live
ones. Inside a scenario you can check **Proposed** on a task to mark it as
something the scenario adds.

Tasks have no separate hidden setting. A task is drawn on its parent, so when
you hide a system or a flow to focus a scenario, its tasks go with it and come
back with it.
""",
    },
    {
        "id": "finding-and-editing",
        "title": "Finding and editing records",
        "summary": "Sorting the catalogs, using the details panel, required "
                   "fields, and who is allowed to change what.",
        "screens": ["systems", "flows", "elements", "contributors"],
        "min_role": permissions.VIEWER,
        "keywords": ["edit", "save", "sort", "required", "delete", "permission",
                     "read-only", "table"],
        "body": """\
Every catalog table sorts by any column — click a heading, click again to
reverse. Clicking a row opens the details panel.

## Editing

Press **Edit** in the details panel, change what you need, then **Save
Changes**. If a required field has been left empty, saving is refused, the
field is outlined in red with a message beneath it, and the form scrolls back
to the top so you can see what is missing. Nothing is saved until every
required field has a value.

Required fields are marked with an asterisk when you add a record.

## Deleting

Systems and data flows can be deleted from the details panel. Deleting asks for
confirmation first. A system still used by a data flow will not delete — remove
or repoint the flow first, and the message tells you how many are in the way.

## Who can change things

* **Viewer** — can read everything, change nothing.
* **Editor** — can create, edit, and delete records.
* **Administrator** — as Editor, plus managing accounts.
* **SuperAdmin** — everything, including Preferences and the audit log.

If you can see a record but not an Edit button, your role is read-only here.
Note that inside a scenario these rules can differ: see *Scenario planning:
deep dive*.

## Attachments

Systems and data flows can carry **resources** — links, or uploaded documents
up to 10 MB. Each has a title and an optional description, and documents keep
their earlier versions.

A data flow can also be given a **Field List**, a fixed slot for the fields it
carries; Manual Entry systems always have one. An attachment can be **cleared**
without removing the slot, which is how a required field list is emptied.
""",
    },

    # ------------------------------------------------------------ governance
    {
        "id": "governance-inheritance",
        "title": "Governance and inheritance",
        "summary": "How a governance value flows from a system down to a "
                   "table and a field, and what overriding one means.",
        "screens": ["schema", "systems"],
        "min_role": permissions.VIEWER,
        "keywords": ["classification", "pii", "ferpa", "retention", "inherit",
                     "override", "steward", "owner"],
        "body": """\
Governance values — data classification, PII, FERPA, retention schedule, owner,
steward — are recorded at three levels: the **system**, a **table** within its
schema, and a **field** within that table.

A value set on a system applies to everything beneath it unless something lower
says otherwise. A table with no classification of its own inherits the system's.
A field with none inherits the table's, which may itself have come from the
system.

This means you set a sensible default once, at the top, and record exceptions
only where they exist.

## Reading the panel

When you open a table or a field, each governance value shows where it came
from: set here, or inherited from above. Clearing an override returns the value
to whatever it inherits — the panel shows what that would be before you commit.

""",
    },
    {
        "id": "database-schemas",
        "title": "Database schemas",
        "summary": "Opening the drill-down, uploading a .sql file, what "
                   "governance carries forward, and reverting.",
        "screens": ["schema"],
        "min_role": permissions.VIEWER,
        "keywords": ["sql", "upload", "tables", "columns", "fields", "revert",
                     "snapshot", "er", "diagram"],
        "body": """\
A data store can have its database schema mapped, giving you every table and
field with foreign keys drawn between them. Open it from a system's **SCHEMA**
tag, or by double-clicking the box on the diagram.

Click a table to set governance for the whole table; click a field to set it
for that field alone. Both inherit from the system unless overridden — see
*Governance and inheritance*.

## Uploading a schema

An Editor can replace a system's schema by uploading a SQL Server script
(`.sql`). The parser reads the `CREATE TABLE` statements, including primary and
foreign keys.

**Governance you have already recorded is kept.** Tables and fields are matched
by name across the upload, so classifications, stewards and retention schedules
carry forward. The summary afterwards tells you how many matched, how many were
added, how many disappeared, and how many governance values were carried over.

## Reverting

Every upload snapshots the previous schema first, so a SuperAdmin can put it
back. Reverting is itself snapshotted, so it too can be undone.

An upload records **one** audit entry summarizing the change rather than one per
field — a schema of a few thousand fields would otherwise bury everything else
in the log.
""",
    },

    # ------------------------------------------------------------- scenarios
    {
        "id": "scenarios-basics",
        "title": "Scenario planning: the basics",
        "summary": "What a scenario is, how to create one, and what it "
                   "contains to begin with.",
        "screens": ["diagram", "scenario"],
        "min_role": permissions.VIEWER,
        "keywords": ["scenario", "plan", "planning", "proposal", "copy",
                     "current architecture", "what if"],
        "body": """\
The live diagram is called **Current Architecture**. It describes what exists
today.

A **scenario** is a private copy of it, used to work out a change before anyone
builds it. You can add systems and flows, change what is there, move things
around, and retire things — and none of it affects Current Architecture.

## Creating one

Anyone with edit rights can create a scenario from **+ Add Scenario** in the
sidebar. You choose:

* **A name.** It appears everywhere as "Your name (Scenario)".
* **What to start from** — Current Architecture, another scenario, or a blank
  page.
* **Optionally, a focus.** Pick the systems your change is about and everything
  else arrives hidden. Nothing is lost; the rest is simply out of the way and
  can be brought back at any time.
* **Who else can see it.** Scenarios are private to the people invited.

## Knowing where you are

Inside a scenario the working area is tinted, a bar names the scenario and its
status, and every catalog header shows which view you are in. The picker beside
that header switches between Current Architecture and any scenario you can open.

## Managing it afterwards

**Manage scenario** on that bar lets the owner rename it, change its status,
invite people, and delete it. Deleting is recoverable — it disappears for
everyone invited, and a SuperAdmin can restore it with everything intact.
""",
    },
    {
        "id": "scenarios-in-depth",
        "title": "Scenario planning: deep dive",
        "summary": "Proposed, modified and retired records; why editing shared "
                   "information does not change Current Architecture; hiding "
                   "versus deleting; schemas; statuses and privacy.",
        "screens": ["scenario", "diagram", "schema"],
        "min_role": permissions.VIEWER,
        "keywords": ["scenario", "proposed", "modified", "removed", "retired",
                     "hidden", "hide", "copy on write", "status", "draft",
                     "approved", "private", "nuance"],
        "body": """\
This article covers the parts of scenario planning that are not obvious. If you
only need to create one, read *Scenario planning: the basics* first.

## What a scenario actually copies

Starting from Current Architecture copies the **systems and data flows**, and
the links recording which data elements each flow carries. Each copy remembers
what it came from.

It does **not** copy database schemas, data elements, or contributors. Those are
shared, and a scenario reads the live values — so the full field list is there
from the moment you create it, and looks completely normal.

The moment you *edit* one of those shared records, the scenario takes its own
copy and changes that instead. Current Architecture is left exactly as it was.
You do not have to do anything to make this happen, and there is nothing to
undo if you change your mind: the live record was never touched.

Anything you create fresh inside a scenario — a new data element, a new
contributor — belongs only to that scenario.

## The four states

Every system and flow in a scenario is in one of four states, shown as a badge
in the catalogs:

* **Proposed** — new here. Drawn with a dashed outline.
* **Modified** — copied from Current Architecture, then changed. Set
  automatically the first time you edit it.
* **Being retired** — exists today, and this plan removes it. Stays on the
  diagram, faded and struck through, so the plan reads as "we retire these, we
  add those" rather than leaving a gap.
* **Unchanged** — copied and untouched.

New records are marked Proposed by default. Clear the checkbox to say "assume
this already exists". Outside a scenario nothing can be Proposed — Current
Architecture describes what exists today, so there is nothing to propose to.

## Retiring versus deleting

These are different statements about a plan, and the record's details offer
both:

* **Retire in this scenario** — it exists today and your plan takes it out of
  service. It stays on the diagram, faded and struck through, so the plan shows
  what goes as well as what arrives. Nothing is deleted, and **Not retired
  after all** undoes it.
* **Delete** — it goes, as though it had never been in the scenario. Use this
  for something you added here and no longer want.

Something invented inside the scenario cannot be retired, because there is
nothing real to retire from; the app says so and points at Delete instead.

Retiring is a scenario idea, not a status. A record's **Status** field
describes its real-world lifecycle — Active, Planned, Under Review — while
retiring describes what *this plan* does to it. A system can be Active today
and still be retired by a proposal.

## Hiding is not deleting

Hiding takes something off the diagram and out of the catalogs **for that
scenario only**. Nothing is deleted and nothing leaves the scenario.

Select rows in the Systems or Data Flows table and use the bar that appears, or
use the button in the details panel. **Show hidden items** on the scenario bar
reveals everything temporarily; **Un-hide everything** puts it back for good.

Two behaviors worth knowing:

* Hiding a system also hides the flows attached to it — a line drawn to nothing
  is worse than no line. Showing it again brings back only the flows whose other
  end is also visible.
* Drawing a flow to something you had hidden brings it back, and tells you. The
  alternative would be a new flow that vanishes the moment you create it.

Hiding is shared with everyone invited, so a walkthrough shows what you intended.

## Schemas inside a scenario

A scenario shows the schema of everything it copied. Changing a table's or a
field's governance, or uploading a new schema, gives the scenario its own copy
of that schema first — Current Architecture keeps what it had. When that
happens, the system is marked **Modified**, because its schema is part of it.

## Statuses

A scenario moves through **Draft**, **Under review**, **Approved**, and
**Archived**. Only a Draft can be edited. The other three are read-only for
everyone, including SuperAdmins — the way back is to return it to Draft, which
is recorded in the audit log. If an approved plan could be quietly edited,
approval would not mean much.

## Who can see and do what

Scenarios are private to the people invited. Someone who is not a member is
told the scenario does not exist rather than that they lack permission, because
a name like "Replace vendor X" is itself sensitive.

A scenario role is separate from your role elsewhere. A colleague who can only
view Current Architecture can be an **editor** inside a scenario they were
invited to, and someone who edits everywhere else can be a **viewer** in yours.

Administrators can see every scenario but cannot edit ones they were not invited
to — enough to answer "what is being planned?" without changing someone's work.
Only the owner and SuperAdmins manage membership, and only a SuperAdmin can
transfer ownership.

""",
    },

    # -------------------------------------------------------- administration
    {
        "id": "accounts-and-audit",
        "title": "Accounts, roles, and the audit log",
        "summary": "What each role can do, why accounts are deactivated "
                   "rather than deleted, and what the audit log records.",
        "screens": ["users", "audit", "preferences"],
        "min_role": permissions.ADMINISTRATOR,
        "keywords": ["role", "permission", "user", "account", "deactivate",
                     "audit", "history", "revert", "password", "utc"],
        "body": """\
## The four roles

* **Viewer** — reads the diagram, catalogs and schemas.
* **Editor** — as Viewer, plus creating, editing and deleting records, and
  uploading schemas.
* **Administrator** — as Editor, plus managing accounts.
* **SuperAdmin** — everything, including Preferences, the audit log, and
  reverting changes.

Administrators can manage Viewers and Editors but cannot create or modify
SuperAdmins. The system refuses to remove or demote the last SuperAdmin, so an
installation can never be left without one.

## Accounts are deactivated, not deleted

Deactivating leaves the person's history intact and attributable. Deleting them
would orphan every record they touched, which defeats the point of an audit
trail. A deactivated account cannot sign in and can be reactivated.

New accounts get a temporary password and must change it at first sign-in.

## Forgotten passwords

If email is set up on this deployment, the sign-in screen offers **Forgot your
password?**. Entering an address sends a link to set a new one; the link works
once and stops working after an hour, and using it signs the account out
everywhere else.

The screen says the same thing whether or not the address has an account here.
That is deliberate: an answer that distinguished them would let anyone outside
the department find out who works here, one address at a time. So if nothing
arrives, check the spam folder, and then ask an administrator \u2014 the address
may simply not have an account.

**If the link is not offered, email is not set up yet** and an administrator
has to issue a temporary password instead. A SuperAdmin can turn it on under
Preferences; see the article on setting up email.

## What the audit log records

Every create, update and delete, field by field, with the old and new values,
who did it, and when. Sign-ins, failed sign-ins and refused actions are recorded
too.

**The log is append-only.** Nothing in the application edits or deletes an audit
entry. Reverting a change writes a *new* entry describing the reversal.

Bulk operations record one summary entry rather than one per record — a schema
upload touching thousands of fields would otherwise bury everything else.

## Filtering and exporting

Filter by date, person, action or record type, and export the result as CSV.

**Dates are UTC**, matching the timestamps in the table. This is deliberate: if
the filter used your local time it would disagree with the column beside it, and
a date plainly visible in the table could return nothing.

## Reverting a change

A SuperAdmin can revert an individual change from the log. **Only edits can be
reverted, and only to systems, data flows, data elements, contributors, and
schema tables and columns.** Creations, deletions, and changes to settings,
accounts, field definitions or attachments are recorded but cannot be undone
from here.

If the record has been edited since, you are warned about the conflict before
anything happens. Whoever made the original change is notified that it was
reverted.

## Scenarios in the log

Work inside a scenario is recorded in that scenario's log, reached with the same
picker. Current Architecture's log stays clean apart from scenario lifecycle
events — created, deleted, membership changed — so an administrator can see what
is being planned without opening each one.
""",
    },
    {
        "id": "email-setup",
        "title": "Setting up email",
        "summary": "Connecting the tracker to your own mail server or Resend, "
                   "where the password goes, and how to test it.",
        "screens": ["preferences"],
        "min_role": permissions.SUPERADMIN,
        "keywords": ["email", "mail", "smtp", "resend", "relay", "password "
                     "reset", "forgot password", "from address", "port", "587",
                     "465", "starttls", "ssl", "credential", "api key",
                     "setup", "configure", "test message", "deliverability"],
        "body": """\
The tracker sends email for one thing at present: a link that lets somebody
who has forgotten their password set a new one. Until this is configured, a
forgotten password means an administrator issuing a temporary one by hand,
which works but does not scale past a handful of people.

Only a SuperAdmin can see or change any of this.

## It is configured in two places, on purpose

Everything except the password or API key is on the **Preferences \u2192 Email**
screen, so you do not need access to the server to set mail up.

**The credential is the exception.** It comes from an environment variable
where the application runs, never from this screen. This prevents passwords
from being stored in the nightly database backups.

## Choosing a transport

**SMTP** works with your organization's own mail relay, and also with
Microsoft 365, Google Workspace, Resend and SendGrid. You will need:

* the host, e.g. `smtp.example.gov`
* the port \u2014 **587** with STARTTLS is the usual pairing; **465** goes with SSL
* a username
* the matching password, set as `SMTP_PASSWORD` in the environment

**Resend** uses their web API instead of an SMTP connection. Choose it if the
place you are hosting will not allow outbound SMTP, which some platforms
block. You will need a Resend account with your sending domain verified, and
the API key set as `RESEND_API_KEY`.

Either way, set the variable and then **restart the application** \u2014 it reads
its environment at startup.

## The other settings

**From address** is the address mail appears to come from. Most relays will
only accept an address on a domain they handle, so a mismatch here is the most
common reason a correctly configured server still refuses to send.

**From name** is what a mail client shows beside the address. Optional.

**Public address of this app** is the address people use to reach the tracker,
e.g. `https://lineage.example.gov`. It has to be set, because it is what the
reset link is built from. It is taken from this setting rather than from
whatever address the request arrived on, so that nobody can cause a reset link
to point somewhere else.

## Checking it works

**Preferences \u2192 Email status** sits under the settings and reports:

* whether the credential was found, and the name of the variable to set if not
* anything else still missing, listed all at once
* a **Send test message** button

The test message goes to your own address and tells you exactly what happened,
including the mail server's reason for a refusal. Save your changes before
pressing it \u2014 it tests what the server currently has, not what is on screen.

This button exists because nothing else will tell you. A reset request
deliberately reveals nothing about whether it worked, so a wrong port is
invisible from the sign-in screen.

## Turning reset on

Set **Allow self-service password reset** to On. The link appears on the
sign-in screen only once email is actually working, so it can never offer
something that quietly fails \u2014 if you have switched it on and the link is not
there, look at Email status.

## If mail is not arriving

Work down these in order:

1. **Email status says the credential is missing.** Set the variable and
   restart the application.
2. **The test message fails with a refusal from the server.** Read the reason
   it gives. "Sender refused" almost always means the from address is on a
   domain your relay does not handle.
3. **The test message fails with a connection or TLS error.** The port and the
   encryption setting disagree. Try 587 with STARTTLS, or 465 with SSL.
4. **The test succeeds but nothing arrives.** It is now a delivery question
   rather than a configuration one: check spam, and check with whoever runs
   your mail that this sender is allowed. Mail from a new address is often
   quarantined for a while.
5. **Email status shows "Test sink is active".** The application is running
   with a testing variable set that writes mail to a file instead of sending
   it. It should never be set on a real deployment.
""",
    },
    {
        "id": "unused-fields",
        "title": "Switching off fields you do not use",
        "summary": "Removing fields your department does not record, from both "
                   "the details panel and the Add window.",
        "screens": ["preferences"],
        "min_role": permissions.SUPERADMIN,
        "keywords": ["hide", "hidden", "unused", "not used", "remove field",
                     "field", "switch off", "turn off", "simplify", "tidy"],
        "body": """\
A SuperAdmin can mark a field as not used, which hides the field in both the
details panel and the Add window.

## Switching one off

**Preferences → General → Field definitions.** Choose the field, then check
**Do not use this field**. It takes effect immediately.

## What happens to anything already recorded

**Nothing is deleted.** Anything already recorded in a field you switch off is
kept, and comes back exactly as it was if you switch the field on again. If
fourteen systems already name a vendor and you switch *Vendor* off, those
fourteen values stay in the database untouched.

One thing worth knowing: a switched-off field can still be carrying information
nobody can see. If you are switching something off because the values in it are
wrong rather than because the field is irrelevant, clear the values first.

## One record type at a time

Eleven fields appear on more than one kind of record — *Data Steward* is on systems, data flows and data elements. A
department might record *Data Standards* on data flows and never on systems, so
turning it off is **not shared across record types, even when definitions might
be**.

## Fields you cannot switch off

Fields that are **required** when creating a record cannot be switched off —
the form could not be completed without them. A system's name and type are two.
Those show the reason rather than simply omitting the control.

## Finding what you have switched off

In the field list, anything switched off is marked **not used** — both in the
dropdown and in the list of fields that already have a definition. Every change
is also recorded in the audit trail, with who made it and when.
""",
    },
    {
        "id": "field-definitions",
        "title": "Explaining what a field means",
        "summary": "Writing definitions that appear as tooltips beside fields, "
                   "so the meaning travels with the form.",
        "screens": ["preferences"],
        "min_role": permissions.SUPERADMIN,
        "keywords": ["definition", "definitions", "tooltip", "help text",
                     "field", "glossary", "meaning", "explain", "data dictionary"],
        "body": """\
Some field names explain themselves. *Name*, *Email*, *Organization* need no
help. Others carry a precise meaning that only the person who set the policy
knows — *Disclosure Avoidance*, *Regulatory Driver*, *Validation at Entry* —
and that meaning usually lives in a document nobody opens while filling in a
form.

A definition written here appears as a small **information marker** beside that
field in the details panel, for every user. Point at it or reach it with the
**Tab** key and the definition appears.

## Writing one

**Preferences → General → Field definitions.** Choose a field from the list,
which shows where each one lives:

    Data Flow → Transfer Details → Frequency

That path matters because a bare label is not enough to choose from. There are
seventy-seven fields, *Classification* exists on both systems and data flows,
and *Format* means different things in different sections.

Write a sentence or two in the words someone filling the field in would use,
and save. A check in the list marks the fields that already have a definition,
and the count beside it shows how many are done — the gaps are the point as
much as the definitions.

To remove one, clear the box and save, or use **Remove definition**.

## One definition across record types

Some fields exist on more than one kind of record. **Data Steward** is a field
on systems, data flows and data elements; eleven fields are like this.

When you pick one of those, there is a check box: **use one definition
everywhere this field appears**. Check it and you are editing a single shared
definition — change it once and all three update. The list of definitions marks
these as **shared**.

Leave it clear and you are defining the field for that record type only, which
is right where the meaning genuinely differs: a system's **Description** says
what the system is for, while a data flow's says what the flow carries.

You can do both. A record type's own definition overrides the shared one, so
Data Steward can be defined once for everything while a data flow says
something more specific. Remove the override and the shared wording reappears —
nothing is lost by trying it.

## One definition, every spelling

A few fields are labeled slightly differently depending on where they appear:
a classification reads *Classification* in the details panel and *Data
Classification* in the Add window. These are the same field — the application
stores them in one place — so **one definition covers all of its labels**.
Preferences tells you when a field has more than one spelling.

## What this is not

It is not a data dictionary for the data itself. A definition here explains
**the field on the form** — what to put in it and why — rather than what a
particular system's column contains. For that, use a data element, which is
defined once and referenced wherever it travels.

Changes are recorded in the audit log like any other, so it is clear who
defined what and when.
""",
    },
    {
        "id": "branding",
        "title": "Adding your logo",
        "summary": "Uploading a logo, where it can sit, and what makes one look "
                   "right against the dark sidebar.",
        "screens": ["preferences"],
        "min_role": permissions.SUPERADMIN,
        "keywords": ["logo", "brand", "branding", "image", "png", "jpg", "jpeg",
                     "upload", "title", "sidebar", "sign-in", "icon"],
        "body": """\
A SuperAdmin can put an organization's logo in the sidebar and on the sign-in
screen. It is in **Preferences**, under General, beside the title and subtitle
it sits with.

## Uploading one

Click **Upload logo** and choose a **PNG or JPEG** file, up to **512 KB** and
**2000 pixels** on either side. The file is checked by its contents rather than
its name, so renaming something to `.png` will not get it accepted.

The preview beside the settings is a real sidebar header at its real width and
color, so it shows what you will actually get — including how a very wide or
very tall image is scaled to fit.

## Where it can sit

Choose **left, right, above or below** the title. The logo is always scaled to
fit a fixed space, so no image can stretch the sidebar or push the navigation
around, whatever shape it is.

If your logo already contains the name, turn on **Hide the title text** so the
name is not shown twice. It is still used for the browser tab and read out to
screen readers, so nothing is lost.

**Nothing takes effect until you click Save preferences** — the image, the
position and the hide-the-title option alike. Until then the preview is the only
thing that changes, and it is marked as not saved.

## What looks good

The sidebar is dark navy. A logo drawn for white paper — dark marks on a white
rectangle — will look like a sticker stuck onto it. What works best is a **PNG
with a transparent background and light-colored marks**.

Because it is displayed about 32 pixels tall, fine detail and small lettering
will not survive. A simple mark reads better at that size than a detailed one.

## Replacing and removing

Choosing a new image replaces what is there once you save; there is no version
history for a logo, unlike a document attached to a record. **Remove** takes it
away entirely and the title goes back to standing on its own — also on save, so
**Keep it after all** is there if you change your mind. Both are recorded in the
audit log.
""",
    },
]


# ---------------------------------------------------------------------------
# The only two functions the API calls. Swapping this module for a database
# table later means rewriting these; nothing else changes.
# ---------------------------------------------------------------------------

def _visible(article, role):
    return permissions.at_least(role, article.get("min_role", permissions.VIEWER))


def list_articles(role, screen=None):
    """Every article this role may read, in three groups.

    Returns ``(pinned, relevant, rest)``:

        pinned    always first, whatever screen you are on
        relevant  about the current screen
        rest      everything else

    Ordering, not filtering: an article that is not about this screen is still
    listed, just lower down. Hiding it would make the index feel unreliable.

    **Pinned is a separate group rather than a screen tag.** Tagging an
    introduction with every screen would put it under the "For this screen"
    heading on the Audit Log, which is not what that heading means. Within each
    group the order of ``ARTICLES`` is preserved.
    """
    items = [a for a in ARTICLES if _visible(a, role)]
    pinned = [a for a in items if a.get("pinned")]
    items = [a for a in items if not a.get("pinned")]
    if screen:
        relevant = [a for a in items if screen in a.get("screens", [])]
        rest = [a for a in items if screen not in a.get("screens", [])]
        return pinned, relevant, rest
    return pinned, [], items


def get_article(article_id, role):
    """One article, or None if it does not exist or this role may not read it."""
    for a in ARTICLES:
        if a["id"] == article_id and _visible(a, role):
            return a
    return None


def public(article, include_body=False):
    """Strip internals before sending to the browser."""
    out = {k: article[k] for k in ("id", "title", "summary", "screens")}
    if include_body:
        out["body"] = article["body"]
    return out
