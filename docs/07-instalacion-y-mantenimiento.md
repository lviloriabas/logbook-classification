# 7. Instalación y mantenimiento

## 7.1 Paquete portable

La entrega operativa es la carpeta BITS completa. No instala servicios, no usa el registro y no requiere permisos de administrador.

Componentes de la copia estándar:

```text
portable/
├── python312/tools/
│   ├── python.exe
│   └── pythonw.exe
└── paddlex/official_models/
    ├── PP-OCRv6_medium_det/
    └── PP-OCRv5_mobile_rec/
```

Python y los dos modelos PaddleOCR son todo lo que la aplicación necesita.

> **PRECAUCIÓN:** No entregue solo `LogbookClassification.exe`. El ejecutable es un lanzador; necesita el código y `portable/` en la misma estructura relativa.

## 7.2 Comprobación

Ejecute:

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1 -Check
```

El informe debe mostrar disponibles Python, dependencias, modelos PaddleOCR y lanzador.

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

El proceso instala Python 3.12.10, las dependencias y los modelos PaddleOCR dentro de `portable/`. Verifica por SHA-256 el paquete de Python; pip y los precargadores aplican sus propias comprobaciones. La ejecución es repetible: un componente completo no vuelve a prepararse salvo que se use `-Force`.

La red solo se requiere para reconstruir el paquete. La aplicación terminada debe iniciar y procesar sin conexión.

## 7.4 Opciones de reconstrucción

| Opción | Efecto |
|---|---|
| `-Check` | Informa el estado sin descargar. |
| `-Launcher` | Instala PyInstaller y regenera el ejecutable. |
| `-Force` | Reconstruye los componentes principales y los modelos Paddle. |
| `-CleanCache` | Elimina `portable/.cache/` al terminar. |

## 7.4.1 Limpieza de copias anteriores al 2026-08-26

Las instalaciones preparadas antes de esa fecha traen dos componentes que el
programa ya no usa (ver `Legends.md`). Se pueden borrar sin tocar nada más;
`setup.ps1` ya no los descarga.

| Ruta | Tamaño típico | Qué era |
|---|---:|---|
| `portable/tesseract/` | 239 MB | Motor OCR alternativo y su `tessdata`. |
| `portable/llama/` | varios GB | Modelos GGUF y `llama-server` del verificador VLM. |
| `portable/.cache/` | variable | Instaladores descargados, incluido el de Tesseract. |

En cada equipo, con el programa cerrado:

```powershell
Remove-Item -LiteralPath .\portable\tesseract -Recurse -Force
Remove-Item -LiteralPath .\portable\llama -Recurse -Force
Remove-Item -LiteralPath .\portable\.cache -Recurse -Force
```

`portable/llama/` y `portable/.cache/` no existen en todas las copias: solo
las tenía quien ejecutó `setup.ps1 -Vlm` o no limpió la caché de
instaladores. Un `Remove-Item` sobre una ruta ausente informa un error que se
puede ignorar.

Queda además la dependencia `pytesseract` dentro del intérprete portable:

```powershell
.\portable\python312\tools\python.exe -m pip uninstall -y pytesseract
```

Ninguna de las tres carpetas se versiona, así que borrarlas no produce
cambios en git. Después, `setup.ps1 -Check` debe seguir informando Python,
dependencias, modelos PaddleOCR y lanzador como disponibles.

## 7.5 Entorno local

`app/utils/portable.py` prepara las rutas antes de importar los motores:

| Variable | Valor normal |
|---|---|
| `PADDLE_PDX_CACHE_HOME` | `portable/paddlex` |
| `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK` | `1` |
| `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT` | `0` |
| `FLAGS_use_mkldnn` | `0` |

Las variables se fijan con `setdefault`. Un valor ya definido en Windows tiene prioridad; compruébelo si una caché o una opción se desvía de la carpeta portable.

PaddleOCR v3 y el reconocedor PaddleX se crean con `device="cpu"`. La compatibilidad con PaddleOCR v2 usa `use_gpu=False`. oneDNN/MKL-DNN permanece desactivado por estabilidad en Windows.

No debe existir una descarga de modelos durante una ejecución. Si Paddle intenta acceder a la red, detenga la entrega y compruebe la caché portable.

## 7.6 Dependencias de ejecución

`requirements.txt` registra las dependencias directas:

- OpenCV, NumPy, PyMuPDF y Pillow;
- PaddlePaddle, PaddleOCR y PaddleX;
- PySide6 y Send2Trash;
- Pydantic y Loguru.

PyMuPDF renderiza y copia los PDF. Poppler no es necesario.

## 7.7 Herramientas

| Herramienta | Uso |
|---|---|
| `tools/precache_paddle.py` | Descarga, inicializa y prueba en CPU los dos modelos OCR de producción. |
| `tools/build_launcher.py` | Genera `LogbookClassification.exe` con PyInstaller, sin consola. |
| `tools/make_icon.py` | Regenera los iconos PNG e ICO. |
| `tools/evaluate_date_images.py` | Compara lecturas de fecha contra datos de referencia. |
| `tools/signature_labeling/extract.py` | Extrae recortes de firmas para etiquetado. |
| `tools/signature_labeling/label_gui.py` | Etiqueta recortes en forma visual. |
| `tools/signature_labeling/tune.py` | Evalúa umbrales y, con `--aplicar`, modifica la plantilla. |
| `tools/signature_labeling/evaluate_background.py` | Evalúa la corrección de firmas por fondo del libro. |

> **PRECAUCIÓN:** Ejecute `tune.py --aplicar` solo sobre una copia controlada. La opción modifica la plantilla indicada por `--template`; con el valor predeterminado modifica `template/aircraft_log.json`.

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
4. procese un batch de aceptación en CPU;
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
