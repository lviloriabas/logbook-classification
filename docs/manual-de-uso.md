# BITS: manual de uso

| Control | Valor |
|---|---|
| Aplicación | BITS, Logbook Classification |
| Plataforma | Windows, ejecución local en CPU |
| Entrada normal | Archivos PDF |
| Salida | Datos de ejecución y PDF de entrega |
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

Cuando falta parte de la fecha, BITS puede completar mes, año o día con evidencia del mismo libro, incluido el último día del mes cuando no se resuelve el día. Todo valor inferido conserva su procedencia. Una advertencia de fecha no manda por sí sola la bitácora al batch `REVISAR`; la guarda decisiva para el separador es la matrícula.

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
| Depuración | Retira duplicados posteriores y páginas en blanco de la ejecución. |
| Exportación | Compone los PDF y actualiza los datos de entrega. |
| Indexado | Escribe en AirVault solo las páginas aprobadas en la revisión. |

## 4. Procedimiento completo

### 4.1 Preparar la entrada

1. Copie los PDF directamente en `input/`, o téngalos disponibles en otra carpeta.
2. Abra `LogbookClassification.exe`.
3. Use **Detectar** para cargar `input/` o **Seleccionar archivos…** para elegir documentos externos.
4. Revise el orden mostrado. **Detectar** ordena por nombre; **Seleccionar archivos…** conserva el orden entregado por el selector. La interfaz no permite reordenarlos y el rango de páginas se numera de forma continua sobre todo el batch.
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

La **Página de referencia** debe estar completa y ser nítida. Se interpreta dentro del tramo seleccionado de cada PDF, no como una página global del batch.

Si la geometría del escaneo es dudosa:

1. active **Visualizar campos**;
2. pulse **Preprocesar**;
3. recorra varias páginas;
4. confirme que los recuadros cubren los datos correctos;
5. ajuste plantilla o referencia si los recuadros se desplazan.

**Preprocesar** solo calcula geometría. No ejecuta OCR ni crea una ejecución. La casilla **Preprocesar recortes** sí actúa durante el OCR.

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

### 4.5.1 Automático

**Automático**, en la misma fila que **Procesar**, hace la cadena entera sin
volver a pulsar nada: procesa, exporta y, si se pidió, depura la ejecución,
sube la entrega a AirVault y la escribe allí.

Hasta dónde llega se elige en **Automatización…**, junto a **Opciones
avanzadas**. Es un menú que se abre encima de la ventana, como el del botón
derecho, y se queda abierto entre clic y clic: marque los pasos que quiera y
salga con un clic fuera o con `Esc`. La ventana de AirVault tiene el mismo
botón y el mismo menú. Procesar y exportar aparecen marcados y apagados
porque siempre se hacen. **Depurar páginas repetidas y en blanco** es opcional y va suelto:
quita las apariciones sobrantes de cada bitácora repetida (nunca la primera)
y las páginas en blanco antes de exportar, así que los PDF salen ya sin
ellas. Los tres pasos de AirVault (subir, indexar y completar el batch)
ocurren uno detrás de otro y se marcan juntos: marcar uno enciende los que
van antes, y apagar uno apaga los que van después. Esperar a que AirVault
deje los batches listos no se elige: va dentro de **Subir a AirVault**,
porque subir sin esperar la respuesta deja el batch en la cola y nadie
vuelve a mirarlo. La espera sí se ve en la línea de pasos, y cada cuánto se
pregunta se elige en **Comprobar cada**, en la ventana de AirVault.

La elección se conserva al cerrar el programa. **Completar batch** es la
misma casilla que **Completar batch** en la ventana de AirVault: marcarla en
un sitio la marca en el otro.

**Cancelar** corta la cadena entera, no solo el paso en curso.

Debajo de la barra de progreso, una línea con los ocho pasos dice hasta
dónde se llegó: gris claro los que faltan, azul el que está en curso, verde
los terminados, rojo el que se cortó y gris apagado los que no se eligieron.
El primero, **Preprocesar**, es el tramo con el que arranca el
procesamiento: endereza y alinea el batch entero antes de leer la primera
página.
Los cuatro de AirVault ocurren en la otra ventana y se marcan igual, así que
la línea sirve para saber si la entrega terminó de subirse sin ir a buscarla.

La cadena trabaja solo sobre la ejecución en marcha. Los batches que
quedaron a medias en ejecuciones anteriores se retoman únicamente al pulsar
**Subir a AirVault** en la ventana de AirVault.

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

Se conserva la primera aparición de cada `log_number`: de una bitácora repetida se va una sola página, la más nueva, y de las que aparecen más de dos veces queda igualmente la primera. Vale lo mismo si marca las casillas a mano y si depura el proceso automático: el cuadro no deja marcar todas las apariciones de una bitácora, y el borrado respeta una aunque se le pidan todas. Las páginas en blanco ya se excluyen del PDF de entrega; **Depurar** también las quita del CSV, JSON y estadísticas. Los duplicados permanecen en el PDF hasta depurarlos. La operación reescribe los datos de la ejecución y no puede dejarla sin páginas. Los PDF anteriores nunca se modifican; cada exportación crea otra copia con sufijo `-2`, `-3` o posterior.

### 4.8 Exportar

1. Confirme las opciones de organización.
2. Pulse **Exportar**.
3. Espere el mensaje de terminación.
4. Abra la carpeta de la ejecución y compruebe los PDF.

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

1. Exporte la ejecución con **Un solo PDF**. Puede usar **Repartir en** para limitar el tamaño de cada parte. Esta modalidad crea el PDF de entrega y su índice de páginas. Si falta el índice, reexporte de esta forma.
2. Confirme que el CSV mínimo conserve matrícula, `log_number` y fecha. Conserve también `flight_number` si debe enviarse a **Description**.
3. Abra **Indexar en AirVault…**.
4. Elija una de las últimas 25 ejecuciones o pulse **Otra ejecución…**.
5. Confirme el nombre del batch y el **Máximo por batch**. La copia portable empieza en 200 páginas y después recuerda la última cantidad elegida aquí o en **Repartir en**; los PDF que la superen se dividen para Quick Upload sin modificar la entrega ni el CSV. El reparto respeta esa cantidad al pie de la letra: todos los batches llevan las mismas páginas y solo el último se queda con el resto. Si una aeronave queda partida entre dos batches, el siguiente abre con una copia de su separador para que ninguno empiece con bitácoras sueltas. Puede cambiar esta cantidad aunque la ejecución ya tenga batches en AirVault: esos se conservan como están y solo se vuelven a repartir las bitácoras que ninguno se llevó, de modo que no se suba nada dos veces ni se quede nada fuera. Deje **Sesión** vacío. En el primer acceso, complete el inicio de sesión y el segundo factor en Edge. Si una carga no aparece con el nombre esperado, la ventana recorre la cola entera buscándola por nombre, por páginas y por Log Page Number. Cuando esa búsqueda no la encuentra y además el archivo lleva subido más de 30 minutos, la da por perdida ahí mismo: ya no queda nombre bajo el que pueda estar y no viene en camino. Si la carga es reciente no se toca nada, porque que AirVault todavía no la haya publicado es lo normal: la ventana revisa tres veces nombres, páginas y contenido y solo después la da por perdida, con uno de dos motivos: que las partes enviadas después ya estén indexadas (la cola pasó de largo, no hay nada que esperar) o que hayan pasado 30 minutos desde que se subió el archivo. Ese tiempo se cuenta desde la fecha del batch, así que una ejecución que retome días más tarde no vuelve a esperar media hora. Dada por perdida, y mientras **Comprobar cada** esté marcado, la vuelve a enviar sola junto con cualquier archivo que no haya llegado a subirse. La espera crece entre un envío y el siguiente (media hora, una hora, hora y media), de modo que una cola que solo va lenta no recibe el mismo archivo cada vuelta del reloj, y se reenvía como mucho dos veces: al tercer intento lo que falla ya no es el envío, y seguir mandando el mismo PDF es como acaban varias copias del mismo batch en la cola el día que AirVault las publica todas. Agotado el tope, el resumen lo dice y queda la orden a mano desde la tabla, que sí sube; el tope apaga el envío, no la búsqueda: mientras **Comprobar cada** esté marcado la ventana sigue mirando la cola por si AirVault acaba publicando la carga, y si aparece se indexa sola. La tabla de batches es la cola de trabajo: con el botón derecho sobre una fila puede subirla, comprobarla, indexarla, cerrarla o sacarla de la cola sin tocar a las demás. Subir a mano vale sobre cualquier fila que no tenga todavía un batch confirmado en AirVault, y no repite la búsqueda larga: consulta la cola una sola vez para no duplicar una carga que el servidor acabara de publicar, y manda el archivo. Si el batch dejaría páginas amarillas (las que AirVault pinta así porque les falta un campo obligatorio) la ventana lo dice antes de subir, con cuántas son y qué les falta, y usted decide: lo limpio es volver a exportar para que esas bitácoras vayan al batch REVISAR, pero con el archivo ya hecho puede salir más barato completarlas a mano en Web Index. Si autoriza, ese batch no se cerrará solo hasta que estén completas. El batch REVISAR nunca pregunta: existe justo para recoger lo dudoso. Con Ctrl o Mayúsculas elige varias y la acción vale para todas: cada una hace lo que le corresponde, y el menú dice a cuántas se aplicaría. No hace falta esperar a que termine lo que esté haciendo: lo que pida mientras tanto queda en cola y arranca solo al quedar libre. Un batch cancelado conserva su ID y lo que ya se le escribió; solo deja de subirse, buscarse e indexarse hasta que lo reanude, y lo que estuviera esperando turno para él se descarta. **Completar batch** se elige una sola vez y se recuerda entre ejecuciones; la casilla de esta ventana y la de **Automatización…** son la misma. Un batch que el programa cierre por su cuenta al terminar de indexarlo queda como **Terminado por el programa**, para distinguirlo del que ya estaba cerrado en AirVault cuando se encontró. Puede elegir otra ejecución mientras una está trabajando: se abre en otra ventana y las dos avanzan simultáneamente. Antes de subir nada, **Vista previa…** (junto al título de la tabla de batches) enseña en cuántos batches quedaría repartida la ejecución con el máximo elegido, cuántas páginas y cuántas bitácoras lleva cada uno, y cuáles ya están en AirVault; mirarla no prepara ni sube nada, así que puede cambiar el máximo y volver a mirarla. Desde ahí, y con el botón derecho sobre una fila de la tabla de batches, abre la lista de las bitácoras que van dentro de un batch, con la página que ocupa cada una, su matrícula, su `log_number`, su fecha y su vuelo. El campo **Buscar**, junto a ese mismo título, hace la pregunta al revés: escriba una bitácora (su `log_number`, su matrícula, su vuelo, su fecha o su archivo de origen) y le dice en qué batches de la cola está, resaltando sus filas y nombrándolos debajo de la tabla con la página que ocupa en cada uno. Puede estar en varios a la vez, que es lo que pasa cuando la bitácora era dudosa y viajó también en el batch REVISAR, o cuando la ejecución se subió dos veces; ‹ y › pasan de un batch al siguiente.
6. La automatización viene con **Indexar páginas** marcada, en **Automatización…**. Pulse **Subir a AirVault**: el programa intenta primero todos los PDF, identifica cada ID y corrige el nombre provisional `Empty-Batch`. No empieza a indexar una parte hasta que el Web Index confirma ese mismo ID con el título esperado. Si no puede distinguir un candidato, muestra sus IDs y cantidades de páginas sin elegir ninguno.
7. Si prefiere mirar antes de escribir, desmarque **Indexar páginas** en **Automatización…** y pulse **Subir a AirVault**: la ventana deja los batches revisados y el resumen dice cuántas páginas se escribirían y cuántas quedan bloqueadas, sin tocar nada. **Vista previa…** y la lista de bitácoras de cada batch (botón derecho sobre su fila) enseñan qué va dentro. El detalle página por página en CSV y HTML sigue disponible por consola, con `run_airvault.py` sin banderas.
8. En el recorrido manual, pulse **Indexar** solo después de esa comprobación. El programa escribirá todas las filas cuya acción sea **escribir**; no hay aprobación individual por página. Si una fila habilitada es incorrecta, no indexe: corrija o reprocese la ejecución, expórtela y repita la revisión.

Antes de mandar cada archivo, BITS comprueba que sus bitácoras no estén ya
en AirVault. Además de la cola del Web Index, mira dos cosas que la cola no
puede ver: un **libro de envíos** propio de la instalación (que no se borra
con **Eliminar el registro local** ni desaparece al reprocesar los mismos
escaneos en otra carpeta) y una consulta a **Web Search** por el número de
tres bitácoras del batch, repartidas de principio a fin. Hace falta porque
completar un batch lo saca de la cola y lo manda a Web Search: desde ese
momento ninguna consulta a la cola lo encuentra, y sin estas dos nada
impedía volver a subirlo.

Si alguna de las dos dice que esas bitácoras ya están arriba, el batch **no
se sube y tampoco se cierra**, y su fila queda como **Posible duplicado**
con el motivo al lado. Es una sospecha, no una certeza: lo que se demuestra
es que el documento está publicado, no que este batch sea el que lo subió.
Mírelo en Web Index y, si de verdad no está, use el botón derecho sobre la
fila y **No es duplicado: volver a permitirlo**.

Las acciones del reporte significan:

- **escribir:** página habilitada;
- **bloqueada:** página que no se tocará;
- **separador:** página que conserva la posición del PDF.

La columna **ya_indexada** indica si la página ya estaba válida. En ese caso, la acción es **bloqueada** y la página se omite.

Las páginas con matrícula ausente, marcada, contradicha o sin respaldo suficiente forman un batch terminado en `REVISAR`. BITS lo sube y lo libera, pero su clasificación e indexado son manuales. No se envían allí todas las inferencias: las de libro coherentes y respaldadas continúan en los batches automáticos.

En el flujo normal, BITS escribe `Doc Type`, `Aircraft`, `Fleet`, `Log Page Number`, `Audit Status`, `End Date` y `Batch Name`. Añade `Lessor` cuando está resuelto. `Description` queda como `<vuelo> AUTO INDEX` cuando se leyó el vuelo y como `AUTO INDEX` cuando no se leyó. La marca solo viaja a AirVault: no modifica el CSV ni el reporte de revisión. Al indexar también borra en AirVault las páginas separadoras del batch automático. El batch `REVISAR` conserva sus páginas y se indexa a mano. No envía los demás campos.

`Fleet` se toma primero de `airvault_flota.json`, donde BITS guarda los valores confirmados por AirVault. Si no hay dato conocido, se infiere por familia y el reporte marca `fleet_inferido=si`; confirme esa fila antes de indexar.

Una diferencia en la cantidad de páginas detiene el batch completo. Un dato obligatorio vacío, un número de bitácora duplicado, datos existentes en AirVault que no coinciden con el manifiesto o una lectura fallida bloquean solo la página afectada. Una matrícula desconocida también bloquea cuando fue posible leer el catálogo remoto. La interfaz no sobrescribe páginas `Valid` ni campos ajenos al índice controlado.

El manifiesto se guarda después de cada página. Si se repite **Subir y revisar**, se reutiliza lo terminado y no se reescriben páginas válidas. **Cancelar** se atiende al terminar la solicitud remota en curso y conserva lo ya escrito. Espere el estado **Cancelado** y confirme en AirVault que ningún batch quedó tomado.

## 5. Terminación y contingencias

### Cancelación del OCR

Pulse **Cancelar** una vez y espere a que termine el procesamiento de las páginas en curso. BITS guarda datos parciales, no genera PDF de entrega y deja los originales en su sitio. No use una ejecución cancelada como entrega final.

### Original no localizado

Abra **Visor de CSV…**, seleccione la ejecución y use **Ubicar PDF…**. La reexportación necesita el JSON consolidado, la plantilla usada y todos los PDF fuente.

### Cierre con trabajo activo

Solicite la cancelación y espere. No fuerce el cierre durante OCR, escritura de datos, exportación o indexado.

### Fin de tarea

Antes de entregar o indexar, confirme:

1. rango y cantidad de páginas;
2. matrícula y fecha por libro;
3. duplicados y páginas en blanco;
4. discrepancias de firmas;
5. contenido de **REVISAR**;
6. PDF final y estado de los batches en AirVault.
