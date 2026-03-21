# Telegram and Runtime Config

Основная настройка лежит в `config/app.yaml`.

Для Telegram важны поля:

```yaml
telegram:
  enabled: true
  allowed_chat_id: -1001234567890
  allowed_thread_id: 777
  download_dir: data/inbox
  send_status_updates: false
```

Где что задавать:

1. `allowed_chat_id` — id группы / супергруппы Telegram.
2. `allowed_thread_id` — id ветки / топика внутри группы.
3. `enabled` — включает polling бота.

Для ссылки вида:

```text
https://t.me/c/2944501825/548/555
```

значения такие:

1. `allowed_chat_id = -1002944501825`
2. `allowed_thread_id = 548`

Приложение теперь умеет принимать и сокращенный вариант `2944501825` и само приводит его к `-1002944501825`.

## Через `.env`

То же самое можно переопределить без правки YAML:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ENABLED=true
TELEGRAM_ALLOWED_CHAT_ID=-1001234567890
TELEGRAM_ALLOWED_THREAD_ID=777
TELEGRAM_SEND_STATUS_UPDATES=false
```

Приоритет такой:

1. `.env`
2. `config/app.yaml`

## Минимум для запуска бота

Нужно задать:

1. `TELEGRAM_BOT_TOKEN`
2. `telegram.enabled: true` или `TELEGRAM_ENABLED=true`
3. `allowed_chat_id` или `TELEGRAM_ALLOWED_CHAT_ID`

`allowed_thread_id` можно оставить пустым, если у группы нет топиков или фильтрация по ветке не нужна.
