# ============ Stage 1: 安装依赖 ============
FROM python:3.12-slim-bookworm AS builder

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

COPY requirements.txt /srv/requirements.txt

RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r /srv/requirements.txt && \
    /opt/venv/bin/playwright install chromium

# ============ Stage 2: 运行时镜像 ============
FROM python:3.12-slim-bookworm

ENV PYTHONPATH="/srv:/srv/ibeam" \
    PLAYWRIGHT_BROWSERS_PATH="/opt/pw-browsers" \
    PATH="/opt/venv/bin:$PATH" \
    IBEAM_GATEWAY_DIR="/srv/clientportal.gw"

# 从 builder 拷贝 venv 和 Chromium 浏览器
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/pw-browsers /opt/pw-browsers

RUN \
    mkdir -p /usr/share/man/man1 /srv/outputs /srv/clientportal.gw /srv/ibeam && \
    addgroup --gid 1000 ibeam && \
    adduser --disabled-password --gecos "" --uid 1000 --gid 1000 --shell /bin/bash ibeam && \
    # JRE（IB Gateway 必需）+ Chromium 运行时共享库
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        default-jre-headless curl \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
        libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 libdbus-1-3 \
        fonts-noto-cjk && \
    rm -rf /var/lib/apt/lists/*

COPY clientportal.gw /srv/clientportal.gw
COPY ibeam /srv/ibeam

RUN \
    chown -R ibeam:ibeam /srv/ibeam /srv/outputs /srv/clientportal.gw && \
    chmod 744 /srv/ibeam/run.sh

WORKDIR /srv/ibeam
USER ibeam

CMD ["python", "ibeam_starter.py"]
