# Codex-only runtime for the AI Company server profile.
# Keep this image deliberately small: all three agents use Codex, so installing
# Claude/Kimi/MiniMax/Trae and their transitive dependencies only makes server
# builds slower and less reliable.
FROM python:3.12-slim

RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' /etc/apt/sources.list.d/debian.sources; \
      sed -i 's|http://security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources; \
    fi

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        openssh-client \
        procps \
        ripgrep \
        tmux \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

ARG NODE_VERSION=22.23.1
ARG TARGETARCH
ARG NODE_DIST_MIRROR=https://npmmirror.com/mirrors/node
RUN ARCH="$([ "$TARGETARCH" = "amd64" ] && echo x64 || echo arm64)" \
    && curl -fsSL "${NODE_DIST_MIRROR}/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${ARCH}.tar.xz" -o /tmp/node.tar.xz \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
    && rm /tmp/node.tar.xz \
    && node --version \
    && npm --version

RUN npm config set registry https://registry.npmmirror.com

RUN npm install --silent --global \
        @larksuite/cli@latest \
        @openai/codex \
    && lark-cli --version \
    && codex --version

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple "setuptools>=68" \
    && pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple --no-build-isolation -e .

COPY scripts/feishu_channel/ ./scripts/feishu_channel/
RUN cd scripts/feishu_channel \
    && npm install --omit=dev --silent --no-fund --no-audit

COPY scripts/deploy/ ./scripts/deploy/
COPY scripts/dev/ ./scripts/dev/

COPY templates/ ./templates/
COPY skills/ ./skills/

ENV CLAUDETEAM_STATE_DIR=/data/state \
    CLAUDETEAM_CONFIG_FILE=/data/claudeteam.toml \
    CLAUDETEAM_TEAM_FILE=/data/team.json \
    CLAUDETEAM_RUNTIME_CONFIG=/data/runtime_config.json \
    LARK_CLI_NO_PROXY=1

VOLUME ["/data"]
CMD ["bash"]
