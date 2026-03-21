"""Image analyzer adapter with sidecar/Tesseract OCR support."""

from __future__ import annotations

from pathlib import Path
from statistics import mean

from app.config import AnalysisProfile, AppConfig
from app.domain.interfaces import AnalyzerAdapter
from app.domain.models import (
    AnalysisArtifacts,
    AnalysisResult,
    AnalysisStatus,
    CaptureEvent,
    DetectedFields,
    OrderType,
    TradeIntent,
    TradeIntentMeta,
    ValidationResult,
)
from app.services.logging import get_logger
from app.services.normalization import enrich_detected_fields_from_lines, extract_detected_fields
from app.storage.files import FileStorage


class HeuristicImageAnalyzer(AnalyzerAdapter):
    """Extracts trade fields from OCR text and builds a normalized trade intent."""

    def __init__(self, config: AppConfig, file_storage: FileStorage) -> None:
        self.config = config
        self.file_storage = file_storage
        self.logger = get_logger(__name__)
        self._rapidocr_engine = None

    def _profile(self) -> AnalysisProfile:
        """Return the default analysis profile."""

        return self.config.analysis_profiles.get(self.config.default_analysis_profile, AnalysisProfile())

    def _load_sidecar_text(self, image_path: Path) -> str | None:
        """Read OCR text from sidecar files if present."""

        candidates = [
            image_path.with_suffix(image_path.suffix + ".ocr.txt"),
            image_path.with_suffix(".ocr.txt"),
            image_path.with_suffix(".txt"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        return None

    def _run_tesseract(self, image_path: Path) -> str | None:
        """Run Tesseract when optional dependencies are installed."""

        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            return None

        if self.config.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.config.tesseract_cmd
        try:
            return pytesseract.image_to_string(Image.open(image_path))
        except (FileNotFoundError, pytesseract.TesseractNotFoundError):
            self.logger.warning(
                "tesseract executable not available, falling back to alternative OCR",
                extra={"event_id": str(image_path)},
            )
            return None

    def _rapidocr(self):
        """Instantiate RapidOCR lazily."""

        if self._rapidocr_engine is not None:
            return self._rapidocr_engine
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            return None
        self._rapidocr_engine = RapidOCR()
        return self._rapidocr_engine

    def _run_rapidocr(self, image_path: Path) -> tuple[str, list[str]] | None:
        """Run RapidOCR when available and return joined text plus line list."""

        engine = self._rapidocr()
        if engine is None:
            return None
        result, _ = engine(str(image_path))
        if not result:
            return "", []
        lines = [str(item[1]).strip() for item in result if len(item) >= 2 and str(item[1]).strip()]
        return "\n".join(lines), lines

    def _load_ocr_payload(self, image_path: Path) -> tuple[str, list[str]]:
        """Load OCR text from sidecar, Tesseract, or RapidOCR."""

        content = self._load_sidecar_text(image_path)
        if content:
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            return content, lines

        content = self._run_tesseract(image_path)
        if content:
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            return content, lines

        rapidocr_payload = self._run_rapidocr(image_path)
        if rapidocr_payload is not None:
            return rapidocr_payload
        return "", []

    def _resolve_order_type(self, capture_event: CaptureEvent) -> OrderType:
        """Infer order type from Telegram caption rules."""

        caption = (capture_event.metadata.caption or "").strip().lower()
        if "limit" in caption or "лимит" in caption:
            return OrderType.LIMIT
        return OrderType.MARKET

    def _build_comment(self, capture_event: CaptureEvent) -> str:
        """Derive trade comment from capture metadata."""

        caption = (capture_event.metadata.caption or "").strip()
        return caption if caption else "generated_from_image"

    def _validate(
        self,
        fields: DetectedFields,
        profile: AnalysisProfile,
        overall_confidence: float,
        field_confidence: dict[str, float],
        order_type: OrderType,
    ) -> ValidationResult:
        """Apply required field and confidence rules from the profile."""

        errors: list[str] = []
        warnings: list[str] = []

        for field_name in profile.required_fields:
            if getattr(fields, field_name) in (None, "", 0):
                errors.append(f"missing required field: {field_name}")

        if fields.lot is not None and fields.lot <= 0:
            errors.append("lot must be positive")
        if fields.stop_loss is not None and fields.stop_loss <= 0:
            errors.append("stop_loss must be positive")
        if fields.take_profit is not None and fields.take_profit <= 0:
            errors.append("take_profit must be positive")
        if order_type == OrderType.LIMIT and fields.entry_price is None:
            errors.append("entry_price is required for limit orders")

        thresholds = profile.confidence_thresholds
        if overall_confidence < thresholds.overall:
            errors.append(f"overall confidence below threshold: {overall_confidence:.2f} < {thresholds.overall:.2f}")

        for name in ("symbol", "direction", "entry_price", "stop_loss", "take_profit", "lot", "risk_percent"):
            threshold = getattr(thresholds, name)
            score = field_confidence.get(name, 0.0)
            value = getattr(fields, name)
            if value is not None and score < threshold:
                errors.append(f"{name} confidence below threshold: {score:.2f} < {threshold:.2f}")

        if fields.entry_price is None and order_type == OrderType.LIMIT:
            warnings.append("limit order requested but entry_price is not available")
        if fields.entry_price is None and order_type == OrderType.MARKET:
            warnings.append("entry_price is not available")
        if fields.risk_percent is None:
            warnings.append("risk_percent is not available")

        return ValidationResult(is_valid=not errors, errors=errors, warnings=warnings)

    def _build_trade_intent(
        self,
        event: CaptureEvent,
        fields: DetectedFields,
        overall_confidence: float,
        order_type: OrderType,
    ) -> TradeIntent:
        """Build a normalized trade intent from validated fields."""

        return TradeIntent(
            source_event_id=event.event_id,
            symbol=fields.symbol or "",
            direction=fields.direction,
            order_type=order_type,
            volume=fields.lot or 0.0,
            entry_price=fields.entry_price,
            stop_loss=fields.stop_loss,
            take_profit=fields.take_profit,
            comment=self._build_comment(event),
            accounts=[
                {"account_alias": account.account_alias, "enabled": account.enabled}
                for account in self.config.accounts
            ],
            execution_policy={
                "dry_run": self.config.mode.value == "dry_run",
                "max_retries": self.config.retry_policy.execution_per_account,
                "require_ui_confirmation": self.config.execution.require_ui_confirmation,
            },
            meta=TradeIntentMeta(
                raw_source_image=event.image_path,
                analysis_confidence=overall_confidence,
            ),
        )

    def analyze(self, capture_event: CaptureEvent) -> AnalysisResult:
        """Analyze an image and persist OCR artifacts."""

        profile = self._profile()
        order_type = self._resolve_order_type(capture_event)
        image_path = Path(capture_event.image_path)
        artifact_dir = self.file_storage.paths.analysis_dir / capture_event.event_id
        ocr_text, ocr_lines = self._load_ocr_payload(image_path)
        ocr_dump_path = self.file_storage.write_text(artifact_dir / "ocr_dump.txt", ocr_text)

        if not ocr_text.strip():
            result = AnalysisResult(
                event_id=capture_event.event_id,
                status=AnalysisStatus.FAILED,
                validation=ValidationResult(is_valid=False, errors=["ocr produced no text"]),
                artifacts=AnalysisArtifacts(ocr_dump_path=str(ocr_dump_path)),
            )
            self.file_storage.write_json(artifact_dir / "analysis_result.json", result)
            return result

        extracted, confidence = extract_detected_fields(ocr_text)
        extracted, confidence_patch = enrich_detected_fields_from_lines(ocr_lines, extracted)
        confidence_payload = confidence.model_dump(mode="json")
        for field_name, score in confidence_patch.items():
            confidence_payload["fields"][field_name] = max(confidence_payload["fields"][field_name], score)
        confidence_payload["overall"] = round(
            mean(
                [
                    confidence_payload["fields"]["symbol"],
                    confidence_payload["fields"]["direction"],
                    confidence_payload["fields"]["stop_loss"],
                    confidence_payload["fields"]["take_profit"],
                    confidence_payload["fields"]["lot"],
                ]
            ),
            4,
        )
        confidence = confidence.__class__.model_validate(confidence_payload)
        fields = DetectedFields.model_validate(extracted)
        validation = self._validate(
            fields,
            profile=profile,
            overall_confidence=confidence.overall,
            field_confidence=confidence.fields.model_dump(),
            order_type=order_type,
        )

        status = AnalysisStatus.SUCCESS
        if not validation.is_valid:
            has_any_signal = any(value is not None for value in extracted.values())
            status = AnalysisStatus.PARTIAL if has_any_signal else AnalysisStatus.FAILED

        trade_intent = (
            self._build_trade_intent(capture_event, fields, confidence.overall, order_type)
            if validation.is_valid
            else None
        )
        result = AnalysisResult(
            event_id=capture_event.event_id,
            status=status,
            detected_fields=fields,
            normalized_trade_intent=trade_intent,
            confidence=confidence,
            validation=validation,
            artifacts=AnalysisArtifacts(ocr_dump_path=str(ocr_dump_path)),
        )

        self.file_storage.write_json(artifact_dir / "analysis_result.json", result)
        if trade_intent:
            self.file_storage.write_json(artifact_dir / "trade_intent.json", trade_intent)
        return result
