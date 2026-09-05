# BITS: detalles técnicos

Este documento resume cómo funciona cada proceso y qué tecnología utiliza. Las instrucciones para el operador están en [README.md](README.md).

## 1. Condiciones del sistema

- Plataforma: Windows portable.
- Ejecución: solo CPU, sin detección de GPU.
- Operación normal: sin descargas y sin instalación en el equipo.
- Intérprete: Python 3.12 dentro de `portable/python312/tools/`.
- Modelos: almacenados en `portable/paddlex/official_models/`.
- Caché de Paddle: dirigida a `portable/paddlex/`.
- Datos persistentes: rutas relativas a la carpeta de BITS.
- CSV: su formato no se cambia sin autorización.

La unidad de distribución es la carpeta completa. `BITS.exe` solo inicia el Python portable y abre `run_gui.py`.

## 2. Componentes y tecnología

| Área | Tecnología | Uso |
|---|---|---|
| Interfaz | PySide6 | Ventanas, tablas, visor PDF y trabajos en segundo plano. |
| PDF | PyMuPDF | Lectura, renderizado, conteo y composición de páginas. |
| Visión | OpenCV y NumPy | Inclinación, alineación, retículas, tinta, blancos y firmas. |
| OCR | PaddleOCR, PaddleX y PaddlePaddle | Detección y reconocimiento en CPU. |
| Imágenes | Pillow | Conversiones, recortes e iconos. |
| Modelos de datos | Pydantic | Configuración, plantillas y validación de estructuras. |
| Reportes | csv y json de Python | CSV principal, CSV completo, JSON y estadísticas. |
| Red | requests y truststore | Acceso HTTPS a AirVault con certificados de Windows. |
| Registro | Loguru | Bitácoras técnicas y rotación de archivos. |
| Eliminación | Send2Trash | Envío recuperable a la Papelera. |
| Pruebas | pytest | Pruebas unitarias y de integración local. |

Las versiones admitidas están en `requirements.txt`. Los modelos de producción son:

```text
Detector: PP-OCRv6_medium_det
Reconocedor: PP-OCRv5_mobile_rec
Dispositivo: cpu
```

oneDNN está desactivado por compatibilidad con Paddle en Windows. No se usan Tesseract, VLM ni OCR por casillas en producción.

## 3. Flujo general

```text
PDF
  -> rango y calibración
  -> inclinación y alineación
  -> recortes y OCR
  -> normalización y reglas del libro
  -> estados, duplicados y discrepancias
  -> CSV, JSON y estadísticas
  -> PDF de entrega
  -> AirVault
```

La GUI y la consola usan el mismo núcleo. Los puntos principales son `Pipeline`, `process_pdf_batch()` y `write_outputs()`.

## 4. Entrada y rango

`PageRange` numera todo el batch desde 1 aunque haya varios PDF. Después divide el rango por archivo y excluye los documentos que no aportan páginas.

`PdfPageRenderer` mantiene cada PDF abierto y renderiza en BGR. La GUI usa 200 DPI como base y la consola 150 DPI. La lectura de fecha puede usar hasta 300 DPI, sin superar la resolución detectada del original.

Archivos principales:

- `app/core/page_range.py`
- `app/vision/pdf_loader.py`
- `app/core/pipeline.py`

## 5. Preprocesamiento y alineación

Cada PDF se calibra con una página de referencia del tramo seleccionado:

1. Canny y Hough estiman la inclinación.
2. ORB y RANSAC estiman rotación, escala y traslación.
3. AKAZE y correlación de fase actúan como respaldo.
4. Una mediana local estabiliza transformaciones cercanas.

La alineación adapta el escaneo a la plantilla. No corrige una plantilla equivocada. OpenCV y NumPy realizan todo el cálculo en CPU.

Archivos principales:

- `app/vision/alignment.py`
- `app/vision/preprocessing.py`
- `app/core/pipeline.py`

## 6. Lectura de una página

El orden interno es:

1. detectar si la página está en blanco;
2. aplicar inclinación y alineación;
3. localizar la retícula de fecha;
4. recortar los campos definidos por la plantilla;
5. mejorar contraste y localizar tinta;
6. ejecutar OCR o detección de firma;
7. normalizar y validar valores;
8. combinar la fecha;
9. calcular el estado inicial.

El OCR usa reconocimiento por línea. Si el valor no supera el formato esperado, puede repetir la lectura con detección completa.

Archivos principales:

- `app/ocr/engine.py`
- `app/ocr/regional.py`
- `app/vision/date_geometry.py`
- `app/utils/postprocess.py`

## 7. Reglas del libro

`log_number` tiene siete dígitos. Sus primeros cinco identifican la serie y los dos últimos separan los libros `00` a `49` y `50` a `99`. El número se usa para agrupar y ordenar, pero nunca se inventa ni se corrige.

### Matrícula

La salida normal es `HP-XXXXCMP` o `HP-XXXXWWP`. El sistema limpia separadores y corrige confusiones comunes entre letras y cifras. Después calcula un consenso por libro. Una inferencia necesita respaldo de páginas independientes y conserva fuente, confianza y alternativas.

El sufijo no se lee de la página: se deduce del número. El `HP-1990` es `WWP`. El `HP-1522` aparece escrito de las dos maneras en las bitácoras, pero AirVault solo lo tiene en su picklist como `HP-1522CMP`, así que el CSV, `fleet.json` y la carga lo escriben siempre así.

Si la verificación de flota está activa, el resultado se compara con `fleet.json`. Un candidato parecido queda para revisión y no se aprueba solo por similitud.

### Fecha

La fecha final usa `YYYY/MM/DD`. Las anclas confiables del mismo libro permiten completar o corregir partes faltantes. La fecha puede repetirse, pero no retroceder al aumentar `log_number`. Toda inferencia queda trazada y marcada para revisión.

### Memoria entre ejecuciones

De cada libro se guardan su matrícula (`book_matriculas.json`) y los extremos de fecha confirmados (`book_fechas.json`). Un libro puede llegar repartido entre entregas, y sin esa memoria las páginas de la segunda vuelven a empezar sin anclas.

Las dos memorias se aprenden del OCR, así que se comprueban contra AirVault, que es el índice que la empresa da por bueno. La comprobación va sola dentro del plan del indexado: las páginas que AirVault ya tenía en verde salen de una lectura que el plan hace igual, así que no cuesta ninguna petición extra ni la pide nadie. Una matrícula que no es de ningún avión de `fleet.json` se descarta sin consultar nada.

Confirmar no cambia nada. Reemplazar una entrada exige dos bitácoras distintas del mismo libro, el mismo respaldo que se exige para indexar sin revisión. Si AirVault no dice lo mismo en todo el libro, no se toca nada.

Archivos principales:

- `app/validation/grouping.py`
- `app/validation/book_corrector.py`
- `app/validation/date_corrector.py`
- `app/validation/book_memory.py`
- `app/validation/fleet.py`

## 8. Vuelo y firmas

`flight_number` es opcional. El normalizador acepta vuelos numéricos y los códigos operativos definidos en la plantilla y en el postproceso. Un valor ambiguo queda vacío.

La discrepancia se juzga en cada bitácora por separado. El libro no interviene: dos páginas seguidas del mismo avión pueden ser una de vuelo y otra de mantenimiento.

El tipo lo deciden solo las casillas limpias: la licencia de técnico y el bloque del capitán.

- mantenimiento (licencia de técnico escrita): requiere firma de piloto, firma de técnico y licencia de técnico;
- vuelo (licencia de técnico vacía y algo escrito en el bloque del capitán o en la firma del piloto): requiere firma de piloto, firma de capitán y licencia de capitán;
- anulada o VOID (licencia de técnico, las dos casillas del capitán y la firma del piloto vacías): se indexa como cualquier otra y no abre discrepancia;
- incierto (ninguna casilla limpia lo dice con seguridad): queda para revisión.

`technician_signature` no decide el tipo. Cae justo debajo de los sellos «MXI Entry Performed By» y «DATE / STA», que la llenan de tinta ajena: de 30 páginas revisadas a mano en las que el detector la daba por escrita, ninguna tenía firma. Un sello solo añade tinta y nunca la quita, así que su lectura «ausente» sigue siendo de fiar y el campo se conserva como requisito de mantenimiento; lo que no soporta es decidir de qué tipo es la bitácora.

Una bitácora VOID se anuló al llenarla y se apartó. Lleva el log page y a veces la matrícula, nada más. No le falta ninguna firma porque no llegó a usarse. Lo que la distingue de un vuelo al que le falta el capitán es la firma del piloto: si el vuelo se realizó, esa firma está.

El detector de firmas analiza tinta, no identidad. Su salida es presente, ausente o incierta. De las dos licencias solo se mira si la casilla está escrita o vacía: no se lee su número, que no es un index field ni entra en la regla. Las discrepancias se guardan aparte y no cambian por sí solas los datos que se escriben en AirVault.

Solo la ausencia confirmada aparta la página. Una lectura incierta se anota en el reporte de discrepancias pero no lleva `page.discrepancy`, no llega al batch REVISAR y se indexa como cualquier otra: ninguna firma es un index field, así que una firma ilegible no puede estropear lo que se escribe en AirVault, y apartarla obligaría a teclear a mano los seis campos que el sistema ya resolvió. Sobre las ejecuciones guardadas eran entre la mitad y dos tercios del batch manual: 191 de las 328 páginas de la ejecución de 2409.

Una alineación que no se pudo verificar anota la firma, pero no borra su lectura: el campo queda en WARNING con la nota «Alineación no confiable» y conserva valor y confianza. Sin ancla fiable la página no se transforma, así que el recorte cae donde lo pone la plantilla, que es donde ya caía. Recortar la confianza dejaba las cinco firmas por debajo de los dos umbrales del campo a la vez, el tipo de página quedaba indeciso y la bitácora salía marcada como discrepancia tuviera lo que tuviera escrito.

Una bitácora con una ausencia confirmada se escribe con el Audit Status `AUDIT IN PROGRESS`, el valor del picklist para lo que queda pendiente de auditar. Es lo único que la distingue en AirVault del resto del batch. Sale de la columna `disc` del CSV, que por eso marca solo las confirmadas.

Archivos principales:

- `app/vision/signature.py`
- `app/validation/discrepancias.py`
- `app/core/pipeline.py`

## 9. Paralelismo

El pipeline usa procesos persistentes para repartir PDF y páginas. Limita la cantidad de workers, reserva memoria y asigna hilos internos por proceso. Los resultados se ordenan de nuevo antes de generar reportes.

La GUI ejecuta trabajos largos mediante `QThread`, pero el OCR pesado se distribuye con procesos. Esto mantiene la ventana activa y aprovecha varios núcleos sin usar GPU.

Archivos principales:

- `app/core/parallelism.py`
- `app/gui/worker.py`
- `app/core/pipeline.py`

## 10. Estados, duplicados y depuración

El estado de página depende de matrícula, `log_number` y fecha:

| Estado | Regla general |
|---|---|
| `OK` | Los tres datos son utilizables. |
| `WARNING` | Falta un dato o existe evidencia débil o inferida. |
| `ERROR` | Ninguno de los datos principales es utilizable. |

`dup=true` se marca desde la segunda aparición de un `log_number` válido. No se comparan las imágenes. La depuración conserva la primera aparición, retira las posteriores y puede eliminar blancos del modelo de la ejecución.

Archivos principales:

- `app/validation/page_status.py`
- `app/validation/duplicates.py`
- `app/validation/depuracion.py`

## 11. Reportes y exportación

`write_outputs()` genera:

- CSV principal con las columnas elegidas;
- CSV completo con estado, confianza, comentario y fuente;
- JSON con lectura cruda, alternativas y métodos de inferencia;
- `stats.json` con el resumen;
- PDF de entrega al exportar;
- índice `_paginas.json` cuando la entrega usa un solo PDF.

Los PDF copian páginas fuente sin rasterizarlas otra vez. Los separadores forman parte del índice de páginas. Una reexportación conserva los archivos anteriores y numera la nueva copia.

Archivos principales:

- `app/reports/outputs.py`
- `app/reports/csv_reporter.py`
- `app/reports/json_reporter.py`
- `app/reports/organize.py`
- `app/reports/stats.py`

## 12. AirVault

El módulo de AirVault trabaja por etapas: preparar, subir, descubrir, planear, indexar y verificar. Aparte de esas queda `memoria`, que no toca ningún batch: contrasta con AirVault lo que el sistema recuerda de cada libro.

### Sesión y red

Microsoft Edge obtiene la sesión federada y guarda su perfil en `portable/edge-airvault/`. Python reutiliza las cookies y obtiene el token antifalsificación de cada aplicación. `requests` maneja HTTP y `truststore` usa el almacén de certificados de Windows.

Las cookies y contraseñas no se guardan en logs. El perfil de Edge contiene una sesión real y debe excluirse de cualquier entrega.

### Subida e identificación

Quick Upload recibe el PDF. BITS sube una parte a la vez, espera su publicación y la identifica por nombre, cantidad de páginas y contenido. Después corrige el nombre provisional cuando hace falta.

Una carga ya aceptada no se reenvía automáticamente si no aparece. Esta regla evita duplicados cuando AirVault tarda en publicar.

### Plan e indexado

El plan relaciona cada página remota con el CSV mediante `_paginas.json`. Valida cantidad, campos obligatorios, duplicados, matrícula y valores existentes. Las páginas válidas no se sobrescriben de forma predeterminada.

Un libro tiene una sola aeronave, así que las páginas del batch que AirVault ya tiene en verde dicen cuál es la de todo el libro. Una página cuya matrícula contradiga esa queda bloqueada (`matricula_del_libro`): no se escribe hasta que alguien mire. Un libro del que AirVault tiene dos matrículas distintas no manda sobre nadie, porque ahí no hay una autoridad sino un desacuerdo.

El manifiesto se actualiza después de cada página. Una ejecución interrumpida puede reanudarse sin repetir lo confirmado. El batch `REVISAR` se conserva para intervención manual.

De la misma lectura sale la comprobación de la memoria de libros (sección 7). El subcomando `memoria` de `run_airvault.py` alcanza además los libros que no vienen en ningún batch, consultando Web Search; informa siempre y solo escribe con `--aplicar`.

Archivos principales:

- `app/airvault/flujo.py`
- `app/airvault/memoria.py`
- `app/airvault/uploader.py`
- `app/airvault/discovery.py`
- `app/airvault/mapping.py`
- `app/airvault/guards.py`
- `app/airvault/indexer.py`
- `app/airvault/manifest.py`

La configuración no secreta está en `airvault.json`. La caché de aeronave, flota y arrendador está en `airvault_flota.json`. El estado de cada trabajo se guarda bajo `output/airvault/`.

## 13. Plantillas

Las plantillas JSON usan coordenadas relativas entre 0 y 1. Cada campo define identificador, tipo, región, formato, postproceso y umbrales. Pydantic valida la estructura al cargarla.

El editor visual conserva las propiedades que no muestra. Después de cambiar una plantilla se debe revisar la geometría, procesar una muestra y ejecutar las pruebas.

Archivos principales:

- `app/templates/schema.py`
- `app/templates/manager.py`
- `app/gui/editor_window.py`

## 14. Portabilidad y reconstrucción

`ensure_portable_env()` prepara estas variables antes de importar Paddle:

```text
PADDLE_PDX_CACHE_HOME=<raíz>\portable\paddlex
PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=1
PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0
FLAGS_use_mkldnn=0
```

Para reconstruir el paquete en una máquina Windows con red:

```powershell
setup.cmd
```

Comprobaciones y tareas disponibles:

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1 -Check
powershell -ExecutionPolicy Bypass -File setup.ps1 -Launcher
powershell -ExecutionPolicy Bypass -File setup.ps1 -Force
powershell -ExecutionPolicy Bypass -File setup.ps1 -CleanCache
```

`-Force` reconstruye el intérprete y los modelos. Debe usarse sobre una copia o dentro de un proceso controlado. Después de cambiar modelos, precárguelos con `tools/precache_paddle.py` y compruebe una ejecución sin red.

## 15. Puntos de entrada

| Archivo | Función |
|---|---|
| `run_gui.py` | Aplicación principal. |
| `run_cli.py` | OCR y generación de salidas por consola. |
| `run_editor.py` | Editor de plantillas. |
| `run_airvault.py` | Etapas de AirVault y reanudación. |
| `setup.ps1` | Reconstrucción y comprobación del portable. |

Use `--help` en las dos consolas para consultar las opciones vigentes. `run_airvault.py todo` no sube archivos: descubre, planea, indexa y verifica un batch ya cargado.

## 16. Pruebas y liberación

Pruebas normales:

```powershell
portable\python312\tools\python.exe -m pytest
portable\python312\tools\python.exe -m pytest tests -k airvault
```

Las pruebas de AirVault usan un cliente falso. No escriben en producción.

Antes de liberar:

1. ejecute toda la suite;
2. procese una muestra real por GUI y consola;
3. compruebe OCR en CPU y sin red;
4. compare CSV, JSON y PDF con una ejecución conocida;
5. valide `fleet.json` y la plantilla;
6. limpie `portable/edge-airvault/`;
7. copie la carpeta completa a otra ubicación y ejecútela sin permisos de administrador;
8. pruebe AirVault por separado y revise el plan antes de indexar.
