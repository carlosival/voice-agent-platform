# List files in the debug directory
docker exec fastapi ls /tmp/stt_debug/

# Copy files from the debug directory
docker cp fastapi:/tmp/stt_debug/ ./stt_debug

# Remove all stopped containers, unused networks, dangling images, build cache
docker system prune -a
# Delete all failed build cache and dangling layers
docker system prune -a --volumes -f

# Enter the container
docker exec -it fastapi bash

# Start a project
docker compose build --no-cache
docker compose up -d

# Stop a project
docker compose down

# See docker containers's inside folder structure
docker compose run --rm {container_name} find . -maxdepth 3

# Inpect docker container network
docker inspect <container_name> | grep -i "NetworkSettings" -A 10

# Run profiles
docker compose --profile tools run --rm seed_db