FROM python:3.11-slim

# The container must run as the same uid/gid that owns the mounted data
# directory, otherwise it cannot write kb.db / custom.css. Use numeric
# USER (no useradd needed) and pick the id at build time:
#   docker compose build --build-arg KB_UID=$(id -u) --build-arg KB_GID=$(id -g)
ARG KB_UID=1000
ARG KB_GID=1000

ENV PYTHONUNBUFFERED=1 \
    HOME=/home/kb

WORKDIR /app

# Dependencies first so editing the app doesn't invalidate the pip layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application. See .dockerignore: data/, .git and __pycache__ never enter
# the image (the DB is a bind-mounted volume at runtime).
COPY . .

RUN mkdir -p /app/data /home/kb && chown -R "${KB_UID}:${KB_GID}" /app /home/kb

# Drop privileges. Any file the app writes under /app (custom.css, WAL files)
# lands in the bind-mounted data/ and is owned by the host user.
USER ${KB_UID}:${KB_GID}

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
