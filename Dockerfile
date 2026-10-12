# Serves the dashboard + API. The Spark pipeline is not run in the container:
# its outputs (results/ and models/) are part of the repository.
FROM python:3.11-slim-bookworm

# Java is needed by Spark for the live prediction endpoint; procps provides `ps`,
# which Spark's launch scripts call
RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless procps \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces runs containers as uid 1000
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    HOST=0.0.0.0 \
    PORT=7860 \
    SPARK_DRIVER_MEMORY=2g
WORKDIR /home/user/app

COPY --chown=user requirements-app.txt .
RUN pip install --no-cache-dir --user -r requirements-app.txt

COPY --chown=user . .

EXPOSE 7860
CMD ["python", "run.py"]
