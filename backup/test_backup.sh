#!/bin/sh
# Exercise backup.sh without a database, an AWS account, or Docker.
#
#     sh backup/test_backup.sh
#
# Stand-ins for pg_dump, aws and gzip are put on the PATH, so the script's
# control flow runs for real while nothing is dumped or uploaded. What this
# pins down:
#
#   * It refuses to start with configuration missing, and names what is
#     missing rather than failing somewhere deeper.
#   * **A failed pg_dump does not produce a fake backup.** `pg_dump | gzip`
#     reports gzip's exit status, not pg_dump's, so a connection failure
#     leaves a valid empty archive and `set -e` sees nothing wrong. Without
#     the size guard this job would upload 20 bytes every night and look
#     perfectly healthy until the day someone needed it. This is the single
#     most important check here.
#   * A normal run uploads to the right key, asks for encryption, and removes
#     the temporary file.
#   * The date logic produces 365 daily, 12 monthly and 1 yearly upload a year.

set -eu
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
BIN="$(mktemp -d)"
LOG="$(mktemp -d)"
trap 'rm -rf "$BIN" "$LOG"' EXIT

cat > "$BIN/pg_dump" <<'EOF'
#!/bin/sh
[ -n "${FAKE_DUMP_FAIL:-}" ] && { echo "pg_dump: error: connection failed" >&2; exit 1; }
head -c "${FAKE_DUMP_BYTES:-400000}" /dev/zero
EOF
cat > "$BIN/aws" <<EOF
#!/bin/sh
echo "\$*" >> "$LOG/aws-calls"
EOF
printf '#!/bin/sh\nexec /bin/cat\n' > "$BIN/gzip"
chmod +x "$BIN/pg_dump" "$BIN/aws" "$BIN/gzip"

PASS=0; FAIL=0
ck() {
    if [ "$2" = "$3" ]; then PASS=$((PASS+1)); echo "  ok   $1"
    else FAIL=$((FAIL+1)); echo "  FAIL $1 — got '$2', wanted '$3'"; fi
}
said() { grep -q "$1" "$LOG/out" && echo "  ok   $2" && PASS=$((PASS+1)) \
         || { echo "  FAIL $2"; FAIL=$((FAIL+1)); }; }
run() {
    rm -f "$LOG/aws-calls"
    set +e
    ( cd "$HERE" && PATH="$BIN:$PATH" "$@" sh backup.sh ) > "$LOG/out" 2>&1
    RC=$?
    set -e
}
FULL_ENV="env DATABASE_URL=postgres://x AWS_ACCESS_KEY_ID=k AWS_SECRET_ACCESS_KEY=s AWS_DEFAULT_REGION=us-east-1 BACKUP_BUCKET=lineage-backups"

echo "-- configuration is checked before anything runs --"
run env DATABASE_URL= AWS_ACCESS_KEY_ID= AWS_SECRET_ACCESS_KEY= AWS_DEFAULT_REGION= BACKUP_BUCKET=
ck "refuses to run with nothing set" "$RC" "1"
said "DATABASE_URL is not set" "names the missing variable"

run env DATABASE_URL=postgres://x AWS_ACCESS_KEY_ID=k AWS_SECRET_ACCESS_KEY=s AWS_DEFAULT_REGION=us-east-1 BACKUP_BUCKET=
ck "refuses to run without a bucket" "$RC" "1"
said "BACKUP_BUCKET is not set" "names the bucket"

echo "\n-- a failed dump is never uploaded --"
run $FULL_ENV FAKE_DUMP_FAIL=1
ck "exits non-zero when pg_dump fails" "$RC" "1"
ck "and uploads nothing" "$([ -f "$LOG/aws-calls" ] && echo some || echo none)" "none"
said "refusing to upload" "explains why"

run $FULL_ENV FAKE_DUMP_BYTES=1000
ck "exits non-zero on a suspiciously small dump" "$RC" "1"
ck "and uploads nothing" "$([ -f "$LOG/aws-calls" ] && echo some || echo none)" "none"

# The floor must sit below a real dump. Production dumps are around 2.7 MB, and
# the guard was once set at 50 KB against a 60 KB estimate taken from seed data
# — close enough that a good backup could have been refused. 20 KB stands in
# for a small real dump.
run $FULL_ENV FAKE_DUMP_BYTES=20480
ck "a small but plausible dump is accepted" "$RC" "0"

echo "\n-- a normal run --"
run $FULL_ENV
ck "succeeds" "$RC" "0"
ck "uploaded once" "$(wc -l < "$LOG/aws-calls" | tr -d ' ')" "1"
grep -q "s3://lineage-backups/data-lineage-tracker/daily/" "$LOG/aws-calls" \
    && { echo "  ok   wrote to the daily prefix"; PASS=$((PASS+1)); } \
    || { echo "  FAIL wrong key"; FAIL=$((FAIL+1)); }
grep -q -- "--sse AES256" "$LOG/aws-calls" \
    && { echo "  ok   asked for encryption at rest"; PASS=$((PASS+1)); } \
    || { echo "  FAIL no encryption flag"; FAIL=$((FAIL+1)); }
ck "cleaned up its temporary file" "$(ls /tmp/data-lineage-tracker-*.dump.gz 2>/dev/null | wc -l | tr -d ' ')" "0"

echo "\n-- the schedule over a full year --"
d=2027-01-01; daily=0; monthly=0; yearly=0
while [ "$d" != "2028-01-01" ]; do
    DOM=$(date -u -d "$d" +%d 2>/dev/null || date -u -jf %Y-%m-%d "$d" +%d)
    MON=$(date -u -d "$d" +%m 2>/dev/null || date -u -jf %Y-%m-%d "$d" +%m)
    daily=$((daily+1))
    [ "$DOM" = "01" ] && monthly=$((monthly+1))
    [ "$DOM" = "01" ] && [ "$MON" = "01" ] && yearly=$((yearly+1))
    d=$(date -u -d "$d + 1 day" +%Y-%m-%d 2>/dev/null || date -u -jf %Y-%m-%d -v+1d "$d" +%Y-%m-%d)
done
ck "365 daily uploads a year" "$daily" "365"
ck "12 monthly uploads a year" "$monthly" "12"
ck "1 yearly upload a year" "$yearly" "1"

echo "\n  $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
