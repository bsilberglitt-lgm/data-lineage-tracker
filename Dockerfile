# Data Lineage Tracker
#
# A plain, portable container: no Render-specific anything. The same image runs
# under Docker Compose locally, on Render, and on AWS (App Runner / ECS) or
# Azure (App Service / Container Apps) later.
#
# Configuration comes entirely from environment variables:
#   DATABASE_URL  PostgreSQL connection string (required)
#   PORT          Port to listen on (default 8765)

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first so this layer is cached across code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8765

# Runs Alembic migrations, seeds if empty, then serves.
CMD ["python", "app.py"]
