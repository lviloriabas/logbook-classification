"""Identidad de la aplicación usada por la barra de tareas de Windows."""

from unittest.mock import patch

from app.utils.app_identity import (
    APP_USER_MODEL_ID,
    set_windows_app_user_model_id,
)


def test_sets_explicit_windows_app_id_before_qt_starts():
    with patch("sys.platform", "win32"), patch(
        "ctypes.windll", create=True
    ) as windll:
        windll.shell32.SetCurrentProcessExplicitAppUserModelID.return_value = 0

        assert set_windows_app_user_model_id() is True

    windll.shell32.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with(
        APP_USER_MODEL_ID
    )


def test_app_id_is_a_safe_noop_outside_windows():
    with patch("sys.platform", "linux"):
        assert set_windows_app_user_model_id() is False


def test_app_id_failure_does_not_prevent_the_gui_from_opening():
    with patch("sys.platform", "win32"), patch(
        "ctypes.windll", create=True
    ) as windll:
        setter = windll.shell32.SetCurrentProcessExplicitAppUserModelID
        setter.side_effect = OSError("shell API unavailable")

        assert set_windows_app_user_model_id() is False
