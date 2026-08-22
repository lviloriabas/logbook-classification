# BITS: manual de desarrollo y mantenimiento

| Control | Valor |
|---|---|
| Aplicación | BITS, Logbook Classification |
| Plataforma objetivo | Windows portable, solo CPU |
| Intérprete portable | Python 3.12.10 |
| Entrada de producción | PDF |
| Revisión del manual | 20 AUG 2026 |

## 1. Objeto y límites

Este manual cubre arquitectura, dependencias, OCR, reglas de bitácora, salidas, AirVault y mantenimiento del paquete portable.

Mantenga estas condiciones:

- ningún motor de inferencia usa GPU;
- la ejecución normal no descarga modelos;
- los activos y cachés persistentes de producción se resuelven desde la carpeta de BITS;
- un libro tiene 50 páginas y una sola aeronave;
- `log_number` tiene siete dígitos;
- la fecha no retrocede dentro del libro;
- la salida CSV no se cambia sin autorización del responsable.

## 2. Puntos de entrada y arquitectura

| Componente | Función |
|---|---|
| `LogbookClassification.exe` | Lanzador pequeño creado con PyInstaller. |
| `launcher_gui.py` | Localiza `portable/python312/tools/pythonw.exe` y abre `run_gui.py`. |
| `run_gui.py` | Prepara el entorno portable, fija la raíz de trabajo y crea `MainWindow`. |
| `run_cli.py` | Procesamiento por consola con el mismo pipeline y las mismas salidas. |
| `run_editor.py` | Editor visual de regiones de plantilla. |
| `run_airvault.py` | Operación de AirVault por etapas y reanudación desde manifiesto. |
| `app/core/` | Configuración, rango global, paralelismo y pipeline. |
| `app/vision/` | Renderizado, inclinación, alineación, geometría, tinta y firmas. |
| `app/ocr/` | Motores y lectura regional. |
| `app/validation/` | Reglas, libros, fechas, flota, duplicados, estados y discrepancias. |
| `app/reports/` | CSV, JSON, estadísticas y PDF de entrega. |
| `app/airvault/` | Sesión, carga, revisión, guardas, indexado y reanudación. |
| `app/gui/` | Ventana principal, visor, editor, AirVault y trabajos en segundo plano. |

La GUI delega los trabajos largos a `PipelineWorker`, `PreprocessWorker` y `OutputsWorker`. GUI y CLI convergen en `Pipeline`, `process_pdf_batch()` y `write_outputs()`.

## 3. Librerías y componentes

### 3.1 Dependencias directas

| Dependencia | Uso |
|---|---|
| `opencv-python>=4.9.0` | Inclinación, alineación, retículas, tinta, firmas y preprocesado. |
| `numpy>=1.26.0` | Imágenes, máscaras, estadísticas y cálculo numérico. |
| `PyMuPDF>=1.24.0` | Apertura, conteo, renderizado, copia y composición de PDF. |
| `Pillow>=10.0.0` | Conversión de imágenes, recortes e icono del programa. |
| `paddlepaddle>=2.6.0` | Runtime de inferencia Paddle en CPU. |
| `paddleocr==3.7.0` | Detección y reconocimiento OCR principal. |
| `paddlex[ocr]==3.7.2` | Predictor por línea y administración de modelos PaddleX. |
| `pytesseract>=0.3.10` | Adaptador del OCR Tesseract opcional. No se usa en la GUI o CLI normal. |
| `PySide6>=6.6.0` | Interfaz, vista previa, tablas y trabajos con `QThread`. |
| `Send2Trash>=1.8.0` | Envío recuperable de archivos a la Papelera. |
| `pydantic>=2.6.0` | Validación de configuración, plantillas y modelos de datos. |
| `loguru>=0.7.2` | Registro operativo y rotación de logs. |
| `requests>=2.31.0` | Sesión HTTP, carga e indexado en AirVault. |

Solo `paddleocr` y `paddlex` tienen versión exacta. Las demás entradas fijan una versión mínima. Para reproducibilidad estricta, registre las versiones instaladas del portable aprobado.

Los módulos `csv`, `json`, `concurrent.futures`, `ctypes`, `pathlib`, `subprocess` y demás componentes de la biblioteca estándar forman parte de Python y no requieren instalación aparte.

### 3.2 Componentes externos

| Componente | Condición |
|---|---|
| Python 3.12.10 | Obligatorio en `portable/python312/tools/`. |
| Modelos PaddleOCR | Obligatorios en `portable/paddlex/official_models/`. |
| Tesseract 5.4 | Opcional; la GUI y la CLI no lo usan como respaldo. |
| Microsoft Edge | Necesario solo para iniciar sesión en AirVault. |
| `llama-server` y modelos GGUF | Opcionales; el verificador VLM está desactivado. |
| `pytest` | Desarrollo; no forma parte de `requirements.txt`. |

## 4. Portabilidad

### 4.1 Estructura esperada

```text
BITS/
├── LogbookClassification.exe
├── app/
├── template/
├── portable/
│   ├── python312/tools/
│   ├── paddlex/official_models/
│   ├── tesseract/             opcional
│   ├── edge-airvault/         generado al iniciar sesión
│   └── llama/                  opcional
├── input/                      creado si falta
└── output/                     creado si falta
```

El EXE no contiene la aplicación ni sus dependencias. La unidad de distribución es la carpeta completa.

`portable/` no se guarda en Git por su tamaño. Un clon del repositorio no es una entrega operativa hasta reconstruir esa carpeta o copiarla desde un paquete aprobado.

`ensure_portable_env()` debe ejecutarse antes de importar PaddleOCR o PaddleX. Define, sin sobrescribir variables existentes:

```text
PADDLE_PDX_CACHE_HOME=<raíz>\portable\paddlex
PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=1
PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0
FLAGS_use_mkldnn=0
TESSDATA_PREFIX=<raíz>\portable\tesseract\tessdata
```

Una variable definida por el sistema tiene prioridad sobre `setdefault()` y puede sacar la caché fuera del paquete. Compruébela cuando una máquina se comporte distinto.

La ruta opcional de Tesseract busca primero el `PATH` del sistema y después `portable/tesseract/`. Para una prueba estrictamente aislada, retire cualquier Tesseract externo del `PATH` o cambie esa prioridad antes de habilitar el respaldo.

El VLM opcional también puede resolver binarios desde variables de entorno o `PATH`. Los archivos temporales pueden usar `%TEMP%`; no son activos persistentes. Revise estas rutas si habilita capacidades experimentales.

Todos los motores se crean con `device="cpu"`, `use_gpu=False` o el equivalente. El VLM opcional arranca con cero capas de GPU. oneDNN está desactivado por un fallo conocido de Paddle en Windows.

En la operación normal, el OCR no requiere red y AirVault sí. Microsoft Edge es el método normal para obtener la sesión; también se admite una cookie y, por consola, una cuenta local. El perfil de Edge queda en `portable/edge-airvault/`. La reconstrucción y las descargas de modelos también requieren red. `airvault.json` contiene URL, repositorio, esquema, tiempos y valores de índice; no contiene credenciales. Las cookies y contraseñas no se escriben en el log.

> **PRECAUCIÓN:** `portable/edge-airvault/` contiene la sesión y sus cookies. Nunca distribuya un perfil autenticado. Excluya o limpie ese directorio antes de liberar el paquete y deje que el equipo de destino lo regenere.

### 4.2 Reconstrucción

Desde una máquina Windows con red:

```powershell
setup.cmd
```

El script descarga Python, instala `requirements.txt`, precarga los dos modelos PaddleOCR y prepara Tesseract. Python y Tesseract se verifican con SHA-256.

Opciones de mantenimiento:

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1 -Check
powershell -ExecutionPolicy Bypass -File setup.ps1 -SkipTesseract
powershell -ExecutionPolicy Bypass -File setup.ps1 -Vlm
powershell -ExecutionPolicy Bypass -File setup.ps1 -Launcher
powershell -ExecutionPolicy Bypass -File setup.ps1 -Force
powershell -ExecutionPolicy Bypass -File setup.ps1 -CleanCache
```

`-Vlm` descarga Qwen3-VL y su `mmproj` desde las URL predeterminadas, pero no activa el VLM. `llama-server.exe` debe copiarse manualmente o suministrarse mediante `BITS_LLAMA_BIN_ZIP`.

> **PRECAUCIÓN:** `-Force` elimina y reconstruye el intérprete de `portable/python312/tools/`, Tesseract y los directorios de modelos. Úselo solo sobre una copia o durante una reconstrucción controlada.

## 5. Proceso interno por página

### 5.1 Entrada y rango

La GUI y la CLI aceptan archivos PDF. `PageRange` numera el batch completo desde 1 y lo divide en tramos por archivo. Los PDF fuera del rango no se envían al pipeline.

`PdfPageRenderer` mantiene PyMuPDF abierto, detecta el DPI de origen y renderiza BGR. La GUI solicita 200 DPI base y la CLI 150. La fecha puede usar hasta 300 DPI, siempre limitada por el DPI del documento; el sistema no amplía por encima de la fuente detectada.

### 5.2 Calibración

Por cada PDF:

1. se elige una página de referencia dentro del tramo;
2. se calibra el documento a media resolución;
3. Canny y Hough estiman la inclinación;
4. ORB con RANSAC estima rotación, escala uniforme y traslación;
5. AKAZE y correlación de fase actúan como respaldo;
6. la mediana local estabiliza transformaciones vecinas.

La alineación corrige variaciones del escaneo. No sustituye una plantilla incorrecta.

### 5.3 Lectura

El orden por página es:

1. detectar página en blanco por varianza de gris;
2. aplicar inclinación y alineación calibradas;
3. renderizar la banda de fecha a su resolución;
4. localizar la retícula `DD|MMM|AA` y sus siete casillas;
5. recortar cada campo de la plantilla;
6. aplicar CLAHE y localización de tinta donde corresponda;
7. ejecutar OCR o detector de firma;
8. normalizar y validar cada valor;
9. combinar las casillas de fecha;
10. calcular el estado de página.

La plantilla principal usa reconocimiento por línea en todos los campos OCR. Si una lectura no supera el postproceso o la expresión regular, se repite con detección completa.

Las firmas no identifican personas. El detector elimina líneas largas y clasifica la tinta como `true`, `false` o `unclear`. Con al menos ocho páginas alineadas, una segunda revisión compara solo las firmas inciertas contra el fondo mediano del mismo campo. Para formar esa referencia, cada campo necesita al menos tres ejemplos firmes presentes y tres ausentes.

### 5.4 Paralelismo

El batch usa procesos persistentes, no hilos de Python para cada página. El planificador reparte primero PDF y después páginas. Limita el máximo a 32 workers, reserva memoria del sistema y asigna de uno a tres hilos internos por proceso. Los resultados conservan el orden del batch.

## 6. Mejora de matrícula y fecha

El sistema mejora matrícula y fecha usando la estructura del libro. `log_number` sirve de clave y secuencia, pero nunca se infiere ni se modifica.

### 6.1 Agrupación

La clave de libro es:

```text
(primeros cinco dígitos, A si logpage 00-49; B si logpage 50-99)
```

La agrupación abarca todos los PDF de la ejecución y se ordena por `log_number`, no por posición física. Si existe un solo libro conocido, sus páginas sin número se agregan a él. Si existen varios, todas las páginas sin número forman un grupo separado.

> **PRECAUCIÓN:** el grupo separado puede mezclar libros reales. No use sus inferencias sin revisar la fuente.

### 6.2 Normalización de matrícula

La salida canónica es `HP-\d{4}(CMP|WWP)`. El postproceso elimina separadores y busca cuatro dígitos. Si no aparecen juntos, traduce confusiones OCR habituales:

```text
O/Q→0  I/L→1  Z→2  S→5  G→6  F→7  B→8
```

Una reconstrucción necesita al menos dos dígitos reales. Como último recurso se aceptan cuatro dígitos dispersos con evidencia débil. Sin cuatro dígitos recuperables, el valor queda vacío. El sufijo es `WWP` si la lectura lo contiene o si el número es `1522` o `1990`; en los demás casos se usa `CMP`.

### 6.3 Consenso por libro

`correct_matricula_by_book()` ejecuta este procedimiento:

1. toma evidencia cruda o normalizada de cada página;
2. multiplica la confianza OCR, con piso de `0.05`, por `1.0` para lectura limpia, `0.8` para reconstruida y `0.25` para débil;
3. conserva un solo voto por `log_number` duplicado, el de mayor peso;
4. vota cada una de las cuatro posiciones y el sufijo por separado;
5. si el número ganador de cuatro dígitos nunca apareció completo, usa el número completo observado con mayor peso acumulado;
6. aplica la ganadora a páginas vacías o distintas;
7. guarda en `votes` cuántas páginas independientes leyeron completa la ganadora;
8. conserva el original en `alternatives` y registra `book_digit_consensus`.

El sufijo se vota por separado. Por ello, la matrícula final completa puede no coincidir con una lectura original, aunque el número de cuatro dígitos sí debe haber aparecido. Sin evidencia útil no se crea una matrícula. Una inferencia desde una lectura vacía o inválida queda en `OK` cuando tiene al menos dos votos y confianza mínima `0.50`. Ese mismo respaldo puede confirmar una lectura coincidente que ya tenía la página pero estaba en `WARNING`; se registra como `book_consensus_confirmation`. Si la propia página produjo otra matrícula canónica, o si el respaldo no alcanza, el valor inferido se conserva pero el campo queda en `WARNING` y la página va a `REVISAR`.

### 6.4 Verificación contra flota

La verificación es posterior al consenso y solo se ejecuta si está activa. `fleet.json` acepta `HP-XXXXCMP` y `HP-XXXXWWP`.

Una matrícula fuera del catálogo se compara por costo de caracteres. El candidato único de menor costo queda como propuesta en `WARNING` y la página va a `REVISAR`. Un empate borra el valor y también deja la página en `WARNING`. Una matrícula vacía no se completa desde la lista.

No existe un límite máximo de distancia. Por eso el catálogo debe estar completo y toda reclasificación debe quedar visible para revisión.

El archivo instalado declara individualmente estas 132 matrículas:

```text
HP-1376CMP..HP-1378CMP
HP-1520CMP..HP-1526CMP, excepto 1522, que usa WWP
HP-1530CMP..HP-1539CMP
HP-1711CMP..HP-1730CMP
HP-1821CMP..HP-1857CMP
HP-1990WWP
HP-9801CMP..HP-9822CMP
HP-9901CMP..HP-9932CMP
```

Los puntos dobles son notación de este manual. `fleet.json` no admite rangos.

### 6.5 Corrección de fecha

`correct_dates_by_book()` ordena y crea anclas con páginas no blancas que tienen `log_number` legible. Si la ejecución contiene un solo libro conocido, el consenso del libro y el relleno del día también pueden alcanzar sus páginas sin número:

1. prueba alternativas OCR y solo aplica una alternativa que reduzca las regresiones;
2. corrige lecturas minoritarias del año por mayoría cuando hay al menos tres lecturas, dos votos y 60 % de apoyo;
3. crea anclas directas solo con alineación fiable, estado `OK`, confianza mínima de `0.50`, fuente no inferida y sin alternativas ni notas de lectura dudosa, numérica o conflictiva;
4. permite como ancla un mes posicional en `WARNING`, con método de casillas y confianza mínima de `0.35`; también exige `log_number`, alineación fiable, fuente directa, sin alternativas ni notas de lectura dudosa, numérica o conflictiva;
5. interpola mes o año entre dos anclas iguales;
6. extrapola un extremo solo con dos anclas iguales y hasta diez números de distancia;
7. usa consenso de libro cuando hay al menos dos anclas unánimes;
8. con mes y año resueltos, completa el día faltante con el último día compatible con las fechas adyacentes o con el fin de mes;
9. recompone `YYYY/MM/DD` y marca conflictos o regresiones.

La inferencia queda en `WARNING`, con fuente y método. Una lectura válida distinta no se sobrescribe durante la interpolación; se marca como conflicto.

## 7. Vuelo, mantenimiento y firmas

### 7.1 Formato de `flight_number`

La expresión regular de la plantilla admite más formatos, pero el postproceso de producción solo entrega:

```text
1 a 4 cifras, excepto solo ceros
CM + 1 a 4 cifras
A + 3 o 4 cifras
TCK | CCK | SPV | SVC | SUP | MTC | SV, con una cifra final opcional
```

El normalizador elimina las etiquetas impresas, recupera cifras manuscritas confundidas con letras y ajusta el código solo si hay un único candidato en el vocabulario. Si hay empate o el formato no está admitido, el campo queda vacío. `flight_number` es opcional y no decide el estado de página.

### 7.2 Clasificación de entrada

El tipo de entrada se decide solo con `technician_license`:

- `true` con confianza mínima de `0.45`: mantenimiento;
- `false` con confianza mínima de `0.55`: vuelo;
- otro resultado: incierto.

Vuelo exige piloto, capitán y licencia de capitán. Mantenimiento exige piloto y técnico; los campos de capitán no intervienen. Si el tipo es incierto, solo se juzgan la licencia técnica y la firma de piloto.

Las discrepancias de firma se guardan en `page.discrepancy`. No cambian `page.status`, que solo representa aptitud para indexar matrícula, número y fecha.

## 8. Estados, duplicados y salidas

`page_status` usa tres datos: `log_number`, matrícula y fecha.

| Estado | Regla |
|---|---|
| `ERROR` | Ningún dato de índice es utilizable. |
| `WARNING` | Falta un dato o existe evidencia dudosa o inferida. |
| `OK` | Los tres datos están presentes y sus campos decisivos están en `OK`. |

La matrícula inferida por `book_correction` es una excepción: queda en `OK` y puede dejar la página en `OK`; su fuente conserva la trazabilidad. Las celdas auxiliares de fecha, firmas y `flight_number` no deciden el estado. `page_status()` clasifica un blanco como `ERROR`, pero el retorno temprano del pipeline lo deja en `WARNING` sin campos. Los resúmenes cuentan los blancos aparte y no los incluyen en los PDF de entrega.

`dup=true` se marca desde la segunda aparición de un `log_number` válido en el orden del batch. No se compara el contenido de la imagen.

En la GUI, **Procesar** guarda CSV, JSON y estadísticas sin componer PDF. **Exportar** genera los PDF de entrega. `write_outputs()` también clasifica discrepancias y aplica la política de fecha del CSV. El índice que relaciona páginas fuente con separadores solo se escribe al generar **Un solo PDF** o sus partes.

El CSV mínimo contiene las columnas seleccionadas. El completo añade confianza, estado, comentario y fuente. Matrícula, día, mes, año y celdas de fecha que no cumplen su formato quedan vacíos en ambos CSV. Un `log_number` inválido puede conservarse con estado `ERROR`. `raw_value`, `alternatives` e `inference_method` quedan solo en JSON. Los PDF copian las páginas fuente sin anotarlas ni rasterizarlas de nuevo.

La reexportación conserva los PDF existentes y numera las nuevas copias. La depuración elimina del modelo las apariciones duplicadas posteriores y los blancos, reescribe los datos y exige reexportar los PDF.

## 9. AirVault

El índice de páginas `<corrida>_paginas.json` representa cada hoja del PDF, incluidos los separadores. Se genera al exportar **Un solo PDF**, con o sin división en partes. No se genera con **Varios PDF** ni cuando se omite la creación de PDF. Sin este archivo no hay correspondencia segura entre fila CSV y página remota.

El flujo es:

1. repartir para Quick Upload los PDF que excedan el máximo elegido en la ventana, 300 páginas por batch de forma predeterminada;
2. cargar cada PDF mediante Quick Upload;
3. detectar el batch nuevo y asignarle nombre;
4. leer páginas y construir un plan sin escribir;
5. generar `revision.html` y `revision.csv`;
6. escribir solo registros habilitados;
7. releerlos y confirmar estado `Valid`;
8. guardar el manifiesto después de cada página;
9. liberar el batch al terminar, cancelar o abandonar.

BITS escribe siempre `Doc Type`, `Aircraft`, `Fleet`, `Log Page Number`, `Audit Status` y `End Date`. Añade `Batch Name` cuando se proporciona y `Lessor` cuando está resuelto. `Description` recibe `<flight_number> AUTO INDEX` cuando existe vuelo y `AUTO INDEX` cuando no existe. Esta marca se agrega al payload remoto y no altera los CSV.

La flota de AirVault se resuelve primero desde `airvault_flota.json`, en la raíz. BITS guarda allí los pares `Aircraft`, `Fleet` y `Lessor` confirmados por AirVault. Si no hay entrada, usa este respaldo: `HK-` produce `EMB`, `HP-98` y `HP-99` producen `MAX`, y las demás `HP-` producen `NG`. El reporte marca `fleet_inferido`; una confirmación posterior de AirVault sustituye el dato inferido en la caché.

El OCR y `fleet.json` solo admiten `HP-XXXXCMP` o `HP-XXXXWWP`. El adaptador de AirVault también acepta `HK-XXXX` con sufijo opcional al importar CSV o caché.

Las validaciones comprueban la cantidad de páginas, los datos obligatorios, la coincidencia de `log_number` y el estado remoto. También comprueban la matrícula cuando el catálogo de AirVault se obtuvo y no está vacío. Un catálogo vacío desactiva esa guarda y debe tratarse como condición de revisión. Una diferencia en la cantidad de páginas detiene el batch. Los demás fallos bloquean la página afectada. La GUI no sobrescribe una página `Valid`.

La sesión normal se obtiene con un perfil propio de Edge. Las peticiones usan `requests`, cookies de sesión y el token `AntiForgery` de cada aplicación de AirVault. Las respuestas transitorias se reintentan. El estado queda en `output/airvault/<job>/parte-XX/manifiesto.json`; `revision.html` y `revision.csv` documentan el plan. El manifiesto permite reanudar sin repetir páginas confirmadas.

El PDF `REVISAR` recoge solo matrículas ausentes o marcadas, conflictos canónicos, alineaciones dudosas e inferencias con menos de dos respaldos. Se sube como batch separado, no se indexa y se libera para intervención manual; las advertencias de fecha no envían por sí solas una página a este batch.

### 9.1 Operación por consola

`run_airvault.py` separa el trabajo en `preparar`, `subir`, `descubrir`, `plan`, `indexar` y `verificar`. El comando `todo` ejecuta descubrir, planificar, indexar y verificar; no sube los PDF. Cada etapa reutiliza el manifiesto del trabajo.

Use `plan` antes de escribir. `indexar --revisar` solicita una confirmación para todas las páginas habilitadas y `--auto` no se detiene.

> **PRECAUCIÓN:** `--sobrescribir` permite reescribir páginas remotas que ya están `Valid`. La GUI y la consola sin esa opción las bloquean.

## 10. Plantillas

`app/templates/schema.py` valida plantillas Pydantic. Cada campo usa coordenadas relativas `x`, `y`, `w`, `h` entre 0 y 1, identificador único, tipo, expresión regular, postproceso y umbrales.

El editor abre PDF a 150 DPI. Permite colocar, mover y redimensionar regiones. Al guardar redondea la geometría a cuatro decimales y conserva las propiedades que la interfaz no expone. Una plantilla debe contener al menos un campo.

Después de cambiar una plantilla:

1. cárguela sobre varios escaneos reales;
2. ejecute **Preprocesar** y revise geometría;
3. procese un rango pequeño;
4. compruebe JSON, estados y recortes;
5. ejecute la suite de pruebas.

## 11. Mantenimiento

### 11.1 Dependencias

Después de cambiar `requirements.txt`, use una copia de desarrollo o una reconstrucción limpia:

```powershell
portable\python312\tools\python.exe -m pip install --upgrade -r requirements.txt
```

No actualice dependencias directamente sobre un portable liberado. Las versiones mínimas pueden instalar combinaciones aún no aprobadas; registre las versiones resultantes antes de liberar. `setup.ps1 -Check` comprueba que Python sea 3.12, pero no exige el parche 3.12.10. También prueba tres imports y los archivos principales de modelos. No sustituye una ejecución real.

### 11.2 Modelos OCR

Los nombres activos son:

```text
PP-OCRv6_medium_det
PP-OCRv5_mobile_rec
```

Si se cambia alguno, actualice `setup.ps1`, `tools/precache_paddle.py`, `app/ocr/engine.py` y `app/core/config.py`. Precargue en `portable/paddlex/`, ejecute una inferencia con red y repita la prueba sin red.

### 11.3 Python, Tesseract y lanzador

Para cambiar Python o Tesseract, actualice la versión, la URL y el SHA-256 en `setup.ps1`; después, reconstruya el paquete desde una instalación limpia. Para regenerar el EXE:

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1 -Launcher
```

PyInstaller es una herramienta de construcción y no se declara como dependencia de ejecución.

### 11.4 Pruebas

En una copia de desarrollo, si falta `pytest`:

```powershell
portable\python312\tools\python.exe -m pip install pytest
```

Pruebas normales:

```powershell
portable\python312\tools\python.exe -m pytest
portable\python312\tools\python.exe -m pytest tests -k airvault
```

Las pruebas de AirVault usan un cliente falso y no escriben en producción. Una liberación debe incluir además una muestra PDF real y la prueba portable sin red.

### 11.5 Lista de liberación

1. Árbol de trabajo revisado; cambios ajenos preservados.
2. `fleet.json` validado con Operaciones.
3. Plantilla probada en escaneos representativos.
4. Suite completa aprobada.
5. Inferencia Paddle aprobada en CPU y sin red.
6. Ninguna caché persistente creada fuera de `portable/`.
7. CSV y JSON comparados con una ejecución conocida.
8. PDF de entrega revisado página por página en una muestra.
9. Perfil autenticado `portable/edge-airvault/` excluido o limpio.
10. Carpeta completa copiada y abierta desde otra ubicación sin privilegios de administrador.
11. PDF de muestra procesado por GUI y CLI sin red desde la copia.
12. AirVault probado aparte, con red, primero mediante **Subir y revisar**.

## 12. Capacidades desactivadas

La GUI y la CLI fijan:

```text
date_ocr_fallback=False
date_slot_ocr=False
vlm_enabled=False
```

Tesseract, el OCR por ranuras y el VLM son capacidades opcionales o experimentales. La presencia de sus componentes no las activa. No documente ninguna como operativa hasta habilitarla, probarla en CPU y confirmar que funciona sin descargas durante la ejecución.
