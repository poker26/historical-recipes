# Мониторинг парка Бегета в Selectel

Общий для всех проектов (ботаника, Кванты, монеты), поднят 04.11.2026.

- `docker-compose.yml`, `prometheus/`, `alertmanager/`, `blackbox/`, `tg-relay/` → `/opt/monitoring` на prod-data.
  Prometheus, Alertmanager и blackbox для внутренних проверок; сеть `supabase_default`, как у Grafana.
- `grafana/` → в `/opt/grafana/provisioning` на prod-data: источник `prometheus_selectel` и дашборд «Парк Selectel».
- `pi/` → `/opt/pi-probe` на малинке: blackbox и Prometheus-агент проверяют сайты из домашней сети и шлют
  результаты через SSH-проброс (служба `promwrite-tunnel`, пользователь `promwrite` на prod-data, ключу разрешён
  только проброс к 127.0.0.1:9090).
- node_exporter — пакет `prometheus-node-exporter` на каждом сервере, только приватный адрес :9100.

Секреты в репозиторий не входят: токен бота тревог лежит в `/opt/monitoring/alertmanager/bot_token`, адрес входа
туннеля с паролем — в `/opt/monitoring/tg-relay/proxy` (тот же, что `TELEGRAM_PROXY` ботаники).

Тревоги шлёт `tg-relay`: Alertmanager сам через вход туннеля не ходит (вход отвечает на CONNECT кадром HTTP/2).
Перечитать конфиг Prometheus: `curl -X POST 127.0.0.1:9090/-/reload` на prod-data.
