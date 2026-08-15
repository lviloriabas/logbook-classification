"""Cálculo de la configuración de paralelismo del OCR (solo CPU).

Medición sobre los recortes reales del pipeline (PP-OCRv5_mobile_rec,
PaddlePaddle 3.3.1 CPU, 12 hilos lógicos): los hilos internos del motor
**no aceleran la inferencia**. El mismo lote tarda lo mismo con 1 hilo que
con 12 (~170 ms por recorte en los seis casos). Lo que sí escala es el
número de procesos:

    procesos x hilos    páginas/s
    1 x 12                0.38
    2 x 6                 0.62
    3 x 4                 0.87   <- reparto anterior
    6 x 2                 1.35
    8 x 1                 1.37
    12 x 1                1.39

La curva se aplana al llegar al número de núcleos físicos (memoria y caché,
no hilos lógicos). Por eso el reparto apunta a **un proceso OCR por núcleo
físico** y deja los hilos internos como resto, en lugar de repartir cuatro
hilos por proceso.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

# Tope duro de procesos: más allá de esto la curva ya está plana y cada
# proceso cuesta una copia de los modelos.
_MAX_WORKERS = 8
# Memoria residente medida por proceso con el detector y el reconocedor
# cargados (~478 MB) más un margen. La app es portable y debe funcionar en
# equipos de oficina, así que el reparto nunca puede agotar la RAM.
_WORKER_MEMORY_MB = 620
# Memoria que se deja libre para el sistema, la GUI y las salidas PDF.
_RESERVED_MEMORY_MB = 1536


def available_cpu_threads() -> int:
    """Devuelve el número de hilos lógicos disponibles para la aplicación."""
    return max(1, os.cpu_count() or 1)


def available_memory_mb() -> int:
    """Memoria física libre en MB (0 si no se puede determinar).

    Usa solo la API de Windows a través de ``ctypes`` para no añadir
    dependencias al paquete portable.
    """
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(
            ctypes.byref(status)
        ):
            return 0
        return int(status.ullAvailPhys // (1024 * 1024))
    except (AttributeError, OSError):
        # Cualquier plataforma sin esta API (pruebas fuera de Windows).
        return 0


def _memory_worker_cap() -> int:
    """Cuántos procesos OCR caben en la memoria libre del equipo."""
    available = available_memory_mb()
    if available <= 0:
        return _MAX_WORKERS
    usable = available - _RESERVED_MEMORY_MB
    if usable <= 0:
        return 1
    return max(1, usable // _WORKER_MEMORY_MB)


def physical_core_estimate(total_threads: int) -> int:
    """Estima los núcleos físicos a partir de los hilos lógicos.

    Los equipos donde corre la aplicación tienen SMT activo, así que la
    mitad de los hilos lógicos es una estimación fiable y no necesita
    consultar el hardware (lo que rompería la portabilidad).
    """
    if total_threads <= 2:
        return 1
    return max(1, total_threads // 2)


def recommended_parallelism(total_threads: int | None = None) -> tuple[int, int]:
    """Distribuye un total de hilos entre procesos e hilos internos.

    Como los hilos internos no aceleran el motor, se maximiza el número de
    procesos hasta el número de núcleos físicos, el tope duro y lo que
    permita la memoria libre.

    El reparto **no** exige que el número de procesos divida exactamente el
    total. Exigirlo hacía que cualquier total primo colapsara a un solo
    proceso: la GUI reserva un hilo para la interfaz, así que un equipo de
    12 hilos pedía 11 y terminaba ejecutando ``(1 worker x 11 hilos)``, la
    configuración más lenta de todas las medidas. Sobra como mucho un hilo
    por proceso, que de todos modos no acelera la inferencia.
    """
    total = max(
        1,
        int(total_threads)
        if total_threads is not None
        else available_cpu_threads(),
    )
    workers = min(
        _MAX_WORKERS,
        total,
        physical_core_estimate(total),
        _memory_worker_cap(),
    )
    return workers, max(1, total // workers)
