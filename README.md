# Logbook Classification

Aplicación profesional para automatizar la **validación de bitácoras aeronáuticas escaneadas** antes de su indexación en AirVault.

## Características

| Característica | Descripción |
|---|---|
| **Pipeline completo** | PDF → imágenes → página en blanco → corrección de inclinación → alineación con plantilla → recorte de regiones → OCR regional → detección de firmas → validación → reporte |
| **Sistema de plantillas** | JSON con coordenadas relativas (0-1), sin coordenadas hardcodeadas |
| **Editor visual** | Aplicación independiente para dibujar regiones sobre la página |
| **OCR por región** | PaddleOCR ejecutado únicamente sobre las regiones definidas |
| **Detección de firmas** | OpenCV: tinta presente/ausente/incierta con umbral adaptativo y análisis de trazos (no identificación de personas) |
| **Alineación automática** | Rotación + traslación + escala vía ORB/RANSAC contra imagen de referencia |
| **Validaciones** | `required`, `regex`, `min_length`, `max_length` — todas desde el JSON |
| **Reportes** | CSV + JSON consolidado (mismo nombre que el CSV) en `datos/`, por página, campo, valor, confianza, estado, comentario |
| **PDFs ordenados** | Reordenamiento de los escaneos por avión y/o mes (combinables), en orden de libro y logpage, listos para indexar |
| **Estadísticas** | `stats.json` con totales, conteo por matrícula y por mes, discrepancias, páginas sin fecha/matrícula y verificación de que ninguna bitácora queda por fuera |
| **Discrepancias de firma** | `discrepancias.pdf` con las páginas de firmas faltantes/inciertas, sin anotaciones, ordenadas por avión y logpage |
| **PDFs fuente** | Todas las salidas PDF conservan las páginas originales; solo se agregan separadores independientes cuando se solicitan |
| **Carpeta por corrida** | Todos los outputs (datos/, stats.json, logs, debug) se generan en `output/<nombre del CSV>/` |
| **GUI moderna** | PySide6 con barra de progreso, vista previa y lista de errores |
| **Logs** | Loguru con rotación diaria |

## Estructura

```
BITS/
├── LogbookClassification.exe      # Ejecutable de doble clic (launcher de la GUI)
├── launcher_gui.py          # Código fuente del launcher
├── run_cli.py               # CLI de línea de comandos
├── run_gui.py               # GUI principal (procesar PDFs)
├── run_editor.py            # Editor de plantillas visual
├── requirements.txt         # Solo referencia (todo ya está instalado en portable)
├── README.md
├── input/                   # PDFs escaneados a procesar (test.pdf, test5.tif)
├── template/                # Plantillas JSON (aircraft_log.json)
├── output/                  # Reportes CSV/JSON, logs, imágenes de verificación
├── assets/                  # Iconos de la app (icon.png, icon.ico, icon.svg)
├── tools/
│   └── precache_paddle.py   # Precarga los modelos OCR en portable/paddlex
├── portable/                # Todo portable, sin instalación en el sistema
│   ├── python312/tools/python.exe   # Python 3.12.10 (requerido: paddle no soporta 3.14)
│   ├── tesseract/                   # Tesseract 5.4.0 + eng (auto-detectado)
│   ├── paddlex/                     # Modelos PaddleOCR precargados (offline)
│   └── poppler/                     # No usado (el render es con PyMuPDF); se puede borrar
└── app/
    ├── core/
    │   ├── config.py        # Configuración (pydantic)
    │   └── pipeline.py      # Orquestador del pipeline
    ├── ocr/
    │   ├── engine.py        # Motor OCR (PaddleOCR + fallback Tesseract)
    │   └── regional.py      # OCR sobre regiones de la plantilla
    ├── vision/
    │   ├── pdf_loader.py    # PDF → imágenes (PyMuPDF)
    │   ├── preprocessing.py # Escala de grises, binarización, deskew
    │   ├── blank_detection.py
    │   ├── alignment.py     # Alineación ORB/RANSAC
    │   ├── marks.py         # Análisis de tinta (común)
    │   ├── signature.py     # Detección de firma
    │   └── checkbox.py      # Detección de checkbox
    ├── templates/
    │   ├── schema.py        # Esquemas pydantic de plantilla
    │   └── manager.py       # Carga/guarda/lista plantillas
    ├── validation/
    │   ├── rules.py         # Reglas por campo
    │   └── validator.py     # Orquestador de validación
    ├── reports/
    │   ├── json_reporter.py
    │   ├── csv_reporter.py
    │   ├── organize.py      # PDFs ordenados por matrícula/mes, discrepancias
    │   ├── stats.py         # stats.json de la corrida
     │   └── debug_pdf.py     # Exportación PDF de páginas originales (modo debug)
    ├── gui/
    │   ├── worker.py        # QThread del pipeline
    │   ├── main_window.py   # Ventana principal
    │   └── editor_window.py # Editor de plantillas
    ├── models/
    │   └── schemas.py       # Modelos de dominio
    └── utils/
        ├── logging.py       # Configuración Loguru
        ├── io.py            # Helpers de archivos
        └── postprocess.py   # Normalización de valores (matrícula, fecha…)
```

## Instalación (Windows sin permisos de administrador)

**No hace falta instalar nada**: el Python 3.12 portable, todas las
dependencias, Tesseract y los modelos OCR viven dentro de la carpeta.
Solo copie la carpeta completa y ejecute `LogbookClassification.exe`.

> **Nota técnica**: la dependencia pesada es PaddlePaddle, que no tiene
> ruedas para Python 3.14; por eso se usa el Python 3.12 portable de
> `portable\python312`. El cache de modelos se redirige a
> `portable\paddlex` (nunca a `C:\Users\<usuario>\.paddlex`) mediante las
> variables `PADDLE_PDX_CACHE_HOME` y `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK`,
> fijadas automáticamente en `app/utils/portable.py`.
>
> `requirements.txt` es solo referencia: si se reconstruye el paquete desde
> cero, se instala con `portable\python312\tools\python.exe -m pip install -r requirements.txt`.

### Reconstrucción del entorno portable desde el repositorio

El repositorio versiona **solo el código fuente**. La carpeta `portable/`
(1.9 GB) que contiene el intérprete Python, dependencias, Tesseract y modelos
OCR **no se incluye** (ni en el repositorio ni en releases por su peso).
Siga estos pasos para reconstruirla:

1. **Python 3.12 portable (embeddable, sin instalación):**

   Descargue el paquete *embeddable* de 64-bit para Windows desde
   <https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip>
   y descomprímalo en `portable\python312`. Edite
   `portable\python312\python312._pth` y agregue las líneas:

   ```
   portable\python312\Lib\site-packages
   .
   ```
   (esto permite instalar paquetes con pip dentro del embeddable).

   > Requerimiento: **Python 3.12** exactamente (3.14 no es soportado por
   > PaddlePaddle). El portable incluye `python.exe` y `pythonw.exe`.

2. **Dependencias de Python:**

   Con el Python portable:

   ```batch
   portable\python312\tools\python.exe -m pip install --upgrade pip
   portable\python312\tools\python.exe -m pip install -r requirements.txt
   ```

3. **Tesseract 5.4.0 + idioma `eng`:**

   Instale Tesseract desde <https://github.com/UB-Mannheim/tesseract/wiki>
   o copie la carpeta `portable\tesseract\` (binario + `tessdata\eng.traineddata`
   + `tessdata\osd.traineddata`) de otra instalación portable. La app lo
   localiza automáticamente en `portable\tesseract\tesseract.exe`.

4. **Modelos PaddleOCR (offline, sin internet en producción):**

   ```batch
   portable\python312\tools\python.exe tools\precache_paddle.py
   ```
   Esto descarga los modelos a `portable\paddlex`. Para usar la app sin
   internet, copie esa carpeta completa entre máquinas.

5. **(Opcional) Modelos VLM para el verificador:**

   ```batch
   portable\python312\tools\python.exe tools\precache_vlm.py
   ```
   Necesita `portable\llama\bin/llama-server(.exe)` y los GGUF bajo
   `portable\llama\models/`.

Una vez reconstruida `portable/`, regenere el launcher:

```batch
portable\python312\tools\python.exe -m pip install pyinstaller
portable\python312\tools\python.exe -m PyInstaller --onefile --noconsole --name LogbookClassification --icon assets\icon.ico --distpath . --workpath build --specpath build launcher_gui.py
```

## Uso

### 0. Ejecutable de doble clic (100% portable, sin consola)

La carpeta completa es autocontenida: se copia a cualquier PC Windows
(y sin permisos de administrador) y funciona. **No instala nada.**

- **`LogbookClassification.exe`** → abre la GUI de validación sin consola.
  Es un launcher pequeño que arranca el Python portable incluido
  (`portable\python312\tools\pythonw.exe run_gui.py`).
- Toda la app escribe solo en `input/` y `output/` relativos a la carpeta.
- Los modelos OCR viven en `portable\paddlex` (sin descargas al primer uso).

Si se quiere regenerar el launcher o el cache de modelos en otra máquina:

```batch
portable\python312\tools\python.exe -m pip install pyinstaller
portable\python312\tools\python.exe -m PyInstaller --onefile --noconsole --name LogbookClassification --icon assets\icon.ico --distpath . --workpath build --specpath build launcher_gui.py

portable\python312\tools\python.exe tools\make_icon.py     # (re)genera assets\icon.png e icon.ico
portable\python312\tools\python.exe tools\precache_paddle.py
```

> El icono de la app (`assets/icon.ico` para el .exe y `assets/icon.png`
> para la barra de título) se genera una sola vez con
> `tools\make_icon.py` (Pillow ya incluido en el Python portable).

### 1. CLI

```batch
portable\python312\tools\python.exe run_cli.py --pdf input\test.pdf --engine tesseract --lang eng --output-dir output
```

> Si se usa el motor `paddle` (por defecto) el `--lang` es `en`; el motor `tesseract` usa `--lang eng`.

Opciones principales:

| Flag | Default | Descripción |
|---|---|---|
| `--pdf` | — | PDF a procesar |
| `--template` | `template/aircraft_log.json` | Plantilla JSON |
| `--output-dir` | `output/` | Carpeta de resultados |
| `--dpi` | 200 | DPI máximo de la página completa; se ajusta al DPI nativo de cada PDF |
| `--max-pages` | — | Procesar solo las primeras N páginas (pruebas) |
| `--limit-books` | — | Procesar solo las primeras N bitácoras (PDFs ordenados de la carpeta de entrada) |
| `--debug` | — | Generar `debug.pdf` con las páginas originales, sin anotaciones |
| `--reference-page` | 1 | Página usada como referencia de alineación |
| `--engine` | `paddle` | Motor OCR (`paddle` o `tesseract`) |
| `--lang` | `en` | Idioma del motor OCR |
| `--threads` (`--cpu-threads`) | Todos los disponibles | Hilos totales del procesador; workers y hilos internos se distribuyen automáticamente |
| `--no-deskew` | — | Desactivar corrección de inclinación |
| `--no-align` | — | Desactivar alineación |
| `--separar-por avion\|mes` | — | Separar las bitácoras en PDFs independientes (repetible y combinable: solo avión, solo mes, o ambos) |
| `--discrepancias` | — | Generar `discrepancias.pdf` con las páginas de firmas faltantes/inciertas, sin anotaciones (no van a los PDFs por avión/mes) |
| `--recortes-firmas` | — | Volcar los recortes de las regiones de firma a `recortes_firmas/` para auditar bounding boxes |

> **Salida**: todos los outputs de la corrida se generan dentro de una
> carpeta con el nombre del CSV (sin extensión), p. ej.
> `output/BITS 03 AUG 2026 19 40/`:
>
> ```
> BITS 03 AUG 2026 19 40/
> ├── datos/
> │   ├── BITS 03 AUG 2026 19 40.CSV     # CSV consolidado
> │   └── BITS 03 AUG 2026 19 40.json    # JSON consolidado (mismo nombre)
> ├── stats.json                         # estadísticas de la corrida
> ├── discrepancias.pdf                  # (opcional, --discrepancias)
> ├── debug.pdf                          # (opcional, --debug)
> ├── logs/
> └── <PDFs ordenados según --separar-por>
> ```

Ejemplo con modo debug y límite de bitácoras:

```batch
portable\python312\tools\python.exe run_cli.py --debug --limit-books 2 --engine tesseract --lang eng
```

## Organización en PDFs y discrepancias de firma

Con `--separar-por` se reordenan los escaneos tal cual (sin encabezados ni
anotaciones, listos para subir a la plataforma de indexado):

```batch
portable\python312\tools\python.exe run_cli.py --separar-por avion --separar-por mes --discrepancias
```

- `--separar-por avion`: un PDF por aeronave, suelto en la carpeta de la
  corrida (`HP-XXXXCMP.pdf`; `sin_matricula.pdf` si no se pudo leer).
- `--separar-por mes`: un PDF por mes detectado (`2026-JUL.pdf`;
  `sf.pdf` para las páginas sin fecha legible).
- Ambos: una **carpeta por matrícula** y dentro un PDF por mes
  (`HP-XXXXCMP/2026-JUL.pdf`, `HP-XXXXCMP/sf.pdf`); las divisiones por
  mes no son carpetas sino PDFs.
- Sin la opción: un único PDF con el mismo nombre que la carpeta de la
  corrida, con todas las páginas agrupadas por matrícula (ordenadas por libro
  y logpage), con una página divisoria con la matrícula en grande entre grupo
  y grupo.
- Dentro de cada PDF las páginas van en orden de **libro** (serie del
  `log_number`) y **logpage**; las páginas con `log_number` ilegible van
  al final en su orden original. Matrícula ilegible → grupo
  `sin_matricula`; fecha ilegible → `sf`. Las páginas en blanco no se
  incluyen. Así, **ninguna bitácora queda por fuera** de los PDFs.
- La inferencia de fechas usa el `log_number` para establecer la secuencia,
  nunca el orden del PDF, pero el valor siempre proviene de lecturas directas
  confiables. Si hay lecturas compatibles a ambos lados, se pueden inferir
  mes y año de las bitácoras intermedias. Warnings, errores y valores ya
  inferidos no sirven como anclas. El día no se infiere: si no se lee, la
  fecha completa queda vacía y la página se marca para revisión. Los
  componentes inferidos conservan su origen y método en JSON y CSV.
- Con `--discrepancias` se genera `discrepancias.pdf`: páginas originales con
  firmas faltantes o inciertas, ordenadas por avión y `log_number`, sin
  anotaciones. Estas páginas **no** se incluyen en los PDFs por avión/mes.

Además, siempre se genera `stats.json` en la carpeta de la corrida con
las estadísticas de la corrida:

- `total_bitacoras` / `bitacoras`: bitácoras procesadas y sus páginas.
- `total_paginas`, `paginas_en_blanco`, `paginas_validas`.
- `por_matricula`: total y desglose `por_mes` de cada matrícula
  (incluida `sin_matricula`).
- `por_mes`: total por mes (`sf` = sin fecha legible).
- `sin_matricula` / `sin_fecha`: páginas que no se pudieron determinar.
- `discrepancias`: total, faltantes, inciertas, por matrícula y detalle.
- `separacion` (cuando se generan PDFs): lista de PDFs con sus páginas,
  `paginas_distribuidas`, `paginas_excluidas_por_discrepancia`,
  `paginas_fuera` (debe ser 0) y `completa` (verificación de que
  ninguna bitácora quedó por fuera).

Reglas de discrepancias (en `app/validation/discrepancias.py`):

| Tipo de página | Detección | Firmas requeridas |
|---|---|---|
| Vuelo | `technician_license` ausente o no confiable | piloto + capitán + licencia del capitán |
| Mantenimiento | `technician_license` presente de forma confiable | piloto + técnico |

La presencia se decide con el detector (true/false/unclear) y la
confianza: una lectura de baja confianza nunca se acusa como falta, se
marca como **incierta** (REVISAR) para evitar discrepancias falsas.

Con `--recortes-firmas` se vuelcan los recortes de las regiones de firma a
`recortes_firmas/<campo>/` para verificar visualmente los bounding boxes
(usar con `--max-pages` para lotes pequeños).

### 2. GUI principal

```batch
portable\python312\tools\python.exe run_gui.py
```

(o doble clic en `LogbookClassification.exe`)

1. Entrada: por defecto se cargan los PDFs de `input/`; se pueden elegir
   varios archivos con "Seleccionar archivos…" o restablecer con "Usar input/".
   La resolución se detecta por cada PDF (sin selector de DPI). La página
   completa usa como máximo 200 DPI y la banda manuscrita de fecha conserva
   hasta 600 DPI nativos mediante render regional. El botón
   "Vaciar input/" mueve todos los archivos de esa carpeta a la Papelera de
   reciclaje después de pedir confirmación.
2. Seleccionar plantilla.
3. Procesamiento: motor OCR (`PaddleOCR` o `Tesseract`) con idioma
   automático, "Bitácoras" (primeras N, 0 = todas), "Páginas" por bitácora,
   corrección de inclinación y alineación.
4. Preprocesamiento: **Preprocesar** aplica corrección de inclinación y
   alineación sin ejecutar OCR, para revisar visualmente las páginas antes del
   procesamiento completo.
5. Salidas: casillas "Matrícula" y "Mes" para separar los PDFs por esos
   criterios (con ambas, separado por matrícula y mes), "Discrepancia"
   (`discrepancias.pdf`) y "Visualizar campos" (los bounding boxes se muestran
   solo en la vista previa; los PDFs no reciben marcas).
 6. Opciones avanzadas (colapsable): hilos totales del procesador, página de
    referencia y OCR de respaldo/ranuras. Las fechas se procesan con
    Qwen3-VL-8B-Instruct automáticamente cuando el runtime está instalado.
    La aplicación detecta los hilos disponibles, selecciona todos por defecto
    y distribuye automáticamente el trabajo entre workers e hilos internos.
7. Procesar → barra de progreso con tiempo transcurrido, restante estimado
   (medido en vivo según el ritmo real de cada bitácora) y, al terminar,
   el tiempo por bitácora. El resultado OCR queda disponible en memoria para
   exportarlo varias veces.
8. La vista previa carga la primera página del PDF seleccionado inmediatamente,
   antes del procesamiento, para revisar los bounding boxes cuando
   "Visualizar campos" está marcado (solo dibujo, sin costo extra). Al terminar
   el OCR, se actualiza con la versión de la corrida. Se reajusta al área de la
   ventana cuando esta cambia de tamaño; los controles de zoom permiten ampliar
   y desplazarse por la página. La tabla de resultados usa las mismas columnas
   del CSV y muestra una sola línea por página.
9. Los outputs (CSV y JSON consolidado en `datos/`, `stats.json`, PDFs
    por matrícula/mes, `discrepancias.pdf`, `debug.pdf`) se exportan
    automáticamente en `output/<nombre del CSV>/` según las casillas
    marcadas. Después de procesar se pueden cambiar las opciones de
    separación y pulsar **Exportar** para generar otra salida con la
    selección actual, sin volver a ejecutar el OCR: el re-export se
    escribe **sobre la misma carpeta de la corrida** (el CSV conserva
    su nombre, los PDFs se regeneran con la nueva separación y se
    eliminan los PDFs de la separación anterior que ya no apliquen).
    El botón Exportar queda disponible en cuanto termina el OCR, aunque
    la generación de salidas de fondo siga en curso (en ese caso el
    re-export se ejecuta apenas termine).

### 3. Editor de plantillas

```batch
portable\python312\tools\python.exe run_editor.py
```

1. Abrir PDF
2. Dibujar rectángulos sobre la página
3. Asignar nombre, tipo (ocr/signature/checkbox/text/date) y reglas
4. Guardar JSON

## Formato de plantilla

```json
{
  "name": "Aircraft Log",
  "version": "1.0",
  "page_size": [2480, 3508],
  "fields": [
    {
      "id": "log_number",
      "type": "ocr",
      "required": true,
      "x": 0.61, "y": 0.08, "w": 0.12, "h": 0.03,
      "regex": "^\\d{7}$"
    },
    {
      "id": "technician_signature",
      "type": "signature",
      "required": true,
      "x": 0.72, "y": 0.88, "w": 0.18, "h": 0.05
    }
  ]
}
```

### Tipos de campo

| Tipo | Procesamiento |
|---|---|
| `ocr` | PaddleOCR → texto + confianza |
| `text` | Idéntico a `ocr` (semánticamente para texto libre) |
| `date` | OCR + postprocesado de fecha |
| `signature` | Análisis de tinta (presente/ausente) |
| `checkbox` | Marcado/vacío por cobertura de tinta |

### Propiedades de campo

| Propiedad | Descripción |
|---|---|
| `required` | Obligatorio → ERROR si vacío |
| `regex` | Patrón que debe cumplir el valor |
| `min_length` / `max_length` | Longitudes mínima/máxima |
| `postprocess` | `matricula`, `date` o `digits` (normalización) |
| `min_ink_ratio` / `max_ink_ratio` | Umbrales de tinta (firma/checkbox) |
| `min_components` | Trazos mínimos (firma) |

## Postprocesado de matrícula

El postprocesador `matricula` normaliza cualquier formato a `HP-XXXXCMP`:

| Entrada | Salida |
|---|---|
| `9904` | `HP-9904CMP` |
| `hp9904` | `HP-9904CMP` |
| `HP-9904` | `HP-9904CMP` |
| `HP-9904CMP` | `HP-9904CMP` |
| `HP-1990WWP` | `HP-1990WWP` |
| `HP-1522WWP` | `HP-1522WWP` |

## Estados de validación

| Estado | Significado |
|---|---|
| `OK` | Todos los campos cumplen |
| `WARNING` | Campo opcional vacío, confianza baja, página en blanco o firma incierta (`unclear`) |
| `ERROR` | Campo obligatorio vacío o no cumple reglas |

## Modo debug

Con `--debug` (CLI) o la casilla "Visualizar campos" (GUI) se genera
`debug.pdf` dentro de la carpeta de la corrida. El PDF contiene únicamente
las páginas originales procesadas, sin leyendas, textos, bandas ni
recuadros. Los bounding boxes siguen disponibles en la vista previa y la
información de validación permanece en CSV, JSON y logs.

## Fiabilidad de la fase de detección

Dos capas de robustez mejoran la lectura de firmas y campos críticos sin
cambiar el resto del flujo:

**Redundancia OCR (activada por defecto).** Si la lectura principal
(PaddleOCR) de un campo crítico no produce un valor válido, se reintenta
ese mismo recorte con Tesseract restringido (PSM 7 + whitelist según el
campo: dígitos / letras de meses / alfanumérico-guion para matrícula).
Cubre día/mes/año, matrícula y `log_number`. Se puede desactivar con
`--no-date-ocr-fallback`.

**Procedencia de resultados.** Cada campo conserva su estado, comentario y
origen (`ocr`, `ocr_fallback`, `vision`, `vlm` o `inferred`). Esto permite
distinguir una lectura directa de un mes/año inferido por intervalo de
`log_number`.

**Firma por color de tinta.** Además de la textura en gris, el detector
mide píxeles saturados oscuros de bolígrafo (azul, etc.): evita marcar
"ausente"/"incierta" una firma azul clara que el canal gris aplana.

**Tipo de página robusto.** Cuando la licencia de técnico es ilegible, la
página ya no se fuerza a "vuelo" (discrepancia falsa a favor del capitán/
licencia) ni a "mantenimiento": se marca como tipo **incierto** y solo se
acusan anomalías robustas (firma de piloto + la propia licencia ilegible).

## Verificador VLM local

Las fechas se envían al VLM **Qwen3-VL-8B-Instruct** por defecto, incluso si
el OCR previo produjo un valor plausible. También se revisan firmas inciertas
y campos críticos vacíos. Si la carpeta `portable/llama/` contiene un
`llama-server` y los GGUF correspondientes, el pipeline procesa cada recorte:

- Respuestas solo terminantes: `PRESENTE`/`AUSENTE` para firmas, o un
  texto que pasa el postprocesado del campo (matrícula, `log_number` o fecha).
- Para fechas, el VLM recibe prompts específicos para `DAY`, `MONTH` y `YR`,
  y se le pide ignorar los separadores verticales impresos.
- Presupuesto `vlm_max_crops` por corrida (default 120) y timeout por
  consulta (`vlm_timeout`, default 60 s).
- Fallback total: si falta el binario/modelo, el servidor no arranca o
  la consulta falla, el resultado previo se conserva intacto.

Para dejar Qwen3-VL listo (una vez, con internet):

    portable\python312\tools\python.exe tools\precache_vlm.py

Rutas automáticas y variables (sobre la carpeta portable):
`portable/llama/bin/llama-server(.exe)` (o el binario en el PATH),
`portable/llama/models/*.gguf` (modelo de texto, sin `mmproj`) y
`portable/llama/models/*mmproj*.gguf` (proyector). Variables opcionales:
`BITS_LLAMA_BIN`, `BITS_LLAMA_MODEL`, `BITS_LLAMA_MMPROJ`.

El descargador acepta explícitamente el preset alternativo SmolVLM2:

    python tools/precache_vlm.py --preset smolvlm2

También se pueden pasar URLs propias con `--model-url` y `--mmproj-url`.

En `stats.json` de cada corrida se añade el bloque `vlm` (crops
consultados, firmas/campos resueltos, o el motivo de desactivación).

## Escalabilidad (puntos de extensión)

El perfil de rendimiento B mantiene un único pool OCR durante todo el lote,
reutiliza el documento PDF abierto dentro de cada proceso y agrupa en una sola
invocación los recortes de Tesseract que comparten PSM y whitelist. La retícula
de fecha se detecta y lee sobre una banda regional de alta resolución; no se
rasteriza la página completa a 600 DPI.

- **Nuevo motor OCR / VL**: implementar el protocolo `OcrEngine` (p. ej. Qwen VL) y registrarlo en `create_engine()`.
- **Nuevo tipo de campo**: añadir procesador en `core/pipeline.py` o registry.
- **AirVault API**: consumir `ValidationReport` (pydantic) desde un cliente HTTP.
- **API REST**: envolver `Pipeline.process()` en un endpoint (FastAPI).
- **Procesamiento paralelo**: cada `_process_page` es independiente → `ProcessPoolExecutor`.
- **Nuevos formularios**: crear un JSON por formulario, sin tocar código.
