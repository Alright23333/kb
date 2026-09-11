#!/bin/bash
# Run the Knowledge Base server
cd /home/user/services/kb
exec /home/user/.hermes/hermes-agent/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080 "$@"
