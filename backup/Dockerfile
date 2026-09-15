# Container for the nightly backup Cron Job.
#
# Separate from the application's Dockerfile on purpose: this runs on its own
# schedule, needs tools the app does not (pg_dump, the AWS CLI), and should not
# grow the app image or its attack surface.
#
# **The pg_dump major version must be at least the server's major version.**
# pg_dump refuses to dump from a newer server than itself, with a message about
# server version mismatch. Render Postgres is 16 unless you chose otherwise, so
# this image pins postgresql16-client. If you upgrade the database, change the
# package here at the same time — it is the kind of thing that only surfaces
# the night after the upgrade.

FROM alpine:3.20

RUN apk add --no-cache \
      postgresql16-client \
      aws-cli \
      gzip \
      wget

WORKDIR /backup
COPY backup.sh ./
RUN chmod +x backup.sh

CMD ["./backup.sh"]
