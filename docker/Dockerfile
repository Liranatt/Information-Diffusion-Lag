# Trading daemon image for the live paper-trading control pipeline.
# Runs the trader daemon or read-only dashboard against the already-running
# paper IB Gateway and Postgres on liranserver.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Etc/UTC

# build-essential covers the rare case where a wheel is unavailable for a dep
# (numba/llvmlite/scipy normally ship manylinux wheels, so this is a safety net).
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first so code changes don't bust the layer cache.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Bake the source in so the image can run standalone; at runtime the compose
# file bind-mounts the repo over /app so refits (new policy CSVs) and git pulls
# propagate without a rebuild.
COPY . .

# Editable install so `core`, `ingest`, `backtesting`, `live`, `database` import
# absolutely from anywhere (no sys.path hacks). The bind-mount at runtime keeps
# /app pointing at the live repo, so the editable finder still resolves.
RUN pip install -e . --no-deps

CMD ["python", "-m", "live.run_live", "--daemon"]
