"""Pruebas del cálculo de paralelismo predeterminado."""

from __future__ import annotations

import unittest
from unittest import mock

from app.core import parallelism
from app.core.parallelism import (
    physical_core_estimate,
    recommended_parallelism,
)


class TestRecommendedParallelism(unittest.TestCase):
    """El reparto se mide con memoria abundante para ser determinista."""

    def setUp(self) -> None:
        patcher = mock.patch.object(
            parallelism, "_memory_worker_cap",
            return_value=parallelism._MAX_WORKERS,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_supera_los_hilos_seleccionados(self):
        for available in (1, 2, 4, 8, 11, 12, 16, 20, 24):
            workers, threads = recommended_parallelism(available)
            self.assertLessEqual(workers * threads, available)
            self.assertGreaterEqual(workers, 1)
            self.assertLessEqual(workers, parallelism._MAX_WORKERS)
            self.assertGreaterEqual(threads, 1)

    def test_un_proceso_por_nucleo_fisico(self):
        self.assertEqual(recommended_parallelism(12), (6, 2))
        self.assertEqual(recommended_parallelism(8), (4, 2))
        self.assertEqual(recommended_parallelism(6), (3, 2))
        self.assertEqual(recommended_parallelism(4), (2, 2))

    def test_un_total_primo_no_colapsa_a_un_proceso(self):
        """La GUI reserva un hilo, así que el total suele ser impar.

        Con la regla de divisores exactos, 11 hilos daban un solo proceso
        (la configuración más lenta medida).
        """
        for total in (5, 7, 11, 13):
            workers, _threads = recommended_parallelism(total)
            self.assertGreater(
                workers, 1, f"{total} hilos colapsaron a un proceso"
            )
        self.assertEqual(recommended_parallelism(11), (5, 2))

    def test_equipos_pequenos(self):
        self.assertEqual(recommended_parallelism(1), (1, 1))
        self.assertEqual(recommended_parallelism(2), (1, 2))

    def test_tope_duro_de_procesos(self):
        workers, _threads = recommended_parallelism(64)
        self.assertEqual(workers, parallelism._MAX_WORKERS)

    def test_estimacion_de_nucleos_fisicos(self):
        self.assertEqual(physical_core_estimate(12), 6)
        self.assertEqual(physical_core_estimate(2), 1)
        self.assertEqual(physical_core_estimate(1), 1)


class TestMemoryCap(unittest.TestCase):
    def test_la_memoria_libre_limita_los_procesos(self):
        with mock.patch.object(
            parallelism, "available_memory_mb", return_value=2600
        ):
            workers, _threads = recommended_parallelism(12)
        self.assertEqual(workers, 1)

    def test_sin_lectura_de_memoria_no_se_limita(self):
        with mock.patch.object(
            parallelism, "available_memory_mb", return_value=0
        ):
            workers, _threads = recommended_parallelism(12)
        self.assertEqual(workers, 6)


if __name__ == "__main__":
    unittest.main()
