"""Contrato del conjunto fijo de evaluación de fechas."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tools.evaluate_date_images import evaluate


FIXTURE = Path(__file__).parent / "fixtures" / "date_images_ground_truth.json"


def test_ground_truth_has_unique_valid_samples():
    samples = json.loads(FIXTURE.read_text(encoding="utf-8"))["samples"]
    assert len(samples) == 10
    assert len({sample["file"] for sample in samples}) == len(samples)
    assert all(re.fullmatch(r"\d{7}", sample["log_number"]) for sample in samples)
    assert all(re.fullmatch(r"20\d{2}/\d{2}/\d{2}", sample["date"])
               for sample in samples)


def test_evaluator_counts_exact_components():
    truth = {
        "a.png": {"file": "a.png", "log_number": "1234500", "date": "2026/07/25"},
        "b.png": {"file": "b.png", "log_number": "1234501", "date": "2026/07/26"},
    }
    report = evaluate(truth, {"a.png": "2026/07/25", "b.png": "2026/08/26"})
    assert report["counts"] == {
        "total": 2,
        "date_exact": 1,
        "date_detected": 2,
        "year_exact": 2,
        "month_exact": 1,
        "day_exact": 2,
    }
