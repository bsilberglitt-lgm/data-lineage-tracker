# Container for the nightly backup Cron Job.
#
# Separate from the application's Dockerfile on purpose: this runs on its own
# schedule, needs tools the app does not (pg_dump, the AWS CLI), and should not
# grow the app image or its attack surface.
#
# **The pg_dump major version must be at least the server's major version.**
# pg_dump refuses to dump from a newer server, and says so plainly:
#
#     pg_dump: error: aborting because of server version mismatch
#     server version: 18.4; pg_dump version: 16.14
#
# Render's managed Postgres is **18**, which is what this pins. If the database
# is ever upgraded, bump `postgresql18-client` and the Alpine version together
# — the client packages are named per major version, and a given Alpine release
# only carries some of them. postgresql18-client first appears in Alpine 3.23.
#
# Check availability before changing the pin:
#   https://pkgs.alpinelinux.org/packages?name=postgresql18-client
#
# This was caught on the first real run, by the size guard in backup.sh rather
# than by anything watching for it — a mismatched pg_dump exits non-zero but
# the gzip downstream of it still succeeds.

FROM alpine:3.23

# `ca-certificates` is named explicitly even though aws-cli pulls it in: this
# is the one service here that makes outbound HTTPS — `aws s3 cp` to the backup
# bucket — so its trust store is the one that actually matters. Naming it means
# a rebuild is visibly a certificate refresh, not incidentally one.
RUN apk add --no-cache \
      ca-certificates \
      postgresql18-client \
      aws-cli \
      gzip \
      wget \
 && update-ca-certificates

# Fail the build rather than the 3am run if either tool is missing or the
# client is somehow older than the server. A broken image that builds cleanly
# and fails nightly is the thing worth avoiding here.
RUN pg_dump --version && aws --version

# Make the AWS CLI verify against the system store that the layer above just
# refreshed, rather than any CA bundle botocore ships inside itself.
#
# This is the "some libraries bundle their own certificates and require
# separate updates" case in Render's eMudhra advisory, and it matters more here
# than anywhere else in the project: if an attacker holding the compromised
# eMudhra keys could impersonate S3 to this job, the thing handed over is a
# full database dump. One environment variable removes the question of which
# store botocore chose.
ENV AWS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /backup
COPY backup.sh ./
RUN chmod +x backup.sh

CMD ["./backup.sh"]
