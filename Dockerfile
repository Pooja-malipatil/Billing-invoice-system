# Base image: slim Python, small download size, no unnecessary OS packages
FROM python:3.12-slim

WORKDIR /app

# Copy only requirements first - this is a deliberate ordering trick:
# Docker caches each layer, so if requirements.txt hasn't changed, Docker
# reuses the cached "pip install" layer instead of re-running it on every
# build. Copying all the code first would bust this cache on every change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the rest of the application code
COPY . .

# Render/most platforms inject PORT at runtime; default to 5000 for local
# `docker run` where nothing sets it.
ENV PORT=5000
EXPOSE 5000

CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT}"]
