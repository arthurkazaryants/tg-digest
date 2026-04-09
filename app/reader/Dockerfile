FROM python:3.12-slim

# Create non-privileged user for security
# Note: This user exists ONLY inside the container
RUN groupadd --system --gid 1001 tg-digest && \
    useradd --system --gid tg-digest --shell /bin/false --home-dir /nonexistent --uid 1001 tg-digest

WORKDIR /app

COPY src/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ .
COPY healthcheck.py .

# Set proper permissions
RUN chown -R tg-digest:tg-digest /app

# Switch to non-privileged user
USER tg-digest

CMD ["python", "main.py"]
