FROM python:3.12-slim

WORKDIR /app

# ffmpeg: the transcriber extracts a small audio track from reel videos before
# sending to Groq (Whisper only needs audio, and it keeps files under Groq's
# free-tier size cap).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code (and, from the local build context, the current places.db as a seed)
COPY . .

# The live DB lives on the mounted volume; the baked places.db is only a seed
# that the entrypoint copies in on first boot.
ENV DB_PATH=/data/places.db
EXPOSE 8000

RUN chmod +x docker-entrypoint.sh
ENTRYPOINT ["./docker-entrypoint.sh"]
