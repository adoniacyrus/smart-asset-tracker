# Multi-stage / Production Dockerfile for Smart Asset Tracker Backend
FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered streaming logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies required by mysqlclient and system operations
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-libmysqlclient-dev \
    build-essential \
    pkg-config \
    curl \
    netcat-traditional \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy backend application source
COPY . /app/

# Ensure entrypoint script has execution permissions
RUN chmod +x /app/entrypoint.sh

# Expose Django Gunicorn port
EXPOSE 8000

# Use container entrypoint to manage DB readiness, migrations, and static collection
ENTRYPOINT ["/app/entrypoint.sh"]

# Default command: launch production Gunicorn WSGI server
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]

