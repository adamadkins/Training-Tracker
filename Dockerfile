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

# Bind to 8080 so we don't depend on $PORT expansion (Railway proxies to this)
CMD ["gunicorn", "-b", "0.0.0.0:8080", "run:app"]
