"""Tests for OCR normalization helpers."""

from app.domain.models import Direction
from app.services.normalization import (
    enrich_detected_fields_from_lines,
    extract_detected_fields,
    normalize_direction,
    normalize_number,
    normalize_symbol,
    select_symbol_from_lines,
)


def test_normalize_number_supports_mixed_separators() -> None:
    assert normalize_number("1,0825") == 1.0825
    assert normalize_number("1.234,56") == 1234.56
    assert normalize_number("1,234.56") == 1234.56


def test_normalize_symbol_and_direction() -> None:
    assert normalize_symbol("eur/usd") == "EURUSD"
    assert normalize_direction("покупка") == Direction.BUY
    assert normalize_direction("sell") == Direction.SELL


def test_extract_detected_fields_from_labeled_ocr() -> None:
    content = """
    Symbol: EURUSD
    Direction: Buy
    Lot: 0.10
    SL: 1.0825
    TP: 1.0890
    Entry: 1.0840
    Risk: 2
    """
    fields, confidence = extract_detected_fields(content)
    assert fields["symbol"] == "EURUSD"
    assert fields["direction"] == Direction.BUY
    assert fields["lot"] == 0.10
    assert fields["stop_loss"] == 1.0825
    assert fields["take_profit"] == 1.089
    assert fields["entry_price"] == 1.084
    assert fields["risk_percent"] == 2.0
    assert confidence.overall > 0.8


def test_enrich_detected_fields_from_calculator_lines() -> None:
    lines = [
        "ivan-dev-lab.github.io",
        "AKTNB:",
        "EUR/USD",
        "BanIOTaAeno3NTa:",
        "USD",
        "Den03NT:",
        "5000",
        "LeHa OTKpbITUA:",
        "1,14688",
        "Take Profit:",
        "1,15299",
        "StopLoss:",
        "1,14520",
        "KonN4eCTBOnOTOB(maX:4.36):",
        "0,30",
    ]
    enriched, patch = enrich_detected_fields_from_lines(lines, {})
    assert enriched["symbol"] == "EURUSD"
    assert enriched["entry_price"] == 1.14688
    assert enriched["take_profit"] == 1.15299
    assert enriched["stop_loss"] == 1.1452
    assert enriched["lot"] == 0.30
    assert enriched["direction"] == Direction.BUY
    assert patch["direction"] > 0.0


def test_select_symbol_from_lines_prefers_asset_over_header_text() -> None:
    lines = [
        "ivan-dev-lab.github.io",
        "AKTNB:",
        "EUR/USD",
    ]
    symbol, confidence = select_symbol_from_lines(lines)
    assert symbol == "EURUSD"
    assert confidence >= 0.9


def test_enrich_detected_fields_from_noisy_telegram_ocr_recovers_entry_and_direction() -> None:
    lines = [
        "ivan-dev-lab.github.io",
        "AxtuB:",
        "EUR/USD",
        "cy",
        "Bantota feno3uta:",
        "USD",
        "roy",
        "Dieno3urt:",
        "5000",
        "Uena orKpbitua:",
        "1,14827",
        "Take Profit:",
        "1,15299",
        "Stop Loss:",
        "1,14697",
        "KonuyectBo notos (max: 4.35):",
        "0,19",
    ]
    enriched, patch = enrich_detected_fields_from_lines(
        lines,
        {
            "symbol": "EURUSD",
            "direction": None,
            "entry_price": None,
            "stop_loss": 1.14697,
            "take_profit": 1.15299,
            "lot": 0.19,
            "risk_percent": None,
        },
    )

    assert enriched["entry_price"] == 1.14827
    assert enriched["direction"] == Direction.BUY
    assert patch["entry_price"] > 0.0
    assert patch["direction"] > 0.0
