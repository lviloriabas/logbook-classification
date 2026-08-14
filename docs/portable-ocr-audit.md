# Auditoría del portable OCR

Fecha de verificación: 2026-08-14.

## Configuración de producción

La GUI y `run_cli.py` fijan internamente:

- detector `PP-OCRv6_medium_det` (59.4 MiB);
- reconocedor `PP-OCRv5_mobile_rec` (16.4 MiB);
- sin segundo motor de fechas;
- sin fallback ni lectura por ranuras con Tesseract.

La selección se basa en el benchmark reproducible descrito en
`docs/ocr-engine-decision.md`. Las opciones de motor/modelo se eliminaron de
la GUI y del CLI para que ningún flujo de producción cambie esta decisión.

## Candidatos confirmados para eliminación mecánica

| Ruta | Tamaño medido | Verificación |
|---|---:|---|
| `portable/paddlex/official_models/PP-OCRv6_medium_rec` | 73.3 MiB | No es referenciado por la configuración fija ni por el precargador portable. |
| `portable/paddlex/official_models/PP-OCRv6_tiny_det` | 1.9 MiB | No es referenciado por la configuración fija ni por el precargador portable. |

Total confirmado: aproximadamente 75.2 MiB. La herramienta diagnóstica
`tools/evaluate_date_images.py` acepta nombres alternativos para repetir el
benchmark, pero no depende de estas copias: Paddle puede resolverlos/descargarlos
explícitamente en un entorno de evaluación. Ningún flujo de la aplicación los
selecciona.

## No eliminar todavía

`portable/tesseract` ocupa 237.9 MiB y ya no participa en GUI ni CLI. Sin
embargo, `tools/evaluate_date_images.py` todavía permite ejecutar el benchmark
de comparación Tesseract. Por tanto no se marca como “sin uso en ningún flujo”
hasta decidir si esa herramienta diagnóstica debe conservar comparación offline.

Tampoco se deben eliminar `paddleocr`, `paddlepaddle`, `paddlex` ni sus
dependencias Python: son necesarias para cargar los dos modelos seleccionados.
