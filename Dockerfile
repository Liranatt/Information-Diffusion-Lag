# Trading daemon image for the live paper-trading control pipeline.
# Runs `python -m interactive_brokers.run_live --daemon` against a companion
# IB Gateway container and a Postgres reachable on the Docker host.
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

CMD ["python", "-m", "interactive_brokers.run_live", "--daemon"]
