"""Cálculo de la configuración de paralelismo del OCR."""

from __future__ import annotations

import math
import os


_MAX_WORKERS = 5
_TARGET_THREADS_PER_WORKER = 4


def available_cpu_threads() -> int:
    """Devuelve el número de hilos lógicos disponibles para la aplicación."""
    return max(1, os.cpu_count() or 1)


def recommended_parallelism(total_threads: int | None = None) -> tuple[int, int]:
    """Distribuye un total de hilos entre workers y hilos internos.

    Se mantienen como máximo cinco procesos para no cargar una copia del
    modelo OCR por cada proceso. Entre las combinaciones que usan exactamente
    todos los hilos se prefiere la que se acerca a cuatro hilos por proceso.
    """
    total = max(
        1,
        int(total_threads)
        if total_threads is not None
        else available_cpu_threads(),
    )
    max_workers = min(_MAX_WORKERS, total)
    target_workers = min(
        max_workers,
        max(1, math.ceil(total / _TARGET_THREADS_PER_WORKER)),
    )
    divisors = [
        workers for workers in range(1, max_workers + 1)
        if total % workers == 0
    ]
    workers = min(
        divisors,
        key=lambda value: (abs(value - target_workers), -value),
    )
    return workers, total // workers
