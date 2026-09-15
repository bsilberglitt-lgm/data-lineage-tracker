#!/bin/sh
# Nightly off-site backup of the Render PostgreSQL database.
#
# Runs as a Render Cron Job. Dumps the database, compresses it, and uploads the
# same file to one, two or three prefixes in S3 depending on the date:
#
#     daily/     every run
#     monthly/   on the 1st of the month
#     yearly/    on the 1st of January
#
# **Nothing here deletes anything.** Old backups are removed by S3 lifecycle
# rules attached to those prefixes (see lifecycle.json). That is deliberate:
# the credential this job runs with has PutObject and nothing else, so if it
# ever leaks, an attacker can add junk to the bucket but cannot destroy the
# backups — which is exactly what ransomware tries to do first. Pruning from
# the script would have meant handing it DeleteObject.
#
# Required environment variables (set in the Render dashboard):
#
#     DATABASE_URL            Internal connection string for the database
#     AWS_ACCESS_KEY_ID       Credentials for the backup IAM user
#     AWS_SECRET_ACCESS_KEY
#     AWS_DEFAULT_REGION      e.g. us-east-1
#     BACKUP_BUCKET           Bucket name, no s3:// prefix
#
# Optional:
#     BACKUP_PREFIX           Defaults to "data-lineage-tracker"
#     HEALTHCHECK_URL         Pinged on success, if you add uptime monitoring

set -eu

PREFIX="${BACKUP_PREFIX:-data-lineage-tracker}"
STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
DAY_OF_MONTH="$(date -u +%d)"
MONTH="$(date -u +%m)"
FILE="/tmp/${PREFIX}-${STAMP}.dump.gz"

# Remove the dump however we leave — including the failure paths below, which
# exit before the end of the script. The cron container is thrown away after
# each run so this rarely matters there, but a dump of the production database
# should not be left lying around anywhere it might persist.
trap 'rm -f "$FILE"' EXIT INT TERM

say() { echo "[$(date -u +%H:%M:%S)] $*"; }

for required in DATABASE_URL AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY \
                AWS_DEFAULT_REGION BACKUP_BUCKET; do
    eval "value=\${$required:-}"
    if [ -z "$value" ]; then
        echo "FATAL: $required is not set. Set it in the Render dashboard." >&2
        exit 1
    fi
done

# -Fc is PostgreSQL's custom format: compressed, and restorable selectively
# with pg_restore (a single table, or schema without data). A plain .sql file
# can only be replayed whole. gzip on top because -Fc's own compression is
# modest and the upload is what costs time.
say "Dumping database"
pg_dump --format=custom --no-owner --no-acl "$DATABASE_URL" | gzip -9 > "$FILE"

SIZE_BYTES="$(wc -c < "$FILE")"
say "Dump complete: $(echo "$SIZE_BYTES" | awk '{printf "%.1f MB", $1/1048576}')"

# A dump far smaller than expected usually means pg_dump failed partway and the
# pipe swallowed the error. Better to fail loudly than to upload a stub and
# believe it is a backup.
#
# The threshold has to sit in the gap between the two, and the gap is wider
# than it first appears. A *failed* dump is well under 1 KB — an empty gzip
# stream is about 20 bytes. A *successful* one always carries the schema DDL
# even with no rows at all, so it is several KB minimum; this database's
# content compresses to roughly 60 KB today, most of it schema_column.
#
# 10 KB sits cleanly between. It was originally 50 KB, which was within a
# whisker of the real figure and would have started rejecting good backups the
# moment the schema catalog shrank.
if [ "$SIZE_BYTES" -lt 10240 ]; then
    echo "FATAL: dump is only ${SIZE_BYTES} bytes — refusing to upload it." >&2
    echo "Check the database is reachable and DATABASE_URL is correct." >&2
    exit 1
fi

upload() {
    target="s3://${BACKUP_BUCKET}/${PREFIX}/$1/$(basename "$FILE")"
    say "Uploading to $1/"
    # SSE-KMS would also work; SSE-S3 (AES256) is the default on new buckets
    # and needs no key management. Set explicitly so it cannot silently lapse.
    aws s3 cp "$FILE" "$target" --sse AES256 --only-show-errors
}

# Written as if-blocks rather than `[ ... ] && upload`. Under `set -e` a false
# test at the end of the script would be the script's own exit status in some
# shells, turning every day that is not the 1st into a "failed" cron run.
upload daily
if [ "$DAY_OF_MONTH" = "01" ]; then
    upload monthly
    if [ "$MONTH" = "01" ]; then
        upload yearly
    fi
fi

# (the trap above removes the dump)

# Optional dead-man's switch. A backup job that stops running is silent by
# nature — you find out when you need the backup. If HEALTHCHECK_URL is set
# (healthchecks.io or similar), failing to ping it raises an alert.
if [ -n "${HEALTHCHECK_URL:-}" ]; then
    wget -q -O /dev/null --timeout=10 "$HEALTHCHECK_URL" || \
        say "WARNING: could not reach the health check URL"
fi

say "Backup finished"
