FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config/wiki-only.example.toml /config/wiki-only.toml

RUN pip install --no-cache-dir .
RUN useradd --system --uid 1000 --create-home lmit \
    && mkdir -p /data/knowledge_base /data/raw /config \
    && chown -R lmit:lmit /data /config

USER lmit

EXPOSE 8765
VOLUME ["/data/knowledge_base"]

CMD ["lmit-wiki", "serve", "--config", "/config/wiki-only.toml", "--host", "0.0.0.0", "--port", "8765"]
