# 6. Operación por línea de comandos

## 6.1 Arranque

Abra una terminal en la raíz de BITS y ejecute:

```batch
portable\python312\tools\python.exe run_cli.py
```

Sin opciones de entrada, el programa procesa los PDF situados directamente en `input/`, ordenados por nombre. No busca en subcarpetas.

El proceso devuelve código `0` cuando termina correctamente y `1` ante una falla controlada de la ejecución. `argparse` devuelve `2` si una opción o un argumento no son válidos.

## 6.2 Configuración predeterminada

La CLI usa:

- plantilla `template/aircraft_log.json`;
- salida `output/`;
- 150 DPI máximos para la página completa;
- todos los hilos de CPU disponibles como presupuesto, con uso efectivo limitado por memoria y por un máximo de 32 procesos;
- corrección de inclinación y alineación activas;
- preprocesado de recortes activo;
- PaddleOCR fijo en CPU;
- un PDF único plano;
- verificación de flota desactivada;
- posibles discrepancias y `errores.pdf` desactivados;
- día específico en el CSV.

La CLI no mueve los PDF terminados a `input/processed/`.

## 6.3 Opciones

| Opción | Función |
|---|---|
| `--pdf ARCHIVO` | Procesa un PDF específico y tiene prioridad sobre `--input-dir`. |
| `--input-dir CARPETA` | Define la carpeta de entrada. Valor inicial: `input`. |
| `--template ARCHIVO` | Define la plantilla JSON. |
| `--output-dir CARPETA` | Define la raíz de resultados. Valor inicial: `output`. |
| `--dpi N` | Define el DPI máximo de página. Valor inicial: `150`. |
| `--pages RANGO` | Limita páginas sobre la numeración global del batch. |
| `--debug` | Genera `debug.pdf` con páginas fuente limpias. |
| `--reference-page N` | Selecciona la página de referencia. Valor inicial: `1`. |
| `--threads N`, `--cpu-threads N` | Define el presupuesto total de hilos de CPU. |
| `--no-deskew` | Desactiva la corrección de inclinación. |
| `--no-align` | Desactiva la alineación. |
| `--no-remove-printed` | Desactiva la construcción del fondo impreso cuando una función lo requiere. |
| `--no-crop-preprocess` | Envía recortes sin localización de tinta ni reescalado. |
| `--separar-por avion` | Crea grupos por matrícula. |
| `--separar-por mes` | Crea grupos por mes. Puede repetirse junto con `avion`. |
| `--un-solo-pdf` | Une los grupos en un PDF con páginas divisorias. Sin criterios, produce el mismo PDF plano predeterminado. |
| `--discrepancias` | Separa las páginas con posibles discrepancias. |
| `--errores` | Genera `errores.pdf`. |
| `--recortes-firmas` | Guarda los recortes usados por el detector de firmas. |
| `--verificar-flota` | Activa la comparación contra el catálogo de aeronaves. |
| `--lista-flota ARCHIVO` | Selecciona otro catálogo. Solo tiene efecto con `--verificar-flota`. Valor inicial: `fleet.json`. |
| `--fecha-csv especifica` | Conserva el día válido y usa fin de mes si falta. |
| `--fecha-csv fin-de-mes` | Representa todas las fechas con el último día del mes. |
| `--campos-importantes LISTA` | Define las columnas del CSV mínimo, separadas por coma. |
| `--verbose` | Muestra detalle de depuración y traceback en la terminal. |
| `-h`, `--help` | Muestra la ayuda incorporada. |

No hay opciones de GPU, motor OCR ni modelo: son fijos en la configuración de producción.

## 6.4 Formato del rango

El rango es inclusivo y comienza en 1. Formas válidas:

| Forma | Resultado |
|---|---|
| `15` | Solo la página global 15. |
| `1-40` | Páginas 1 a 40. |
| `200-` | Desde la 200 hasta el final. |
| `-25` | Desde el inicio hasta la 25. |

También se aceptan dos puntos o guion largo como separador. El programa abre brevemente todos los PDF para contar páginas y repartir el rango; solo los tramos incluidos pasan al OCR. Los números guardados en los reportes siguen siendo los números locales reales de cada archivo.

## 6.5 Ejemplos

Procesar toda la entrada:

```batch
portable\python312\tools\python.exe run_cli.py
```

Procesar un PDF y un rango:

```batch
portable\python312\tools\python.exe run_cli.py --pdf input\libro.pdf --pages 1-40
```

Crear varios PDF por aeronave y mes:

```batch
portable\python312\tools\python.exe run_cli.py --separar-por avion --separar-por mes
```

Crear un PDF único con divisiones y revisiones:

```batch
portable\python312\tools\python.exe run_cli.py --separar-por avion --un-solo-pdf --discrepancias --errores
```

Verificar contra otra lista de flota:

```batch
portable\python312\tools\python.exe run_cli.py --verificar-flota --lista-flota datos\fleet.json
```

Auditar firmas en un tramo pequeño:

```batch
portable\python312\tools\python.exe run_cli.py --pages 1-10 --recortes-firmas
```

## 6.6 Información durante la ejecución

La terminal informa:

- entrada, plantilla y carpeta de salida;
- modelos OCR fijos;
- distribución de procesos e hilos;
- avance global de páginas;
- resumen `OK`, `WARNING` y `ERROR` por PDF;
- correcciones de matrícula y fecha por libro;
- rutas finales de CSV, JSON, estadísticas y PDF;
- tiempo total.

Use `--verbose` cuando deba diagnosticar una falla. El registro completo se conserva en `logs/` dentro de la carpeta de la ejecución.

## 6.7 Diferencias frente a la GUI

| Función | GUI | CLI |
|---|---|---|
| DPI inicial de página | 200 | 150 |
| Verificación de flota | Activada | Requiere `--verificar-flota` |
| Entrega inicial | PDF único por matrícula, con discrepancias | PDF único plano |
| Reserva para interfaz | Un hilo, salvo cambio del operador | No aplica |
| Cancelación controlada | Sí | No |
| Preprocesamiento visual | Sí | No |
| Archivo a `input/processed/` | Solo PDF situados directamente en `input/`, tras una ejecución y exportación completas | No |
| Recortes de firmas | No | Sí |

Ambas superficies aplican el mismo núcleo, los mismos correctores y el mismo generador de salidas.
