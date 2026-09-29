FROM python:3.12-alpine

WORKDIR /app

# Copy relay server script
COPY engram/sync/relay.py /app/relay.py

# Create data directory for SQLite store
RUN mkdir -p /data

EXPOSE 8421

ENTRYPOINT ["python", "/app/relay.py", "--host", "0.0.0.0", "--port", "8421", "--db", "/data/relay.db"]
