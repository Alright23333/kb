FROM python:3.11-slim

WORKDIR /app

# Dependencies first so editing the app doesn't invalidate the pip layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application. See .dockerignore: data/, .git and __pycache__ never enter
# the image (the DB is a bind-mounted volume at runtime).
COPY . .

RUN mkdir -p /app/data

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
