# Installing the Data Lineage Tracker

This is the whole job, from nothing to a working deployment your staff can
sign in to. It assumes you can run a container somewhere and create a
PostgreSQL database, and nothing else.

If you only want to **look at it first**, skip to [Trying it on your own
machine](#trying-it-on-your-own-machine). That takes two commands and no
decisions.

---

## What you need

| | |
|---|---|
| **A container host** | Anything that runs a Docker image and gives it a port. Render, AWS App Runner or ECS, Azure App Service or Container Apps, Google Cloud Run, or your own VM with Docker. |
| **PostgreSQL** | Developed and run against **18**. Nothing in the schema is known to require it, but 18 is what is tested, and the restore procedure assumes the dump and the server are the same major version. |
| **A hostname with TLS** | See [Choosing a hostname](#choosing-a-hostname-this-matters-more-than-it-sounds) — there is a trap here. |
| **Somewhere to send mail** *(optional)* | Needed for self-service password reset, and for two-factor codes sent by email if you allow those. Your own relay, Amazon SES, Azure Communication Services or Resend. |
| **An S3-compatible bucket** *(optional)* | For the nightly backup job. See `backup/README.md`. |

The application itself is Python and Tornado with a single-file React
frontend. There is no build step, no bundler and no Node in the runtime
image — the page is compiled in the browser.

---

## Trying it on your own machine

```bash
git clone https://github.com/bsilberglitt-lgm/data-lineage-tracker.git
cd data-lineage-tracker
docker compose up
```

Then open **http://localhost:8765**.

Sign in as `admin@example.gov`. The password is generated on first run and
printed **once** in the output — look for it in the `docker compose up` log.
You will be made to change it immediately.

What you are looking at is **invented example data**: a small fictional
district with eight systems, the flows between them, some data elements and
one mapped database schema. None of it is real. It exists so there is
something to look at before you have entered anything. Delete it as you go,
like any other record; it is only ever created into a database with no systems
in it, so once it is gone it stays gone.

To start over completely:

```bash
docker compose down -v   # deletes the local database
docker compose up
```

---

## Choosing a hostname (this matters more than it sounds)

**Run it under a hostname on a domain your organization controls.** Not the
hosting platform's own subdomain.

This is not about appearances. Institutional DNS filtering — Cisco Umbrella and
its equivalents — categorizes hostnames, and a platform subdomain like
`your-app-7fa6.onrender.com` can be classified under **"Newly Seen Domains"**
and blocked outright for everyone on the network. That happened to this
project: staff on agency equipment got a TLS error naming the filtering
service, not the application, and nothing about the deployment was wrong.

Two further things follow from the same decision:

- **Password reset mail should come from the same domain the application is
  served from.** Mail from one domain carrying a link to another is the pattern
  that reads as phishing to a spam filter and to a careful reader.
- **A brand-new domain is itself newly seen.** If you register one for this,
  register it well before you point staff at it, and expect to ask your network
  team for an allowlist entry if it is flagged. One request naming your
  permanent address is a much easier conversation than one about a platform
  subdomain.

A subdomain of your existing organizational domain avoids all of this, because
the parent domain is already established and trusted.

---

## Configuration

Everything comes from **environment variables**. Nothing is baked into the
image, so the same image runs locally, on Render, and on AWS or Azure.

### Required

| Variable | What it is |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string, e.g. `postgresql://user:pass@host:5432/dbname` |

### First run only

| Variable | What it is |
|---|---|
| `BOOTSTRAP_SUPERADMIN_EMAIL` | Creates the first SuperAdmin. **Only acts when no accounts exist at all**, so it is safe to leave set. |
| `BOOTSTRAP_SUPERADMIN_PASSWORD` | Optional. If omitted, a strong password is generated and printed once to the log. Either way the account must change it at first sign-in. |
| `BOOTSTRAP_SUPERADMIN_FIRST_NAME` | Optional. |
| `BOOTSTRAP_SUPERADMIN_LAST_NAME` | Optional. |

**If you do not set the email**, the first start prints a line saying so and
serves an application nobody can sign in to. Set it and restart.

### Two-factor authentication

| Variable | What it is |
|---|---|
| `TWOFA_SECRET_KEY` | Required before anybody can enroll an authenticator app. At least 32 characters. |

Generate one and keep it with your other secrets:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Read this before you set it.** Every user's TOTP secret is *derived* from
this key rather than stored, so a stolen database or backup contains no secret
at all — there is nothing in it to decrypt. The consequence is the other side
of the same coin:

- **Losing this key voids every enrollment.** Everyone has to set up their
  authenticator app again.
- **Changing it does the same.** Treat it as permanent, and back it up
  wherever you keep things whose loss costs you a day.
- It never needs rotating on a schedule: it is not a credential anybody
  authenticates with, and it is useless without the database.

Until it is set, enrollment refuses and says so, and two-factor authentication
cannot be made mandatory. The `_FILE` convention below is not available for
this one: it is read at process start rather than per use.

### Mail credentials

Only one of these, matching the transport you choose in Preferences. Everything
else about mail is configured in the application itself — see [Email](#email).

| Variable | What it is |
|---|---|
| `SMTP_PASSWORD` | The relay password, for the SMTP transport. |
| `SMTP_PASSWORD_FILE` | Or the **path of a file** holding it. |
| `RESEND_API_KEY` | The API key, for the Resend transport. |
| `RESEND_API_KEY_FILE` | Or the path of a file holding it. |

**Prefer the `_FILE` form.** AWS ECS task secrets, Azure Key Vault through the
CSI driver, and Docker secrets all deliver a secret to a container as a mounted
file. Using it means the credential stays in your secret store rather than
being copied into deployment configuration — and because the file is read for
every message, rotating the secret takes effect without a restart.

### Optional tuning

| Variable | Default | What it is |
|---|---|---|
| `PORT` | `8765` | Port to listen on. Most platforms set this for you. |
| `DB_POOL_MAX` | `5` | Maximum database connections. Raise for more concurrent users; a small instance does not need more. |
| `SCRYPT_N` | `32768` | Password hashing cost. Raise on a larger instance. **Changing it does not invalidate existing passwords** — each hash records the parameters it was made with. |

### Recovery — when nobody can sign in

Set **all three** together:

| Variable | What it is |
|---|---|
| `BOOTSTRAP_SUPERADMIN_RESET` | `true` |
| `BOOTSTRAP_SUPERADMIN_EMAIL` | The account to reset |
| `BOOTSTRAP_SUPERADMIN_PASSWORD` | The password to set — you supply it; none is generated |

On the next start that account's password is set and it is forced to change it
at the next sign-in. Every use is written to the audit log, and only somebody
who can already change the deployment's environment can trigger it.

**Remove `BOOTSTRAP_SUPERADMIN_RESET` afterward**, or a later restart silently
resets the password again — which, if that password has since been changed from
inside the application, means an unplanned restart quietly reverting it.

Once email is configured, ordinary self-service reset covers almost every case
this is for.

### Do not set these on a real deployment

You will find all three in `docker-compose.yml`, because that file is the local
development setup. They are listed here so that nobody discovers them there
and carries them across.

| Variable | Why not |
|---|---|
| `BOOTSTRAP_SUPERADMIN_SKIP_PASSWORD_CHANGE` | Lets the first SuperAdmin keep the password you set in the environment instead of being made to change it. Convenient locally, where the database is recreated often. On a real deployment it means **a live administrator account whose password is sitting in your deployment configuration**, readable by anyone with access to it and unchanged by anybody signing in. |
| `EMAIL_SINK_PATH` | Writes mail to a file and reports success. Password reset would appear to work and deliver nothing — and the symptom arrives weeks later, as users who never got their mail and an administrator with no reason to doubt the settings they can see. |
| `EMAIL_SINK_CONFIRM` | Its companion. The application **refuses to start** if the path is set without it, which is deliberate: one variable copied from a test environment is a mistake and crashes, and two together are a decision. |

---

## Deploying

The image is a plain container with no platform-specific anything in it.

```bash
docker build -t data-lineage-tracker .
docker run -p 8765:8765 \
  -e DATABASE_URL='postgresql://…' \
  -e BOOTSTRAP_SUPERADMIN_EMAIL='you@example.gov' \
  data-lineage-tracker
```

On a managed platform, point it at this repository or at the built image, set
the environment variables above, and give it a port. Nothing else is needed.

**What happens on every start**, in order, so you know what the logs are
telling you:

1. **Database migrations run** — `alembic upgrade head`. This is automatic and
   idempotent. You never run migrations by hand, and you never need to create
   tables before the first start.
2. **The example district is seeded**, but only into a database with no systems
   in it.
3. Default preferences, release notes, field definitions and task types are
   ensured — each additive, each safe to re-run.
4. **The first SuperAdmin is created**, if no accounts exist.
5. The server binds its port.

Because migrations run at startup, **a deploy and a schema upgrade are the same
event.** Take a backup before deploying a release that includes a migration.

### Behind a reverse proxy

The application serves plain HTTP and expects TLS to be terminated in front of
it, which is what every managed platform does. Two things to get right:

- **Set the public address in the application**, under Preferences → Email →
  *Public address of this app*. Password-reset links are built from that
  setting and **never** from the incoming request's `Host` header — reading the
  header is the standard way reset links get poisoned. A stale value here means
  working links pointing at the wrong hostname.
- **Do not strip the path.** The application serves one non-root page,
  `/reset-password`, and the API under `/api/`.

---

## Email

Optional, and used for two things:

- **self-service password reset** — without it, a forgotten password means an
  administrator issuing a temporary one, which is workable for a handful of
  people and the first thing a larger department will hit;
- **two-factor codes sent by email**, if a SuperAdmin allows them. That is off
  until switched on, and an authenticator app needs no mail at all.

Neither is offered while mail is not working, rather than offered and failing.
`Preferences → Email status` says which of the conditions for the reset link is
missing.

**Configured in two places on purpose.** The host, port, encryption, username,
from address and public address are settings a SuperAdmin edits in
**Preferences → Email**, so setting mail up does not require access to the
server. Only the password or API key comes from the environment, because a
credential in the database would travel in every backup.

### Choosing a transport

| Option | When it fits |
|---|---|
| **Your own relay (SMTP)** | Your organization runs one. Often needs no username at all, because it authorizes servers by address — set encryption to match and leave the username blank. |
| **Amazon SES** | You are on AWS. An ordinary SMTP interface with its own credentials. |
| **Azure Communication Services** | You are on Azure. SMTP at `smtp.azurecomm.net:587`, where the username encodes the resource, application and tenant, and the password is an Entra client secret. |
| **Resend** | No relay available. Speaks both SMTP and an HTTP API; SMTP is the simpler path. |

**Do not use Microsoft 365 as the relay.** Microsoft began disabling
password-based sign-in for sending through Exchange Online in December 2026,
and this application authenticates with a password only. A setup pointing at
`smtp.office365.com` will stop working. If your organization runs M365, send
through your own relay, SES, or ACS instead — all three are ordinary SMTP as
far as this application is concerned.

### Turning it on

The order matters, because the reset link stays hidden until mail genuinely
works — it will not offer something that quietly fails.

1. Verify a sending domain with whoever will carry the mail, and add the SPF,
   DKIM and DMARC records they give you. Use the **same domain** the
   application is served from.
2. Set the credential variable in the environment and restart.
3. Fill in **Preferences → Email**, including the public address.
4. Press **Send test message**. It goes to your own address and reports exactly
   what the mail server said. Nothing else will tell you this: a reset request
   deliberately reveals nothing about whether it worked, so a wrong port is
   invisible from the sign-in screen.
5. Only once a test message has actually arrived, set **Allow self-service
   password reset** to On.
6. Tell your staff which address reset mail comes from, before anyone needs it.

---

## Backups

**Nothing is backed up unless you set it up.** The application does not do it
for you, and no part of this software will warn you that you have not.

`backup/` holds a container that dumps the database nightly to S3-compatible
storage, with daily, monthly and annual retention. `backup/README.md` has the
setup and — more importantly — the restore procedure.

Two properties of that job are deliberate and worth preserving if you adapt it:

- **Retention is done by storage lifecycle rules, not by the script**, and the
  credential it uses can write and nothing else. A credential that cannot
  delete is one that ransomware cannot use to destroy your backup history.
- **A dump smaller than 10 KB is refused.** `pg_dump | gzip` reports *gzip's*
  exit status, so an unreachable database produces a valid empty archive and a
  script that believes it succeeded. Without that check the job would upload a
  stub every night and look healthy until the day you needed it.

**Test a restore before you need one.** A backup nobody has restored is a
hypothesis.

---

## Upgrading

```bash
git pull
# rebuild and redeploy however your platform does it
```

Migrations run on the next start. Take a backup first if the release includes
one; `alembic/versions/` tells you.

Release notes appear in the application itself — the version in the corner of
every page is a link.

---

## Verifying a deployment

```bash
python3 selftest.py https://your-hostname --email you@example.gov
```

It prompts for the password, exercises authentication, role enforcement, the
governance inheritance chain and the audit trail, and creates then deactivates
a temporary account to prove roles hold. Expect all checks to pass.

It **cannot** catch a rendering fault — click through the application after any
upgrade. `RELEASE_CHECKLIST.md` is the procedure this project uses and is worth
reading even if you do not adopt it wholesale.

---

## Where to go next

- **`SECURITY.md`** — the security model, and how to report a vulnerability.
- **`CONTRIBUTING.md`** — if you intend to change the code.
- **`SCENARIOS.md`** — the design of the scenario-planning feature.
- **`backup/README.md`** — backup setup and restore.
- **The help panel in the application** — written for the people who will use
  it rather than the people who run it.
