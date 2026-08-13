"""Pruebas de selección explícita de modelos multimodales."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from app.verifier import launcher
from app.verifier.launcher import resolve_paths


def test_resolve_paths_prefers_explicit_model_when_no_environment_override(
    tmp_path: Path,
):
    binary = tmp_path / "llama-server"
    model = tmp_path / "smol.gguf"
    mmproj = tmp_path / "smol-mmproj.gguf"
    for path in (binary, model, mmproj):
        path.touch()

    with patch.dict(
        os.environ,
        {
            "BITS_LLAMA_BIN": str(binary),
            "BITS_LLAMA_MODEL": "",
            "BITS_LLAMA_MMPROJ": "",
        },
        clear=False,
    ):
        paths = resolve_paths(model=model, mmproj=mmproj)

    assert paths.model == model
    assert paths.mmproj == mmproj
    assert paths.complete


def test_environment_model_override_wins_over_explicit_path(tmp_path: Path):
    binary = tmp_path / "llama-server"
    env_model = tmp_path / "qwen.gguf"
    env_mmproj = tmp_path / "qwen-mmproj.gguf"
    explicit_model = tmp_path / "smol.gguf"
    for path in (binary, env_model, env_mmproj, explicit_model):
        path.touch()

    with patch.dict(
        os.environ,
        {
            "BITS_LLAMA_BIN": str(binary),
            "BITS_LLAMA_MODEL": str(env_model),
            "BITS_LLAMA_MMPROJ": str(env_mmproj),
        },
        clear=False,
    ):
        paths = resolve_paths(model=explicit_model, mmproj=env_mmproj)

    assert paths.model == env_model
    assert paths.mmproj == env_mmproj


def test_autodetection_prefers_qwen_over_other_models(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    qwen = models / "Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
    smol = models / "SmolVLM2-500M-Instruct-Q4_K_M.gguf"
    qwen_mmproj = models / "mmproj-Qwen3-VL-8B-Instruct-f16.gguf"
    smol_mmproj = models / "mmproj-SmolVLM2-500M-Instruct-f16.gguf"
    for path in (qwen, smol, qwen_mmproj, smol_mmproj):
        path.touch()

    with patch.object(launcher, "_models_dir", return_value=models):
        assert launcher._pick_model() == qwen
        assert launcher._pick_mmproj() == qwen_mmproj


def test_explicit_qwen_model_selects_matching_mmproj(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    qwen = models / "Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
    smol_mmproj = models / "mmproj-SmolVLM2-500M-Instruct-f16.gguf"
    qwen_mmproj = models / "mmproj-F16.gguf"
    for path in (qwen, smol_mmproj, qwen_mmproj):
        path.touch()

    with patch.object(launcher, "_models_dir", return_value=models):
        assert launcher._pick_mmproj(qwen) == qwen_mmproj
