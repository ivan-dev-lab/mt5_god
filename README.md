# MT5_GOD

Модульная система автоматизации пайплайна:

`capture -> analyze -> execute`

Проект реализует три взаимозаменяемых адаптера:

1. `CaptureAdapter` для приема входных изображений.
2. `AnalyzerAdapter` для извлечения и валидации `trade_intent`.
3. `ExecutorAdapter` для UI-исполнения в MT5.

По умолчанию система запускается в безопасном режиме `dry_run`. Реальный UI-исполнитель для MT5 включается только через конфиг и требует установленного набора зависимостей для UI/OCR.

## Установка

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

Дополнительно:

```powershell
pip install -e .[telegram]
pip install -e .[vision]
pip install -e .[ui]
```

## Команды

```powershell
python -m app run-bot
python -m app analyze .\path\to\image.png
python -m app run-once .\path\to\image.png
python -m app execute .\examples\trade_intent.json
python -m app test-accounts
```

## Тесты

```powershell
pytest
```
