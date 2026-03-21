# MT5 UI templates

Сейчас visual executor умеет искать актив двумя способами:

1. `symbol_search_mode: ocr_text`
2. `symbol_search_mode: template`

Рекомендуемый режим для твоего сценария: `ocr_text`.

## Что нужно для OCR-поиска актива

Исполнитель читает текст в верхнем левом блоке MT5, где видны кнопки/строки инструментов, и двойным кликом открывает нужный символ.

Для этого нужно настроить в `config/ui_profiles.yaml`:

1. `symbol_search_mode: ocr_text`
2. `symbol_search_anchor: symbols_panel`
3. `anchors.symbols_panel.fallback_region`

`fallback_region` задается как:

```yaml
[left, top, width, height]
```

То есть тебе нужен прямоугольник, который покрывает список/кнопки активов слева сверху.

## Какие шаблоны все равно нужны

Даже при OCR-поиске символа нужны стабильные шаблоны для остальных шагов:

1. `anchors/main_window.png`
2. `anchors/file_menu.png`
3. `anchors/order_window.png`
4. `anchors/volume_field.png`
5. `anchors/stop_loss_field.png`
6. `anchors/take_profit_field.png`
7. `anchors/entry_price_field.png` если поле реально есть
8. `anchors/comment_field.png` если поле реально есть
9. `anchors/submit_button.png` или `buy_button.png` / `sell_button.png`
10. `anchors/confirmation_success.png`
11. `anchors/confirmation_error.png`
12. `anchors/account_<alias>.png` если нужно переключение аккаунтов через меню `Файл`

## Что мне от тебя нужно для точной доводки MT5

1. Скрин главного окна MT5 с видимым блоком активов слева сверху.
2. Скрин окна ордера.
3. Скрин меню `Файл` с пунктами переключения аккаунтов, если оно реально используется.
4. Путь к терминалу MT5.
5. Примерные координаты или один шаблон области `symbols_panel`, если список активов всегда в одном месте.

Если кнопки активов в твоем MT5 действительно читаются OCR нормально, отдельные файлы `symbols/<SYMBOL>.png` больше не нужны.

По умолчанию проект использует `dry_run`, поэтому отсутствие реальных шаблонов не мешает запуску тестов и базового pipeline.
