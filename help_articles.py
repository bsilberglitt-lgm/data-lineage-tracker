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
                     "licence", "license", "backup", "hosting", "architecture",
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

It is being developed for the Maine Department of Education. The intention is to
release it under an open source license, so that other states and agencies can
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
those flows — a student identifier, an enrolment date. They are defined once
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
        "summary": "Node colours, arrows, dragging versus clicking, zooming, "
                   "and opening a database schema.",
        "screens": ["diagram"],
        "min_role": permissions.VIEWER,
        "keywords": ["map", "picture", "nodes", "arrows", "zoom", "drag", "key",
                     "legend", "colours", "colors", "bend", "connect",
                     "connection point", "anchor", "route", "overlap",
                     "bidirectional", "crossing"],
        "body": """\
Each box is a system. Its colour shows its **type** — Source, Destination, Data
Store, Manual Entry — and the key in the corner lists them. The information
button beside the key explains what each type means. Colour is never the only
signal: the type is also printed inside the box and shown in the details panel.

Arrows are data flows, drawn from source to destination. Each is labelled with
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

An upload records **one** audit entry summarising the change rather than one per
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

Two behaviours worth knowing:

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

A SuperAdmin can revert an individual change from the log. If the record has
been edited since, you are warned about the conflict before anything happens.
Whoever made the original change is notified that it was reverted.

## Scenarios in the log

Work inside a scenario is recorded in that scenario's log, reached with the same
picker. Current Architecture's log stays clean apart from scenario lifecycle
events — created, deleted, membership changed — so an administrator can see what
is being planned without opening each one.
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
A SuperAdmin can put an organisation's logo in the sidebar and on the sign-in
screen. It is in **Preferences**, under General, beside the title and subtitle
it sits with.

## Uploading one

Click **Upload logo** and choose a **PNG or JPEG** file, up to **512 KB** and
**2000 pixels** on either side. The file is checked by its contents rather than
its name, so renaming something to `.png` will not get it accepted.

The preview beside the settings is a real sidebar header at its real width and
colour, so it shows what you will actually get — including how a very wide or
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
with a transparent background and light-coloured marks**.

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
