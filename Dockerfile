FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SKRIDSKO_CACHE_DIR=/var/cache/skridsko-bot

WORKDIR /app

# tzdata is needed for Europe/Stockholm; lxml ships manylinux wheels so no build deps.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY skridsko_bot ./skridsko_bot

RUN useradd --create-home --uid 10001 skridsko \
 && mkdir -p "$SKRIDSKO_CACHE_DIR" \
 && chown -R skridsko:skridsko /app "$SKRIDSKO_CACHE_DIR"

USER skridsko

# Fails once the container has not completed a post in over 26 hours.
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import os,sys,time; p=os.path.join(os.environ['SKRIDSKO_CACHE_DIR'],'healthy'); sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p) < 26*3600 else 1)"

ENTRYPOINT ["python", "-m", "skridsko_bot"]
