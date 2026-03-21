# Symbol Templates

Папка для шаблонов отдельных тикеров:

`assets/templates/mt5/symbols/<SYMBOL>.png`

Дополнительные варианты для выделенной строки:

- `assets/templates/mt5/symbols/<SYMBOL>_selected.png`
- `assets/templates/mt5/symbols/<SYMBOL>_active.png`

Примеры:

- `assets/templates/mt5/symbols/EURUSD.png`
- `assets/templates/mt5/symbols/EURUSD_selected.png`
- `assets/templates/mt5/symbols/GBPUSD.png`
- `assets/templates/mt5/symbols/XAUUSD.png`

Что сохранять:

- небольшой скрин только строки тикера в списке слева;
- без графика и без лишних соседних строк;
- лучше в том же масштабе DPI и в той же теме MT5, в которой работает бот.

Как это используется:

1. бот сначала пытается найти `symbols/<SYMBOL>.png`;
2. если обычный шаблон не сработал, бот пробует `symbols/<SYMBOL>_selected.png` и `symbols/<SYMBOL>_active.png`;
3. если шаблоны не нашли строку, бот делает fallback на OCR в `symbols_panel`.
