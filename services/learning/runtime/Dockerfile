FROM ghcr.io/astral-sh/uv:0.12.22 AS uv
FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
COPY services/learning/application/pyproject.toml services/learning/application/pyproject.toml
COPY services/learning/application/src services/learning/application/src
COPY services/learning/remediation/pyproject.toml services/learning/remediation/pyproject.toml
COPY services/learning/remediation/src services/learning/remediation/src
COPY packages/scheduling/pyproject.toml packages/scheduling/pyproject.toml
COPY packages/scheduling/src packages/scheduling/src
COPY packages/chess-core/pyproject.toml packages/chess-core/pyproject.toml
COPY packages/chess-core/src packages/chess-core/src
COPY contracts/content-publication/pyproject.toml contracts/content-publication/pyproject.toml
COPY contracts/content-publication/src contracts/content-publication/src
COPY contracts/content-publication/schemas contracts/content-publication/schemas
COPY contracts/content-publication/fixtures contracts/content-publication/fixtures
COPY contracts/game-evidence/pyproject.toml contracts/game-evidence/pyproject.toml
COPY contracts/game-evidence/src contracts/game-evidence/src
COPY contracts/game-evidence/schemas contracts/game-evidence/schemas
COPY contracts/game-evidence/fixtures contracts/game-evidence/fixtures
COPY services/learning/runtime/pyproject.toml services/learning/runtime/uv.lock services/learning/runtime/
COPY services/learning/runtime/src services/learning/runtime/src
COPY services/learning/runtime/hatch_build.py services/learning/runtime/hatch_build.py
COPY services/learning/remediation/hatch_build.py services/learning/remediation/hatch_build.py
COPY database/tempo_learning_database database/tempo_learning_database
COPY database/tempo_remediation_database database/tempo_remediation_database
COPY services/learning/runtime/fixtures services/learning/runtime/fixtures
RUN uv sync --project services/learning/runtime --locked --no-dev --no-editable --no-cache --python /usr/local/bin/python
FROM python:3.12-slim
COPY --from=build /build/services/learning/runtime/.venv /opt/learning
ENV PATH="/opt/learning/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
WORKDIR /tmp
EXPOSE 8001
ENTRYPOINT ["/opt/learning/bin/python", "-I", "-m", "tempo_learning_runtime.cli"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8001"]
