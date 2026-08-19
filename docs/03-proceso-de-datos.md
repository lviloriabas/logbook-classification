# 3. Proceso de datos

## 3.1 Planificación del lote

El sistema ordena los PDF por nombre y convierte el rango global en tramos por archivo. Cada tramo conserva el número de página real del documento fuente. Los archivos se abren brevemente para contar sus páginas; uno fuera del rango no pasa al OCR.

Antes de procesar, el sistema detecta el DPI nativo. La página completa se renderiza al menor valor entre el DPI solicitado y el disponible. La banda de fecha puede usar más detalle, hasta 300 DPI, sin ampliar por encima de la fuente.

## 3.2 Calibración

La calibración prepara una referencia por documento:

1. abre el PDF con PyMuPDF;
2. renderiza la página de referencia;
3. detecta y corrige inclinación, si está activada;
4. obtiene los puntos de alineación;
5. prepara la transformación común;
6. cuando una función activa lo requiere y hay tres páginas o más, estima el fondo impreso repetido.

La alineación usa características ORB y una transformación robusta calculada con RANSAC. AKAZE y correlación de fase sirven como respaldo. Las transformaciones confiables se estabilizan con la mediana de páginas vecinas. Si no se alcanza la calidad mínima, la página continúa sin forzar una transformación dudosa y queda marcada para revisión.

El fondo impreso puede servir como evidencia para casillas y geometría de fecha. La plantilla y la configuración de producción actuales no lo restan del OCR. El motor conserva el escaneo alineado original para no borrar escritura repetida legítima.

## 3.3 Tratamiento de una página

La secuencia de página es la siguiente:

1. Renderizado del tramo seleccionado.
2. Detección de página en blanco.
3. Corrección de inclinación.
4. Alineación contra la referencia.
5. Localización dinámica de las casillas de fecha.
6. Recorte de los campos de plantilla.
7. OCR regional o análisis de tinta, según el tipo de campo.
8. Normalización de valores.
9. Combinación de día, mes y año.
10. Aplicación de reglas y cálculo del estado.

Una página en blanco no pasa por la lectura completa. Permanece registrada en CSV, JSON y estadísticas, pero no se incorpora a los PDF organizados.

## 3.4 Lectura de texto

PaddleOCR usa el detector `PP-OCRv6_medium_det` y el reconocedor `PP-OCRv5_mobile_rec`, ambos en CPU. El motor procesa solo las regiones de la plantilla.

Cada campo define un modo:

- `detect`: localiza texto dentro del recorte y después lo reconoce;
- `line`: trata el recorte como una sola línea. Si el resultado no cumple el postproceso o la expresión regular, el sistema repite la lectura con detector.

El preprocesado opcional localiza tinta y ajusta el tamaño del recorte. Las fechas usan la retícula detectada en cada página; por ello, el sistema no depende únicamente de las coordenadas fijas del JSON.

La configuración de producción de GUI y CLI no encadena Tesseract ni VLM. Ambos mecanismos existen como soporte técnico, pero permanecen desactivados en los puntos de entrada normales.

## 3.5 Firmas y casillas

El detector de firmas no identifica personas. Determina `true`, `false` o `unclear` según la tinta presente en la región.

El análisis:

- estima el color del papel local;
- descarta reglas largas del formulario;
- mide concentración y extensión de tinta;
- normaliza la escala respecto del DPI;
- reduce el efecto del fondo gris y de la calca de otra página.

Con ocho páginas alineadas o más dentro del mismo PDF, las firmas inciertas pueden compararse contra el fondo del mismo campo, calculado con una muestra de hasta 32 páginas. Esta revisión no une un libro repartido entre varios PDF. Los resultados firmes no se sustituyen por esa comparación.

Las casillas se evalúan por cobertura de tinta y cantidad de componentes. Los umbrales proceden de la plantilla.

## 3.6 Normalización

El postproceso convierte lecturas variables en formatos controlados:

- `digits`: conserva dígitos para `log_number`;
- `matricula`: produce `HP-XXXXCMP` o `HP-XXXXWWP` cuando hay evidencia suficiente;
- `day`, `month`, `year`: valida componentes manuscritos;
- `char`: conserva una sola posición de la retícula de fecha;
- `flight_number`: ajusta la lectura a las formas que admite el casillero.

El casillero de vuelo no tiene un formato fijo. Las formas admitidas son:

| Forma | Ejemplo | Criterio |
|---|---|---|
| Vuelo numerado | `395`, `4605` | Entre una y cuatro cifras. |
| Vuelo con prefijo | `CM472` | Un prefijo leído junto a tres cifras se normaliza a `CM`. La `A` se conserva. |
| Código de mantenimiento | `TCK`, `CCK`, `SPV`, `SVC`, `SUP`, `MTC`, `SV` | Se ajusta al código más parecido a un trazo de distancia. Una cifra final forma parte del código: `SV2`, `SVC2`. |

El código se compara por clase de trazo y no letra a letra, porque el reconocedor devuelve la P como `9`, `D` o `R`, la S como `5` y la T como `J`: `S9V`, `SRV` y `52V` son el mismo `SPV` de la página. La comparación toma la lectura entera, con los dígitos que haya dentro, y admite un solo trazo distinto; a igualdad de trazos gana el código con la misma cantidad de caracteres.

Una lectura que no corresponde a ninguna de esas formas no se escribe. La letra confundida con una cifra se recupera cuando el tramo conserva evidencia numérica y un empate entre dos códigos no se resuelve: `ZCC` es un `700` manuscrito y no un `CCK`.

El valor OCR sin normalizar queda en `raw_value`. Los candidatos descartados pero útiles quedan en `alternatives` para las correcciones posteriores.

## 3.7 Estado de campo y de página

Las reglas estructurales de campo proceden del JSON:

- valor obligatorio;
- expresión regular;
- longitud mínima o máxima.

El umbral general de baja confianza procede de `AppConfig`. Las firmas usan además los umbrales de confianza definidos en la plantilla para la clasificación de discrepancias.

Un campo puede quedar `OK`, `WARNING` o `ERROR`. El estado general de la página representa su capacidad de indexación:

| Estado | Criterio operativo |
|---|---|
| `OK` | `log_number`, matrícula y fecha están disponibles y los campos decisivos no tienen una duda activa. |
| `WARNING` | La página puede indexarse parcialmente o conserva un dato que requiere confirmación. |
| `ERROR` | Una página no blanca no aporta ninguno de los datos de indexación disponibles en la plantilla. |

Las páginas en blanco se identifican y contabilizan por separado. Las firmas, las celdas individuales de fecha y el número de vuelo no deciden el estado de indexación. Las firmas se evalúan en la clasificación de discrepancias.

## 3.8 Corrección por libro

Después de leer todos los PDF, el sistema agrupa las páginas por los cinco primeros dígitos de `log_number` y por el tramo `00`–`49` o `50`–`99`. El orden físico de los PDF no se usa como secuencia.

### Matrícula

Cada libro debe corresponder a una sola aeronave. El corrector vota cada posición de la matrícula con las lecturas del libro y pondera confianza y calidad. El bloque de cuatro dígitos debe corresponder a una lectura observada. El sufijo `CMP` o `WWP` se vota por separado, por lo que la combinación final puede no haber aparecido completa en una sola página.

La matrícula ganadora completa o corrige las páginas del libro. El valor anterior permanece en alternativas o comentarios para auditoría. Esta corrección queda aceptada en el estado del campo y se distingue mediante `source=book_correction`. Si el libro no aporta una lectura utilizable, la matrícula queda sin resolver.

### Fecha

El corrector ordena por `log_number` y aplica, en este orden:

1. consenso del año;
2. selección de alternativas compatibles con la secuencia;
3. interpolación de mes y año entre lecturas confiables;
4. inferencia limitada en los extremos del libro;
5. relleno por consenso cuando no hay conflicto;
6. recuperación conservadora del día al cierre del mes;
7. detección de regresiones y fechas no resueltas.

Una inferencia queda identificada como tal. Un conflicto no se oculta; el campo se marca para revisión.

## 3.9 Verificación de flota

Si la verificación está activa, el sistema compara cada matrícula canónica con `fleet.json`.

- Si la matrícula existe, la conserva.
- Si no existe y hay un único avión más parecido, la reclasifica y conserva la lectura original como alternativa.
- Si hay empate o la lectura no permite comparación, elimina la asignación y envía la página a **REVISAR**.

Si el archivo no existe, está vacío o no aporta matrículas válidas, el sistema registra una advertencia y deja las lecturas sin cambios.

La comparación pondera las confusiones habituales entre dígitos manuscritos y el sufijo. No aplica una distancia máxima: si existe un único candidato, ese candidato gana. Toda reclasificación queda en `WARNING` y registra su origen; por esto es indispensable que el catálogo esté completo.

## 3.10 Duplicados y discrepancias

El sistema marca `dup=true` cuando un `log_number` ya apareció antes en el lote.

La clasificación de firmas determina el tipo de entrada:

| Tipo | Evidencia | Condición esperada |
|---|---|---|
| Vuelo | Licencia de técnico ausente con confianza suficiente. | Piloto, capitán y licencia de capitán presentes. |
| Mantenimiento | Licencia de técnico presente con confianza suficiente. | Piloto y técnico presentes; capitán y licencia de capitán ausentes. |
| Incierto | Licencia de técnico no concluyente. | Se revisan la licencia y las anomalías comunes a ambos tipos, sin forzar una clasificación. |

Un incumplimiento firme —firma requerida ausente o firma prohibida presente— se registra en la categoría confirmada histórica `missing`. Una lectura débil o `unclear` se clasifica como `uncertain`. La página recibe `disc=true` en ambos casos y conserva la razón exacta.

## 3.11 Paralelismo y orden

El sistema usa procesos persistentes, no GPU. La distribución considera hilos lógicos y memoria libre. Favorece un proceso OCR por hilo disponible, limita el total a 32 procesos y presupuesta aproximadamente 850 MB por proceso. Reserva entre 1,5 y 3 GB, según la memoria instalada, y asigna hasta tres hilos internos cuando no puede abrir más procesos.

Con varios PDF, el planificador puede repartir documentos completos. Con pocos documentos, reparte páginas. Las colas son acotadas y el orden final se restablece antes de los correctores y reportes. Una falla en la ruta por archivo se reintenta por la ruta de páginas.

## 3.12 Trazabilidad de una lectura

Cada campo conserva:

- valor normalizado;
- texto OCR original;
- confianza;
- estado y comentario;
- fuente;
- método de inferencia;
- alternativas consideradas.

Fuentes habituales: `ocr`, `date_cells`, `book_correction`, `fleet_validation`, `ocr_fallback`, `vlm` e `inferred`. El JSON consolidado conserva estos datos para auditoría.
