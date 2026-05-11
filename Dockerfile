FROM python:3.12-slim

WORKDIR /app

ARG HTTP_PROXY
ARG HTTPS_PROXY

ENV TZ=Asia/Shanghai

# apt: no proxy needed (domestic mirrors fast enough)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev tzdata curl fonts-noto-cjk && \
    ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    rm -rf /var/lib/apt/lists/*

# nodesource needs proxy
RUN curl -fsSL --proxy "${HTTP_PROXY}" https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.cloud.tencent.com/pypi/simple/ --trusted-host mirrors.cloud.tencent.com -r requirements.txt

COPY src/ src/
COPY config/ config/

RUN useradd -m -u 1000 agent && \
    mkdir -p data /home/agent/.claude/skills && \
    chown -R agent:agent /app /home/agent

USER agent

RUN cd /home/agent && npm config set registry https://mirrors.cloud.tencent.com/npm/ && \
    npm init -y && npm install playwright && \
    npx playwright install chromium 2>/dev/null || true

CMD ["python", "-m", "src.main"]
