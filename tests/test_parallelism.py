"""Pruebas del cálculo de paralelismo predeterminado."""

from __future__ import annotations

import unittest
from unittest import mock

from app.core import parallelism
from app.core.parallelism import recommended_parallelism


class ParallelismTestCase(unittest.TestCase):
    """Fija el equipo simulado: el reparto depende de la máquina.

    ``recommended_parallelism`` consulta los hilos lógicos reales, así que
    sin fijarlos las expectativas dependerían del PC donde corren las
    pruebas. Se simula el equipo de referencia de las mediciones: 12 hilos
    lógicos (6 núcleos físicos con SMT).
    """

    machine_threads = 12

    def _patch(self, target: str, value) -> None:
        patcher = mock.patch.object(parallelism, target, return_value=value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def setUp(self) -> None:
        self._patch("available_cpu_threads", self.machine_threads)


class TestRecommendedParallelism(ParallelismTestCase):
    """El reparto se mide con memoria abundante para ser determinista."""

    def setUp(self) -> None:
        super().setUp()
        self._patch("_memory_worker_cap", parallelism._MAX_WORKERS)

    def test_no_supera_los_hilos_seleccionados(self):
        for available in (1, 2, 4, 8, 11, 12, 16, 20, 24):
            workers, threads = recommended_parallelism(available)
            self.assertLessEqual(workers * threads, available)
            self.assertGreaterEqual(workers, 1)
            self.assertLessEqual(workers, parallelism._MAX_WORKERS)
            self.assertGreaterEqual(threads, 1)

    def test_un_proceso_por_hilo_logico(self):
        """La configuración más rápida medida en los tres equipos probados.

        Pararse en los núcleos físicos costaba 17% (6 núcleos: 12x1 = 0.516
        frente a 6x1 = 0.441 páginas/s) y 34% en el equipo de 4 núcleos
        (8x1 = 0.431 frente a 4x1 = 0.321).
        """
        self.assertEqual(recommended_parallelism(12), (12, 1))
        self.assertEqual(recommended_parallelism(8), (8, 1))
        self.assertEqual(recommended_parallelism(6), (6, 1))
        self.assertEqual(recommended_parallelism(4), (4, 1))

    def test_un_presupuesto_pequeno_no_colapsa_a_un_proceso(self):
        """Medido: 3 procesos rinden 2.06x más que 1 proceso con 3 hilos.

        El presupuesto de hilos no dice cuántos núcleos tiene el equipo. Al
        estimarlos como la mitad del presupuesto, pedir 2 o 3 hilos daba un
        solo proceso, la configuración más lenta de todas las medidas.
        """
        self.assertEqual(recommended_parallelism(3), (3, 1))
        self.assertEqual(recommended_parallelism(2), (2, 1))

    def test_un_total_primo_no_colapsa_a_un_proceso(self):
        """La GUI reserva un hilo, así que el total suele ser impar."""
        for total in (5, 7, 11, 13):
            workers, _threads = recommended_parallelism(total)
            self.assertGreater(
                workers, 1, f"{total} hilos colapsaron a un proceso"
            )
        self.assertEqual(recommended_parallelism(11), (11, 1))

    def test_equipos_pequenos(self):
        self.assertEqual(recommended_parallelism(1), (1, 1))

    def test_tope_duro_de_procesos(self):
        with mock.patch.object(
            parallelism, "available_cpu_threads", return_value=64
        ):
            workers, _threads = recommended_parallelism(64)
        self.assertEqual(workers, parallelism._MAX_WORKERS)

    def test_los_hilos_internos_nunca_pasan_del_techo(self):
        """La inferencia satura en 3 hilos; repartir más solo desperdicia."""
        with mock.patch.object(
            parallelism, "_memory_worker_cap", return_value=1
        ):
            workers, threads = recommended_parallelism(12)
        self.assertEqual((workers, threads), (1, parallelism._MAX_ENGINE_THREADS))


class TestMemoryCap(ParallelismTestCase):
    def test_la_memoria_libre_limita_los_procesos(self):
        with mock.patch.object(
            parallelism, "available_memory_mb", return_value=2600
        ):
            workers, threads = recommended_parallelism(12)
        self.assertEqual(workers, 1)
        # Un proceso solitario sí aprovecha los hilos internos (1.31x): son
        # el único paralelismo que le queda, hasta el techo de saturación.
        self.assertEqual(threads, parallelism._MAX_ENGINE_THREADS)

    def test_sin_lectura_de_memoria_no_se_limita(self):
        with mock.patch.object(
            parallelism, "available_memory_mb", return_value=0
        ):
            workers, _threads = recommended_parallelism(12)
        self.assertEqual(workers, 12)


if __name__ == "__main__":
    unittest.main()
