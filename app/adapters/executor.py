"""MT5 executor adapters."""

from __future__ import annotations

from collections import namedtuple
from difflib import SequenceMatcher
import importlib.util
import os
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from app.config import AccountConfig, AppConfig
from app.domain.exceptions import ConfigurationError, UiAutomationError
from app.domain.interfaces import ExecutorAdapter
from app.domain.models import (
    AccountExecutionResult,
    AccountSwitchTestResult,
    ExecutionResult,
    ExecutionStatus,
    OrderType,
    TradeIntent,
    utc_now,
)
from app.services.logging import get_logger
from app.services.normalization import normalize_symbol
from app.storage.files import FileStorage

ScreenBox = namedtuple("ScreenBox", ["left", "top", "width", "height"])


def find_symbol_token_match(tokens: list[dict[str, object]], symbol: str) -> dict[str, int] | None:
    """Return a bounding box for the target symbol from OCR tokens."""

    target = normalize_symbol(symbol)
    if not target:
        return None

    for start in range(len(tokens)):
        for width in range(1, 4):
            chunk = tokens[start : start + width]
            if len(chunk) != width:
                continue
            identity = {
                (
                    int(token["block_num"]),
                    int(token["par_num"]),
                    int(token["line_num"]),
                )
                for token in chunk
            }
            if len(identity) != 1:
                continue
            candidate = normalize_symbol("".join(str(token["text"]) for token in chunk))
            if candidate != target:
                continue
            left = min(int(token["left"]) for token in chunk)
            top = min(int(token["top"]) for token in chunk)
            right = max(int(token["left"]) + int(token["width"]) for token in chunk)
            bottom = max(int(token["top"]) + int(token["height"]) for token in chunk)
            return {
                "left": left,
                "top": top,
                "width": right - left,
                "height": bottom - top,
                "center_x": left + (right - left) // 2,
                "center_y": top + (bottom - top) // 2,
            }
    return None


def normalize_account_login(raw: str | None) -> str:
    """Normalize an account login to digits-only form."""

    return "".join(character for character in str(raw or "") if character.isdigit())


def find_account_login_token_match(tokens: list[dict[str, object]], account_login: str) -> dict[str, int] | None:
    """Return a bounding box for the target account login from OCR tokens."""

    target = normalize_account_login(account_login)
    if not target:
        return None

    for start in range(len(tokens)):
        for width in range(1, 4):
            chunk = tokens[start : start + width]
            if len(chunk) != width:
                continue
            identity = {
                (
                    int(token["block_num"]),
                    int(token["par_num"]),
                    int(token["line_num"]),
                )
                for token in chunk
            }
            if len(identity) != 1:
                continue
            candidate = normalize_account_login("".join(str(token["text"]) for token in chunk))
            if candidate != target:
                continue
            left = min(int(token["left"]) for token in chunk)
            top = min(int(token["top"]) for token in chunk)
            right = max(int(token["left"]) + int(token["width"]) for token in chunk)
            bottom = max(int(token["top"]) + int(token["height"]) for token in chunk)
            return {
                "left": left,
                "top": top,
                "width": right - left,
                "height": bottom - top,
                "center_x": left + (right - left) // 2,
                "center_y": top + (bottom - top) // 2,
            }
    return None


def focused_region(region: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Return a narrower Market Watch ROI that excludes most of the chart area."""

    left, top, width, height = region
    return left, top, min(width, 220), min(height, 460)


def format_exception(exc: Exception) -> str:
    """Return a non-empty exception message for diagnostics."""

    message = str(exc).strip()
    return message or exc.__class__.__name__


def normalize_ui_text(raw: str | None) -> str:
    """Normalize OCR UI text for fuzzy matching."""

    return re.sub(r"[^0-9a-zа-я]+", "", str(raw or "").lower().replace("ё", "е"))


def find_text_token_match(tokens: list[dict[str, object]], target_texts: list[str], *, minimum_score: float = 0.6) -> dict[str, int] | None:
    """Return the best OCR token box for a short UI label."""

    targets = [normalize_ui_text(text) for text in target_texts if normalize_ui_text(text)]
    if not targets:
        return None

    best_match: dict[str, int] | None = None
    best_score = 0.0
    for start in range(len(tokens)):
        for width in range(1, 7):
            chunk = tokens[start : start + width]
            if len(chunk) != width:
                continue
            identity = {
                (
                    int(token.get("block_num", 0)),
                    int(token.get("par_num", 0)),
                    int(token.get("line_num", 0)),
                )
                for token in chunk
            }
            if len(identity) != 1:
                continue
            candidate = normalize_ui_text("".join(str(token["text"]) for token in chunk))
            if not candidate:
                continue
            for target in targets:
                if candidate == target:
                    score = 1.0
                elif candidate in target or target in candidate:
                    score = 0.9
                else:
                    score = SequenceMatcher(None, candidate, target).ratio()
                if score < minimum_score or score <= best_score:
                    continue
                left = min(int(token["left"]) for token in chunk)
                top = min(int(token["top"]) for token in chunk)
                right = max(int(token["left"]) + int(token["width"]) for token in chunk)
                bottom = max(int(token["top"]) + int(token["height"]) for token in chunk)
                best_score = score
                best_match = {
                    "left": left,
                    "top": top,
                    "width": right - left,
                    "height": bottom - top,
                    "center_x": left + (right - left) // 2,
                    "center_y": top + (bottom - top) // 2,
                }
    return best_match


class DryRunMt5Executor(ExecutorAdapter):
    """Simulates MT5 execution without touching the terminal UI."""

    def __init__(self, config: AppConfig, file_storage: FileStorage) -> None:
        self.config = config
        self.file_storage = file_storage
        self.logger = get_logger(__name__)

    def execute(self, trade_intent: TradeIntent) -> ExecutionResult:
        """Produce a synthetic execution trace for every enabled account."""

        started_at = utc_now()
        account_results: list[AccountExecutionResult] = []
        execution_dir = self.file_storage.paths.execution_dir / trade_intent.trade_id

        for account in trade_intent.accounts:
            status = ExecutionStatus.SKIPPED if trade_intent.execution_policy.dry_run else ExecutionStatus.SUCCESS
            technical_log = [
                "launch terminal skipped in dry-run",
                f"validated symbol={trade_intent.symbol}",
                f"prepared direction={trade_intent.direction.value}",
                f"prepared volume={trade_intent.volume}",
                "order submission skipped",
            ]
            account_results.append(
                AccountExecutionResult(
                    account_alias=account.account_alias,
                    status=status,
                    order_submit_detected=False,
                    technical_log=technical_log,
                )
            )

        result = ExecutionResult(
            trade_id=trade_intent.trade_id,
            status="success",
            started_at=started_at,
            finished_at=utc_now(),
            accounts=account_results,
        )
        self.file_storage.write_json(execution_dir / "execution_result.json", result)
        return result

    def test_accounts(self) -> AccountSwitchTestResult:
        """Produce a synthetic account-switching trace without touching MT5."""

        started_at = utc_now()
        run_id = str(uuid4())
        account_results: list[AccountExecutionResult] = []
        execution_dir = self.file_storage.paths.execution_dir / run_id

        for account in (account for account in self.config.accounts if account.enabled):
            technical_log = [
                "launch terminal skipped in dry-run",
                f"switch account {account.account_alias}",
                "account switch method dry_run",
            ]
            account_results.append(
                AccountExecutionResult(
                    account_alias=account.account_alias,
                    status=ExecutionStatus.SKIPPED,
                    order_submit_detected=False,
                    technical_log=technical_log,
                )
            )

        result = AccountSwitchTestResult(
            run_id=run_id,
            status="success",
            started_at=started_at,
            finished_at=utc_now(),
            accounts=account_results,
        )
        self.file_storage.write_json(execution_dir / "account_switch_test.json", result)
        return result


class VisualMt5Executor(ExecutorAdapter):
    """Template-driven MT5 UI executor with step-by-step verification hooks."""

    def __init__(self, config: AppConfig, file_storage: FileStorage) -> None:
        self.config = config
        self.file_storage = file_storage
        self.logger = get_logger(__name__)
        self.ui_profile = config.ui_profiles.get(config.default_ui_profile)
        if self.ui_profile is None:
            raise ConfigurationError(f"missing UI profile: {config.default_ui_profile}")
        if not config.execution.mt5_terminal_path:
            raise ConfigurationError("visual executor requires execution.mt5_terminal_path")
        self._validate_runtime_dependencies()

    def _validate_runtime_dependencies(self) -> None:
        """Fail fast when required UI automation dependencies are missing."""

        missing: list[str] = []
        if importlib.util.find_spec("pyautogui") is None:
            missing.append("pyautogui")
        requires_ocr = self.ui_profile.symbol_search_mode == "ocr_text"
        if requires_ocr:
            if importlib.util.find_spec("pytesseract") is None:
                missing.append("pytesseract")
            elif not self.config.tesseract_cmd:
                missing.append("tesseract.exe")
        if missing:
            joined = ", ".join(missing)
            raise ConfigurationError(
                f"visual executor requires installed UI dependencies: {joined}. "
                "Run `python -m pip install -e .[ui]` and set `TESSERACT_CMD` if needed."
            )

    def _pyautogui(self):
        """Import `pyautogui` lazily so dry-run mode stays lightweight."""

        try:
            import pyautogui
        except ImportError as exc:
            raise ConfigurationError("Missing `pyautogui`. Run `python -m pip install -e .[ui]`.") from exc
        return pyautogui

    def _pytesseract(self):
        """Import `pytesseract` lazily for OCR-assisted UI search."""

        try:
            import pytesseract
        except ImportError as exc:
            raise ConfigurationError("Missing `pytesseract`. Run `python -m pip install -e .[ui]`.") from exc
        if self.config.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.config.tesseract_cmd
        return pytesseract

    def _save_screenshot(self, trade_id: str, name: str) -> str | None:
        """Save a screenshot for diagnostics when possible."""

        try:
            pyautogui = self._pyautogui()
        except ConfigurationError:
            return None
        target = self.file_storage.paths.screenshots_dir / trade_id / f"{name}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            pyautogui.screenshot(str(target))
        except Exception as exc:
            self.logger.warning("screenshot capture failed", extra={"event_id": name, "status": format_exception(exc)})
            return None
        return str(target)

    def _anchor_candidates(self, anchor_name: str) -> list[Path]:
        """Return possible template paths for an anchor, including backward-compatible fallbacks."""

        anchor = self.ui_profile.anchors.get(anchor_name)
        if anchor is None or not anchor.template:
            return []
        configured = self.ui_profile.templates_dir / anchor.template
        candidates = [configured]
        basename_candidate = self.ui_profile.templates_dir / Path(anchor.template).name
        if basename_candidate not in candidates:
            candidates.append(basename_candidate)
        default_name_candidate = self.ui_profile.templates_dir / f"{anchor_name}.png"
        if default_name_candidate not in candidates:
            candidates.append(default_name_candidate)
        for base_candidate in list(candidates):
            stem = base_candidate.stem
            suffix = base_candidate.suffix or ".png"
            for variant in ("_idle", "_active", "_selected"):
                variant_candidate = base_candidate.with_name(f"{stem}{variant}{suffix}")
                if variant_candidate not in candidates:
                    candidates.append(variant_candidate)
        return candidates

    def _anchor_exists(self, anchor_name: str) -> bool:
        """Return whether an anchor template exists on disk."""

        return any(candidate.exists() for candidate in self._anchor_candidates(anchor_name))

    def _anchor_path(self, anchor_name: str) -> Path:
        """Resolve a configured anchor template path."""

        anchor = self.ui_profile.anchors.get(anchor_name)
        if anchor is None or not anchor.template:
            raise UiAutomationError(f"missing anchor template: {anchor_name}")
        for candidate in self._anchor_candidates(anchor_name):
            if candidate.exists():
                return candidate
        raise UiAutomationError(f"template file not found for anchor {anchor_name}: {anchor.template}")

    def _anchor_region(self, anchor_name: str) -> tuple[int, int, int, int]:
        """Resolve a screen region either from template match or configured fallback coordinates."""

        anchor = self.ui_profile.anchors.get(anchor_name)
        if anchor is None:
            raise UiAutomationError(f"missing anchor configuration: {anchor_name}")
        if anchor.fallback_region:
            left, top, width, height = anchor.fallback_region
            return int(left), int(top), int(width), int(height)
        box = self._locate_anchor(anchor_name)
        return int(box.left), int(box.top), int(box.width), int(box.height)

    def _locate_anchor(self, anchor_name: str):
        """Find an anchor on screen using template matching."""

        pyautogui = self._pyautogui()
        anchor = self.ui_profile.anchors.get(anchor_name)
        if anchor is None:
            raise UiAutomationError(f"missing anchor configuration: {anchor_name}")
        template_candidates = self._anchor_candidates(anchor_name)
        attempts = max(1, self.config.retry_policy.ui_lookup + 1)
        for attempt in range(1, attempts + 1):
            for template_path in template_candidates:
                if not template_path.exists():
                    continue
                try:
                    location = pyautogui.locateOnScreen(str(template_path), confidence=self.ui_profile.confidence_threshold)
                except Exception as exc:
                    if exc.__class__.__name__ == "ImageNotFoundException":
                        location = None
                    elif isinstance(exc, OSError):
                        raise UiAutomationError(
                            f"screen grab failed while locating anchor {anchor_name}: {format_exception(exc)}"
                        ) from exc
                    else:
                        raise UiAutomationError(
                            f"locateOnScreen failed for anchor {anchor_name}: {format_exception(exc)}"
                        ) from exc
                if location is not None:
                    return location
            if attempt < attempts:
                self._activate_terminal_window(timeout_seconds=1.5)
                time.sleep(0.5)
        if anchor.fallback_region:
            left, top, width, height = anchor.fallback_region
            return ScreenBox(left=int(left), top=int(top), width=int(width), height=int(height))
        raise UiAutomationError(f"anchor not found: {anchor_name}")

    def _click_anchor(self, anchor_name: str, *, double: bool = False) -> None:
        """Click or double-click an anchor."""

        pyautogui = self._pyautogui()
        box = self._locate_anchor(anchor_name)
        center = pyautogui.center(box)
        if double:
            self._double_click_screen_point(center.x, center.y)
        else:
            pyautogui.click(center.x, center.y)

    def _double_click_screen_point(self, x: int, y: int) -> None:
        """Perform an explicit double click at screen coordinates."""

        pyautogui = self._pyautogui()
        pyautogui.moveTo(x, y, duration=0.1)
        original_pause = getattr(pyautogui, "PAUSE", None)
        if original_pause is not None:
            pyautogui.PAUSE = 0
        try:
            pyautogui.click(x, y, clicks=2, interval=0.04)
        finally:
            if original_pause is not None:
                pyautogui.PAUSE = original_pause
        time.sleep(0.2)

    def _type_into_anchor(self, anchor_name: str, value: str) -> None:
        """Focus a field anchor, clear it, and type a value."""

        pyautogui = self._pyautogui()
        self._click_anchor(anchor_name, double=True)
        time.sleep(0.15)
        self._clear_active_input(pyautogui)
        time.sleep(0.05)
        pyautogui.write(value, interval=0.02)

    def _send_ctrl_a_native(self) -> None:
        """Select all text in the active control using native Windows key events."""

        import ctypes

        user32 = ctypes.windll.user32
        vk_control = 0x11
        vk_a = 0x41
        keyup = 0x0002
        user32.keybd_event(vk_control, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(vk_a, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(vk_a, 0, keyup, 0)
        user32.keybd_event(vk_control, 0, keyup, 0)

    def _clear_active_input(self, pyautogui) -> None:
        """Clear the currently focused input field aggressively."""

        pyautogui.hotkey("ctrl", "a")
        self._send_ctrl_a_native()
        time.sleep(0.05)
        pyautogui.press("backspace")
        time.sleep(0.05)
        pyautogui.hotkey("ctrl", "a")
        self._send_ctrl_a_native()
        time.sleep(0.05)
        pyautogui.press("delete")
        time.sleep(0.05)

    def _set_clipboard_text(self, value: str) -> None:
        """Set Unicode text into the Windows clipboard."""

        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        cfh_unicode_text = 13
        gmem_moveable = 0x0002
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.restype = wintypes.HGLOBAL

        opened = False
        for _ in range(5):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.1)
        if not opened:
            raise UiAutomationError("failed to open clipboard for password paste")
        try:
            if not user32.EmptyClipboard():
                raise UiAutomationError("failed to clear clipboard for password paste")

            data = value + "\x00"
            size = len(data.encode("utf-16-le"))
            handle = kernel32.GlobalAlloc(gmem_moveable, size)
            if not handle:
                raise UiAutomationError("failed to allocate clipboard buffer")

            locked = kernel32.GlobalLock(handle)
            if not locked:
                kernel32.GlobalFree(handle)
                raise UiAutomationError("failed to lock clipboard buffer")
            try:
                ctypes.memmove(locked, data.encode("utf-16-le"), size)
            finally:
                kernel32.GlobalUnlock(handle)

            if not user32.SetClipboardData(cfh_unicode_text, handle):
                kernel32.GlobalFree(handle)
                raise UiAutomationError("failed to set clipboard data")
        finally:
            user32.CloseClipboard()

    def _send_ctrl_v_native(self) -> None:
        """Paste clipboard contents using native Windows keyboard events."""

        import ctypes

        user32 = ctypes.windll.user32
        vk_control = 0x11
        vk_v = 0x56
        keyup = 0x0002
        user32.keybd_event(vk_control, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(vk_v, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(vk_v, 0, keyup, 0)
        user32.keybd_event(vk_control, 0, keyup, 0)

    def _paste_into_anchor(self, anchor_name: str, value: str) -> None:
        """Focus a field anchor, clear it, and paste a value via clipboard."""

        pyautogui = self._pyautogui()
        self._click_anchor(anchor_name, double=True)
        time.sleep(0.1)
        self._clear_active_input(pyautogui)
        self._set_clipboard_text(value)
        time.sleep(0.1)
        self._send_ctrl_v_native()
        time.sleep(0.1)

    def _paste_into_anchor_if_available(self, anchor_name: str, value: str, *, required: bool = True) -> None:
        """Paste into a required or optional field anchor."""

        if not self._anchor_exists(anchor_name):
            if required:
                raise UiAutomationError(f"missing template for required field: {anchor_name}")
            self.logger.info("optional anchor skipped", extra={"event_id": anchor_name})
            return
        self._paste_into_anchor(anchor_name, value)

    def _launch_terminal(self) -> None:
        """Launch MT5 via `os.startfile` as required by the spec."""

        os.startfile(self.config.execution.mt5_terminal_path)
        time.sleep(2.0)

    def _shutdown_terminal(self) -> None:
        """Force-close MT5 so the next account starts from a fresh terminal session."""

        executable_name = Path(self.config.execution.mt5_terminal_path).name
        result = subprocess.run(
            ["taskkill", "/IM", executable_name, "/F", "/T"],
            capture_output=True,
            text=True,
            check=False,
        )
        output = " ".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip()).lower()
        if result.returncode != 0 and "not found" not in output and "не найден" not in output:
            raise UiAutomationError(
                f"terminal shutdown failed: {output or f'taskkill exit code {result.returncode}'}"
            )
        time.sleep(1.5)

    def _window_title_hints(self) -> list[str]:
        """Build likely MT5 window-title hints from the configured terminal path."""

        hints = ["MetaTrader"]
        terminal_path = self.config.execution.mt5_terminal_path
        if terminal_path:
            parent_name = Path(terminal_path).parent.name
            hints.extend([parent_name, Path(terminal_path).stem])
            for token in parent_name.replace("-", " ").replace("_", " ").split():
                normalized = token.strip()
                if len(normalized) >= 4 and normalized.lower() not in {"terminal", "program", "files", "mt5"}:
                    hints.append(normalized)

        unique: list[str] = []
        seen: set[str] = set()
        for hint in hints:
            lowered = hint.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            unique.append(hint)
        return unique

    def _window_match_score(self, title: str) -> int:
        """Score how likely a window title belongs to the target MT5 terminal."""

        lowered = title.lower()
        if "visual studio code" in lowered:
            return 0
        score = 0
        for hint in self._window_title_hints():
            normalized_hint = hint.lower()
            if normalized_hint and normalized_hint in lowered:
                score += 100 if normalized_hint == "metatrader" else 40
        if "fundingpips" in lowered:
            score += 60
        if "demo-счет" in lowered or "hedge" in lowered:
            score += 20
        return score

    def _activate_terminal_window(self, timeout_seconds: float = 12.0) -> bool:
        """Bring the MT5 window to the foreground when possible."""

        try:
            import pygetwindow as gw
        except ImportError:
            return False

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            candidates: list[tuple[int, object, str]] = []
            for window in gw.getAllWindows():
                title = (getattr(window, "title", "") or "").strip()
                if not title:
                    continue
                score = self._window_match_score(title)
                if score <= 0:
                    continue
                candidates.append((score, window, title))
            for _, window, title in sorted(candidates, key=lambda item: item[0], reverse=True):
                try:
                    if getattr(window, "isMinimized", False):
                        window.restore()
                    try:
                        window.maximize()
                    except Exception:
                        pass
                    window.activate()
                    time.sleep(0.8)
                    self.logger.info("mt5 window activated", extra={"event_id": title})
                    return True
                except Exception:
                    continue
            time.sleep(0.5)
        return False

    def _verify_terminal_ready(self) -> None:
        """Ensure the MT5 window is on top before template/OCR work starts."""

        activated = self._activate_terminal_window()
        if self._anchor_exists("main_window"):
            try:
                self._locate_anchor("main_window")
                return
            except UiAutomationError:
                if activated:
                    self.logger.warning("main window template not found after activation; proceeding with active MT5 window")
                    return
                raise
        if not activated:
            raise UiAutomationError("MT5 window was not activated")

    def _configured_account(self, account_alias: str) -> AccountConfig | None:
        """Return the configured account for a trade target alias."""

        for account in self.config.accounts:
            if account.account_alias == account_alias:
                return account
        return None

    def _wait_for_anchor(self, anchor_name: str, timeout_seconds: float = 3.0) -> bool:
        """Wait for an anchor to become visible."""

        if not self._anchor_exists(anchor_name):
            return False
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                self._locate_anchor(anchor_name)
                return True
            except UiAutomationError:
                time.sleep(0.25)
        return False

    def _wait_for_anchor_to_close(self, anchor_name: str, timeout_seconds: float = 6.0) -> bool:
        """Wait for an anchor to disappear from screen."""

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                self._locate_anchor(anchor_name)
            except UiAutomationError:
                return True
            time.sleep(0.25)
        return False

    @staticmethod
    def _region_from_box(box, ratios: list[float]) -> tuple[int, int, int, int]:
        """Build a relative ROI inside a located anchor box."""

        if len(ratios) != 4:
            raise UiAutomationError(f"relative region must contain 4 values, got: {ratios}")
        left = int(box.left + box.width * ratios[0])
        top = int(box.top + box.height * ratios[1])
        width = int(box.width * ratios[2])
        height = int(box.height * ratios[3])
        return left, top, width, height

    def _click_menu_label_by_ocr(self, label: str | None = None, aliases: tuple[str, ...] = ()) -> str:
        """Click the configured top-menu label by OCR inside the configured menu-bar ROI."""

        region = tuple(int(value) for value in self.ui_profile.top_menu_region)
        labels = self.ui_profile.top_menu_labels or ([label, *aliases] if label else ["File"])
        self._click_text_in_region_by_ocr(
            region,
            labels,
            languages=self.ui_profile.top_menu_ocr_languages,
            minimum_score=0.5,
            description="top menu label",
        )
        return "ocr_top_menu"

    def _open_file_menu(self) -> str:
        """Open the `Файл` menu, preferring OCR over template matching."""

        last_error: UiAutomationError | None = None
        for method_name, action in (
            ("ocr_file_menu", lambda: self._click_menu_label_by_ocr("Файл", aliases=("Фаи",))),
            ("template_file_menu", lambda: self._click_anchor("file_menu")),
        ):
            try:
                action()
                if self._wait_for_anchor("file_menu_opened", timeout_seconds=1.5):
                    return method_name
            except UiAutomationError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise UiAutomationError("file menu could not be opened")

    def _click_text_in_region_by_ocr(
        self,
        region: tuple[int, int, int, int],
        labels: list[str],
        *,
        languages: list[str] | None = None,
        minimum_score: float = 0.6,
        description: str,
    ) -> str:
        """Click a text label by OCR inside the provided region."""

        pyautogui = self._pyautogui()
        tokens = self._ocr_tokens_from_region(region, languages=languages)
        match = find_text_token_match(tokens, labels, minimum_score=minimum_score)
        if match is None:
            visible = ", ".join(str(token["text"]) for token in tokens[:12])
            requested = ", ".join(labels)
            raise UiAutomationError(f"{description} not found by OCR: {requested}; visible text: {visible}")
        pyautogui.click(region[0] + match["center_x"], region[1] + match["center_y"])
        return description

    def _click_connect_account_menu_item_by_ocr(self) -> str:
        """Click the configured connect-account item in the opened File menu."""

        region = self._anchor_region("file_menu_connect_account")
        labels = self.ui_profile.connect_account_menu_labels or ["Login to Trade Account"]
        self._click_text_in_region_by_ocr(
            region,
            labels,
            languages=self.ui_profile.connect_account_menu_ocr_languages,
            minimum_score=0.45,
            description="connect-account menu item",
        )
        return "ocr_connect_account_menu_item"

    def _select_account_login(self, account_login: str) -> None:
        """Open the login dropdown, OCR available accounts, and select the requested login."""

        pyautogui = self._pyautogui()
        connect_dialog = self._locate_anchor("connect_dialog")
        login_field_region = self._region_from_box(connect_dialog, self.ui_profile.account_login_field_ratio)
        dropdown_region = self._region_from_box(connect_dialog, self.ui_profile.account_dropdown_region_ratio)

        pyautogui.click(login_field_region[0] + login_field_region[2] // 2, login_field_region[1] + login_field_region[3] // 2)
        time.sleep(0.2)
        pyautogui.hotkey("alt", "down")
        time.sleep(0.3)

        tokens = self._ocr_tokens_from_region(dropdown_region)
        match = find_account_login_token_match(tokens, account_login)
        if match is None:
            visible = ", ".join(str(token["text"]) for token in tokens[:16])
            raise UiAutomationError(f"account login not found by OCR: {account_login}; visible text: {visible}")

        pyautogui.click(dropdown_region[0] + match["center_x"], dropdown_region[1] + match["center_y"])
        time.sleep(0.25)
        pyautogui.press("enter")
        if not self._wait_for_anchor_to_close("connect_dialog", timeout_seconds=8.0):
            raise UiAutomationError("connect dialog did not close after selecting account")
        time.sleep(1.0)
        self._verify_terminal_ready()

    def _switch_account(self, account_alias: str, *, assume_current: bool = False) -> str:
        """Switch account through the MT5 connect-account dialog."""

        account = self._configured_account(account_alias)
        account_login = account.account_login if account else None
        current_account_login = self._current_account_login_from_title()
        target_account_login = normalize_account_login(account_login)
        if current_account_login and target_account_login and current_account_login == target_account_login:
            self.logger.info("target MT5 account already active", extra={"event_id": account_alias, "status": target_account_login})
            return f"already_on_account:{target_account_login}"
        if assume_current and not account_login:
            self.logger.info("using current terminal account", extra={"event_id": account_alias})
            return "assume_current_account"
        if not account_login:
            raise UiAutomationError(f"account login is not configured for alias: {account_alias}")
        required_anchors = ["file_menu_connect_account", "connect_dialog"]
        if not all(self._anchor_exists(anchor_name) for anchor_name in required_anchors):
            missing = ", ".join(anchor_name for anchor_name in required_anchors if not self._anchor_exists(anchor_name))
            raise UiAutomationError(f"account switching template missing: {missing}")

        menu_method = self._open_file_menu()
        connect_menu_method = "template_connect_account_menu_item"
        try:
            connect_menu_method = self._click_connect_account_menu_item_by_ocr()
        except UiAutomationError:
            self._click_anchor("file_menu_connect_account")
        if not self._wait_for_anchor("connect_dialog", timeout_seconds=4.0):
            raise UiAutomationError("connect dialog did not open")
        self._select_account_login(account_login)
        return f"{menu_method}|{connect_menu_method}|connect_dialog_login_dropdown:{account_login}"

    def _dropdown_region_from_login_anchor(self, login_box) -> tuple[int, int, int, int]:
        """Build the account-dropdown OCR ROI from the login-field anchor."""

        offset = self.ui_profile.account_dropdown_region_from_login
        if len(offset) != 4:
            raise UiAutomationError(f"account_dropdown_region_from_login must contain 4 values, got: {offset}")
        return (
            int(login_box.left + offset[0]),
            int(login_box.top + offset[1]),
            int(offset[2]),
            int(offset[3]),
        )

    def _dismiss_transient_ui(self) -> None:
        """Close transient menus/dialogs and return focus to the terminal canvas."""

        pyautogui = self._pyautogui()
        for _ in range(2):
            pyautogui.press("esc")
            time.sleep(0.15)

    def _active_mt5_window_title(self) -> str | None:
        """Return the most likely active MT5 window title when discoverable."""

        try:
            import pygetwindow as gw
        except ImportError:
            return None

        candidates: list[tuple[int, str]] = []
        for window in gw.getAllWindows():
            title = (getattr(window, "title", "") or "").strip()
            if not title:
                continue
            score = self._window_match_score(title)
            if score <= 0:
                continue
            candidates.append((score, title))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def _wait_for_account_login_in_title(self, account_login: str, timeout_seconds: float = 12.0) -> None:
        """Wait until the MT5 window title reflects the requested account login."""

        target = normalize_account_login(account_login)
        if not target:
            return
        deadline = time.time() + timeout_seconds
        last_title = ""
        while time.time() < deadline:
            self._activate_terminal_window(timeout_seconds=1.0)
            last_title = self._active_mt5_window_title() or ""
            if target in normalize_account_login(last_title):
                return
            time.sleep(0.4)
        raise UiAutomationError(f"account login not visible in MT5 title after switch: {account_login}; title={last_title}")

    def _wait_for_any_anchor(self, anchor_names: list[str], timeout_seconds: float = 3.0) -> str | None:
        """Wait until any anchor from the provided list becomes visible."""

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            for anchor_name in anchor_names:
                if not self._anchor_exists(anchor_name):
                    continue
                try:
                    self._locate_anchor(anchor_name)
                    return anchor_name
                except UiAutomationError:
                    continue
            time.sleep(0.2)
        return None

    def _current_account_login_from_title(self) -> str | None:
        """Extract the leading MT5 account login from the active window title."""

        title = self._active_mt5_window_title() or ""
        match = re.match(r"\s*(\d{5,})", title)
        if not match:
            return None
        return normalize_account_login(match.group(1))

    def _account_login_template_candidates(self, account_login: str) -> list[Path]:
        """Return per-account login field templates from `assets/templates/mt5/accounts`."""

        normalized = normalize_account_login(account_login)
        if not normalized:
            return []
        base = self.ui_profile.templates_dir / "accounts" / f"{normalized}.png"
        candidates = [base]
        for suffix in ("_active", "_selected", "_focused"):
            candidates.append(base.with_name(f"{normalized}{suffix}.png"))
        return candidates

    def _locate_optional_template(self, template_paths: list[Path], *, confidence: float | None = None):
        """Locate one of the provided templates on screen or return `None`."""

        pyautogui = self._pyautogui()
        threshold = confidence if confidence is not None else max(0.78, self.ui_profile.confidence_threshold - 0.08)
        attempts = max(1, self.config.retry_policy.ui_lookup + 1)
        for attempt in range(1, attempts + 1):
            for template_path in template_paths:
                if not template_path.exists():
                    continue
                try:
                    location = pyautogui.locateOnScreen(str(template_path), confidence=threshold)
                except Exception as exc:
                    if exc.__class__.__name__ == "ImageNotFoundException":
                        location = None
                    elif isinstance(exc, OSError):
                        raise UiAutomationError(
                            f"screen grab failed while locating template {template_path.name}: {format_exception(exc)}"
                        ) from exc
                    else:
                        raise UiAutomationError(
                            f"locateOnScreen failed for template {template_path.name}: {format_exception(exc)}"
                        ) from exc
                if location is not None:
                    return location
            if attempt < attempts:
                self._activate_terminal_window(timeout_seconds=1.0)
                time.sleep(0.2)
        return None

    def _focus_account_login_field(self, target_account_login: str) -> str:
        """Focus the login input field using account-specific templates when available."""

        pyautogui = self._pyautogui()
        current_account_login = self._current_account_login_from_title()
        candidate_logins: list[str] = []
        for account_login in (current_account_login, target_account_login):
            normalized = normalize_account_login(account_login)
            if normalized and normalized not in candidate_logins:
                candidate_logins.append(normalized)

        for candidate_login in candidate_logins:
            location = self._locate_optional_template(self._account_login_template_candidates(candidate_login))
            if location is None:
                continue
            center = pyautogui.center(location)
            self._double_click_screen_point(center.x, center.y)
            return f"connect_login_template_{candidate_login}"

        try:
            self._click_anchor("connect_login", double=True)
            return "connect_login_double_click"
        except UiAutomationError:
            return "connect_login_keyboard_fallback"

    def _open_file_menu(self) -> str:
        """Open the File menu via template matching."""

        pyautogui = self._pyautogui()
        self._verify_terminal_ready()
        for method in ("template_file_menu", "alt_f_file_menu", "template_file_menu_retry"):
            self._dismiss_transient_ui()
            if method == "template_file_menu" or method == "template_file_menu_retry":
                self._click_anchor("file_menu")
            else:
                pyautogui.hotkey("alt", "f")
            if self._wait_for_anchor("file_menu_opened", timeout_seconds=1.5):
                return method
        raise UiAutomationError("file menu could not be opened")

    def _open_connect_dialog(self) -> str:
        """Open the connect-account dialog from the File menu with retries."""

        pyautogui = self._pyautogui()
        last_error: UiAutomationError | None = None
        for _ in range(2):
            menu_method = self._open_file_menu()
            self._click_anchor("file_menu_connect_account")
            if self._wait_for_any_anchor(["connect_login", "connect_ok"], timeout_seconds=2.5):
                return f"{menu_method}|template_connect_account_menu_item"
            pyautogui.press("enter")
            if self._wait_for_any_anchor(["connect_login", "connect_ok"], timeout_seconds=1.5):
                return f"{menu_method}|template_connect_account_menu_item_enter"
            last_error = UiAutomationError("connect dialog did not open")
            self._dismiss_transient_ui()
            self._activate_terminal_window(timeout_seconds=1.5)
        if last_error is not None:
            raise last_error
        raise UiAutomationError("connect dialog did not open")

    def _select_account_login(self, account_login: str, password: str | None = None) -> str:
        """Replace login/password fields with target credentials and confirm with Enter."""

        pyautogui = self._pyautogui()
        login_focus_method = self._focus_account_login_field(account_login)
        time.sleep(0.2)
        self._clear_active_input(pyautogui)
        pyautogui.write(account_login, interval=0.02)
        if password:
            self._paste_into_anchor_if_available("connect_password", password, required=True)
        time.sleep(0.25)
        pyautogui.press("enter")
        self._wait_for_account_login_in_title(account_login)
        time.sleep(0.6)
        self._dismiss_transient_ui()
        self._verify_terminal_ready()
        return f"{login_focus_method}|enter_confirm"

    def _switch_account(self, account_alias: str, *, assume_current: bool = False) -> str:
        """Switch account through the MT5 connect-account dialog."""

        account = self._configured_account(account_alias)
        account_login = account.account_login if account else None
        password = account.password if account else None
        current_account_login = self._current_account_login_from_title()
        target_account_login = normalize_account_login(account_login)
        if current_account_login and target_account_login and current_account_login == target_account_login:
            self.logger.info("target MT5 account already active", extra={"event_id": account_alias, "status": target_account_login})
            return f"already_on_account:{target_account_login}"
        if assume_current and not account_login:
            self.logger.info("using current terminal account", extra={"event_id": account_alias})
            return "assume_current_account"
        if not account_login:
            raise UiAutomationError(f"account login is not configured for alias: {account_alias}")

        required_anchors = ["file_menu", "file_menu_opened", "file_menu_connect_account"]
        if not all(self._anchor_exists(anchor_name) for anchor_name in required_anchors):
            missing = ", ".join(anchor_name for anchor_name in required_anchors if not self._anchor_exists(anchor_name))
            raise UiAutomationError(f"account switching template missing: {missing}")

        menu_method = self._open_connect_dialog()
        login_method = self._select_account_login(account_login, password=password)
        return f"{menu_method}|{login_method}:{account_login}"

    def _symbol_template_candidates(self, symbol: str) -> list[Path]:
        """Return optional template variants for a ticker row."""

        normalized = symbol.upper()
        return [
            self.ui_profile.templates_dir / "symbols" / f"{normalized}.png",
            self.ui_profile.templates_dir / "symbols" / f"{normalized}_selected.png",
            self.ui_profile.templates_dir / "symbols" / f"{normalized}_active.png",
        ]

    def _select_symbol(self, symbol: str) -> None:
        """Select a symbol using template lookup with optional OCR fallback."""

        template_lookup_error: UiAutomationError | None = None
        has_symbol_template = any(path.exists() for path in self._symbol_template_candidates(symbol))
        if has_symbol_template:
            self.logger.info("using symbol template lookup", extra={"event_id": symbol})
            try:
                self._select_symbol_by_template(symbol)
                return
            except UiAutomationError as exc:
                template_lookup_error = exc
                if self.ui_profile.symbol_search_mode == "ocr_text":
                    self.logger.warning(
                        "symbol template lookup failed, falling back to OCR",
                        extra={"event_id": symbol, "status": str(exc)},
                    )
        if self.ui_profile.symbol_search_mode == "ocr_text":
            try:
                self._select_symbol_by_ocr(symbol)
                return
            except UiAutomationError as exc:
                if template_lookup_error is not None:
                    raise UiAutomationError(
                        f"symbol lookup failed for {symbol}: template lookup failed ({template_lookup_error}); "
                        f"OCR fallback failed ({exc})"
                    ) from exc
                raise
        if template_lookup_error is not None:
            raise template_lookup_error
        self._select_symbol_by_template(symbol)

    def _select_symbol_by_template(self, symbol: str) -> None:
        """Select a symbol by double-clicking its configured template."""

        symbol_templates = [path for path in self._symbol_template_candidates(symbol) if path.exists()]
        if not symbol_templates:
            primary_path = self._symbol_template_candidates(symbol)[0]
            raise UiAutomationError(f"symbol template missing: {primary_path}")
        pyautogui = self._pyautogui()
        for symbol_template in symbol_templates:
            try:
                box = pyautogui.locateOnScreen(str(symbol_template), confidence=self.ui_profile.confidence_threshold)
            except Exception as exc:
                if exc.__class__.__name__ == "ImageNotFoundException":
                    box = None
                elif isinstance(exc, OSError):
                    raise UiAutomationError(
                        f"screen grab failed while locating symbol {symbol}: {format_exception(exc)}"
                    ) from exc
                else:
                    raise UiAutomationError(
                        f"locateOnScreen failed for symbol {symbol}: {format_exception(exc)}"
                    ) from exc
            if box is None:
                continue
            center = pyautogui.center(box)
            self._double_click_screen_point(center.x, center.y)
            return
        raise UiAutomationError(f"symbol not found on screen: {symbol}")

    def _ocr_tokens_from_region(
        self,
        region: tuple[int, int, int, int],
        *,
        languages: list[str] | None = None,
    ) -> list[dict[str, object]]:
        """Run OCR in the specified region and return token metadata."""

        pyautogui = self._pyautogui()
        pytesseract = self._pytesseract()
        image = pyautogui.screenshot(region=region)
        language_candidates = languages or [self.ui_profile.ocr_language]
        last_exception: Exception | None = None
        for language in language_candidates:
            try:
                data = pytesseract.image_to_data(
                    image,
                    output_type=pytesseract.Output.DICT,
                    config=self.ui_profile.ocr_config,
                    lang=language,
                )
            except Exception as exc:
                last_exception = exc
                continue
            tokens: list[dict[str, object]] = []
            total = len(data.get("text", []))
            for index in range(total):
                text = str(data["text"][index]).strip()
                if not text:
                    continue
                tokens.append(
                    {
                        "text": text,
                        "left": int(data["left"][index]),
                        "top": int(data["top"][index]),
                        "width": int(data["width"][index]),
                        "height": int(data["height"][index]),
                        "block_num": int(data["block_num"][index]),
                        "par_num": int(data["par_num"][index]),
                        "line_num": int(data["line_num"][index]),
                    }
                )
            if tokens:
                return tokens
        if last_exception is not None:
            raise UiAutomationError(f"OCR failed for region {region}: {format_exception(last_exception)}") from last_exception
        return []

    def _symbol_search_regions(self) -> list[tuple[int, int, int, int]]:
        """Return OCR regions to try for Market Watch symbol lookup."""

        base_region = self._anchor_region(self.ui_profile.symbol_search_anchor)
        regions = [base_region]
        narrowed = focused_region(base_region)
        if narrowed != base_region:
            regions.append(narrowed)
        return regions

    def _select_symbol_by_ocr(self, symbol: str) -> None:
        """Select a symbol by OCR text match inside a configured top-left panel region."""

        pyautogui = self._pyautogui()
        last_visible = ""
        for region in self._symbol_search_regions():
            tokens = self._ocr_tokens_from_region(region)
            match = find_symbol_token_match(tokens, symbol)
            if match is not None:
                self._double_click_screen_point(region[0] + match["center_x"], region[1] + match["center_y"])
                return
            last_visible = ", ".join(str(token["text"]) for token in tokens[:16])
        raise UiAutomationError(f"symbol not found by OCR: {symbol}; visible text: {last_visible}")

    def _order_window_ready_anchors(self) -> list[str]:
        """Return anchors that indicate the order dialog is visible."""

        candidate_names = [
            "order_window",
            "order_ok",
            "volume_field",
            "stop_loss_field",
            "take_profit_field",
            "entry_price_field",
            "buy_button",
            "sell_button",
            "submit_button",
        ]
        return [anchor_name for anchor_name in candidate_names if self._anchor_exists(anchor_name)]

    def _wait_for_order_window(self, timeout_seconds: float = 2.5) -> bool:
        """Wait briefly for any configured order-form anchor to appear."""

        ready_anchors = self._order_window_ready_anchors()
        if not ready_anchors:
            return False
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            for anchor_name in ready_anchors:
                try:
                    self._locate_anchor(anchor_name)
                    return True
                except Exception:
                    continue
            time.sleep(0.25)
        return False

    def _wait_for_order_window_to_close(self, timeout_seconds: float = 2.5) -> bool:
        """Wait briefly until all visible order-form anchors disappear."""

        ready_anchors = self._order_window_ready_anchors()
        if not ready_anchors:
            return True
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            still_open = False
            for anchor_name in ready_anchors:
                try:
                    self._locate_anchor(anchor_name)
                    still_open = True
                    break
                except Exception:
                    continue
            if not still_open:
                return True
            time.sleep(0.25)
        return False

    def _open_order_window_after_symbol_selection(self) -> str:
        """Ensure the order window opens after the symbol was selected."""

        pyautogui = self._pyautogui()
        if self._wait_for_order_window(timeout_seconds=1.5):
            return "symbol_double_click"

        pyautogui.press("enter")
        if self._wait_for_order_window(timeout_seconds=1.2):
            return "enter_key"

        pyautogui.press("f9")
        if self._wait_for_order_window(timeout_seconds=2.0):
            return "f9_hotkey"

        raise UiAutomationError("order window did not open after symbol selection")

    def _verify_submission(self) -> None:
        """Check a success or error anchor after submit."""

        if not self._anchor_exists("confirmation_success") and not self._anchor_exists("confirmation_error"):
            raise UiAutomationError("submission confirmation templates are not configured")
        try:
            self._locate_anchor("confirmation_success")
        except UiAutomationError:
            self._locate_anchor("confirmation_error")
            raise UiAutomationError("MT5 confirmation indicates an error")

    def _close_order_form_if_possible(self, technical_log: list[str]) -> None:
        """Best-effort close of the order form via a focused Alt+F4 sequence."""

        pyautogui = self._pyautogui()
        technical_log.append("close order form")
        focus_anchor = self._wait_for_any_anchor(
            ["order_ok", "order_window", "volume_field", "stop_loss_field", "take_profit_field", "buy_button", "sell_button"],
            timeout_seconds=1.0,
        )
        if focus_anchor is not None:
            try:
                self._click_anchor(focus_anchor)
                technical_log.append(f"order form focus via {focus_anchor}")
            except UiAutomationError as exc:
                technical_log.append(f"order form focus skipped: {format_exception(exc)}")
        else:
            technical_log.append("order form focus skipped: no visible order anchor")

        for attempt in range(1, 3):
            self._activate_terminal_window(timeout_seconds=1.0)
            self._send_alt_f4(pyautogui, technical_log, attempt)
            technical_log.append(f"order form close requested via alt+f4 attempt {attempt}")
            if self._wait_for_order_window_to_close(timeout_seconds=1.2):
                technical_log.append("order form closed after alt+f4")
                return
            time.sleep(0.25)
        technical_log.append("order form close not verified after alt+f4")

    def _send_alt_f4(self, pyautogui, technical_log: list[str], attempt: int) -> None:
        """Send Alt+F4 via pyautogui or native Windows input as fallback."""

        sent_pyautogui = False
        if hasattr(pyautogui, "keyDown") and hasattr(pyautogui, "keyUp") and hasattr(pyautogui, "press"):
            pyautogui.keyDown("alt")
            time.sleep(0.05)
            pyautogui.press("f4")
            time.sleep(0.05)
            pyautogui.keyUp("alt")
            sent_pyautogui = True
        elif hasattr(pyautogui, "hotkey"):
            pyautogui.hotkey("alt", "f4")
            sent_pyautogui = True
        if sent_pyautogui:
            technical_log.append(f"pyautogui alt+f4 sent attempt {attempt}")
        self._send_alt_f4_native(technical_log, attempt)

    def _send_alt_f4_native(self, technical_log: list[str], attempt: int) -> None:
        """Fallback Alt+F4 delivery through native Windows keyboard events."""

        try:
            import ctypes

            user32 = ctypes.windll.user32
            vk_alt = 0x12
            vk_f4 = 0x73
            keyup = 0x0002
            user32.keybd_event(vk_alt, 0, 0, 0)
            time.sleep(0.05)
            user32.keybd_event(vk_f4, 0, 0, 0)
            time.sleep(0.05)
            user32.keybd_event(vk_f4, 0, keyup, 0)
            user32.keybd_event(vk_alt, 0, keyup, 0)
            technical_log.append(f"native alt+f4 fallback used attempt {attempt}")
        except Exception as exc:
            technical_log.append(f"native alt+f4 fallback failed attempt {attempt}: {format_exception(exc)}")

    def _account_login_for_alias(self, account_alias: str) -> str | None:
        """Return the configured normalized login for an account alias."""

        account = self._configured_account(account_alias)
        if account is None:
            return None
        return normalize_account_login(account.account_login)

    def _prioritize_accounts_by_current_title(self, accounts: list[object]) -> list[object]:
        """Move the currently active MT5 account to the front when it is in the target list."""

        current_account_login = self._current_account_login_from_title()
        if not current_account_login:
            return accounts

        prioritized: list[object] = []
        remaining: list[object] = []
        for account in accounts:
            account_alias = getattr(account, "account_alias", None)
            direct_login = normalize_account_login(getattr(account, "account_login", None))
            target_login = direct_login or (self._account_login_for_alias(account_alias) if account_alias else None)
            if target_login and target_login == current_account_login:
                prioritized.append(account)
            else:
                remaining.append(account)
        return prioritized + remaining if prioritized else accounts

    def _type_into_anchor_if_available(self, anchor_name: str, value: str, *, required: bool = True) -> None:
        """Type into a required or optional field anchor."""

        if not self._anchor_exists(anchor_name):
            if required:
                raise UiAutomationError(f"missing template for required field: {anchor_name}")
            self.logger.info("optional anchor skipped", extra={"event_id": anchor_name})
            return
        self._type_into_anchor(anchor_name, value)

    def _submit_order(self, trade_intent: TradeIntent, technical_log: list[str]) -> str:
        """Submit the order with an explicit direction-to-button mapping."""

        if trade_intent.direction.value == "buy" and self._anchor_exists("buy_button"):
            technical_log.append("click buy button")
            self._click_anchor("buy_button")
            return "buy_button"
        if trade_intent.direction.value == "sell" and self._anchor_exists("sell_button"):
            technical_log.append("click sell button")
            self._click_anchor("sell_button")
            return "sell_button"
        technical_log.append("click generic submit button")
        self._click_anchor("submit_button")
        return "submit_button"

    @staticmethod
    def _overall_status(account_results: list[AccountExecutionResult]) -> str:
        """Return success/partial/failed for a per-account run."""

        successes = sum(1 for result in account_results if result.status == ExecutionStatus.SUCCESS)
        failures = sum(1 for result in account_results if result.status == ExecutionStatus.FAILED)

        overall = "success"
        if failures and successes:
            overall = "partial"
        elif failures and not successes:
            overall = "failed"
        return overall

    def _prepare_account_context(
        self,
        account_alias: str,
        technical_log: list[str],
        *,
        assume_current_account: bool = False,
        launch_terminal: bool = True,
    ) -> None:
        """Launch or activate MT5 and switch to the requested account."""

        attempts = 2
        last_error: UiAutomationError | None = None
        for attempt in range(1, attempts + 1):
            if launch_terminal or attempt > 1:
                technical_log.append("launch terminal" if attempt == 1 and launch_terminal else "relaunch terminal")
                self._launch_terminal()
            technical_log.append("activate terminal window")
            self._verify_terminal_ready()
            self._dismiss_transient_ui()

            try:
                technical_log.append(f"switch account {account_alias}")
                switch_method = self._switch_account(account_alias, assume_current=assume_current_account)
                technical_log.append(f"account switch method {switch_method}")
                return
            except UiAutomationError as exc:
                last_error = exc
                if attempt < attempts:
                    technical_log.append(f"retry switch after failure: {format_exception(exc)}")
                    continue
                raise
        if last_error is not None:
            raise last_error

    def _execute_account(
        self,
        trade_intent: TradeIntent,
        account_alias: str,
        *,
        assume_current_account: bool = False,
    ) -> AccountExecutionResult:
        """Execute the visual scenario for one account."""

        technical_log: list[str] = []
        screenshot_path: str | None = None
        try:
            self._prepare_account_context(
                account_alias,
                technical_log,
                assume_current_account=assume_current_account,
                launch_terminal=True,
            )

            technical_log.append(f"select symbol {trade_intent.symbol}")
            self._select_symbol(trade_intent.symbol)

            technical_log.append("verify order window")
            order_window_method = self._open_order_window_after_symbol_selection()
            technical_log.append(f"order window opened via {order_window_method}")

            technical_log.append("fill volume")
            self._type_into_anchor_if_available("volume_field", str(trade_intent.volume), required=True)

            if trade_intent.stop_loss is not None:
                technical_log.append("fill stop loss")
                self._type_into_anchor_if_available("stop_loss_field", str(trade_intent.stop_loss), required=True)
            if trade_intent.take_profit is not None:
                technical_log.append("fill take profit")
                self._type_into_anchor_if_available("take_profit_field", str(trade_intent.take_profit), required=True)
            if trade_intent.order_type != OrderType.MARKET and trade_intent.entry_price is not None:
                technical_log.append("fill entry price")
                self._type_into_anchor_if_available("entry_price_field", str(trade_intent.entry_price), required=True)

            if trade_intent.comment:
                technical_log.append("fill comment")
                self._type_into_anchor_if_available("comment_field", trade_intent.comment, required=False)

            screenshot_path = self._save_screenshot(trade_intent.trade_id, f"{account_alias}_pre_submit")
            if trade_intent.execution_policy.dry_run:
                self._close_order_form_if_possible(technical_log)
                technical_log.append("dry-run enabled, submit skipped")
                return AccountExecutionResult(
                    account_alias=account_alias,
                    status=ExecutionStatus.SKIPPED,
                    order_submit_detected=False,
                    screenshot_path=screenshot_path,
                    technical_log=technical_log,
                )

            technical_log.append("submit order")
            submit_route = self._submit_order(trade_intent, technical_log)
            technical_log.append(f"submit route {submit_route}")

            if trade_intent.execution_policy.require_ui_confirmation:
                technical_log.append("verify submission confirmation")
                self._verify_submission()

            technical_log.append("submission confirmed")
            return AccountExecutionResult(
                account_alias=account_alias,
                status=ExecutionStatus.SUCCESS,
                order_submit_detected=True,
                screenshot_path=screenshot_path,
                technical_log=technical_log,
            )
        except Exception as exc:
            technical_log.append(f"failure: {format_exception(exc)}")
            screenshot_path = screenshot_path or self._save_screenshot(trade_intent.trade_id, f"{account_alias}_error")
            return AccountExecutionResult(
                account_alias=account_alias,
                status=ExecutionStatus.FAILED,
                order_submit_detected=False,
                error_message=format_exception(exc),
                screenshot_path=screenshot_path,
                technical_log=technical_log,
            )

    def test_accounts(self) -> AccountSwitchTestResult:
        """Launch MT5 and run a test-only pass over all enabled accounts."""

        started_at = utc_now()
        run_id = str(uuid4())
        execution_dir = self.file_storage.paths.execution_dir / run_id
        account_results: list[AccountExecutionResult] = []
        enabled_accounts = self._prioritize_accounts_by_current_title(
            [account for account in self.config.accounts if account.enabled]
        )

        for index, account in enumerate(enabled_accounts):
            technical_log: list[str] = []
            screenshot_path: str | None = None
            try:
                self._prepare_account_context(
                    account.account_alias,
                    technical_log,
                    assume_current_account=index == 0,
                    launch_terminal=index == 0,
                )
                screenshot_path = self._save_screenshot(run_id, f"{account.account_alias}_switched")
                technical_log.append("account switch verified")
                account_results.append(
                    AccountExecutionResult(
                        account_alias=account.account_alias,
                        status=ExecutionStatus.SUCCESS,
                        order_submit_detected=False,
                        screenshot_path=screenshot_path,
                        technical_log=technical_log,
                    )
                )
            except Exception as exc:
                technical_log.append(f"failure: {format_exception(exc)}")
                screenshot_path = screenshot_path or self._save_screenshot(run_id, f"{account.account_alias}_switch_error")
                account_results.append(
                    AccountExecutionResult(
                        account_alias=account.account_alias,
                        status=ExecutionStatus.FAILED,
                        order_submit_detected=False,
                        error_message=format_exception(exc),
                        screenshot_path=screenshot_path,
                        technical_log=technical_log,
                    )
                )

        result = AccountSwitchTestResult(
            run_id=run_id,
            status=self._overall_status(account_results),
            started_at=started_at,
            finished_at=utc_now(),
            accounts=account_results,
        )
        self.file_storage.write_json(execution_dir / "account_switch_test.json", result)
        return result

    def execute(self, trade_intent: TradeIntent) -> ExecutionResult:
        """Execute a validated trade intent against MT5 UI."""

        started_at = utc_now()
        execution_dir = self.file_storage.paths.execution_dir / trade_intent.trade_id
        enabled_accounts = self._prioritize_accounts_by_current_title(
            [account for account in trade_intent.accounts if account.enabled]
        )
        account_results: list[AccountExecutionResult] = []
        for index, account in enumerate(enabled_accounts):
            result = self._execute_account(
                trade_intent,
                account.account_alias,
                assume_current_account=index == 0,
            )
            account_results.append(result)
            result.technical_log.append("shutdown terminal")
            try:
                self._shutdown_terminal()
                result.technical_log.append("terminal shutdown requested")
            except Exception as exc:
                result.technical_log.append(f"terminal shutdown failed: {format_exception(exc)}")
        result = ExecutionResult(
            trade_id=trade_intent.trade_id,
            status=self._overall_status(account_results),
            started_at=started_at,
            finished_at=utc_now(),
            accounts=account_results,
        )
        self.file_storage.write_json(execution_dir / "execution_result.json", result)
        return result
