# Build and run from repo root so the container uses the latest code.
FROM python:3.12-slim

WORKDIR /app

# Install dependencies (install psycopg2-binary explicitly so cache can't skip it)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir psycopg2-binary==2.9.10

# Copy application code (repo root = app root)
COPY . .

EXPOSE 8080

# Run migrations + one-time init (create_all + seed if empty), then start app. Init runs once, not in every worker.
ENV FLASK_APP=run:app
# Longer timeout so heavy pages don't hit WORKER TIMEOUT (default 30s)
CMD ["sh", "-c", "flask db upgrade && python init_db.py && exec gunicorn -b 0.0.0.0:8080 --timeout 120 run:app"]
