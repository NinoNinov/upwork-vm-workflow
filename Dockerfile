FROM python:3.11-slim-bookworm

# ---------------------------------------------------------------------------
# System dependencies for Google Chrome + seleniumbase
# ---------------------------------------------------------------------------
# Real Google Chrome (NOT Debian's open-source chromium fork). Critical for
# anti-bot bypass: Cloudflare/Datadome fingerprint several signals that differ
# between the two (User-Agent string, Widevine DRM presence, bundled fonts).
# xvfb + x11-utils: virtual display so Chrome can launch non-headless.
# git / build-essential: needed for pip install of upwork_analysis from GitHub.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates \
    && wget -qO- https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-linux-signing-key.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-linux-signing-key.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        google-chrome-stable \
        xvfb \
        x11-utils \
        fonts-liberation \
        libasound2 \
        libnss3 \
        libxss1 \
        libgbm1 \
        libdrm2 \
        git \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Point seleniumbase / undetected-chromedriver at the real Chrome binary.
# chromedriver is installed by `python -m seleniumbase install chromedriver`
# below -- it auto-matches the installed Chrome version.
ENV CHROME_BIN=/usr/bin/google-chrome \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# ---------------------------------------------------------------------------
# Python dependencies (separate layer to maximize cache hits)
# ---------------------------------------------------------------------------
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Install chromedriver matching the installed Google Chrome version.
RUN python -m seleniumbase install chromedriver || true

# ---------------------------------------------------------------------------
# Application code
# ---------------------------------------------------------------------------
COPY *.py /app/
COPY job_titles.csv countries_continents.csv /app/
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Volume-mounted in production; created here so first run has a writable dir.
RUN mkdir -p /app/state /secrets /app/logs

# Xvfb needs /tmp/.X11-unix to exist as world-writable + sticky (the non-root
# scraper user cannot create it at runtime: euid != 0).
RUN mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

# Non-root user (gives chromium the sandbox it wants -- run with --no-sandbox via env).
# seleniumbase writes uc_driver into its own site-packages dir at runtime, so the
# drivers/ dir must be writable by the runtime user.
RUN useradd --create-home --shell /bin/bash scraper && \
    chown -R scraper:scraper /app /secrets && \
    chown -R scraper:scraper /usr/local/lib/python3.11/site-packages/seleniumbase/drivers && \
    chmod -R u+w /usr/local/lib/python3.11/site-packages/seleniumbase/drivers
USER scraper

ENTRYPOINT ["/app/entrypoint.sh"]
