FROM python:3.12-slim

# Create non-privileged user with proper home directory
RUN mkdir -p /home/tg-digest && \
    useradd --system --shell /bin/false --home-dir /home/tg-digest --uid 1000 --gid 1000 tg-digest || true && \
    chown -R tg-digest:tg-digest /home/tg-digest && \
    chmod 700 /home/tg-digest

WORKDIR /app

COPY src/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ .

# Set proper permissions
RUN chown -R tg-digest:tg-digest /app

# Switch to non-privileged user
USER tg-digest

CMD ["python", "main.py"]
