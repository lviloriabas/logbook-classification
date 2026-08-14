"""Estimación de tiempo basada en throughput real de pared."""

from __future__ import annotations


def wall_ms_per_page(elapsed_seconds: float, completed_pages: int) -> float | None:
    """Costo efectivo por página, incluyendo la concurrencia del lote."""
    if elapsed_seconds <= 0 or completed_pages <= 0:
        return None
    return elapsed_seconds * 1000.0 / completed_pages


def estimate_remaining_seconds(
    *,
    total_pages: int,
    completed_pages: int,
    elapsed_seconds: float,
    cached_ms_per_page: float,
) -> float | None:
    """Estima lo pendiente mezclando historial y throughput observado.

    El ritmo observado usa tiempo de pared del lote, no la suma de tiempos de
    cada worker. Durante las primeras páginas conserva más peso del historial
    para evitar saltos por la inicialización del modelo; a partir de veinte
    páginas el lote actual domina en 90%.
    """
    if total_pages <= 0:
        return None
    pending = max(0, total_pages - max(0, completed_pages))
    if pending == 0:
        return 0.0
    cached = max(1.0, float(cached_ms_per_page))
    observed = wall_ms_per_page(elapsed_seconds, completed_pages)
    if observed is None:
        rate = cached
    else:
        observed_weight = min(0.90, completed_pages / 20.0)
        rate = cached * (1.0 - observed_weight) + observed * observed_weight
    return pending * rate / 1000.0

