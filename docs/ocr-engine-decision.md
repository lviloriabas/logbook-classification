# Decisión del motor OCR

Fecha de evaluación: 2026-08-14.

Se evaluaron los diez escaneos etiquetados en
`tests/fixtures/date_images_ground_truth.json` con
`tools/evaluate_date_images.py`, sin segunda pasada ni OCR por ranuras.

| Configuración | Fecha exacta | Detectada | Tiempo por imagen |
|---|---:|---:|---:|
| Paddle v5 mobile rec + v6 medium det | 8/10 | 10/10 | 9.38 s |
| Paddle v6 medium rec + v6 medium det | 5/10 | 7/10 | 5.35 s |
| Paddle v5 mobile rec + v6 tiny det | 4/10 | 5/10 | 2.01 s |
| Tesseract directo | 0/10 | 0/10 | 0.30 s |

## Selección

Producción usa siempre `PP-OCRv5_mobile_rec` con
`PP-OCRv6_medium_det`. Fue la única configuración que detectó todas las
fechas y alcanzó 80% de exactitud completa sin encadenar motores. El error
restante se trata mediante la heurística secuencial por libro, no ejecutando
otro OCR sobre cada página.

La GUI no expone selectores de motor general ni de fechas. También mantiene
desactivadas la segunda pasada Tesseract y la lectura Tesseract por ranuras.
Las opciones del CLI quedan reservadas a diagnóstico y reproducción de esta
comparación; no son configuración del usuario del portable.

## Reproducción

```powershell
portable\python312\tools\python.exe tools\evaluate_date_images.py --run `
  --engine paddle --rec-model PP-OCRv5_mobile_rec `
  --det-model PP-OCRv6_medium_det --no-fallback --no-slot-ocr
```

PaddleX 3.7.2 requiere instalar su extra `ocr`; tener solamente `paddlex` y
`paddleocr` no basta para inicializar el pipeline.
