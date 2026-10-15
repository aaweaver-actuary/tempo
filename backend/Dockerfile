FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt .
COPY packages/scheduling /packages/scheduling
COPY packages/chess-core /packages/chess-core
COPY services/learning/application /services/learning/application
COPY services/learning/runtime /services/learning/runtime
COPY contracts/content-publication /contracts/content-publication
COPY contracts/game-evidence /contracts/game-evidence
RUN pip install --no-cache-dir -r requirements.txt /packages/scheduling /packages/chess-core /services/learning/application /services/learning/runtime /contracts/content-publication /contracts/game-evidence
COPY backend/app ./app
COPY public/data/tactics-catalog.json ./public/data/tactics-catalog.json
COPY public/data/tactics-packs ./public/data/tactics-packs
ENV TEMPO_CATALOG_ROOT=/app/public
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
