FROM python:3.11-slim-bookworm

# ---------------------------------------------------------------------------
# System dependencies for chromium + seleniumbase
# ---------------------------------------------------------------------------
# chromium / chromium-driver: headless browser used by upwork_analysis
# fonts-liberation, libnss3, libxss1, libasound2: standard chromium runtime libs
# git: needed for `pip install git+https://...` of upwork_analysis
# build-essential: a few transitive deps still need to compile
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    fonts-liberation \
    libasound2 \
    libnss3 \
    libxss1 \
    libgbm1 \
    libdrm2 \
    git \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Tell seleniumbase / chromedriver where Chrome lives
ENV CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# ---------------------------------------------------------------------------
# Python dependencies (separate layer to maximize cache hits)
# ---------------------------------------------------------------------------
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Make seleniumbase's chromedriver match the installed chromium. Will use the
# system chromedriver if versions already align (cheap fallback).
RUN python -m seleniumbase install chromedriver || true

# ---------------------------------------------------------------------------
# Application code
# ---------------------------------------------------------------------------
COPY *.py /app/
COPY job_titles.csv countries_continents.csv /app/

# Volume-mounted in production; created here so first run has a writable dir.
RUN mkdir -p /app/state /secrets /app/logs

# Non-root user (gives chromium the sandbox it wants -- run with --no-sandbox via env)
RUN useradd --create-home --shell /bin/bash scraper && \
    chown -R scraper:scraper /app /secrets
USER scraper

ENTRYPOINT ["python", "-u", "main.py"]
