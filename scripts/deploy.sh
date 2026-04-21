#!/bin/bash

set -e

echo "Navigating to /opt/tg-digest"
cd /opt/tg-digest

echo "Pulling latest changes from git"
git pull

echo "Building reader image without cache"
docker compose build --no-cache reader

echo "Stopping reader service"
docker compose down reader

echo "Starting reader service in detached mode"
docker compose up -d reader

echo "Deployment completed successfully"