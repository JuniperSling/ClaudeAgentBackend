FROM python:3.12-slim

WORKDIR /app

ENV TZ=Asia/Shanghai
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev tzdata && \
    ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/

RUN useradd -m -u 1000 agent && \
    mkdir -p data && \
    chown -R agent:agent /app

USER agent

CMD ["python", "-m", "src.main"]
