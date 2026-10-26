# Flame web app. Code, the built web client and the local search model are in the image; the regulation data
# (PDFs, parsed paragraphs, graph, index, runs) is mounted from the host so it can be deleted after the hackathon.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy HF_HOME=/opt/hf PATH="/app/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 flame

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
# the embedding model the assistant uses for questions, so the server never downloads it at run time
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('Snowflake/snowflake-arctic-embed-m-v1.5')" \
    && chmod -R a+rX /opt/hf
ENV HF_HUB_OFFLINE=1

COPY app ./app
COPY pipeline ./pipeline
COPY web/dist ./web/dist
COPY deploy/oracle/start.sh /start.sh

EXPOSE 8000
CMD ["/start.sh"]
