#!/usr/bin/env zsh

# This script is used to update the Tempo application and its dependencies.

# 1. Make sure you are on main
export DOCKER_CONTEXT=desktop-linux
export COMPOSE_PROJECT_NAME=tempo
CURRENT_BRANCH=$(git branch --show-current)

if [ "$CURRENT_BRANCH" != "main" ]; then
  echo "You are not on the main branch. Please switch to the main branch and try again."
  exit 1
fi

# 2. Start the database and cache services
docker compose up -d --wait --build postgres redis

# 3. Check the health of the database and cache services
DB_HEALTH=$(docker compose exec -T redis redis-cli ping)
if [ "$DB_HEALTH" != "PONG" ]; then
  echo "Redis is not healthy. Expected PONG but got $DB_HEALTH. Please check the Redis service and try again."
  exit 1
fi

# 4. Get the current schema version from the database and the schema version from the code
PYTHON_SCHEMA_VERSION=$(cat backend/app/schema_version.py)
DB_SCHEMA_VERSION=$(docker compose exec -T postgres psql -U tempo -d tempo -c 'SELECT MAX(version) FROM tempo_schema_migrations;')

if [ "$PYTHON_SCHEMA_VERSION" != "$DB_SCHEMA_VERSION" ]; then
  echo "Schema version mismatch. Please run the migrations and try again."
  exit 1
fi

# 5. Build the Docker images for the services
docker compose build api foreground-worker background-worker background-scheduler web defense-engine maia-worker

# 6. Stop the old services before updating
docker compose stop web defense-engine maia-worker api foreground-worker background-worker background-scheduler

# 7. Start workers and API services
docker compose up -d --no-build --wait --wait-timeout 180 foreground-worker background-worker background-scheduler api

# 8. Start the database and cache services
docker compose up -d --no-build web defense-engine maia-worker postgres-backup

# 9. Verify that the services are running
docker compose ps -a
curl --fail --show-error http://localhost:8000/api/health
open http://localhost:3000
