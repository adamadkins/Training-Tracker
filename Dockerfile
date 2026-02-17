# Build and run from repo root so the container uses the latest code.
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (repo root = app root)
COPY . .

# PORT is set by Railway at runtime; default 8080 if not set
ENV PORT=8080
EXPOSE 8080

# Shell expands $PORT at runtime; use 8080 if Railway doesn't set PORT
CMD ["sh", "-c", "gunicorn -b 0.0.0.0:${PORT:-8080} run:app"]
