# BITS: manual de uso

| Control | Valor |
|---|---|
| Aplicación | BITS, Logbook Classification |
| Plataforma | Windows, ejecución local en CPU |
| Entrada normal | Archivos PDF |
| Salida | Datos de corrida y PDF de entrega |
| Revisión del manual | 20 AUG 2026 |

## 1. Objeto

BITS lee bitácoras aeronáuticas escaneadas, ordena sus páginas y prepara los datos que se indexan en AirVault. El programa trabaja en cuatro etapas:

1. prepara y alinea cada página;
2. lee matrícula, número de bitácora, fecha, vuelo y firmas;
3. aplica las reglas del libro y separa los casos que requieren revisión;
4. genera los datos y los PDF de entrega.

El OCR se ejecuta dentro del equipo. La conexión a red solo es necesaria para instalar o actualizar el paquete y para usar AirVault.

## 2. Requisitos de operación

Antes de iniciar, confirme lo siguiente:

- la carpeta de BITS está completa, incluida `portable/`;
- los originales son PDF y se pueden abrir;
- la plantilla corresponde al formato de la bitácora;
- `fleet.json` contiene todas las aeronaves vigentes si se verificará la flota;
- `output/` tiene espacio para los datos y las copias PDF;
- ningún PDF de la tarea se moverá ni sustituirá durante el proceso.

Para indexar se requiere conexión a AirVault y una cuenta autorizada. Microsoft Edge es el medio normal para iniciar sesión. Como respaldo, el operador puede pegar una cookie en **Sesión**; queda oculta y no se guarda.

> **PRECAUCIÓN:** la verificación de flota supone que `fleet.json` es el catálogo completo. Si falta una aeronave, una lectura válida puede reclasificarse como otra matrícula parecida.

## 3. Reglas que usa el sistema

### 3.1 Libro y número de bitácora

Un libro físico tiene 50 páginas y pertenece a una sola aeronave. `log_number` debe contener exactamente siete dígitos.

Los primeros cinco dígitos identifican la serie. Los dos últimos identifican uno de dos libros físicos consecutivos:

- `00` a `49`: primer libro físico de la serie;
- `50` a `99`: segundo libro físico de la serie.

BITS usa este número para agrupar y ordenar. No inventa ni corrige un `log_number` ilegible. Una página sin siete dígitos queda para revisión y no se usa en la corrección secuencial de fechas.

### 3.2 Matrícula

El formato de salida es `HP-XXXXCMP` o `HP-XXXXWWP`, donde `XXXX` son cuatro dígitos. BITS acepta separadores y mayúsculas o minúsculas en la lectura, corrige confusiones comunes de escritura y normaliza el resultado.

Primero se decide la matrícula dominante del libro. Una página vacía o ilegible puede usarla automáticamente cuando la respaldan por lo menos dos lecturas independientes. Si la página había dado otra matrícula completa, la inferencia se conserva para ayudar a revisar pero la página pasa a **REVISAR**: no se coloca bajo un separador que contradice su propia lectura.

Después, si **Verificar matrículas** está activo, se compara con `fleet.json`. Una matrícula fuera de la lista se cambia al candidato único más parecido y queda en `WARNING`, dentro de **REVISAR**; el parecido por sí solo no autoriza indexarla. Si hay empate, se borra el valor y también pasa a **REVISAR**.

La lista instalada contiene 132 aeronaves:

| Serie | Matrículas configuradas |
|---|---|
| 1376 a 1378 | `HP-1376CMP` a `HP-1378CMP` |
| 1520 a 1526 | sufijo `CMP`, excepto `HP-1522WWP` |
| 1530 a 1539 | sufijo `CMP` |
| 1711 a 1730 | sufijo `CMP` |
| 1821 a 1857 | sufijo `CMP` |
| 1990 | `HP-1990WWP` |
| 9801 a 9822 | sufijo `CMP` |
| 9901 a 9932 | sufijo `CMP` |

Use **Editar lista de matrículas…** para registrar altas y bajas. El archivo guarda cada matrícula por separado; los rangos de la tabla solo resumen su contenido actual.

### 3.3 Fecha

La fecha manuscrita se espera en casillas `DD|MMM|AA`. El programa localiza la retícula en cada página, por lo que no depende de una posición fija. Acepta meses en español o inglés y conserva la fecha final como `YYYY/MM/DD`.

Dentro del mismo libro, la fecha no puede retroceder cuando aumenta `log_number`. Varias páginas pueden tener el mismo día. La regla no cruza de un libro a otro.

Cuando falta parte de la fecha, BITS puede completar mes, año o día con evidencia del mismo libro, incluido el último día del mes cuando no se resuelve el día. Todo valor inferido conserva su procedencia. Una advertencia de fecha no manda por sí sola la bitácora al lote `REVISAR`; la guarda decisiva para el separador es la matrícula.

### 3.4 Número de vuelo

`flight_number` es opcional. El mismo casillero admite vuelos numerados y códigos operativos:

- vuelos numerados: uno a cuatro dígitos, excepto solo ceros; `CM` seguido de uno a cuatro dígitos; o `A` seguido de tres o cuatro dígitos;
- códigos operativos: `TCK`, `CCK`, `SPV`, `SVC`, `SUP`, `MTC` o `SV`, con una cifra final opcional.

El sistema corrige letras y cifras de trazo parecido. Si la lectura no encaja sin ambigüedad, deja el campo vacío. Este campo no decide si la página es de vuelo o mantenimiento y no cambia el estado general de la página.

El tipo se decide por la licencia del técnico:

| Resultado de licencia técnica | Tipo | Comprobación de firmas |
|---|---|---|
| Presente | Mantenimiento | piloto y técnico presentes; los campos de capitán no intervienen |
| Ausente | Vuelo | piloto, capitán y licencia de capitán presentes |
| Incierto | Incierto | se revisan licencia técnica y firma de piloto |

### 3.5 Significado de los procesos

| Proceso | Resultado |
|---|---|
| Corrección de inclinación | Endereza el escaneo. |
| Alineación | Ajusta la página a la geometría de la plantilla. |
| Preprocesado de recortes | Localiza tinta y amplía cada región antes de leerla. |
| OCR | Convierte la escritura de las regiones en valores. |
| Postproceso | Lleva cada valor a su formato válido o lo deja vacío. |
| Corrección por libro | Usa la regla de una aeronave por libro y la secuencia de fechas. |
| Verificación de flota | Compara la matrícula resuelta con `fleet.json`. |
| Discrepancias | Comprueba las firmas exigidas para vuelo o mantenimiento. |
| Depuración | Retira duplicados posteriores y páginas en blanco de la corrida. |
| Exportación | Compone los PDF y actualiza los datos de entrega. |
| Indexado | Escribe en AirVault solo las páginas aprobadas en la revisión. |

## 4. Procedimiento completo

### 4.1 Preparar la entrada

1. Copie los PDF directamente en `input/`, o téngalos disponibles en otra carpeta.
2. Abra `LogbookClassification.exe`.
3. Use **Detectar** para cargar `input/` o **Seleccionar archivos…** para elegir documentos externos.
4. Revise el orden mostrado. **Detectar** ordena por nombre; **Seleccionar archivos…** conserva el orden entregado por el selector. La interfaz no permite reordenarlos y el rango de páginas se numera de forma continua sobre todo el lote.
5. Si solo procesará una parte, indique la primera y la última página global.

Los archivos que no aportan páginas al rango no entran al OCR.

### 4.2 Seleccionar plantilla y flota

1. Elija la plantilla correcta.
2. Active **Verificar matrículas** para usar el catálogo instalado.
3. Abra **Editar lista de matrículas…** si hubo altas, bajas o cambios de sufijo.
4. Guarde la lista antes de procesar.

La plantilla normal es `template/aircraft_log.json`. Use otra solo si sus regiones fueron comprobadas sobre ese formulario.

### 4.3 Ajustar el procesamiento

Mantenga activadas **Corrección de inclinación**, **Alineación** y **Preprocesar recortes** durante la operación normal. Seleccione los hilos de CPU que puede usar el equipo. **Reservar un núcleo** deja capacidad para otras tareas.

La **Página de referencia** debe estar completa y ser nítida. Se interpreta dentro del tramo seleccionado de cada PDF, no como una página global del lote.

Si la geometría del escaneo es dudosa:

1. active **Visualizar campos**;
2. pulse **Preprocesar**;
3. recorra varias páginas;
4. confirme que los recuadros cubren los datos correctos;
5. ajuste plantilla o referencia si los recuadros se desplazan.

**Preprocesar** solo calcula geometría. No ejecuta OCR ni crea una corrida. La casilla **Preprocesar recortes** sí actúa durante el OCR.

### 4.4 Definir la entrega

Antes de procesar, seleccione cómo se organizará la salida:

- **Un solo PDF** inserta separadores para matrícula o mes;
- **Varios PDF** genera archivos separados por los criterios marcados;
- **Posibles discrepancias** aparta las páginas con firmas faltantes o inciertas;
- **Errores** genera un PDF auxiliar con datos críticos sin resolver;
- **Fecha del CSV** conserva el día leído, usa fin de mes si falta el día, o fuerza fin de mes para todas las filas;
- **Campos importantes** define las columnas del CSV mínimo y la vista resumida.

Los recuadros de **Visualizar campos** aparecen solo en pantalla. No se imprimen en los PDF de entrega.

### 4.5 Procesar

1. Confirme archivos, rango, plantilla, flota y opciones.
2. Pulse **Procesar**.
3. Vigile páginas terminadas, tiempo transcurrido y avance por PDF.
4. No mueva los originales ni cierre la aplicación mientras se guardan datos.
5. Espere el mensaje **Procesamiento terminado**.

Al finalizar, BITS guarda CSV, JSON y estadísticas. Los PDF de entrega se generan después con **Exportar**. Los originales que estaban directamente en `input/` pasan a `input/processed/`; los seleccionados fuera de `input/` no se mueven.

### 4.6 Revisar los resultados

La tabla contiene una fila por página. Haga doble clic en una fila para mostrar esa página en la vista previa.

| Estado | Acción |
|---|---|
| `OK` | Los tres datos de índice están resueltos sin valores dudosos. |
| `WARNING` | Revise un dato inferido, débil o incompleto. |
| `ERROR` | Ninguno de los tres datos de índice está resuelto. |

Los tres datos de índice son `log_number`, matrícula y fecha. Las firmas se evalúan por separado en **Posibles discrepancias**. El número de vuelo es opcional.

Revise como mínimo:

- cada `log_number` y su secuencia;
- cambios de fecha dentro del libro;
- matrículas con fuente de libro o flota;
- `dup=true` desde la segunda aparición de un mismo `log_number`;
- `disc=true` por firmas;
- páginas de **REVISAR** y, si activó **Errores**, `errores.pdf`.

### 4.7 Depurar

Use **Depurar** antes de la entrega para quitar páginas repetidas o en blanco.

1. Marque **Duplicados**, **Páginas en blanco** o ambas.
2. Confirme el conteo.
3. Pulse **Eliminar**.
4. Exporte de nuevo si ya existían PDF de entrega.

Se conserva la primera aparición de cada `log_number`. Las páginas en blanco ya se excluyen del PDF de entrega; **Depurar** también las quita del CSV, JSON y estadísticas. Los duplicados permanecen en el PDF hasta depurarlos. La operación reescribe los datos de la corrida y no puede dejarla sin páginas. Los PDF anteriores nunca se modifican; cada exportación crea otra copia con sufijo `-2`, `-3` o posterior.

### 4.8 Exportar

1. Confirme las opciones de organización.
2. Pulse **Exportar**.
3. Espere el mensaje de terminación.
4. Abra la carpeta de la corrida y compruebe los PDF.

Una reexportación no repite OCR. Reescribe los datos y estadísticas, conserva los PDF anteriores y agrega `-2`, `-3` y siguientes cuando un nombre ya existe.

La estructura normal es:

```text
output/
└── BITS DD MON YYYY HH MM/
    ├── datos/
    │   ├── <corrida>.CSV
    │   ├── <corrida>_completo.CSV
    │   ├── <corrida>.json
    │   └── <corrida>_paginas.json    solo para PDF único o sus partes
    ├── stats.json
    └── <PDF de entrega>
```

El CSV principal contiene las columnas seleccionadas. El CSV completo añade confianza, estado, comentario y fuente. Matrícula, día, mes, año y casillas de fecha quedan vacíos si no cumplen su formato. Un `log_number` inválido puede conservarse con estado `ERROR` y debe revisarse. El JSON conserva la lectura cruda, alternativas y método de inferencia.

### 4.9 Indexar en AirVault

1. Exporte la corrida con **Un solo PDF**. Puede usar **Repartir en** para limitar el tamaño de cada parte. Esta modalidad crea el PDF de entrega y su índice de páginas. Si falta el índice, reexporte de esta forma.
2. Confirme que el CSV mínimo conserve matrícula, `log_number` y fecha. Conserve también `flight_number` si debe enviarse a **Description**.
3. Abra **Indexar en AirVault…**.
4. Elija una de las últimas 25 corridas o pulse **Otra ejecución…**.
5. Confirme el nombre del lote y el **Máximo por batch**. El valor inicial es 300 páginas; los PDF que lo superen se dividen para Quick Upload sin modificar la entrega ni el CSV. Deje **Sesión** vacío. En el primer acceso, complete el inicio de sesión y el segundo factor en Edge.
6. Pulse **Subir y revisar**. El programa sube cada PDF, encuentra su lote y prepara el plan sin indexar.
7. Pulse **Ver reporte…** y revise `revision.html`.
8. Confirme `pagina_lote`, `archivo_origen`, `pagina_origen`, matrícula, flota, `log_number`, fecha, acción y avisos. Revise toda fila con `fleet_inferido=si`. Las discrepancias de firmas no aparecen en este reporte y no bloquean AirVault; revíselas antes en la corrida.
9. Pulse **Indexar** solo después de aprobar el reporte completo. El programa escribirá todas las filas cuya acción sea **escribir**; no hay aprobación individual por página. Si una fila habilitada es incorrecta, no indexe: corrija o reprocese la corrida, expórtela y repita la revisión.

Las acciones del reporte significan:

- **escribir:** página habilitada;
- **bloqueada:** página que no se tocará;
- **separador:** página que conserva la posición del PDF.

La columna **ya_indexada** indica si la página ya estaba válida. En ese caso, la acción es **bloqueada** y la página se omite.

Las páginas con matrícula ausente, marcada, contradicha o sin respaldo suficiente forman un lote terminado en `REVISAR`. BITS lo sube y lo libera, pero su clasificación e indexado son manuales. No se envían allí todas las inferencias: las de libro coherentes y respaldadas continúan en los batches automáticos.

En el flujo normal, BITS escribe `Doc Type`, `Aircraft`, `Fleet`, `Log Page Number`, `Audit Status`, `End Date` y `Batch Name`. Añade `Lessor` cuando está resuelto. `Description` queda como `<vuelo> AUTO INDEX` cuando se leyó el vuelo y como `AUTO INDEX` cuando no se leyó. La marca solo viaja a AirVault: no modifica el CSV ni el reporte de revisión. Al indexar también borra en AirVault las páginas separadoras del lote automático. El lote `REVISAR` conserva sus páginas y se indexa a mano. No envía los demás campos.

`Fleet` se toma primero de `airvault_flota.json`, donde BITS guarda los valores confirmados por AirVault. Si no hay dato conocido, se infiere por familia y el reporte marca `fleet_inferido=si`; confirme esa fila antes de indexar.

Una diferencia en la cantidad de páginas detiene el lote completo. Un dato obligatorio vacío, un número de bitácora duplicado, datos existentes en AirVault que no coinciden con el manifiesto o una lectura fallida bloquean solo la página afectada. Una matrícula desconocida también bloquea cuando fue posible leer el catálogo remoto. La interfaz no sobrescribe páginas `Valid` ni campos ajenos al índice controlado.

El manifiesto se guarda después de cada página. Si se repite **Subir y revisar**, se reutiliza lo terminado y no se reescriben páginas válidas. **Cancelar** se atiende al terminar la solicitud remota en curso y conserva lo ya escrito. Espere el estado **Cancelado** y confirme en AirVault que ningún lote quedó tomado.

## 5. Terminación y contingencias

### Cancelación del OCR

Pulse **Cancelar** una vez y espere a que termine el procesamiento de las páginas en curso. BITS guarda datos parciales, no genera PDF de entrega y deja los originales en su sitio. No use una corrida cancelada como entrega final.

### Original no localizado

Abra **Visor de CSV…**, seleccione la corrida y use **Ubicar PDF…**. La reexportación necesita el JSON consolidado, la plantilla usada y todos los PDF fuente.

### Cierre con trabajo activo

Solicite la cancelación y espere. No fuerce el cierre durante OCR, escritura de datos, exportación o indexado.

### Fin de tarea

Antes de entregar o indexar, confirme:

1. rango y cantidad de páginas;
2. matrícula y fecha por libro;
3. duplicados y páginas en blanco;
4. discrepancias de firmas;
5. contenido de **REVISAR**;
6. PDF final y reporte de AirVault.
