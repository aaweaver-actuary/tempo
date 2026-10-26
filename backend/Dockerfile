FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt .
COPY packages/scheduling /packages/scheduling
COPY packages/chess-core /packages/chess-core
COPY packages/game-review-contracts /packages/game-review-contracts
COPY packages/evidence-math /packages/evidence-math
COPY packages/game-ingestion /packages/game-ingestion
COPY packages/repertoire-comparison /packages/repertoire-comparison
COPY packages/game-classification /packages/game-classification
COPY services/learning/application /services/learning/application
COPY services/learning/runtime /services/learning/runtime
COPY services/learning/remediation /services/learning/remediation
COPY contracts/content-publication /contracts/content-publication
COPY contracts/game-evidence /contracts/game-evidence
COPY database/tempo_learning_database /database/tempo_learning_database
COPY database/tempo_remediation_database /database/tempo_remediation_database
RUN pip install --no-cache-dir -r requirements.txt /packages/scheduling /packages/chess-core /packages/game-review-contracts /packages/evidence-math /packages/game-ingestion /packages/repertoire-comparison /packages/game-classification /services/learning/application /services/learning/remediation /services/learning/runtime /contracts/content-publication /contracts/game-evidence
COPY backend/app ./app
COPY database/tempo_legacy_database ./tempo_legacy_database
COPY public/data/tactics-catalog.json ./public/data/tactics-catalog.json
COPY public/data/tactics-packs ./public/data/tactics-packs
ENV TEMPO_CATALOG_ROOT=/app/public
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
