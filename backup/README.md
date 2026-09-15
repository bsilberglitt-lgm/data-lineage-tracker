# Off-site database backups

Nightly `pg_dump` of the production database, uploaded to Amazon S3 by a Render
Cron Job. Render's own point-in-time recovery covers the last 3 days and lives
in the same account as the thing it protects; this covers everything else.

**Retention:** 30 daily, 12 monthly, 3 years of annual snapshots.

| File | What it is |
|---|---|
| `backup.sh` | The job. Dumps, compresses, uploads. |
| `Dockerfile` | Its container — `pg_dump` and the AWS CLI, nothing else. |
| `iam-policy.json` | Permissions for the backup user. Write-only, one prefix. |
| `lifecycle.json` | The rules that delete old backups. |
| `test_backup.sh` | Exercises the script with no database, AWS, or Docker. |

## Two decisions worth knowing before you change anything

**The job cannot delete.** Its IAM user holds `s3:PutObject` and nothing else —
no delete, no read. Old backups are removed by S3 lifecycle rules instead. If
the Render environment is ever compromised, the credential found there can add
files to the bucket but cannot destroy the history, which is the first thing
ransomware attempts. Do not "simplify" this by having the script prune.

**A dump under 10 KB is treated as a failure.** `pg_dump | gzip` reports
*gzip's* exit status, so a database that is unreachable produces a valid, empty
archive and a script that thinks it succeeded. Without that check this job
would upload a stub every night and look perfectly healthy until the day
someone needed it.

The threshold sits in a wide gap: a failed dump is under 1 KB, while a
successful one carries the schema DDL even with no rows and is several KB at
minimum. Production dumps are around **2.7 MB**, so there is roughly 270x of
headroom. If that figure ever approaches 10 KB something has gone badly wrong
anyway.

---

## Part 1 — AWS setup (once)

These steps are in the AWS console unless a command is given. Region: pick one
and use it consistently; `us-east-1` is fine.

- [ ] **Create the bucket.** S3 → Create bucket. A name must be globally
  unique, so something like `maine-doe-lineage-backups`.

  - **Block all public access** — leave the default box ticked.
  - **Bucket Versioning: Enable.** Protects against a corrupted file being
    written over a good one.
  - **Default encryption: SSE-S3 (SSE-KMS also works, and costs a little.)**

- [ ] **Apply the lifecycle rules.** Edit `lifecycle.json`, remove the
  `_comment` key — the API rejects unknown fields — then:

  ```bash
  aws s3api put-bucket-lifecycle-configuration \
    --bucket YOUR-BUCKET \
    --lifecycle-configuration file://backup/lifecycle.json
  ```

  Or paste the three rules by hand under Management → Lifecycle rules.

  > Versioning and lifecycle interact in a way that surprises people: with
  > versioning on, `Expiration` makes the *current* version a delete marker
  > rather than freeing the storage. Add a
  > `NoncurrentVersionExpiration` of 30 days if you want old versions actually
  > cleared. At this data size it is pennies either way.

- [ ] **Create the IAM user.** IAM → Users → Create user, name it something
  like `lineage-backup-writer`. **Do not** give it console access.

  Attach `iam-policy.json` as an inline policy, with `YOUR-BUCKET` replaced.

- [ ] **Create an access key** for that user: Security credentials → Create
  access key → *Application running outside AWS*. Copy both halves now; the
  secret is shown once.

  These go straight into Render in the next part. Do not paste them into
  email, a document, or a chat window — including this one.

## Part 2 — The Render Cron Job

- [ ] **New → Cron Job**, pointed at this same repository.

- [ ] Settings:

  | Field | Value |
  |---|---|
  | Name | `lineage-nightly-backup` |
  | Region | **the same Render region as the database** |
  | Language / Runtime | Docker |
  | Root Directory | `backup` |
  | Schedule | `0 7 * * *` |
  | Compute Plan | the smallest available |

  **Set Language to Docker first.** The Dockerfile fields do not appear until
  you do — before that Render shows Build Command and Start Command instead,
  and it looks as though the options are missing.

  Setting **Root Directory** to `backup` is the short way: Render then finds
  `Dockerfile` there and uses that directory as the build context. The longer
  equivalent is Dockerfile Path `./backup/Dockerfile` **and** Docker Build
  Context Directory `./backup`, which may be hidden under *Advanced*. The
  context has to be `backup/`, because the Dockerfile says `COPY backup.sh ./`
  — point it at the repository root and the build fails saying it cannot find
  the script.

  **Region is Render's own region, not AWS's.** It must match the database, so
  the internal connection string resolves. Where the S3 bucket lives is
  unrelated and is set by `AWS_DEFAULT_REGION` below — a bucket in Ohio
  (`us-east-2`) works perfectly well with a job running in Virginia. Uploads
  into S3 are free from anywhere, so there is no reason to move a bucket to
  match.

  `0 7 * * *` is **07:00 UTC**, which is 2am Eastern in winter and 3am in
  summer — Render schedules in UTC and does not follow daylight saving. Pick
  the hour for the season you care about; nothing here is time-sensitive.

- [ ] **Environment variables:**

  | Key | Value |
  |---|---|
  | `DATABASE_URL` | The database's **Internal** connection string |
  | `AWS_ACCESS_KEY_ID` | From Part 1 |
  | `AWS_SECRET_ACCESS_KEY` | From Part 1 |
  | `AWS_DEFAULT_REGION` | The **bucket's** AWS region — `us-east-1` Virginia, `us-east-2` Ohio |
  | `BACKUP_BUCKET` | Bucket name only, no `s3://` |

  Use the **Internal** connection string, not the external one. It keeps the
  traffic inside Render's network and does not require external connections to
  be open.

- [ ] **Run it once by hand** — "Trigger Run" — and read the log. Expect:

  ```
  Dumping database
  Dump complete: 2.7 MB
  Uploading to daily/
  Backup finished
  ```

  **2.7 MB was the real figure on the first production run** (September 2026).
  Measured against the restored copy, the size is almost entirely **uploaded
  documents**: `resource_version` alone is 3.2 MB uncompressed, against 808 KB
  for the schema catalog and 560 KB for the whole audit trail. Documents are
  already-compressed formats sitting in binary columns, so gzip barely touches
  them.

  That means **the dump grows with attachments, not with usage** — and each one
  is carried in all 45 retained copies. Expect it to creep up, not down; a
  sharp **drop** is the thing worth investigating.

- [ ] **Confirm the object is really there**, with the size you expect:

  ```bash
  aws s3 ls s3://YOUR-BUCKET/data-lineage-tracker/daily/ --human-readable
  ```

- [ ] **Do Part 3 now, not later.** An untested backup is a file, not a backup.

## Part 3 — Prove a restore works

Do this when you set the job up, and once a quarter afterwards. It takes about
ten minutes and is the only thing that turns "we have backups" into a fact.

- [ ] **Download the newest backup.** Easiest from the browser: S3 console →
  your bucket → `data-lineage-tracker/daily/` → tick the newest object →
  **Download**. Then:

  ```bash
  mv ~/Downloads/data-lineage-tracker-*.dump.gz .
  gunzip data-lineage-tracker-*.dump.gz
  ```

  Or with the AWS CLI, if it is configured on this machine:

  ```bash
  aws s3 cp s3://YOUR-BUCKET/data-lineage-tracker/daily/ . --recursive \
    --exclude "*" --include "$(date -u +%Y-%m-%d)*"
  gunzip data-lineage-tracker-*.dump.gz
  ```

  > **If `aws s3 cp` hangs with no output**, the CLI has no credentials and is
  > waiting on the EC2 instance-metadata endpoint looking for a role that does
  > not exist on a laptop. `aws sts get-caller-identity` confirms it in a
  > second. Use the browser instead, or run `aws configure` with a key for
  > **your own** IAM user — the backup writer's key holds `PutObject` only and
  > cannot read the backups back.

- [ ] Restore it into a scratch database beside your local one:

  ```bash
  docker compose up -d db
  docker compose exec db createdb -U lineage restore_test
  docker compose exec -T db pg_restore -U lineage -d restore_test --no-owner \
    < data-lineage-tracker-*.dump
  ```

  `pg_restore` printing warnings about roles or extensions is normal. Errors
  about *relations* are not.

- [ ] Check the data arrived — not just that the command exited 0:

  ```bash
  docker compose exec db psql -U lineage -d restore_test -c \
    "SELECT (SELECT COUNT(*) FROM system)      AS systems,
            (SELECT COUNT(*) FROM data_flow)   AS flows,
            (SELECT COUNT(*) FROM app_user)    AS users,
            (SELECT COUNT(*) FROM audit_event) AS audit_rows;"
  ```

  Compare against production. A restore that produces an empty schema exits
  successfully and looks fine until you count rows.

- [ ] **Point the app at it** and sign in. This is the step that catches a
  backup which restores but does not *work* — a missing extension, say.

  ```bash
  docker compose run --rm -e DATABASE_URL=postgresql://lineage:lineage@db:5432/restore_test \
    -p 8766:8765 app
  ```

  Open <http://localhost:8766>, sign in, open the diagram.

- [ ] Clean up:

  ```bash
  docker compose exec db dropdb -U lineage restore_test
  rm -f data-lineage-tracker-*.dump
  ```

  Delete the downloaded dump. It is a full copy of production, including every
  password hash, sitting in your Downloads folder.

## When it breaks

**`pg_dump: error: aborting because of server version mismatch`**

`pg_dump` refuses to dump from a server newer than itself. The message gives
both versions. Fix by bumping `postgresql<N>-client` in the `Dockerfile` — and
the Alpine base along with it, since each Alpine release only carries certain
client majors. Check first at
<https://pkgs.alpinelinux.org/packages?name=postgresql18-client>.

This will happen again whenever Render upgrades the database, and it will
happen silently at 3am rather than when you upgrade. The size guard is what
turns it into a failed run you can see instead of a stub quietly uploaded.

**`AccessDenied` on the upload**

The bucket name in the IAM policy does not match `BACKUP_BUCKET`, or the
policy's `Resource` is missing the `/data-lineage-tracker/*` suffix. Both
`Resource` lines need the real bucket name.

**`could not translate host name`**

The external database URL was used instead of the internal one, or the cron job
is in a different **Render** region from the database. Render's region and the
bucket's AWS region are unrelated — see Part 2.

**The job stops running and nobody notices**

There is no alerting yet. Set `HEALTHCHECK_URL` to a dead-man's-switch service
if you want one, or check the bucket during the quarterly restore drill.

## Known follow-ups

- **The bucket is in a personal AWS account.** Fine for proving the pipeline,
  but production backups of a state system should live in a Maine DOE account:
  access needs to survive you leaving, and an auditor will ask. Moving later
  means creating the bucket and user again and changing four environment
  variables — the script does not care whose account it is.

- **Nothing alerts if the job stops running.** A backup job failing is silent
  by design; you find out when you need it. `backup.sh` supports an optional
  `HEALTHCHECK_URL` for a dead-man's-switch service (healthchecks.io has a free
  tier): it pings on success, and the service alerts when a ping does not
  arrive.

- **Uploaded files are not in these dumps in a useful form.** Documents,
  schema `.sql` files and the logo are binary columns, so they *are* included —
  but that also means the dump grows with every upload. This is the storage
  that CLAUDE.md earmarks for S3; now that a bucket exists, moving it is worth
  doing, and would shrink these backups considerably.

## Testing the script

```bash
sh backup/test_backup.sh
```

Runs against stand-ins for `pg_dump`, `aws` and `gzip`, so it needs no
database, no AWS account and no Docker. It covers the configuration check, the
truncated-dump guard, the upload key and encryption flag, temporary-file
cleanup, and the date logic across a full year.
