"""OCR parsing and normalization helpers."""

from __future__ import annotations

import re
from statistics import mean

from app.domain.models import ConfidenceFields, ConfidenceResult, Direction

SYMBOL_PATTERN = re.compile(r"\b[A-Za-z]{3}[/\-_ ]?[A-Za-z]{3}\b")

LABELS = {
    "symbol": ["symbol", "pair", "instrument", "символ", "инструмент", "пара"],
    "direction": ["direction", "side", "направление", "сделка"],
    "entry_price": ["entry", "open", "вход", "цена"],
    "stop_loss": ["stop loss", "sl", "стоп", "stop"],
    "take_profit": ["take profit", "tp", "тейк", "профит"],
    "lot": ["lot", "volume", "объем", "объём", "лот"],
    "risk_percent": ["risk", "риск"],
}

DIRECTION_MAP = {
    "buy": Direction.BUY,
    "long": Direction.BUY,
    "покупка": Direction.BUY,
    "купить": Direction.BUY,
    "sell": Direction.SELL,
    "short": Direction.SELL,
    "продажа": Direction.SELL,
    "продать": Direction.SELL,
}

LABEL_HINTS = {
    "entry_price": ("entry", "open", "otk", "otkp", "otkr", "orkp", "cena", "uena", "xena", "price"),
    "stop_loss": ("stoploss", "stop", "sl"),
    "take_profit": ("takeprofit", "take", "profit", "tp"),
    "lot": ("lot", "lotov", "max", "kolich", "quantity"),
    "risk_percent": ("risk",),
}

SYMBOL_HINTS = ("asset", "pair", "akt", "symb", "instr", "par")


def normalize_number(raw: str | None) -> float | None:
    """Normalize OCR numeric strings to `float`."""

    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    cleaned = re.sub(r"[^\d,.\-]", "", cleaned)
    if not cleaned:
        return None

    if cleaned.count(",") and cleaned.count("."):
        decimal = "," if cleaned.rfind(",") > cleaned.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        cleaned = cleaned.replace(thousands, "")
        cleaned = cleaned.replace(decimal, ".")
    elif cleaned.count(","):
        if cleaned.count(",") > 1:
            parts = cleaned.split(",")
            cleaned = "".join(parts[:-1]) + "." + parts[-1]
        else:
            cleaned = cleaned.replace(",", ".")
    elif cleaned.count(".") > 1:
        parts = cleaned.split(".")
        cleaned = "".join(parts[:-1]) + "." + parts[-1]

    try:
        return float(cleaned)
    except ValueError:
        return None


def normalize_symbol(raw: str | None) -> str | None:
    """Convert symbol variants to canonical `EURUSD`-style form."""

    if raw is None:
        return None
    match = SYMBOL_PATTERN.search(raw.upper())
    if not match:
        return None
    return re.sub(r"[^A-Z]", "", match.group(0))


def normalize_direction(raw: str | None) -> Direction | None:
    """Convert direction aliases to the domain enum."""

    if raw is None:
        return None
    return DIRECTION_MAP.get(raw.strip().lower())


def extract_labeled_text(content: str, field: str) -> tuple[str | None, float]:
    """Extract a line value by its known labels and return confidence."""

    aliases = LABELS[field]
    pattern = re.compile(
        rf"(?im)^\s*(?:{'|'.join(re.escape(alias) for alias in aliases)})\s*[:=]?\s*(.+?)\s*$"
    )
    match = pattern.search(content)
    if not match:
        return None, 0.0
    return match.group(1).strip(), 0.98


def extract_detected_fields(content: str) -> tuple[dict[str, object | None], ConfidenceResult]:
    """Extract domain fields from OCR text using labels and heuristics."""

    content = content or ""

    symbol_raw, symbol_score = extract_labeled_text(content, "symbol")
    symbol = normalize_symbol(symbol_raw)
    if symbol is None:
        fallback = SYMBOL_PATTERN.search(content.upper())
        if fallback:
            symbol = normalize_symbol(fallback.group(0))
            symbol_score = 0.75

    direction_raw, direction_score = extract_labeled_text(content, "direction")
    direction = normalize_direction(direction_raw)
    if direction is None:
        for key, value in DIRECTION_MAP.items():
            if re.search(rf"\b{re.escape(key)}\b", content, re.IGNORECASE):
                direction = value
                direction_score = 0.80
                break

    numbers: dict[str, float | None] = {}
    scores: dict[str, float] = {}
    for field in ("entry_price", "stop_loss", "take_profit", "lot", "risk_percent"):
        raw, score = extract_labeled_text(content, field)
        numbers[field] = normalize_number(raw)
        scores[field] = score if numbers[field] is not None else 0.0

    confidence_fields = ConfidenceFields(
        symbol=symbol_score if symbol else 0.0,
        direction=direction_score if direction else 0.0,
        entry_price=scores["entry_price"],
        stop_loss=scores["stop_loss"],
        take_profit=scores["take_profit"],
        lot=scores["lot"],
        risk_percent=scores["risk_percent"],
    )
    overall = mean(
        [
            confidence_fields.symbol,
            confidence_fields.direction,
            confidence_fields.stop_loss,
            confidence_fields.take_profit,
            confidence_fields.lot,
        ]
    )
    return (
        {
            "symbol": symbol,
            "direction": direction,
            "entry_price": numbers["entry_price"],
            "stop_loss": numbers["stop_loss"],
            "take_profit": numbers["take_profit"],
            "lot": numbers["lot"],
            "risk_percent": numbers["risk_percent"],
        },
        ConfidenceResult(overall=round(overall, 4), fields=confidence_fields),
    )


def simplify_label(text: str) -> str:
    """Normalize OCR label text for fuzzy substring matching."""

    return re.sub(r"[^a-z0-9]+", "", text.lower())


def extract_inline_number(text: str) -> float | None:
    """Extract the first numeric token from a line."""

    match = re.search(r"[-+]?\d[\d\s.,]*", text)
    return normalize_number(match.group(0)) if match else None


def _find_next_number(lines: list[str], start: int, window: int = 3) -> float | None:
    """Return the next numeric line after `start`."""

    for index in range(start + 1, min(len(lines), start + 1 + window)):
        number = extract_inline_number(lines[index])
        if number is not None:
            return number
    return None


def _find_label_index(lines: list[str], field_name: str) -> int | None:
    """Return the first line index whose simplified text matches field hints."""

    hints = LABEL_HINTS[field_name]
    for index, line in enumerate(lines):
        simplified = simplify_label(line.strip())
        if simplified and any(hint in simplified for hint in hints):
            return index
    return None


def _infer_entry_between_targets(
    lines: list[str],
    stop_loss: float | None,
    take_profit: float | None,
) -> tuple[float | None, float]:
    """Infer entry price from unlabeled numeric lines between stop loss and take profit."""

    if stop_loss is None or take_profit is None:
        return None, 0.0

    lower = min(stop_loss, take_profit)
    upper = max(stop_loss, take_profit)
    stop_index = _find_label_index(lines, "stop_loss")
    take_index = _find_label_index(lines, "take_profit")
    boundary = min(index for index in (stop_index, take_index) if index is not None) if any(
        index is not None for index in (stop_index, take_index)
    ) else None

    candidates: list[tuple[int, float]] = []
    for index, line in enumerate(lines):
        value = extract_inline_number(line)
        if value is None:
            continue
        if not (lower < value < upper):
            continue
        if boundary is not None and index >= boundary:
            continue
        candidates.append((index, value))

    if len(candidates) == 1:
        return candidates[0][1], 0.76
    return None, 0.0


def select_symbol_from_lines(lines: list[str]) -> tuple[str | None, float]:
    """Select the most plausible trading symbol from OCR lines."""

    best_symbol: str | None = None
    best_score = float("-inf")
    for index, line in enumerate(lines):
        symbol = normalize_symbol(line)
        if not symbol:
            continue

        stripped = line.strip()
        score = 0.0
        if any(separator in stripped for separator in ("/", "_", "-", " ")):
            score += 2.0
        if "." in stripped:
            score -= 3.0
        if len(stripped) > 12:
            score -= 1.0
        if stripped.upper() == stripped:
            score += 1.0
        if index > 0:
            previous = simplify_label(lines[index - 1])
            if any(hint in previous for hint in SYMBOL_HINTS):
                score += 3.0

        if score > best_score:
            best_score = score
            best_symbol = symbol

    if best_symbol is None:
        return None, 0.0
    confidence = 0.92 if best_score >= 4.0 else 0.80 if best_score >= 2.0 else 0.70
    return best_symbol, confidence


def enrich_detected_fields_from_lines(
    lines: list[str],
    detected_fields: dict[str, object | None],
) -> tuple[dict[str, object | None], dict[str, float]]:
    """Fill missing fields from OCR line order and fuzzy label hints."""

    enriched = dict(detected_fields)
    confidence_patch: dict[str, float] = {}

    best_symbol, best_symbol_confidence = select_symbol_from_lines(lines)
    if best_symbol is not None and (
        enriched.get("symbol") is None or best_symbol_confidence >= 0.80
    ):
        enriched["symbol"] = best_symbol
        confidence_patch["symbol"] = best_symbol_confidence

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        lowered = stripped.lower()
        if enriched.get("direction") is None:
            direction = normalize_direction(lowered)
            if direction:
                enriched["direction"] = direction
                confidence_patch["direction"] = 0.90

        simplified = simplify_label(stripped)
        for field_name, hints in LABEL_HINTS.items():
            if enriched.get(field_name) is not None:
                continue
            if not any(hint in simplified for hint in hints):
                continue
            next_value = _find_next_number(lines, index)
            value = next_value if field_name == "lot" and next_value is not None else extract_inline_number(stripped)
            if value is None:
                value = next_value
            if value is None:
                continue
            enriched[field_name] = value
            confidence_patch[field_name] = 0.78 if field_name in {"entry_price", "lot"} else 0.88

    if enriched.get("entry_price") is None:
        inferred_entry, inferred_confidence = _infer_entry_between_targets(
            lines,
            enriched.get("stop_loss"),
            enriched.get("take_profit"),
        )
        if inferred_entry is not None:
            enriched["entry_price"] = inferred_entry
            confidence_patch["entry_price"] = inferred_confidence

    if enriched.get("direction") is None:
        entry = enriched.get("entry_price")
        take_profit = enriched.get("take_profit")
        stop_loss = enriched.get("stop_loss")
        if all(value is not None for value in (entry, take_profit, stop_loss)):
            if take_profit > entry > stop_loss:
                enriched["direction"] = Direction.BUY
                confidence_patch["direction"] = 0.72
            elif take_profit < entry < stop_loss:
                enriched["direction"] = Direction.SELL
                confidence_patch["direction"] = 0.72

    return enriched, confidence_patch
