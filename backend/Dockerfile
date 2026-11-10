FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt .
COPY packages/scheduling /packages/scheduling
COPY packages/chess-core /packages/chess-core
COPY packages/evidence-math /packages/evidence-math
COPY services/learning/application /services/learning/application
COPY contracts/game-evidence /contracts/game-evidence
RUN pip install --no-cache-dir -r requirements.txt /packages/scheduling /packages/chess-core /packages/evidence-math /services/learning/application /contracts/game-evidence
# These pure source libraries share already-pinned chess/Pydantic dependencies.
# Keep their bounded adoption after the existing dependency layer so a feedback
# source change does not reinstall dependencies or rebuild optional runtimes.
COPY packages/game-review-contracts /packages/game-review-contracts
ENV PYTHONPATH=/packages/game-review-contracts/src:/packages/evidence-math/src
COPY backend/app ./app
COPY database/tempo_storage ./tempo_storage
COPY public/data/tactics-catalog.json ./public/data/tactics-catalog.json
COPY public/data/tactics-packs ./public/data/tactics-packs
ENV TEMPO_CATALOG_ROOT=/app/public
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
