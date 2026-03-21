"""Tests for MT5 executor template resolution."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.adapters.executor import (
    DryRunMt5Executor,
    VisualMt5Executor,
    find_account_login_token_match,
    find_text_token_match,
    focused_region,
    format_exception,
)
from app.config import AccountConfig, ExecutionSettings, UiAnchor
from app.domain.exceptions import UiAutomationError
from app.domain.models import (
    AccountExecutionResult,
    AccountTarget,
    Direction,
    ExecutionPolicy,
    ExecutionStatus,
    OrderType,
    TradeIntent,
    TradeIntentMeta,
)
from app.storage.files import FileStorage


def test_visual_executor_anchor_path_falls_back_to_template_root(app_config, tmp_path: Path) -> None:
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "main_window.png").write_bytes(b"template")

    config = app_config.model_copy(deep=True)
    config.ui_profiles["default_mt5"].templates_dir = templates_dir
    config.ui_profiles["default_mt5"].anchors["main_window"] = UiAnchor(template="anchors/main_window.png")
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )

    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    assert executor._anchor_path("main_window") == templates_dir / "main_window.png"


def test_focused_region_limits_market_watch_width_and_height() -> None:
    assert focused_region((0, 55, 420, 700)) == (0, 55, 220, 460)
    assert focused_region((10, 20, 180, 300)) == (10, 20, 180, 300)


def test_type_into_anchor_selects_all_and_clears_before_typing(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    hotkeys: list[tuple[str, ...]] = []
    presses: list[str] = []
    writes: list[tuple[str, float]] = []
    native_selects: list[str] = []

    fake_pyautogui = SimpleNamespace(
        hotkey=lambda *keys: hotkeys.append(keys),
        press=lambda key: presses.append(key),
        write=lambda text, interval=0.0: writes.append((text, interval)),
    )

    monkeypatch.setattr(executor, "_pyautogui", lambda: fake_pyautogui)
    monkeypatch.setattr(executor, "_send_ctrl_a_native", lambda: native_selects.append("ctrl+a"))
    clicks: list[tuple[str, bool]] = []
    monkeypatch.setattr(executor, "_click_anchor", lambda anchor_name, double=False: clicks.append((anchor_name, double)))

    executor._type_into_anchor("volume_field", "0.25")

    assert clicks == [("volume_field", True)]
    assert hotkeys == [("ctrl", "a"), ("ctrl", "a")]
    assert native_selects == ["ctrl+a", "ctrl+a"]
    assert presses == ["backspace", "delete"]
    assert writes == [("0.25", 0.02)]


def test_paste_into_anchor_selects_all_and_pastes_from_clipboard(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    presses: list[str] = []
    clipboard_values: list[str] = []
    clicks: list[tuple[str, bool]] = []
    paste_calls: list[str] = []

    fake_pyautogui = SimpleNamespace(
        hotkey=lambda *keys: None,
        press=lambda key: presses.append(key),
    )

    monkeypatch.setattr(executor, "_pyautogui", lambda: fake_pyautogui)
    monkeypatch.setattr(executor, "_click_anchor", lambda anchor_name, double=False: clicks.append((anchor_name, double)))
    monkeypatch.setattr(executor, "_set_clipboard_text", lambda value: clipboard_values.append(value))
    monkeypatch.setattr(executor, "_send_ctrl_v_native", lambda: paste_calls.append("paste"))

    executor._paste_into_anchor("connect_password", "EpGwRU]1%")

    assert clicks == [("connect_password", True)]
    assert presses == ["backspace", "delete"]
    assert paste_calls == ["paste"]
    assert clipboard_values == ["EpGwRU]1%"]


def test_visual_executor_prefers_symbol_template_when_available(app_config, tmp_path: Path, monkeypatch) -> None:
    templates_dir = tmp_path / "templates"
    symbols_dir = templates_dir / "symbols"
    symbols_dir.mkdir(parents=True)
    (symbols_dir / "EURUSD.png").write_bytes(b"template")

    config = app_config.model_copy(deep=True)
    config.ui_profiles["default_mt5"].templates_dir = templates_dir
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )

    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))
    called = {"template": 0, "ocr": 0}

    monkeypatch.setattr(executor, "_select_symbol_by_template", lambda symbol: called.__setitem__("template", called["template"] + 1))
    monkeypatch.setattr(executor, "_select_symbol_by_ocr", lambda symbol: called.__setitem__("ocr", called["ocr"] + 1))

    executor._select_symbol("EURUSD")

    assert called["template"] == 1
    assert called["ocr"] == 0


def test_visual_executor_falls_back_to_ocr_when_template_lookup_fails(
    app_config,
    tmp_path: Path,
    monkeypatch,
) -> None:
    templates_dir = tmp_path / "templates"
    symbols_dir = templates_dir / "symbols"
    symbols_dir.mkdir(parents=True)
    (symbols_dir / "EURUSD.png").write_bytes(b"template")

    config = app_config.model_copy(deep=True)
    config.ui_profiles["default_mt5"].templates_dir = templates_dir
    config.ui_profiles["default_mt5"].symbol_search_mode = "ocr_text"
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )

    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))
    called = {"template": 0, "ocr": 0}

    def fail_template(symbol: str) -> None:
        called["template"] += 1
        raise UiAutomationError("template not found on screen")

    def pass_ocr(symbol: str) -> None:
        called["ocr"] += 1

    monkeypatch.setattr(executor, "_select_symbol_by_template", fail_template)
    monkeypatch.setattr(executor, "_select_symbol_by_ocr", pass_ocr)

    executor._select_symbol("EURUSD")

    assert called["template"] == 1
    assert called["ocr"] == 1


def test_visual_executor_stays_template_only_by_default(app_config, tmp_path: Path, monkeypatch) -> None:
    templates_dir = tmp_path / "templates"
    symbols_dir = templates_dir / "symbols"
    symbols_dir.mkdir(parents=True)
    (symbols_dir / "EURUSD.png").write_bytes(b"template")

    config = app_config.model_copy(deep=True)
    config.ui_profiles["default_mt5"].templates_dir = templates_dir
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )

    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    monkeypatch.setattr(executor, "_select_symbol_by_template", lambda symbol: (_ for _ in ()).throw(UiAutomationError("template not found")))
    monkeypatch.setattr(executor, "_select_symbol_by_ocr", lambda symbol: (_ for _ in ()).throw(AssertionError("OCR should not run")))

    with pytest.raises(UiAutomationError, match="template not found"):
        executor._select_symbol("EURUSD")


def test_visual_executor_supports_selected_symbol_template(app_config, tmp_path: Path, monkeypatch) -> None:
    templates_dir = tmp_path / "templates"
    symbols_dir = templates_dir / "symbols"
    symbols_dir.mkdir(parents=True)
    selected_template = symbols_dir / "EURUSD_selected.png"
    selected_template.write_bytes(b"template")

    config = app_config.model_copy(deep=True)
    config.ui_profiles["default_mt5"].templates_dir = templates_dir
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )

    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))
    clicks: list[tuple[int, int]] = []

    class Box:
        left = 10
        top = 20
        width = 30
        height = 12

    fake_pyautogui = SimpleNamespace(
        locateOnScreen=lambda path, confidence: Box() if Path(path) == selected_template else None,
        center=lambda box: SimpleNamespace(x=25, y=26),
    )

    monkeypatch.setattr(executor, "_pyautogui", lambda: fake_pyautogui)
    monkeypatch.setattr(executor, "_double_click_screen_point", lambda x, y: clicks.append((x, y)))

    executor._select_symbol_by_template("EURUSD")

    assert clicks == [(25, 26)]


def test_find_account_login_token_match_handles_digit_tokens() -> None:
    tokens = [
        {"text": "11142094", "left": 0, "top": 0, "width": 60, "height": 10, "block_num": 1, "par_num": 1, "line_num": 1},
        {"text": "11330693", "left": 0, "top": 16, "width": 60, "height": 10, "block_num": 1, "par_num": 1, "line_num": 2},
        {"text": "5048198927", "left": 0, "top": 32, "width": 80, "height": 10, "block_num": 1, "par_num": 1, "line_num": 3},
    ]

    match = find_account_login_token_match(tokens, "5048198927")

    assert match is not None
    assert match["center_y"] == 37


def test_find_text_token_match_finds_top_menu_label() -> None:
    tokens = [
        {"text": "Файл", "left": 10, "top": 8, "width": 24, "height": 10},
        {"text": "Вид", "left": 46, "top": 8, "width": 18, "height": 10},
    ]

    match = find_text_token_match(tokens, ["Файл"])

    assert match is not None
    assert match["center_x"] == 22


def test_find_text_token_match_handles_english_menu_label() -> None:
    tokens = [
        {"text": "File", "left": 10, "top": 8, "width": 24, "height": 10},
        {"text": "View", "left": 46, "top": 8, "width": 24, "height": 10},
    ]

    match = find_text_token_match(tokens, ["File"])

    assert match is not None
    assert match["center_x"] == 22


def test_find_text_token_match_handles_multiword_menu_item() -> None:
    tokens = [
        {"text": "Login", "left": 10, "top": 40, "width": 32, "height": 10, "block_num": 1, "par_num": 1, "line_num": 1},
        {"text": "to", "left": 46, "top": 40, "width": 10, "height": 10, "block_num": 1, "par_num": 1, "line_num": 1},
        {"text": "Trade", "left": 60, "top": 40, "width": 30, "height": 10, "block_num": 1, "par_num": 1, "line_num": 1},
        {"text": "Account", "left": 94, "top": 40, "width": 42, "height": 10, "block_num": 1, "par_num": 1, "line_num": 1},
    ]

    match = find_text_token_match(tokens, ["Login to Trade Account"])

    assert match is not None
    assert match["left"] == 10
    assert match["width"] == 126


def test_switch_account_uses_connect_dialog_login_flow(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    config.accounts = [
        AccountConfig(account_alias="main_1", account_login="5048198927", enabled=True),
    ]
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    clicks: list[str] = []
    anchor_checks = {
        "file_menu": True,
        "file_menu_opened": True,
        "file_menu_connect_account": True,
        "connect_login": True,
        "connect_ok": True,
    }

    monkeypatch.setattr(executor, "_anchor_exists", lambda anchor_name: anchor_checks.get(anchor_name, False))
    monkeypatch.setattr(executor, "_click_anchor", lambda anchor_name, double=False: clicks.append(anchor_name))
    monkeypatch.setattr(executor, "_open_connect_dialog", lambda: "template_file_menu|template_connect_account_menu_item")
    selected_logins: list[str] = []
    monkeypatch.setattr(
        executor,
        "_select_account_login",
        lambda account_login: selected_logins.append(account_login) or "connect_login_double_click|enter_confirm",
    )

    method = executor._switch_account("main_1")

    assert method == "template_file_menu|template_connect_account_menu_item|connect_login_double_click|enter_confirm:5048198927"
    assert clicks == []
    assert selected_logins == ["5048198927"]


def test_switch_account_uses_current_account_when_login_missing(app_config) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    config.accounts = [AccountConfig(account_alias="main_1", account_login=None, enabled=True)]
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    method = executor._switch_account("main_1", assume_current=True)

    assert method == "assume_current_account"


def test_switch_account_skips_relogin_when_target_account_is_already_active(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    config.accounts = [AccountConfig(account_alias="main_1", account_login="5048198927", enabled=True)]
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    calls: list[str] = []
    monkeypatch.setattr(executor, "_current_account_login_from_title", lambda: "5048198927")
    monkeypatch.setattr(executor, "_open_connect_dialog", lambda: calls.append("open_connect_dialog") or "dialog")
    monkeypatch.setattr(executor, "_select_account_login", lambda account_login: calls.append(account_login) or "selected")

    method = executor._switch_account("main_1")

    assert method == "already_on_account:5048198927"
    assert calls == []


def test_select_account_login_replaces_text_and_confirms(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    hotkeys: list[tuple[str, ...]] = []
    presses: list[str] = []
    writes: list[tuple[str, float]] = []
    verified: list[str] = []

    fake_pyautogui = SimpleNamespace(
        hotkey=lambda *keys: hotkeys.append(keys),
        press=lambda key: presses.append(key),
        write=lambda text, interval=0.0: writes.append((text, interval)),
    )

    monkeypatch.setattr(executor, "_pyautogui", lambda: fake_pyautogui)
    monkeypatch.setattr(executor, "_focus_account_login_field", lambda account_login: "connect_login_double_click")
    monkeypatch.setattr(executor, "_wait_for_account_login_in_title", lambda account_login, timeout_seconds=12.0: verified.append(f"title:{account_login}"))
    monkeypatch.setattr(executor, "_dismiss_transient_ui", lambda: verified.append("dismiss"))
    monkeypatch.setattr(executor, "_verify_terminal_ready", lambda: verified.append("ready"))

    method = executor._select_account_login("5048198927")

    assert method == "connect_login_double_click|enter_confirm"
    assert hotkeys == [("ctrl", "a")]
    assert presses == ["backspace", "delete", "enter"]
    assert writes == [("5048198927", 0.02)]
    assert verified == ["title:5048198927", "dismiss", "ready"]


def test_select_account_login_types_password_when_configured(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    hotkeys: list[tuple[str, ...]] = []
    presses: list[str] = []
    writes: list[tuple[str, float]] = []
    pasted_passwords: list[tuple[str, str, bool]] = []

    fake_pyautogui = SimpleNamespace(
        hotkey=lambda *keys: hotkeys.append(keys),
        press=lambda key: presses.append(key),
        write=lambda text, interval=0.0: writes.append((text, interval)),
    )

    monkeypatch.setattr(executor, "_pyautogui", lambda: fake_pyautogui)
    monkeypatch.setattr(executor, "_focus_account_login_field", lambda account_login: "connect_login_double_click")
    monkeypatch.setattr(
        executor,
        "_paste_into_anchor_if_available",
        lambda anchor_name, value, required=True: pasted_passwords.append((anchor_name, value, required)),
    )
    monkeypatch.setattr(executor, "_wait_for_account_login_in_title", lambda account_login, timeout_seconds=12.0: None)
    monkeypatch.setattr(executor, "_dismiss_transient_ui", lambda: None)
    monkeypatch.setattr(executor, "_verify_terminal_ready", lambda: None)

    method = executor._select_account_login("5048198927", password="secret-pass")

    assert method == "connect_login_double_click|enter_confirm"
    assert hotkeys == [("ctrl", "a")]
    assert presses == ["backspace", "delete", "enter"]
    assert writes == [("5048198927", 0.02)]
    assert pasted_passwords == [("connect_password", "secret-pass", True)]


def test_select_account_login_uses_current_account_template_when_available(app_config, tmp_path: Path, monkeypatch) -> None:
    templates_dir = tmp_path / "templates"
    accounts_dir = templates_dir / "accounts"
    accounts_dir.mkdir(parents=True)
    template_path = accounts_dir / "5048198927.png"
    template_path.write_bytes(b"template")

    config = app_config.model_copy(deep=True)
    config.ui_profiles["default_mt5"].templates_dir = templates_dir
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    moves: list[tuple[int, int]] = []
    hotkeys: list[tuple[str, ...]] = []
    presses: list[str] = []
    writes: list[tuple[str, float]] = []

    class Box:
        left = 30
        top = 40
        width = 100
        height = 24

    fake_pyautogui = SimpleNamespace(
        locateOnScreen=lambda path, confidence: Box() if Path(path) == template_path else None,
        center=lambda box: SimpleNamespace(x=80, y=52),
        moveTo=lambda x, y, duration=0.0: moves.append((x, y)),
        click=lambda *args, **kwargs: None,
        hotkey=lambda *keys: hotkeys.append(keys),
        press=lambda key: presses.append(key),
        write=lambda text, interval=0.0: writes.append((text, interval)),
    )

    monkeypatch.setattr(executor, "_pyautogui", lambda: fake_pyautogui)
    monkeypatch.setattr(executor, "_active_mt5_window_title", lambda: "5048198927 - MetaQuotes-Demo: Demo Account - Hedge - MetaQuotes Ltd.")
    monkeypatch.setattr(executor, "_wait_for_account_login_in_title", lambda account_login, timeout_seconds=12.0: None)
    monkeypatch.setattr(executor, "_dismiss_transient_ui", lambda: None)
    monkeypatch.setattr(executor, "_verify_terminal_ready", lambda: None)

    method = executor._select_account_login("11142094")

    assert method == "connect_login_template_5048198927|enter_confirm"
    assert moves == [(80, 52)]
    assert hotkeys == [("ctrl", "a")]
    assert presses == ["backspace", "delete", "enter"]
    assert writes == [("11142094", 0.02)]


def test_select_account_login_falls_back_to_keyboard_when_login_anchor_is_missing(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    clicks: list[tuple[str, bool]] = []
    hotkeys: list[tuple[str, ...]] = []
    presses: list[str] = []
    writes: list[tuple[str, float]] = []

    fake_pyautogui = SimpleNamespace(
        hotkey=lambda *keys: hotkeys.append(keys),
        press=lambda key: presses.append(key),
        write=lambda text, interval=0.0: writes.append((text, interval)),
    )

    def click_anchor(anchor_name: str, double: bool = False) -> None:
        clicks.append((anchor_name, double))
        if anchor_name == "connect_login":
            raise UiAutomationError("anchor not found: connect_login")

    monkeypatch.setattr(executor, "_pyautogui", lambda: fake_pyautogui)
    monkeypatch.setattr(executor, "_click_anchor", click_anchor)
    monkeypatch.setattr(executor, "_wait_for_account_login_in_title", lambda account_login, timeout_seconds=12.0: None)
    monkeypatch.setattr(executor, "_dismiss_transient_ui", lambda: None)
    monkeypatch.setattr(executor, "_verify_terminal_ready", lambda: None)

    method = executor._select_account_login("11330693")

    assert method == "connect_login_keyboard_fallback|enter_confirm"
    assert clicks == [("connect_login", True)]
    assert hotkeys == [("ctrl", "a")]
    assert presses == ["backspace", "delete", "enter"]
    assert writes == [("11330693", 0.02)]


def test_visual_executor_test_accounts_reuses_prepare_account_context(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    config.accounts = [
        AccountConfig(account_alias="main_1", account_login="5048198927", enabled=True),
        AccountConfig(account_alias="main_2", account_login="11330693", enabled=True),
    ]
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    prepared: list[tuple[str, bool, bool]] = []
    screenshots: list[tuple[str, str]] = []

    monkeypatch.setattr(
        executor,
        "_prepare_account_context",
        lambda account_alias, technical_log, assume_current_account=False, launch_terminal=True: prepared.append(
            (account_alias, assume_current_account, launch_terminal)
        ),
    )
    monkeypatch.setattr(executor, "_current_account_login_from_title", lambda: None)
    monkeypatch.setattr(
        executor,
        "_save_screenshot",
        lambda run_id, name: screenshots.append((run_id, name)) or f"shots/{name}.png",
    )

    result = executor.test_accounts()

    assert result.status == "success"
    assert [account.status for account in result.accounts] == [ExecutionStatus.SUCCESS, ExecutionStatus.SUCCESS]
    assert prepared == [
        ("main_1", True, True),
        ("main_2", False, False),
    ]
    assert [account.screenshot_path for account in result.accounts] == [
        "shots/main_1_switched.png",
        "shots/main_2_switched.png",
    ]
    assert all(account.technical_log[-1] == "account switch verified" for account in result.accounts)


def test_prepare_account_context_retries_switch_after_failure(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    launches: list[str] = []
    switches: list[str] = []
    monkeypatch.setattr(executor, "_launch_terminal", lambda: launches.append("launch"))
    monkeypatch.setattr(executor, "_verify_terminal_ready", lambda: None)
    monkeypatch.setattr(executor, "_dismiss_transient_ui", lambda: None)

    attempts = iter([UiAutomationError("connect dialog did not open"), "template_file_menu|template_connect_account_menu_item|connect_login_replace_text|enter_confirm:5048198927"])

    def switch(account_alias: str, assume_current: bool = False) -> str:
        switches.append(account_alias)
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(executor, "_switch_account", switch)

    technical_log: list[str] = []
    executor._prepare_account_context("main_1", technical_log, assume_current_account=False, launch_terminal=False)

    assert launches == ["launch"]
    assert switches == ["main_1", "main_1"]
    assert "retry switch after failure: connect dialog did not open" in technical_log


def test_dry_run_executor_test_accounts_returns_skipped(app_config) -> None:
    executor = DryRunMt5Executor(config=app_config, file_storage=FileStorage(app_config.paths))

    result = executor.test_accounts()

    assert result.status == "success"
    assert len(result.accounts) == 2
    assert all(account.status == ExecutionStatus.SKIPPED for account in result.accounts)


def test_format_exception_returns_class_name_for_blank_messages() -> None:
    class EmptyFailure(Exception):
        def __str__(self) -> str:
            return ""

    assert format_exception(EmptyFailure()) == "EmptyFailure"


def test_locate_anchor_uses_fallback_region_when_template_is_not_found(app_config, tmp_path: Path, monkeypatch) -> None:
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "file_menu.png").write_bytes(b"template")

    config = app_config.model_copy(deep=True)
    config.ui_profiles["default_mt5"].templates_dir = templates_dir
    config.ui_profiles["default_mt5"].anchors["file_menu"] = UiAnchor(
        template="file_menu.png",
        fallback_region=[12, 8, 68, 28],
    )
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )

    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    class ImageNotFoundException(Exception):
        pass

    fake_pyautogui = SimpleNamespace(
        locateOnScreen=lambda path, confidence: (_ for _ in ()).throw(ImageNotFoundException()),
    )
    monkeypatch.setattr(executor, "_pyautogui", lambda: fake_pyautogui)
    monkeypatch.setattr(executor, "_activate_terminal_window", lambda timeout_seconds=0.0: True)

    box = executor._locate_anchor("file_menu")

    assert (box.left, box.top, box.width, box.height) == (12, 8, 68, 28)


def test_open_file_menu_clicks_template_once(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    calls: list[str] = []
    hotkeys: list[tuple[str, ...]] = []
    fake_pyautogui = SimpleNamespace(
        press=lambda key: calls.append(f"press:{key}"),
        hotkey=lambda *keys: hotkeys.append(keys),
    )
    monkeypatch.setattr(executor, "_pyautogui", lambda: fake_pyautogui)
    monkeypatch.setattr(executor, "_verify_terminal_ready", lambda: None)
    monkeypatch.setattr(executor, "_dismiss_transient_ui", lambda: calls.append("dismiss"))
    monkeypatch.setattr(executor, "_click_anchor", lambda anchor_name, double=False: calls.append(anchor_name))
    monkeypatch.setattr(executor, "_wait_for_anchor", lambda anchor_name, timeout_seconds=0.0: True)

    method = executor._open_file_menu()

    assert method == "template_file_menu"
    assert calls == ["dismiss", "file_menu"]
    assert hotkeys == []
    return

    def fail_template(anchor_name: str, double: bool = False) -> None:
        calls.append(anchor_name)
        raise UiAutomationError("template miss")

    monkeypatch.setattr(executor, "_click_anchor", fail_template)
    monkeypatch.setattr(executor, "_click_menu_label_by_ocr", lambda *args, **kwargs: calls.append("ocr:Р¤Р°Р№Р»") or "ocr_top_menu")
    monkeypatch.setattr(executor, "_wait_for_anchor", lambda anchor_name, timeout_seconds=0.0: True)

    method = executor._open_file_menu()

    assert method == "ocr_file_menu"
    assert calls and calls[0].startswith("ocr:")
    return
    assert calls == ["ocr:Файл"]


def test_open_file_menu_falls_back_to_alt_f(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    calls: list[str] = []
    hotkeys: list[tuple[str, ...]] = []
    wait_results = iter([False, True])
    fake_pyautogui = SimpleNamespace(
        press=lambda key: calls.append(f"press:{key}"),
        hotkey=lambda *keys: hotkeys.append(keys),
    )

    monkeypatch.setattr(executor, "_pyautogui", lambda: fake_pyautogui)
    monkeypatch.setattr(executor, "_verify_terminal_ready", lambda: None)
    monkeypatch.setattr(executor, "_dismiss_transient_ui", lambda: calls.append("dismiss"))
    monkeypatch.setattr(executor, "_click_anchor", lambda anchor_name, double=False: calls.append(anchor_name))
    monkeypatch.setattr(executor, "_wait_for_anchor", lambda anchor_name, timeout_seconds=0.0: next(wait_results))

    method = executor._open_file_menu()

    assert method == "alt_f_file_menu"
    assert calls == ["dismiss", "file_menu", "dismiss"]
    assert hotkeys == [("alt", "f")]


def test_open_connect_dialog_accepts_connect_ok_anchor(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    clicks: list[str] = []
    fake_pyautogui = SimpleNamespace(press=lambda key: clicks.append(f"press:{key}"))

    monkeypatch.setattr(executor, "_pyautogui", lambda: fake_pyautogui)
    monkeypatch.setattr(executor, "_open_file_menu", lambda: "template_file_menu")
    monkeypatch.setattr(executor, "_click_anchor", lambda anchor_name, double=False: clicks.append(anchor_name))
    monkeypatch.setattr(executor, "_wait_for_any_anchor", lambda anchor_names, timeout_seconds=0.0: "connect_ok")

    method = executor._open_connect_dialog()

    assert method == "template_file_menu|template_connect_account_menu_item"
    assert clicks == ["file_menu_connect_account"]


def test_window_match_score_excludes_visual_studio_code_titles(app_config) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    mt5_score = executor._window_match_score("11330693 - FundingPips2-SIM: Демо-счет - Hedge - FundingPips Corp (2)")
    vscode_score = executor._window_match_score("mt5_god.exe - General - Visual Studio Code")

    assert mt5_score > 0
    assert vscode_score == 0


def test_double_click_screen_point_uses_two_clicks(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    calls: list[tuple[str, tuple, dict]] = []

    fake_pyautogui = SimpleNamespace(
        moveTo=lambda *args, **kwargs: calls.append(("moveTo", args, kwargs)),
        click=lambda *args, **kwargs: calls.append(("click", args, kwargs)),
    )
    monkeypatch.setattr(executor, "_pyautogui", lambda: fake_pyautogui)

    executor._double_click_screen_point(100, 200)

    click_calls = [call for call in calls if call[0] == "click"]
    assert len(click_calls) == 1
    assert click_calls[0][2]["clicks"] == 2


def test_open_order_window_after_symbol_selection_falls_back_to_f9(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    checks = iter([False, False, True])
    pressed: list[str] = []
    fake_pyautogui = SimpleNamespace(press=lambda key: pressed.append(key))

    monkeypatch.setattr(executor, "_pyautogui", lambda: fake_pyautogui)
    monkeypatch.setattr(executor, "_wait_for_order_window", lambda timeout_seconds=0.0: next(checks))

    method = executor._open_order_window_after_symbol_selection()

    assert method == "f9_hotkey"
    assert pressed == ["enter", "f9"]


def test_wait_for_order_window_accepts_field_anchors(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    monkeypatch.setattr(
        executor,
        "_anchor_exists",
        lambda anchor_name: anchor_name in {"volume_field", "buy_button"},
    )
    monkeypatch.setattr(
        executor,
        "_locate_anchor",
        lambda anchor_name: SimpleNamespace() if anchor_name == "volume_field" else (_ for _ in ()).throw(UiAutomationError("not found")),
    )

    assert executor._wait_for_order_window(timeout_seconds=0.01) is True


@pytest.mark.parametrize(
    ("direction", "expected_anchor", "expected_log"),
    [("buy", "buy_button", "click buy button"), ("sell", "sell_button", "click sell button")],
)
def test_submit_order_uses_direction_specific_anchor(app_config, monkeypatch, direction: str, expected_anchor: str, expected_log: str) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    clicked: list[str] = []
    monkeypatch.setattr(executor, "_anchor_exists", lambda anchor_name: anchor_name == expected_anchor)
    monkeypatch.setattr(executor, "_click_anchor", lambda anchor_name, double=False: clicked.append(anchor_name))

    technical_log: list[str] = []
    route = executor._submit_order(
        SimpleNamespace(direction=SimpleNamespace(value=direction)),
        technical_log,
    )

    assert route == expected_anchor
    assert clicked == [expected_anchor]
    assert technical_log == [expected_log]


def test_close_order_form_requests_alt_f4(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    events: list[tuple[str, tuple[str, ...] | str]] = []
    fake_pyautogui = SimpleNamespace(
        keyDown=lambda key: events.append(("keyDown", key)),
        press=lambda key: events.append(("press", key)),
        keyUp=lambda key: events.append(("keyUp", key)),
    )

    monkeypatch.setattr(executor, "_pyautogui", lambda: fake_pyautogui)
    monkeypatch.setattr(executor, "_wait_for_any_anchor", lambda anchor_names, timeout_seconds=0.0: "order_ok")
    monkeypatch.setattr(executor, "_click_anchor", lambda anchor_name, double=False: events.append(("click", anchor_name)))
    monkeypatch.setattr(executor, "_activate_terminal_window", lambda timeout_seconds=0.0: True)
    monkeypatch.setattr(executor, "_wait_for_order_window_to_close", lambda timeout_seconds=0.0: True)

    technical_log: list[str] = []
    executor._close_order_form_if_possible(technical_log)

    assert events == [
        ("click", "order_ok"),
        ("keyDown", "alt"),
        ("press", "f4"),
        ("keyUp", "alt"),
    ]
    assert technical_log == [
        "close order form",
        "order form focus via order_ok",
        "pyautogui alt+f4 sent attempt 1",
        "order form close requested via alt+f4 attempt 1",
        "order form closed after alt+f4",
    ]


def test_close_order_form_uses_native_alt_f4_when_pyautogui_cannot_send_keys(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    native_calls: list[int] = []
    monkeypatch.setattr(executor, "_pyautogui", lambda: SimpleNamespace())
    monkeypatch.setattr(executor, "_wait_for_any_anchor", lambda anchor_names, timeout_seconds=0.0: None)
    monkeypatch.setattr(executor, "_activate_terminal_window", lambda timeout_seconds=0.0: True)
    monkeypatch.setattr(
        executor,
        "_send_alt_f4_native",
        lambda technical_log, attempt: native_calls.append(attempt) or technical_log.append(f"native alt+f4 fallback used attempt {attempt}"),
    )
    monkeypatch.setattr(executor, "_wait_for_order_window_to_close", lambda timeout_seconds=0.0: True)

    technical_log: list[str] = []
    executor._close_order_form_if_possible(technical_log)

    assert native_calls == [1]
    assert technical_log == [
        "close order form",
        "order form focus skipped: no visible order anchor",
        "native alt+f4 fallback used attempt 1",
        "order form close requested via alt+f4 attempt 1",
        "order form closed after alt+f4",
    ]


def test_close_order_form_retries_when_window_stays_open(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    events: list[tuple[str, str]] = []
    fake_pyautogui = SimpleNamespace(
        keyDown=lambda key: events.append(("keyDown", key)),
        press=lambda key: events.append(("press", key)),
        keyUp=lambda key: events.append(("keyUp", key)),
    )

    close_checks = iter([False, True])
    monkeypatch.setattr(executor, "_pyautogui", lambda: fake_pyautogui)
    monkeypatch.setattr(executor, "_wait_for_any_anchor", lambda anchor_names, timeout_seconds=0.0: None)
    monkeypatch.setattr(executor, "_activate_terminal_window", lambda timeout_seconds=0.0: True)
    monkeypatch.setattr(executor, "_wait_for_order_window_to_close", lambda timeout_seconds=0.0: next(close_checks))

    technical_log: list[str] = []
    executor._close_order_form_if_possible(technical_log)

    assert events == [
        ("keyDown", "alt"),
        ("press", "f4"),
        ("keyUp", "alt"),
        ("keyDown", "alt"),
        ("press", "f4"),
        ("keyUp", "alt"),
    ]
    assert technical_log == [
        "close order form",
        "order form focus skipped: no visible order anchor",
        "pyautogui alt+f4 sent attempt 1",
        "order form close requested via alt+f4 attempt 1",
        "pyautogui alt+f4 sent attempt 2",
        "order form close requested via alt+f4 attempt 2",
        "order form closed after alt+f4",
    ]




def test_execute_account_closes_order_form_after_success(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    trade_intent = TradeIntent(
        source_event_id="event-1",
        symbol="EURUSD",
        direction=Direction.BUY,
        order_type=OrderType.MARKET,
        volume=0.1,
        stop_loss=1.1,
        take_profit=1.2,
        accounts=[AccountTarget(account_alias="main_1", enabled=True)],
        execution_policy=ExecutionPolicy(dry_run=False, require_ui_confirmation=True),
        meta=TradeIntentMeta(raw_source_image="data/inbox/test.png", analysis_confidence=0.95),
    )

    monkeypatch.setattr(executor, "_prepare_account_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(executor, "_select_symbol", lambda symbol: None)
    monkeypatch.setattr(executor, "_open_order_window_after_symbol_selection", lambda: "symbol_double_click")
    monkeypatch.setattr(executor, "_type_into_anchor_if_available", lambda *args, **kwargs: None)
    monkeypatch.setattr(executor, "_save_screenshot", lambda trade_id, name: f"data/screenshots/{trade_id}/{name}.png")
    monkeypatch.setattr(executor, "_submit_order", lambda trade_intent, technical_log: "buy_button")
    monkeypatch.setattr(executor, "_verify_submission", lambda: None)

    closed: list[str] = []
    monkeypatch.setattr(executor, "_close_order_form_if_possible", lambda technical_log: closed.append("closed") or technical_log.extend(["close order form", "order form close requested via alt+f4 attempt 1", "order form closed after alt+f4"]))

    result = executor._execute_account(trade_intent, "main_1", assume_current_account=True)

    assert result.status == ExecutionStatus.SUCCESS
    assert closed == []
    assert "submission confirmed" in result.technical_log


def test_execute_prioritizes_current_account_from_title(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    config.accounts = [
        AccountConfig(account_alias="main_1", account_login="5048198927", enabled=True),
        AccountConfig(account_alias="main_2", account_login="11330693", enabled=True),
    ]
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    trade_intent = TradeIntent(
        source_event_id="event-1",
        symbol="EURUSD",
        direction=Direction.BUY,
        order_type=OrderType.MARKET,
        volume=0.1,
        accounts=[
            AccountTarget(account_alias="main_1", enabled=True),
            AccountTarget(account_alias="main_2", enabled=True),
        ],
        execution_policy=ExecutionPolicy(dry_run=True, require_ui_confirmation=False),
        meta=TradeIntentMeta(raw_source_image="data/inbox/test.png", analysis_confidence=0.95),
    )

    calls: list[tuple[str, bool]] = []
    shutdowns: list[str] = []
    monkeypatch.setattr(executor, "_current_account_login_from_title", lambda: "11330693")
    monkeypatch.setattr(
        executor,
        "_execute_account",
        lambda trade_intent, account_alias, assume_current_account=False: calls.append((account_alias, assume_current_account))
        or AccountExecutionResult(
            account_alias=account_alias,
            status=ExecutionStatus.SUCCESS,
            order_submit_detected=False,
            screenshot_path=None,
            error_message=None,
            technical_log=[],
        ),
    )
    monkeypatch.setattr(executor, "_shutdown_terminal", lambda: shutdowns.append("shutdown"))

    result = executor.execute(trade_intent)

    assert result.status == "success"
    assert calls == [("main_2", True), ("main_1", False)]
    assert shutdowns == ["shutdown", "shutdown"]
    assert result.accounts[0].technical_log[-2:] == ["shutdown terminal", "terminal shutdown requested"]
    assert result.accounts[1].technical_log[-2:] == ["shutdown terminal", "terminal shutdown requested"]


def test_execute_continues_when_terminal_shutdown_fails(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    trade_intent = TradeIntent(
        source_event_id="event-1",
        symbol="EURUSD",
        direction=Direction.BUY,
        order_type=OrderType.MARKET,
        volume=0.1,
        accounts=[
            AccountTarget(account_alias="main_1", enabled=True),
            AccountTarget(account_alias="main_2", enabled=True),
        ],
        execution_policy=ExecutionPolicy(dry_run=True, require_ui_confirmation=False),
        meta=TradeIntentMeta(raw_source_image="data/inbox/test.png", analysis_confidence=0.95),
    )

    monkeypatch.setattr(executor, "_current_account_login_from_title", lambda: None)
    monkeypatch.setattr(
        executor,
        "_execute_account",
        lambda trade_intent, account_alias, assume_current_account=False: AccountExecutionResult(
            account_alias=account_alias,
            status=ExecutionStatus.SUCCESS,
            order_submit_detected=False,
            screenshot_path=None,
            error_message=None,
            technical_log=[],
        ),
    )

    attempts = {"count": 0}

    def fail_once() -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise UiAutomationError("taskkill failed")

    monkeypatch.setattr(executor, "_shutdown_terminal", fail_once)

    result = executor.execute(trade_intent)

    assert result.status == "success"
    assert "terminal shutdown failed: taskkill failed" in result.accounts[0].technical_log
    assert result.accounts[1].technical_log[-1] == "terminal shutdown requested"


def test_test_accounts_prioritizes_current_account_from_title(app_config, monkeypatch) -> None:
    config = app_config.model_copy(deep=True)
    config.execution = ExecutionSettings(
        backend="visual",
        mt5_terminal_path=Path(r"C:\Program Files\FundingPips 2 MT5 Terminal\terminal64.exe"),
        require_ui_confirmation=True,
    )
    config.accounts = [
        AccountConfig(account_alias="main_1", account_login="5048198927", enabled=True),
        AccountConfig(account_alias="main_2", account_login="11330693", enabled=True),
    ]
    executor = VisualMt5Executor(config=config, file_storage=FileStorage(config.paths))

    prepared: list[tuple[str, bool, bool]] = []
    monkeypatch.setattr(executor, "_current_account_login_from_title", lambda: "11330693")
    monkeypatch.setattr(
        executor,
        "_prepare_account_context",
        lambda account_alias, technical_log, assume_current_account=False, launch_terminal=True: prepared.append(
            (account_alias, assume_current_account, launch_terminal)
        ),
    )
    monkeypatch.setattr(executor, "_save_screenshot", lambda run_id, name: f"shots/{name}.png")

    result = executor.test_accounts()

    assert result.status == "success"
    assert prepared == [
        ("main_2", True, True),
        ("main_1", False, False),
    ]
