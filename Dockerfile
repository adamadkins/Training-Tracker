# Build and run from repo root so the container uses the latest code.
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (repo root = app root)
COPY . .

# Use PORT from host (Render/Railway/etc. set this)
ENV PORT=8080
EXPOSE 8080

CMD gunicorn -b 0.0.0.0:${PORT} run:app
