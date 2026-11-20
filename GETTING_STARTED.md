# Getting started

Your first hour with the Data Lineage Tracker, written for the person who has
just been given an account — or who has just stood it up and is looking at a
diagram of somebody else's school district.

If you are deploying it, read [`INSTALL.md`](INSTALL.md) first. If you are
changing the code, [`CONTRIBUTING.md`](CONTRIBUTING.md).

**There is a help panel inside the application**, on every screen, and it is
more detailed than this document. This is the orientation; that is the manual.

---

## What this is for

Most organizations can answer "what systems do we have?" and "who runs them?"
What they usually cannot answer quickly is the question that actually comes up:

> This field is wrong in a public report. Where did it come from, what touched
> it on the way, and who do I talk to?

This tool holds the answer as a picture plus the records behind it. Four things
and the relationships between them:

- **Systems** — the places data lives. A district's student information system,
  an integration platform, a data warehouse, a reporting portal.
- **Data flows** — the arrows between systems. What moves, how often, by what
  method, whether it carries personal information, and what happens to it on
  the way.
- **Data elements** — the fields themselves, defined once, so "enrollment date"
  means the same thing in every conversation about it.
- **Contributors** — the people accountable. Data Owners, Data Stewards, system
  administrators.

Then **tasks**, which are the work a flow depends on: the validation steps
before a load is accepted, the stored procedure somebody runs, the file a
person moves by hand on the first Monday of the month. Those are where timing
slips, and the diagram shows them as circles on the thing they belong to.

**What it is not**: it does not hold your actual data, it does not connect to
your databases, and it does not discover anything automatically. It is a record
of what your organization knows, kept deliberately.

---

## Step 1: the district on your screen is fictional

A brand new installation seeds **an invented school district** so the diagram
opens on something legible rather than on an empty canvas: eight systems laid
out from collection through to public reporting, seven data flows, twelve data
elements, two contributors, and one mapped database schema to drill into.

Every name is a **role rather than a product** — "Statewide Integration
Platform", "Operational Data Store", "Federal Reporting Extracts" — precisely so
you cannot mistake it for real. The contributors are job titles, not people,
with no email addresses.

**Spend ten minutes clicking through it before you delete it.** It is shaped
like a real deployment on purpose, and it is the fastest way to see what the
tool does with a filled-in record.

### Then clear it out

When you are ready to put your own architecture in, delete the seed. **Order
matters**: a system that any flow points at cannot be deleted, and the
application will tell you so rather than quietly cascading. So:

1. **Data Flows Catalog** — delete the seven flows.
2. **Systems Catalog** — delete the eight systems. This takes each system's
   mapped schema tables and columns with it.
3. **Data Elements Glossary** — delete the twelve elements.
4. **Contributors** — delete the two. The seed contributors are not linked to
   any account, so this is allowed; a contributor who **is** linked to a user
   account is refused until you unlink it.

For your own people later, prefer **deactivating** over deleting. The audit
trail refers to contributors by identifier, so deleting somebody leaves history
pointing at a record that no longer exists — and deactivating never clears
their assignments, so a system that named them as its Data Steward goes on
naming them, shown as deactivated. The seed contributors are fine to delete
because they have no history to protect.

The seed is only written into a database that has **no systems at all**, so once
you have cleared it, it will not come back on the next restart.

If you would rather not delete anything, nothing breaks — but a diagram with
somebody else's systems on it is a diagram nobody trusts, and that distrust is
hard to undo later.

---

## Step 2: find out what you can do

Your **role** decides what you see, and it is worth knowing which you have
before you wonder why a button is missing. Your name is at the top right.

| Role | Can |
|---|---|
| **Viewer** | Read the diagram, the catalogs and the schema drill-down |
| **Editor** | Also create, change and delete records |
| **Administrator** | Also manage user accounts and roles |
| **SuperAdmin** | Also Preferences and the Audit Log |

Two things that follow:

- **Every role check is enforced on the server.** A hidden button is a
  convenience, not the boundary — so there is no harm in exploring.
- **Administrators cannot create or change SuperAdmins**, and the application
  refuses to remove or demote the **last** SuperAdmin, so a deployment cannot
  be left with nobody able to administer it.

---

## Step 3: build your own first flow

Resist the urge to enter everything. Pick **one** pipeline you already
understand well — ideally one that has caused you a problem recently — and
enter only that. You will learn more from one flow entered properly than from
thirty entered hurriedly, and you will discover what your organization does not
actually know, which is usually the valuable part.

A workable first hour:

1. **Add the two systems** at either end. Name, type, who manages it, status.
   Leave the fields you do not know empty — an empty field is honest, and a
   guess is worse than a blank.
2. **Add the flow between them.** Source, destination, frequency, method,
   format. The name fills itself in from the two systems until you type your
   own.
3. **Say whether it carries personal information.** This is the field people
   come back for.
4. **Add the one or two people** who are accountable, and attach them.
5. **Add a task** for any step a person has to perform. If somebody has to be at
   their desk for this pipeline to work, that is worth recording — it is
   exactly where timing slips, and the diagram will show it.
6. **Drag the boxes** until the picture reads left to right. Alignment guides
   appear as you drag; hold Shift to switch them off.

Then stop and look at it. A diagram of one pipeline that is correct is
something you can show somebody.

---

## Step 4: the two features people miss

Both of these are the reason to use this rather than a drawing tool, and both
are easy to go months without finding.

### Scenarios

A **scenario** is a private working copy of the whole picture. You can retire a
system in it, add three that do not exist yet, repoint flows, and compare the
result against what you run today — without touching the live record, and
without anybody else seeing a half-finished plan.

Changes inside one are marked: **proposed** for something you invented there,
**modified** for something you changed, **retired** for something you plan to
switch off, drawn struck through rather than simply vanishing, so the plan reads
as "we retire these, we add those" rather than as a gap.

This is what the tool is for during a migration. Read the two scenario articles
in the help panel before your first one.

### Governance inheritance

A data element can take its Data Owner and Data Steward from the system or flow
that carries it, rather than having them entered again on every record. So when
accountability changes, it changes in one place. A record can always override
what it inherits, and the inherited value is shown as inherited rather than
copied — so it cannot quietly go stale.

---

## Step 5: make it yours

For a SuperAdmin, in **Preferences**:

- **Add your logo**, in one of four positions.
- **Switch off the fields you do not use.** Per record type, and it never
  deletes anything — the stored value survives, so this is a safe setting to
  get wrong.
- **Write definitions for your fields**, so what "Managed By" means in your
  organization is explained where somebody is filling it in. A definition can
  be written once and shared everywhere that field appears, or kept different
  per record type.
- **Configure email**, if you want self-service password reset. It is off until
  both the setting and a working mail transport exist, because a reset link that
  promises an email and delivers nothing is worse than no link.
- **System types and their colors** are configured here too. The palette has
  been measured for contrast; prefer it to arbitrary colors.

---

## Where to go next

**The help panel, on every screen.** It carries articles on the diagram, tasks,
finding and editing records, governance and inheritance, database schemas,
scenario planning (basics and a deep dive), accounts and roles and the audit
log, and each of the Preferences features above.

A few habits worth forming early:

- **An empty field beats a guess.** This becomes a reference document, and a
  plausible wrong answer in it costs more than a gap.
- **Record the manual steps.** They are invisible everywhere else, and they are
  what breaks.
- **Use a scenario for anything speculative.** The live picture should always be
  what you actually run.
- **Nothing you do is untraceable.** Every change is written to the audit log
  with the old and new values, and a change can be reverted from there. The log
  is append-only: reverting writes a *new* entry rather than removing the old
  one, so the record of what happened is never edited.
- **User accounts are deactivated, never deleted** — and deactivating one
  revokes its sessions immediately, not at next sign-in.
