"""Icono nativo usado por la barra de tareas de Windows."""

from unittest.mock import call, patch

from app.utils.app_identity import (
    ICON_BIG,
    ICON_SMALL,
    WM_SETICON,
    set_windows_taskbar_icon,
)


class _Window:
    def winId(self) -> int:
        return 12345


def test_installs_big_and_small_icons_on_the_native_window(tmp_path):
    icon = tmp_path / "icon.ico"
    icon.touch()
    window = _Window()

    with patch("sys.platform", "win32"), patch(
        "ctypes.windll", create=True
    ) as windll:
        user32 = windll.user32
        user32.GetSystemMetrics.return_value = 32
        user32.LoadImageW.side_effect = [1001, 1002]

        assert set_windows_taskbar_icon(window, icon) is True

    assert window._bits_native_icon_handles == (1001, 1002)
    assert user32.SendMessageW.call_args_list == [
        call(12345, WM_SETICON, ICON_BIG, 1001),
        call(12345, WM_SETICON, ICON_SMALL, 1002),
    ]


def test_taskbar_icon_is_a_safe_noop_outside_windows(tmp_path):
    icon = tmp_path / "icon.ico"
    icon.touch()
    with patch("sys.platform", "linux"):
        assert set_windows_taskbar_icon(_Window(), icon) is False


def test_taskbar_icon_requires_an_existing_ico(tmp_path):
    with patch("sys.platform", "win32"):
        assert set_windows_taskbar_icon(
            _Window(), tmp_path / "missing.ico"
        ) is False
        png = tmp_path / "icon.png"
        png.touch()
        assert set_windows_taskbar_icon(_Window(), png) is False


def test_native_icon_failure_does_not_prevent_gui_startup(tmp_path):
    icon = tmp_path / "icon.ico"
    icon.touch()
    with patch("sys.platform", "win32"), patch(
        "ctypes.windll", create=True
    ) as windll:
        windll.user32.LoadImageW.side_effect = OSError("icon unavailable")

        assert set_windows_taskbar_icon(_Window(), icon) is False
