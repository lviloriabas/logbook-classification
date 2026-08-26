# Auditoría del portable OCR

Fecha de verificación: 2026-08-14.

## Configuración de producción

La GUI y `run_cli.py` fijan internamente:

- detector `PP-OCRv6_medium_det` (59.4 MiB);
- reconocedor `PP-OCRv5_mobile_rec` (16.4 MiB);
- sin segundo motor de fechas;
- sin motores alternativos: Tesseract se eliminó del programa el 2026-08-26.

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

## Ya eliminados

`portable/tesseract` (237.9 MiB) puede borrarse: desde el 2026-08-26 el
programa tiene un solo motor y ni la aplicación ni las herramientas de
diagnóstico lo invocan. Lo mismo vale para `portable/llama`, los modelos del
verificador VLM que se retiró en la misma fecha.

## No eliminar

No se deben eliminar `paddleocr`, `paddlepaddle`, `paddlex` ni sus
dependencias Python: son necesarias para cargar los dos modelos seleccionados.
