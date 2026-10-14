# Serves the dashboard and API. No Java or Spark here: the pipeline's outputs (results/, models/)
# are part of the repository, and the DuckDB search index is downloaded from a Hugging Face
# dataset repo (SEARCH_INDEX_REPO), at build time if a build secret is available, otherwise in the
# background after the server starts.
FROM python:3.11-slim-bookworm

# Hugging Face Spaces runs containers as uid 1000
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    HOST=0.0.0.0 \
    PORT=7860 \
    SEARCH_MEMORY_LIMIT=2GB \
    PYTHONUNBUFFERED=1
WORKDIR /home/user/app

COPY --chown=user requirements-app.txt .
RUN pip install --no-cache-dir --user -r requirements-app.txt

COPY --chown=user config.py run.py ./
COPY --chown=user backend backend
COPY --chown=user serving serving
COPY --chown=user frontend frontend
COPY --chown=user models models
COPY --chown=user results results

# Try to bake the index into the image. Needs SEARCH_INDEX_REPO (a Space variable, exposed as a build
# argument) and, for a private dataset repo, the HF_TOKEN secret. If either is missing the build still
# succeeds and the server downloads the index after it starts.
ARG SEARCH_INDEX_REPO
RUN --mount=type=secret,id=HF_TOKEN,mode=0444,required=false \
    if [ -n "$SEARCH_INDEX_REPO" ]; then \
      HF_TOKEN="$(cat /run/secrets/HF_TOKEN 2>/dev/null || true)" python -m serving.fetch_index \
      || echo "Could not fetch the search index at build time; it will be downloaded at startup."; \
    fi

EXPOSE 7860
CMD ["python", "run.py"]
