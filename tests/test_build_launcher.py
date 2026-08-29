"""El empaquetado no depende de la computadora donde vive el proyecto."""

from pathlib import Path

from tools.build_launcher import build_command


def test_build_paths_are_derived_from_the_current_portable_root(tmp_path):
    moved_root = tmp_path / "BITS copiado"
    command = build_command(moved_root, "python-portable.exe")
    resolved = moved_root.resolve()

    assert command[0] == "python-portable.exe"
    assert command[command.index("--name") + 1] == "BITSBitacoras"
    assert str(resolved / "assets" / "icon.ico") in command
    assert str(resolved / "launcher_gui.py") in command
    assert str(resolved / "build") in command
