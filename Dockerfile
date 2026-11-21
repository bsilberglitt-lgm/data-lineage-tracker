# Data Lineage Tracker
#
# A plain, portable container: no Render-specific anything. The same image runs
# under Docker Compose locally, on Render, and on AWS (App Runner / ECS) or
# Azure (App Service / Container Apps) later.
#
# Configuration comes entirely from environment variables:
#   DATABASE_URL  PostgreSQL connection string (required)
#   PORT          Port to listen on (default 8765)

FROM python:3.15-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Refresh the system certificate store explicitly rather than inheriting
# whatever `python:3.12-slim` happened to ship with.
#
# Render's eMudhra advisory (October 2026) puts this on us: a Docker service
# uses whichever CA store its image bundles, so "rebuild and the certificates
# update" is only true if the base image itself has been rebuilt since the
# compromised intermediates were distrusted. That is outside our control and
# invisible when it is stale, so the upgrade is stated here instead.
#
# This application makes no outbound HTTPS calls of its own — the only TLS
# client in it is libpq talking to Postgres — so the practical exposure is
# close to nil. It is here because a correct CA store should not depend on a
# base image's release cadence, and because the next thing added to this
# service might well call out.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && update-ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Install dependencies first so this layer is cached across code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8765

# Runs Alembic migrations, seeds if empty, then serves.
CMD ["python", "app.py"]
