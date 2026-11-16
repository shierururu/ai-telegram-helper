FROM python:3.14.4-slim-trixie@sha256:2ca02f32b4d9d893863367ce07ec1972819f476dd38d8612f2a9cb6a41cbb727 AS source-import
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src:/app
WORKDIR /app
RUN apt-get update \
    && apt-get install --no-install-recommends -y postgresql-client-17=17.11-0+deb13u1 \
    && rm -rf /var/lib/apt/lists/*
COPY src/alpha_course/domain ./src/alpha_course/domain
COPY src/alpha_course/__init__.py ./src/alpha_course/__init__.py
COPY tools/import_source ./tools/import_source
# Docker initializes a fresh named volume with this private directory ownership.
RUN mkdir -m 0700 /corpus && chown 10001:10001 /corpus
USER 10001:10001
CMD ["python", "-m", "tools.import_source", "/source/archive.backup", "--prepare", "--output", "/corpus/corpus.json"]

FROM python:3.14.4-slim-bookworm@sha256:fc74d22ffd0d5ac395a4b7bdda75a4539758862c49ebf3005647084631e63789 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
# PDF rendering (WeasyPrint) needs Pango and Cyrillic-capable fonts. Liberation 1.07 in bookworm
# has no rouble sign U+20BD: DejaVu (in every print.css font stack) draws it instead of a box.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libpango-1.0-0 libpangoft2-1.0-0 \
        fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home app
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN mkdir /app/index && chown 10001:10001 /app/index
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "alpha_course.entrypoints.check"]

FROM runtime AS test
USER root
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements-dev.txt
COPY pyproject.toml ./
COPY Dockerfile compose.yaml ./
COPY tests ./tests
COPY tools/import_source ./tools/import_source
USER 10001:10001
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider"]
