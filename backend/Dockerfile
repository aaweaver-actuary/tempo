FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt .
COPY packages/scheduling /packages/scheduling
COPY packages/chess-core /packages/chess-core
COPY services/learning/application /services/learning/application
COPY services/learning/runtime /services/learning/runtime
COPY services/learning/remediation /services/learning/remediation
COPY contracts/content-publication /contracts/content-publication
COPY contracts/game-evidence /contracts/game-evidence
COPY database/tempo_learning_database /database/tempo_learning_database
COPY database/tempo_remediation_database /database/tempo_remediation_database
RUN pip install --no-cache-dir -r requirements.txt /packages/scheduling /packages/chess-core /services/learning/application /services/learning/remediation /services/learning/runtime /contracts/content-publication /contracts/game-evidence
# These pure source libraries share already-pinned chess/Pydantic dependencies.
# Keep their bounded adoption after the existing dependency layer so a feedback
# source change does not reinstall dependencies or rebuild optional runtimes.
COPY packages/game-review-contracts /packages/game-review-contracts
COPY contracts/analysis-jobs /contracts/analysis-jobs
COPY packages/evidence-math /packages/evidence-math
COPY packages/game-ingestion /packages/game-ingestion
COPY packages/repertoire-comparison /packages/repertoire-comparison
COPY packages/game-classification /packages/game-classification
ENV PYTHONPATH=/packages/game-review-contracts/src:/packages/evidence-math/src:/packages/game-ingestion/src:/packages/repertoire-comparison/src:/packages/game-classification/src:/contracts/analysis-jobs/src
COPY backend/app ./app
COPY backend/managed-runtime-entrypoint.sh ./managed-runtime-entrypoint.sh
COPY database/tempo_legacy_database ./tempo_legacy_database
COPY public/data/tactics-catalog.json ./public/data/tactics-catalog.json
COPY public/data/tactics-packs ./public/data/tactics-packs
ENV TEMPO_CATALOG_ROOT=/app/public
EXPOSE 8000
ENTRYPOINT ["/app/managed-runtime-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
