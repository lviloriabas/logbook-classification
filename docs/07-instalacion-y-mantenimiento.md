# 7. Instalación y mantenimiento

## 7.1 Paquete portable

La entrega operativa es la carpeta BITS completa. No instala servicios, no usa el registro y no requiere permisos de administrador.

Componentes de la copia estándar:

```text
portable/
├── python312/tools/
│   ├── python.exe
│   └── pythonw.exe
├── paddlex/official_models/
│   ├── PP-OCRv6_medium_det/
│   └── PP-OCRv5_mobile_rec/
└── tesseract/                    # opcional para la operación normal
    ├── tesseract.exe
    └── tessdata/
```

Python y los dos modelos PaddleOCR son indispensables. Tesseract queda disponible para tareas técnicas, pero la configuración normal de GUI y CLI no lo usa y puede omitirse con `-SkipTesseract`. `portable/llama/` es opcional y no forma parte de la operación normal.

> **PRECAUCIÓN:** No entregue solo `LogbookClassification.exe`. El ejecutable es un lanzador; necesita el código y `portable/` en la misma estructura relativa.

## 7.2 Comprobación

Ejecute:

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1 -Check
```

El informe debe mostrar disponibles Python, dependencias, modelos PaddleOCR y lanzador. Tesseract y VLM pueden figurar como opcionales según la configuración de la copia.

Compruebe después el arranque:

```batch
portable\python312\tools\python.exe run_gui.py
```

## 7.3 Reconstrucción desde el repositorio

La carpeta `portable/` no se versiona. Un clon nuevo requiere reconstrucción o una copia portable aprobada.

Con internet disponible, ejecute:

```batch
setup.cmd
```

o:

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

El proceso instala Python 3.12.10, dependencias, modelos PaddleOCR y Tesseract 5.4.0 dentro de `portable/`. Verifica por SHA-256 el paquete de Python y el instalador de Tesseract; pip y los precargadores aplican sus propias comprobaciones. La ejecución es repetible: un componente completo no vuelve a prepararse salvo que se use `-Force`.

La red solo se requiere para reconstruir el paquete. La aplicación terminada debe iniciar y procesar sin conexión.

## 7.4 Opciones de reconstrucción

| Opción | Efecto |
|---|---|
| `-Check` | Informa el estado sin descargar. |
| `-SkipTesseract` | Omite Tesseract. |
| `-Vlm` | Descarga modelos del verificador visual opcional. No lo activa en GUI ni CLI. |
| `-Launcher` | Instala PyInstaller y regenera el ejecutable. |
| `-Force` | Reconstruye los componentes principales y los modelos Paddle. Los GGUF existentes no se vuelven a descargar. |
| `-CleanCache` | Elimina `portable/.cache/` al terminar. |

## 7.5 Entorno local

`app/utils/portable.py` prepara las rutas antes de importar los motores:

| Variable | Valor normal |
|---|---|
| `PADDLE_PDX_CACHE_HOME` | `portable/paddlex` |
| `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK` | `1` |
| `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT` | `0` |
| `FLAGS_use_mkldnn` | `0` |
| `TESSDATA_PREFIX` | `portable/tesseract/tessdata` cuando existe |

Las variables se fijan con `setdefault`. Un valor ya definido en Windows tiene prioridad; compruébelo si una caché o una opción se desvía de la carpeta portable.

PaddleOCR v3 y el reconocedor PaddleX se crean con `device="cpu"`. La compatibilidad con PaddleOCR v2 usa `use_gpu=False`. oneDNN/MKL-DNN permanece desactivado por estabilidad en Windows.

No debe existir una descarga de modelos durante una corrida. Si Paddle intenta acceder a la red, detenga la entrega y compruebe la caché portable.

## 7.6 Dependencias de ejecución

`requirements.txt` registra las dependencias directas:

- OpenCV, NumPy, PyMuPDF y Pillow;
- PaddlePaddle, PaddleOCR, PaddleX y pytesseract;
- PySide6 y Send2Trash;
- Pydantic y Loguru.

PyMuPDF renderiza y copia los PDF. Poppler no es necesario.

## 7.7 Herramientas

| Herramienta | Uso |
|---|---|
| `tools/precache_paddle.py` | Descarga, inicializa y prueba en CPU los dos modelos OCR de producción. |
| `tools/precache_vlm.py` | Prepara modelos GGUF y, si se proporciona su paquete, `llama-server` para pruebas opcionales. |
| `tools/build_launcher.py` | Genera `LogbookClassification.exe` con PyInstaller, sin consola. |
| `tools/make_icon.py` | Regenera los iconos PNG e ICO. |
| `tools/evaluate_date_images.py` | Compara lecturas de fecha contra datos de referencia. |
| `tools/signature_labeling/extract.py` | Extrae recortes de firmas para etiquetado. |
| `tools/signature_labeling/label_gui.py` | Etiqueta recortes en forma visual. |
| `tools/signature_labeling/tune.py` | Evalúa umbrales y, con `--aplicar`, modifica la plantilla. |
| `tools/signature_labeling/evaluate_background.py` | Evalúa la corrección de firmas por fondo del libro. |

> **PRECAUCIÓN:** Ejecute `tune.py --aplicar` solo sobre una copia controlada. La opción modifica la plantilla indicada por `--template`; con el valor predeterminado modifica `template/aircraft_log.json`.

El verificador VLM admite los presets `qwen3-vl-8b-instruct` y `smolvlm2`. Sus rutas pueden definirse con `BITS_LLAMA_BIN`, `BITS_LLAMA_MODEL` y `BITS_LLAMA_MMPROJ`. Aunque esté instalado, GUI y CLI lo mantienen desactivado.

`setup.ps1 -Vlm` descarga normalmente los GGUF. Para obtener `llama-server.exe`, defina `BITS_LLAMA_BIN_ZIP`, pase `--bin-url` a `tools/precache_vlm.py` o copie el binario a `portable/llama/bin/`.

## 7.8 Pruebas

Ejecute la suite desde la raíz:

```batch
portable\python312\tools\python.exe -m pytest
```

`pytest.ini` limita la búsqueda a `tests/` y excluye `portable/`, `.venv/` y `output/`.

Áreas cubiertas:

- CPU obligatoria y entorno portable;
- launcher y arranque sin consola;
- rangos, paralelismo y progreso;
- OCR regional, fechas, firmas y alineación;
- corrección por libro y flota;
- duplicados y posibles discrepancias;
- CSV, JSON, estadísticas y PDF;
- reexportación, historial, vista previa y editor.

`pytest` no aparece en `requirements.txt` de ejecución. Si una reconstrucción destinada a desarrollo no lo contiene, instálelo solo en esa copia de mantenimiento.

## 7.9 Liberación de una copia

Antes de distribuir:

1. ejecute la suite completa;
2. ejecute `setup.ps1 -Check`;
3. desconecte la red o bloquee el acceso externo;
4. procese un lote de aceptación en CPU;
5. compruebe CSV, JSON, `stats.json`, PDF y registros;
6. confirme que `paginas_fuera` sea cero cuando exista el bloque de separación;
7. copie la carpeta completa a otra ruta escribible de Windows y abra allí `LogbookClassification.exe`;
8. entregue la carpeta completa.

## 7.10 Diagnóstico

| Indicación | Acción |
|---|---|
| El ejecutable informa que falta Python portable | Restaure `portable/python312/tools/` o reconstruya el paquete. |
| PaddleOCR solicita un modelo | Ejecute `tools/precache_paddle.py` durante mantenimiento con red y vuelva a probar sin conexión. |
| No aparecen PDF en **Detectar** | Compruebe que estén directamente en `input/` y tengan extensión `.pdf`. |
| La alineación queda no confiable | Revise la página de referencia y la geometría de plantilla. |
| Muchas páginas van a **REVISAR** | Compruebe `fleet.json`, la geometría, la confianza de matrícula y si los libros mezclan lecturas canónicas distintas; una inferencia coherente con dos respaldos no debería ir allí. |
| Falta un PDF en el visor histórico | Use **Ubicar PDF…** y seleccione su carpeta actual. |
| La reexportación histórica está desactivada | Compruebe JSON consolidado, plantilla y todos los PDF fuente. |

## 7.11 Referencias de ingeniería

- [Decisión del motor OCR](ocr-engine-decision.md)
- [Auditoría del OCR portable](portable-ocr-audit.md)
