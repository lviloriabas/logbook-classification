"""Cálculo de la configuración de paralelismo del OCR (solo CPU).

Medido sobre la carga OCR real del pipeline (PP-OCRv6_medium_det +
PP-OCRv5_mobile_rec, PaddlePaddle CPU, Ryzen 5 3600), en páginas por
segundo. Las máquinas más pequeñas se simulan confinando la afinidad de CPU
(los procesos hijos heredan la máscara), con las parejas SMT verificadas:
los lógicos 0 y 1 son el mismo núcleo físico (dos procesos ahí rinden 0.105;
repartidos en dos núcleos, 0.167).

    equipo              procesos x hilos    páginas/s
    2 núcleos / 4 hilos     4 x 1             0.221   <- mejor
                            2 x 1             0.169
                            2 x 2             0.158
    4 núcleos / 8 hilos     8 x 1             0.431   <- mejor
                            4 x 1             0.321
                            4 x 2             0.310
    6 núcleos / 12 hilos   12 x 1             0.516   <- mejor
                            8 x 2             0.494
                            8 x 1             0.460
                            6 x 1             0.441
                            6 x 2             0.430
                            6 x 3             0.376
                            4 x 2             0.339
                            4 x 1             0.331
                            4 x 3             0.317
                            3 x 2             0.285
                            3 x 4             0.261
                            3 x 1             0.242
                            2 x 3             0.209
                            2 x 2             0.205
                            2 x 6             0.197
                            2 x 1             0.172
                            1 x 12            0.119
                            1 x 3             0.116
                            1 x 1             0.091

La regla es la misma en los tres equipos: **un proceso por hilo lógico, con
un solo hilo interno**. A igualdad de concurrencia total los procesos ganan
por 20-40% (4x1 frente a 2x2, 8x1 frente a 4x2, 12x1 frente a 6x2), y parar
en los núcleos físicos deja mucho sobre la mesa: 12x1 rinde 17% más que 6x1,
y en el equipo de 4 núcleos 8x1 rinde 34% más que 4x1. El SMT sí aporta en
esta carga, al contrario de lo que suponía la versión anterior.

Los hilos internos aceleran un proceso aislado, pero poco y con techo bajo:
1.31x, ya saturado en 3 hilos (782 -> 592 ms por recorte; 6 y 12 hilos miden
igual que 3). Por eso solo se reparten cuando sobran hilos del presupuesto y
nunca pasan de ``_MAX_ENGINE_THREADS``.

Paralelismo con hilos de Python (``ThreadPoolExecutor`` repartiendo páginas
en un solo proceso) está descartado por medición, no por teoría: 6 hilos
rinden 0.090 páginas/s, exactamente lo mismo que un solo hilo (0.091). El
GIL no se suelta durante la inferencia, así que solo los procesos escalan.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

# Tope duro de procesos. No es el punto donde la curva se aplana —medido,
# sigue subiendo hasta un proceso por hilo lógico— sino un seguro contra
# equipos con muchísimos hilos, donde cada proceso cuesta una copia de los
# modelos y el límite real pasa a ser la memoria.
_MAX_WORKERS = 16
# Techo de hilos internos por motor: la inferencia satura en 3 (782 -> 592 ms
# por recorte; con 6 y 12 hilos mide lo mismo que con 3).
_MAX_ENGINE_THREADS = 3
# Memoria residente medida por proceso worker, con margen. No son solo los
# modelos: medido, un worker llega a 808 MB = 540 MB de detector +
# reconocedor cargados, más ~268 MB de cache interno de MuPDF por renderizar
# páginas (se estabiliza ahí y no crece; ``store_shrink`` lo devuelve entero).
# El valor anterior, 620 MB, contaba solo los modelos y subestimaba el pico un
# 30%: con un proceso por hilo lógico eso llegó a reventar un lote de 12
# workers con un fallo de paginación al importar numpy.
_WORKER_MEMORY_MB = 850
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


def recommended_parallelism(total_threads: int | None = None) -> tuple[int, int]:
    """Distribuye un presupuesto de hilos entre procesos e hilos internos.

    ``total_threads`` es el presupuesto de hilos lógicos que el usuario cede
    al procesamiento, **no** el tamaño del equipo. Son cantidades distintas:
    la GUI reserva un hilo para la interfaz y además deja elegir menos. El
    tamaño del equipo se consulta siempre a la máquina.

    Confundirlas era un error caro. Al estimar los núcleos como la mitad del
    presupuesto, pedir 3 hilos daba ``(1 worker x 3 hilos)`` —como si el
    equipo tuviera un solo núcleo— cuando lo correcto son 3 procesos: medido,
    0.116 frente a 0.239 páginas/s, la mitad de velocidad. Con 2 hilos el
    mismo fallo costaba 1.58x.

    El objetivo es **un proceso por hilo lógico del presupuesto**, que es la
    configuración más rápida medida en los tres tamaños de equipo probados.
    Los límites reales son la memoria libre y el tope duro, no los núcleos
    físicos: pararse en ellos costaba un 17% en el equipo de 6 núcleos y un
    34% en el de 4.

    Los hilos internos solo se reparten con lo que sobra cuando la memoria
    impide crear más procesos —ese proceso aprovecha los hilos restantes en
    vez de desperdiciarlos— y nunca pasan de ``_MAX_ENGINE_THREADS``, porque
    la inferencia satura en 3.
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
        available_cpu_threads(),
        _memory_worker_cap(),
    )
    threads = min(max(1, total // workers), _MAX_ENGINE_THREADS)
    return workers, threads
