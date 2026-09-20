FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0

COPY pothole_agent ./pothole_agent

ENV PYTHONUNBUFFERED=1
# Cloud Run provides $PORT; default to 8080 for local docker runs.
CMD exec gunicorn --bind :${PORT:-8080} --workers 2 --threads 8 --timeout 0 "pothole_agent.webapp:create_app()"
