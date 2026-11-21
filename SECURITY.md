# Security

This document is for two readers: somebody deciding whether their organization
can run this, and somebody who has found a problem in it.

It describes what the software actually does, including where it stops. A
security document that only lists strengths is not useful to either reader, so
the gaps are in here too, under [What this does not
do](#what-this-does-not-do).

---

## Reporting a vulnerability

Use **"Report a vulnerability"** on this repository's
[Security tab](../../security/advisories/new). That opens a private advisory
visible only to you and the maintainer — not a public issue, and not email.

**Please do not open a normal issue for a security problem.** If you have no
GitHub account and cannot create one, open an issue saying only that you have
a security report and asking how to send it; leave the details out of it.

This is a small project with one maintainer, so set your expectations
accordingly: you will get an acknowledgement, and a fix if the report is
actionable, but there is no response-time commitment behind it and no bounty.

If you are an adopting organization and you find something, telling the
maintainer helps every other adopter — including the state agency this was
built for.

---

## What the data is

Worth stating plainly, because it changes how much of the rest matters: **this
application holds metadata, not records about people.** It describes which
systems hold data, how data moves between them, what the fields mean, and who
is responsible — names and work contact details of staff, not student or
citizen records.

So a breach here does not expose personal data about the public. What it does
expose is **a map of where that data lives**, which is useful to somebody
planning an attack on the systems it describes. Treat it as sensitive internal
architecture documentation rather than as a system of record.

Two things held in the database are more sensitive than that description
suggests. **Uploaded documents**: the Resources feature accepts arbitrary
files, and nothing stops somebody attaching a spreadsheet of real data. And
**uploaded schema scripts**, which are a precise description of a real
database's structure. Both are covered under [Uploads](#uploads).

---

## Authentication

**Passwords** are hashed with **scrypt** (`n=32768, r=8, p=1` by default),
each with its own 16-byte random salt. The stored form is
`scrypt$n$r$p$salt$hash`, so **each hash records the parameters it was made
with** — raising `SCRYPT_N` on a bigger instance strengthens new passwords
without invalidating existing ones. Verification is a constant-time
comparison.

**The password policy** is configurable and defaults to 12 characters with an
uppercase letter, a lowercase letter, a number and a symbol. It is enforced
server-side on every path that sets a password, including reset.

**Sessions** are random 32-byte tokens. **Only a SHA-256 hash of the token is
stored**, so a reader of the database — or of a backup — cannot use what they
find to sign in. The cookie is `HttpOnly`, `SameSite=Strict`, and `Secure`
whenever the request arrived over HTTPS (read from `X-Forwarded-Proto`, so it
works behind a terminating proxy).

Sessions expire two ways: **8 hours idle** by default, configurable, and **30
days absolute** regardless of activity.

**Brute force** is handled in two stages: a progressive delay after 3 failed
attempts, then a lockout after **10 failures in 15 minutes**, counted per
email address. A lockout answers **429** with a clear message rather than
pretending the password was wrong.

**CSRF** uses a per-session token required in an `X-CSRF-Token` header on
every unsafe method. The header is the mechanism rather than a form field
because a browser will not attach a custom header cross-origin.

---

## Authorization

Four roles — Viewer, Editor, Administrator, SuperAdmin — and **every role
check is server-side, on every request.** Hiding a control in the interface is
a convenience, not a boundary, and the code says so in those words.

Three properties worth knowing:

- **Administrators cannot create or modify SuperAdmins.** The privilege ladder
  does not let you climb it.
- **The system refuses to remove or demote the last SuperAdmin**, so a
  deployment cannot be left with nobody able to administer it.
- **A refused action is written to the audit log**, so attempts are visible
  rather than merely blocked.

Inside a **scenario** — a private working copy of the diagram — authority comes
from scenario membership rather than the system role, which is what allows a
Viewer to be invited as an editor of one plan. That is a deliberate move of the
check rather than a skipped check: every scenario write calls an access check
first, and a non-member gets a 404 rather than a 403, so the existence of
somebody else's scenario is not disclosed.

---

## The audit trail

**Append-only.** Nothing in the application edits or deletes an audit row;
reverting a change writes a *new* row describing the reversal. It records
creates, updates and deletes field by field with old and new values, plus
sign-ins, failed sign-ins and refused actions.

Bulk operations write **one summary row** rather than one per record — a schema
upload touches thousands of fields, and auditing each would bury real activity.

**Retention is configurable and defaults to keeping everything**, and changing
the setting does not delete anything on its own.

---

## Secrets

**No credential is stored in the database.** Mail passwords and API keys come
from the environment — either a variable or, preferably, the path of a file
holding one.

The reason is backups: a credential in the database travels in every retained
nightly snapshot, and encrypting it there would need a key that must itself
come from the environment, so it would move the secret rather than protect it.

The application reports whether a credential was **found** and the **name** of
the variable, never the value. A test in the suite asserts the mail password
appears in no API response.

**Nothing logs a message body.** A password-reset email contains a working
token, so a log line carrying one is a credential at rest in whatever
aggregates the logs.

---

## Password reset

Five decisions here are security rather than polish:

- **Reset tokens are stored as a SHA-256 hash**, single-use, and expire after
  an hour. The expiry is written at creation, so changing the setting later
  cannot extend tokens already issued.
- **Spending a token invalidates every other outstanding token** for that
  account, and the reset **revokes every session** — nobody is signed in
  during a reset, so an unrevoked session would be somebody else's.
- **The link is built from a configured public address, never from the
  request's `Host` header.** Reading the header is the standard way reset links
  get poisoned: an attacker requests a reset for somebody else's address with a
  `Host` of their choosing, and the victim receives a genuine token pointing at
  the attacker's server.
- **The token travels in the URL fragment**, not the query string. A fragment
  is never transmitted to the server, so it cannot appear in the access logs of
  a proxy, load balancer or CDN — where it would otherwise sit as a live
  credential readable by anyone with log access.
- **The response never reveals whether an address has an account.** Same status
  and same body for an address with an account, one without, and a deactivated
  one. An endpoint that distinguished them would be a free list of who works at
  the organization, which for a government agency is a phishing target list.
  It is also why a *send failure* is not reported to the caller.

Rate limiting is **per account rather than per IP**, because the thing being
prevented is somebody's inbox being flooded and an attacker doing that will not
keep the same address. The HTTP response does not change when the limit bites;
only the mail stops.

---

## Uploads

There are **three upload paths with different defenses**, and the differences
matter.

### The branding logo: identified, and narrowly restricted

**Identified by its magic bytes**, never by file extension or by the browser's
declared content type. The type that inspection returns is what gets stored and
what the endpoint later serves, so the two cannot disagree.

**SVG is not accepted here**, because an SVG can carry script and the logo is
rendered as part of the application's own page.

It has its own limits on both bytes **and** pixel dimensions, because a byte
cap alone does not stop a decompression bomb.

### Resource documents: arbitrary files, contained rather than filtered

The Resources feature accepts **any file**, because its job is to let people
attach the memo, spreadsheet or diagram that explains a system. Its content
type comes from the browser and is therefore attacker-controlled. Three things
contain that:

- **Almost everything is served as an attachment.** Only a narrow list of types
  is ever rendered inline; anything else gets
  `Content-Disposition: attachment`, so an uploaded HTML or script file cannot
  execute in this site's context.
- **Every response carries a restrictive Content Security Policy** —
  `default-src 'none'; style-src 'unsafe-inline'`. This is what makes the
  inline cases safe: `script-src` falls back to `default-src`, so **script
  inside an inline-rendered SVG does not run**, and nothing can be fetched from
  anywhere. The `style-src` exception exists so an SVG's own CSS still renders.
- **`X-Content-Type-Options: nosniff`** stops a browser from deciding for
  itself that a file is something more interesting than its declared type, and
  the filename is stripped to `[A-Za-z0-9._-]` before it reaches a header.

So SVG *is* accepted and rendered inline for documents, and is safe because of
containment rather than exclusion. An earlier draft of this document said SVG
was refused everywhere, which was true of the logo and wrong here.

### Schema scripts: parsed as text, and never served back

A data store's schema is refreshed by uploading a SQL script. Three properties:

- **Nothing executes it.** The file is decoded as text and read with regular
  expressions to find `CREATE TABLE` blocks and foreign keys. There is no
  database connection to the system being described, no driver, and no
  evaluation of the script's contents — so a hostile `.sql` file is a parse
  failure, not a command.
- **The uploaded script is stored and is never returned.** The bytes are kept
  in a snapshot so an upload can be reverted, but **no endpoint serves them**.
  The snapshot listing returns the filename, the table and column counts and a
  summary; the script itself goes in and does not come out.
- **The permission check runs before the parse**, deliberately, so that the
  time a request takes cannot tell a non-member whether a system exists.

Uploading requires the Editor role, and the same 10 MB cap applies.

**This path deserves its own thought in a review**, because a schema script is
a precise description of a real production database — table and column names,
keys, relationships. It is the most sensitive single thing the application
accepts. It is held in your database and therefore carried in your backups,
and it is readable by anyone with database access. If that is not acceptable
in your environment, enter schemas by hand rather than uploading the scripts.

### What the software cannot decide for you

Nothing stops somebody attaching a spreadsheet of real student data to a system
record. The file is then held in your database and carried in your backups.
That is a governance question for the deploying organization.

---

## Accounts are deactivated, not deleted

Deactivating revokes every session **immediately** — not at next sign-in — and
`load_session` refuses an inactive user on every subsequent request.

Deletion is not offered, because the audit trail refers to people by
identifier: deleting an account would leave history pointing at a record that
no longer exists.

---

## What leaves the deployment

Worth being specific, because this is the question a security review asks and
the answer is short.

| Destination | When | Carrying |
|---|---|---|
| Your mail transport | A password reset, or an administrator pressing "Send test message" | The recipient address and a reset link |
| `unpkg.com` | Every page load | Nothing. React, ReactDOM and Babel are fetched by the browser from a CDN, pinned by version and verified by hash |
| Your backup storage | Nightly, if you set the backup job up | A full database dump |

**That is the complete list.** The application makes no other outbound calls,
reports no telemetry, and contacts no analytics or error-reporting service.
`mailer.py` is standard-library only, so adopting the mail feature adds no
dependency to audit.

The `unpkg.com` row is the one a review asks about, because three script tags
fetched from a public CDN on every page load would otherwise mean **that CDN
can serve arbitrary JavaScript into your users' browsers.**

Two things close that, and it takes both. The **versions are pinned**, which
stops a surprise upgrade. And each tag carries a **Subresource Integrity**
hash, so the browser verifies the bytes it received against a `sha384` digest
recorded in the page and refuses to execute anything else. npm package versions
are immutable, so after those hashes were taken, any change to those files is
an attack — and it fails closed rather than running.

Be precise about what that does and does not buy. It is trust-on-first-use: the
hashes attest to what the CDN served when they were computed, so a CDN already
compromised at that moment would have been pinned in that state. What it
prevents is a *future* substitution, which is the realistic threat for a
long-lived deployment. CI re-checks the published hashes against what the CDN
currently serves, so a divergence becomes a failed build rather than something
a user discovers.

If your review will not accept a CDN dependency at all, the alternative is to
vendor the three files into `static/` and serve them yourself. Nothing else in
the application depends on them being remote.

---

## What this does not do

Listed because a review will ask, and because finding out later is worse.

- **No two-factor authentication.** The preference exists and is switched off;
  TOTP is the intended implementation and is not built.
- **No single sign-on.** No OIDC or SAML. Everyone has a local password. For an
  organization with an identity provider this is the most likely objection, and
  it is on the roadmap.
- **No formal penetration test.** Nothing here has been reviewed by anyone
  outside the project.
- **Known accessibility gaps**, documented in `ACCESSIBILITY.md` — focus
  trapping in modals, and no testing with a real screen reader.
- **No encryption at rest beyond what your database and storage provide.**
  There is no application-layer encryption of fields.
- **No rate limiting on the API in general.** Sign-in and password reset are
  limited; ordinary reads and writes are not, and rely on authentication.
- **Uploaded documents and schema scripts are stored in the database**, not in
  object storage, so they are carried in every backup. Intentional for now; the
  file sizes make it cheap and the alternative needs a second credential with
  delete permission.

---

## If you are adapting this

Three properties are easy to undo by accident and are load-bearing:

1. **The backup credential can write and nothing else** — no read, no delete.
   A credential that cannot delete is one that ransomware cannot use to destroy
   your backup history. Do not "simplify" retention by having the script prune
   old files; use storage lifecycle rules.
2. **The audit log is append-only.** If you add a code path that updates or
   deletes an audit row, it stops being evidence.
3. **Role checks live on the server.** If you add an endpoint, it needs its own
   check; the interface hiding a button is not one.

`CONTRIBUTING.md` has the rest of the rules for changing the code.
