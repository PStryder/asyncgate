FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY src/ src/
COPY alembic.ini .
COPY alembic/ alembic/

# Install Python dependencies
RUN pip install --no-cache-dir .

# Create non-root user
RUN addgroup --system --gid 1001 asyncgate && \
    adduser --system --uid 1001 --gid 1001 asyncgate && \
    chown -R asyncgate:asyncgate /app

USER asyncgate

# Expose ports
EXPOSE 8080
EXPOSE 9091

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/v1/health').raise_for_status()"

# Run the application
CMD ["uvicorn", "asyncgate.main:app", "--host", "0.0.0.0", "--port", "8080"]
