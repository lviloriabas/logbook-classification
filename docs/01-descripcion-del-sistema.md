# 1. Descripción del sistema

## 1.1 Función

BITS procesa documentos PDF de bitácoras aeronáuticas. Para cada página, el sistema:

1. prepara y alinea el escaneo;
2. lee las regiones definidas por una plantilla;
3. normaliza los datos de indexación;
4. determina presencia de firmas y marcas;
5. aplica reglas de página, libro y flota;
6. registra resultados y organiza las páginas fuente.

El resultado no reemplaza la revisión humana. Las lecturas incompletas, inciertas o no asignables quedan marcadas para revisión.

## 1.2 Limitaciones de operación

- Plataforma: Windows, sin permisos de administrador.
- Dispositivo de inferencia: CPU únicamente.
- Entrada de producción: archivos PDF.
- Operación normal: sin conexión, con intérprete, dependencias y modelos dentro de `portable/`.
- Unidad de indexación: página de bitácora.
- Formato de matrícula confirmado: `HP-XXXXCMP` o `HP-XXXXWWP`.
- Formato de `log_number`: siete dígitos.

> **PRECAUCIÓN:** No retire archivos ni apague el equipo mientras el estado indique procesamiento o generación de salidas. Una interrupción puede dejar una corrida parcial.

## 1.3 Reglas del libro

Cada libro contiene 50 páginas y corresponde a un solo avión. Los cinco primeros dígitos de `log_number` identifican la serie. Los dos últimos identifican la página:

- `00` a `49`: primera mitad o libro A;
- `50` a `99`: segunda mitad o libro B.

Dentro del mismo libro, una fecha posterior en `log_number` no debe ser anterior a la precedente. La misma fecha puede repetirse. El control de secuencia no se aplica entre libros distintos.

## 1.4 Elementos del programa

| Elemento | Función |
|---|---|
| `LogbookClassification.exe` | Abre la aplicación sin consola mediante el Python portable. |
| `run_gui.py` | Inicia la ventana principal. |
| `run_cli.py` | Ejecuta lotes desde una terminal. |
| `run_editor.py` | Inicia el editor visual de plantillas. |
| `app/core/` | Configura y coordina el procesamiento. |
| `app/vision/` | Renderiza, endereza, alinea y analiza tinta. |
| `app/ocr/` | Lee texto por regiones con PaddleOCR. |
| `app/templates/` | Valida y administra las plantillas JSON. |
| `app/models/` | Define los resultados compartidos de campo, página y documento. |
| `app/validation/` | Valida campos, libros, fechas, flota y firmas. |
| `app/reports/` | Escribe CSV, JSON, estadísticas y PDF. |
| `app/gui/` | Contiene la interfaz principal, el visor y el editor. |
| `app/verifier/` | Contiene el verificador VLM local opcional, desactivado en producción. |
| `app/utils/` | Resuelve entorno portable, archivos, registros y normalización. |
| `template/` | Contiene las definiciones de campos. |
| `input/` | Recibe los PDF pendientes. |
| `input/processed/` | Conserva los PDF terminados que procedían de `input/`. |
| `output/` | Conserva una carpeta independiente por corrida. |
| `portable/` | Contiene Python, dependencias, Tesseract y modelos locales. |
| `tools/` | Contiene utilidades de preparación, evaluación y calibración. |
| `tests/` | Contiene la verificación automatizada. |

## 1.5 Flujo funcional

```text
PDF de entrada
  -> selección del rango
  -> calibración y alineación
  -> lectura OCR y análisis de tinta
  -> validación de cada campo
  -> corrección por libro y verificación de flota
  -> clasificación de firmas y duplicados
  -> CSV + JSON + estadísticas + PDF de entrega
```

La interfaz y la línea de comandos usan el mismo generador de salidas. Con la misma entrada y las mismas opciones, la estructura de la corrida es equivalente.
