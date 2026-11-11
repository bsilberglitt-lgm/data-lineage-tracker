# Scenario planning — how it works

A review document for v0.3.0. It describes what was built on the server side,
the decisions behind it, and what has actually been proven by testing rather
than assumed. There is a companion script that demonstrates all of it against a
running copy — see **Seeing it work** at the end.

---

## The idea in one paragraph

The live diagram is now called **Current Architecture**. Alongside it, anyone
from Editor upwards can create a **scenario**: a private copy of the diagram
used to plan a change before it is built. A scenario can start from Current
Architecture, from another scenario, or from a blank page. Inside it you can add
systems and flows, retire existing ones, and move things around, and none of it
touches the live diagram. Scenarios are private to the people invited to them.

---

## 1. How the data is stored

There is no separate set of scenario tables. Systems, flows, and everything else
live in the tables they always have, with one extra column:

| Column | Meaning |
|---|---|
| `scenario_id` | Which view this record belongs to. **Empty means Current Architecture.** |
| `origin_id` | The live record this was copied from, if any. |
| `change_state` | `unchanged`, `proposed`, `modified`, or `removed`. |

**Why one set of tables rather than two.** A parallel set of scenario tables
would mean every future column had to be added in two places, and the two would
drift apart the first time someone forgot. The cost of the approach chosen is
that *every* query has to filter on `scenario_id`, and a single query that
forgets would show one person's private plan on the live diagram.

That risk is handled by never writing the filter by hand. It comes from one
helper in `scenarios.py`, and there is a test that fails if a scenario record
ever appears in a live response. This is the single most important thing to know
about the design, and the thing to protect in any future change.

**Identifiers.** A scenario's copy of `SYS-001` is `S3-SYS-001` — the number is
the scenario. This keeps identifiers unique, keeps the original legible in the
audit log, and means a leaked record is obvious on sight rather than looking
like ordinary data.

---

## 2. What gets copied, and what doesn't

Cloning Current Architecture copies **systems, data flows, and the links
between flows and data elements**. It does not copy database schemas, data
elements, contributors, or agreements.

Those are shared. Inside a scenario they read through to the live values, so the
full field list is there and looks completely normal. The moment somebody edits
one, that single record forks into the scenario and the live original is left
alone — **copy-on-write**.

**Why.** Your seeded schemas alone are 118 tables and 3,304 columns, and
`schema_snapshot` also stores the raw uploaded `.sql` file. Copying all of it
per scenario would multiply the largest tables in the database — five scenarios
would make the schema tables twenty times their current size — for data that
almost nobody edits in a scenario. Copy-on-write gives the same behavior: full
independence when you use it, nothing duplicated when you don't.

**Verified:** in an empty scenario, all 33 data elements are visible. After
forking one, there are still 33 — the fork *replaces* the live one in that view
rather than appearing alongside it — and the live table is unchanged.

Something created fresh inside a scenario (a new data element, a new
contributor) belongs only to that scenario, exactly as you asked.

---

## 3. The four change states

| State | Means | On the diagram |
|---|---|---|
| `unchanged` | Copied from live and untouched | Normal |
| `proposed` | New in this scenario | Dotted border / dotted line |
| `modified` | Copied from live, then edited here | Marked as changed |
| `removed` | Exists today; this plan retires it | Struck through, still visible |

New items default to **proposed**, and the checkbox can be cleared to say
"assume this already exists". `proposed` is refused outright on Current
Architecture — there is nothing to propose *to* — rather than being silently
ignored, so a checkbox can never look saved when it isn't.

`modified` is detected automatically: edit a copied record and it flips. Editing
a proposed record leaves it proposed, because it is still new.

**Retiring and deleting are separate actions**, as you chose. Retiring
something that exists today makes it `removed` — it stays visible so the plan
reads as "we retire these, we add those" — while deleting removes it outright.
Trying to *retire* something that only ever existed in the scenario is refused
with an explanation, since there is nothing real to retire.

Both directions live in `scenarios.set_retired()`, and `DELETE` takes
`mode=remove` or `mode=restore`. They were separated deliberately: retiring was
one-way until 0.4.4, because `mark_modified` leaves `removed` alone on purpose,
so a mis-click could not be undone without rebuilding the scenario. Restoring
returns a record to `unchanged` rather than `modified`, because whether it had
local edits before being retired is no longer knowable, and claiming "modified"
when nothing differs from live would misreport the change set.

**The Proposed checkbox does not touch a retired record.** It used to write
`change_state` unconditionally, so saving a retired system with the box clear
quietly un-retired it, and with the box checked claimed a live system had been
invented in the scenario.

---

## 3a. Hiding — focusing a scenario

A scenario about NEO and Federal Reporting shouldn't be buried under six other
systems. Any record on the diagram can be **hidden**: it stays in the scenario
in every respect, it just stops being drawn or listed.

**Hiding is not a fifth change state, and that is deliberate.** The four states
answer *"how does this differ from live?"* — a statement about the plan. Hiding
answers *"am I looking at this right now?"* — a statement about the view. If
they were one field, hiding a proposed system would erase the fact that it was
proposed.

How it behaves:

- **Shared across the scenario.** Everyone invited sees the same focused
  diagram, so a walkthrough in a meeting shows what you intended. Any scenario
  editor can change it.
- **Applies to the diagram and the catalogs**, with one show-everything toggle
  revealing both.
- **Multi-select.** Hiding twenty systems is one request and **one** audit
  event, not twenty.
- **Flows follow their systems.** Hiding a system hides the flows attached to
  it, because a line drawn to nothing is worse than no line. Showing a system
  brings back only the flows whose *other* end is also visible.
- **Drawing to something hidden brings it back**, and says so. Otherwise a flow
  you just created would be invisible the moment you made it.
- **Choose the focus at creation.** "Start from Current Architecture, focused on
  NEO and Federal Reporting" copies everything and hides the rest, so nothing is
  lost.

> **Hiding is cosmetic and must stay that way.** It must never remove a record
> from analysis. When field-level gap analysis arrives, hiding NEO cannot make
> data stop flowing through NEO — otherwise tidying the diagram would silently
> manufacture clean results. This is written into migration 0005 as well, so it
> is hard to undo by accident.

**Verified:** creating a scenario focused on two systems copies all 8 and shows
2; the show-all toggle reveals all 8; unhiding three shows 5; all 7 flows remain
present throughout; and Current Architecture is untouched by any of it.

---

## 4. Who can do what

Two role systems, deliberately independent. Your system role governs Current
Architecture. Your scenario role governs one scenario, and a scenario role can
be higher *or* lower than your system role.

### Creating and seeing

| | Create a scenario | See scenarios |
|---|---|---|
| Viewer | No | Only ones they own or were invited to |
| Editor | Yes | Only ones they own or were invited to |
| Administrator | Yes | **All of them** |
| SuperAdmin | Yes | All of them |

### Acting inside one

| | Read | Edit contents | Manage members, rename, status, delete |
|---|---|---|---|
| Not a member | **404 — cannot tell it exists** | No | No |
| Administrator, not a member | Yes | **No** | No |
| Scenario viewer | Yes | No | No |
| Scenario editor | Yes | Yes | No |
| Owner | Yes | Yes | Yes |
| SuperAdmin | Yes | Yes | Yes |

Notes on the choices:

- **A non-member gets 404, not 403.** A 403 confirms the scenario exists, and a
  name like "Replace Vendor X" is itself sensitive.
- **An Administrator can look but not edit.** They can answer "what is being
  planned?" without quietly changing someone's work.
- **An owner cannot grant ownership or any admin-level role**, only editor or
  viewer, exactly as specified. Only a SuperAdmin transfers ownership; when they
  do, the previous owner stays on as an editor rather than losing access to
  their own work.
- **Removing someone leaves their contributions in place.** The audit trail is
  append-only and the work belongs to the scenario, not the person.

**Verified:** a system Viewer invited as a scenario editor can create a system
inside that scenario, and is still refused (403) on Current Architecture. An
Administrator who is not a member can list the scenario but gets 403 on writing
to it. A non-member gets 404 on reading it, writing to it, and on the list.

---

## 5. Lifecycle

**Draft → Under review → Approved → Archived**, and back to Draft at any point.

Only **Draft** accepts changes. The other three are frozen — including for a
SuperAdmin, who has to return the scenario to Draft first, which is recorded in
the audit log. That is a deliberate reading of "should not allow changes": if an
Approved plan could be quietly edited by anyone, approval would not mean much.

**Deleting is recoverable.** It sets a deletion timestamp; nothing is dropped.
The scenario disappears from everyone's list immediately, the members are
notified, and the owner or a SuperAdmin can restore it with everything intact.

**Verified:** freezing rejects an edit with a clear 409 even for a SuperAdmin,
thawing restores it, and delete → restore round-trips with all contents present.

---

## 6. Audit and notifications

Work done inside a scenario is audited **inside that scenario**, so the live
audit log doesn't fill up with other people's planning.

Three things are written to the **live** log so an administrator can see what is
happening without opening each scenario: **creation, deletion, and membership
changes**. Cloning writes a single summary event ("copied 8 systems and 7 data
flows"), never one per record — the same rule the schema upload follows.

People are notified when they are invited to a scenario, when they are removed
from one, when a scenario they belong to is deleted, and when they are given
ownership.

---

## 6a. Using it

The sidebar lists **Current Architecture** with your scenarios beneath it, and
**+ Add Scenario** for Editors and above. Inside a scenario the working area is
tinted, a bar names the scenario and its status, and every catalog header shows
which view you are in with a picker to switch. **Manage scenario** on that bar
opens details, people and deletion, for the owner and SuperAdmins.

Hiding is done by selecting rows in the Systems or Data Flows table and using
the bar that appears above them; **Show hidden items** and **Un-hide
everything** are on the scenario bar.

---

## 7. What is not built yet

Being explicit, so nothing here is a surprise later.

- **No gap analysis.** Deferred by agreement until every data element is
  accounted for. Worth repeating what the current data would support: only 2 of
  your 7 data flows declare which elements they carry, and nothing links the
  3,304 schema columns to the 33 data elements. Both are data-entry gaps rather
  than code gaps.
- **No promotion to live.** A scenario cannot be applied to Current
  Architecture. Every scenario record keeps `origin_id` pointing at what it came
  from, so a future release can compare and promote without rework.
- **No drift warning.** If Current Architecture changes after you branch, the
  scenario is not told and does not re-sync.
- **Promoting a scenario into Current Architecture.** Deliberately out of scope
  for 0.3.0; `origin_id` is recorded so a later release can compare and apply.
- **Re-syncing after live changes.** A scenario does not notice, or absorb,
  changes made to Current Architecture after it was created.

---

## Seeing it work

Two ways, and the second is the one that proves anything.

**Read the rules above.** They are the design.

**Then watch them hold.** With the app running locally:

```bash
docker compose up
```

That command keeps running and streaming logs — that is the app working, not a
hang. Open a **second terminal tab** (Cmd + T) and run:

```bash
cd ~/Desktop/Desktop\ Folder/Ben\'s\ Folder/Claude\ Cowork/data-lineage-tracker
python3 tools/scenario_walkthrough.py --email you@example.gov
```

(Or start the app detached with `docker compose up -d` and keep one terminal.)

It creates a scenario from Current Architecture, tries the things that should be
refused, and prints what happened in plain language — including a before/after
count of the live diagram, so you can see for yourself that nothing leaked. It
cleans up after itself, and it never writes to Current Architecture beyond one
system it creates and deletes.
