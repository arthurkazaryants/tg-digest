FROM python:3.12-slim

# Create non-privileged user for security (defense-in-depth)
RUN useradd --system --shell /bin/false --home-dir /nonexistent --uid 1000 --gid 1000 tg-digest || true

WORKDIR /app

COPY src/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ .

# Set proper permissions
RUN chown -R tg-digest:tg-digest /app

# Switch to non-privileged user
USER tg-digest

CMD ["python", "main.py"]
