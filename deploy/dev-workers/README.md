# Воркеры Temporal ботаники на dev

С 04.11.2026 (Selectel) Temporal и воркеры ботаники живут на dev (`176.53.242.27`, приватный
`192.168.0.3`), в `/opt/hr-workers`: рядом с этим compose лежат каталог `backend` из рабочей
копии и `.env`.

`.env` это копия прод-`.env` с prod-app, в которой переопределены три адреса:

- `TEMPORAL_ADDRESS=192.168.0.3:7233` — Temporal на этой же машине (`/opt/temporal`);
- `INTERNAL_API_URL=http://192.168.0.1:8126` — бэкенд через nginx ботаники по приватной сети;
- `SITE_INTERNAL_URL=https://botanik.fun` — сайт на prod-app слушает только localhost.

База, Qdrant и MinIO уже указаны приватными адресами prod-data, BGE-M3 работает на самом dev.
Обновить код: залить свежий `backend`, затем `docker compose build && docker compose up -d`.
