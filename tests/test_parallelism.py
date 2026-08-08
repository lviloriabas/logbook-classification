"""Pruebas del cálculo de paralelismo predeterminado."""

from __future__ import annotations

import unittest

from app.core.parallelism import recommended_parallelism


class TestRecommendedParallelism(unittest.TestCase):
    def test_usa_todos_los_hilos_disponibles(self):
        for available in (1, 2, 4, 8, 12, 16, 20, 24):
            workers, threads = recommended_parallelism(available)
            self.assertEqual(workers * threads, available)
            self.assertGreaterEqual(workers, 1)
            self.assertLessEqual(workers, 5)
            self.assertGreaterEqual(threads, 1)

    def test_prioriza_cuatro_hilos_por_worker(self):
        self.assertEqual(recommended_parallelism(20), (5, 4))
        self.assertEqual(recommended_parallelism(16), (4, 4))
        self.assertEqual(recommended_parallelism(12), (3, 4))
        self.assertEqual(recommended_parallelism(8), (2, 4))

    def test_respeta_cualquier_total_seleccionado(self):
        for selected in (3, 5, 7, 10, 18):
            workers, threads = recommended_parallelism(selected)
            self.assertEqual(workers * threads, selected)


if __name__ == "__main__":
    unittest.main()
