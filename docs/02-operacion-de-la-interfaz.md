# 2. Operación de la interfaz

## 2.1 Preparación del batch

1. Compruebe que la carpeta portable esté completa.
2. Copie los PDF pendientes en `input/` o manténgalos en una carpeta de trabajo autorizada.
3. Compruebe que la plantilla corresponda al formulario escaneado.
4. Si usa verificación de flota, actualice `fleet.json` antes de procesar.
5. Compruebe que `output/` tenga espacio para la ejecución y sus PDF.

> **PRECAUCIÓN:** La lista de flota debe contener todas las aeronaves vigentes. Una lista incompleta puede reclasificar una lectura válida como otra matrícula.

## 2.2 Arranque

Abra `LogbookClassification.exe`. El lanzador inicia `run_gui.py` con `portable/python312/tools/pythonw.exe` y no abre una consola.

Al iniciar, la ventana carga las plantillas disponibles y detecta automáticamente los PDF situados directamente en `input/`, ordenados por nombre.

Si el ejecutable no inicia, ejecute:

```batch
portable\python312\tools\python.exe run_gui.py
```

El mensaje de la terminal identifica una dependencia o componente portable ausente.

## 2.3 Selección de entrada

- **Seleccionar archivos…:** selecciona uno o más PDF de cualquier carpeta.
- **Detectar:** carga los PDF situados directamente en `input/`.
- **Vaciar input:** envía a la Papelera los archivos situados directamente en `input/`. No elimina `input/processed/`.
- **Vaciar output:** envía a la Papelera todo el contenido de `output/`, incluidas ejecuciones, registros y `.performance.json`.

Las acciones de vaciado solicitan confirmación. **Vaciar output** queda bloqueado durante preprocesamiento, OCR o exportación. **Vaciar input** queda bloqueado durante OCR o exportación, pero no durante el preprocesamiento.

> **PRECAUCIÓN:** No use **Vaciar input** mientras ejecuta **Preprocesar**, aunque el mando esté disponible.

El rango **Páginas** usa una numeración continua para todo el batch. Si el primer PDF tiene 10 páginas, la página 11 del batch es la primera del segundo PDF. La interfaz cuenta previamente las páginas de todos los documentos; los que no intersectan el rango no pasan al OCR.

## 2.4 Configuración normal

Seleccione la plantilla en **Plantilla**. Use **Buscar…** para una plantilla externa o **Abrir editor** para ajustar regiones.

Mantenga activadas estas funciones durante una operación normal:

- **Corrección de inclinación:** endereza el escaneo antes de alinearlo.
- **Alineación:** ajusta cada página contra la referencia.
- **Preprocesar recortes:** localiza la tinta y escala cada región antes del OCR.
- **Verificar matrículas:** compara el resultado con `fleet.json`.

La aplicación fija PaddleOCR en CPU. La GUI trabaja a un máximo de 200 DPI para la página completa y limita la resolución al detalle nativo del PDF.

## 2.5 Configuración de salida

**Repartir en** escribe la entrega en varios PDF en lugar de uno solo, de a
lo sumo las páginas indicadas. Una ejecución completa son unas 900 páginas y
casi dos gigas: repartida, cada archivo es un batch manejable en AirVault y
una subida que si se corta no obliga a rehacer todo. El corte se hace entre
secciones, así que las bitácoras de un mismo avión no se separan salvo que
un avión solo no quepa.


La entrega inicial queda configurada así:

- **Un solo PDF**;
- separación por **Matrícula**;
- sección **Posibles discrepancias** activada;
- fecha del CSV en **Día específico (si falta, fin de mes)**.

Seleccione **Varios PDF** para crear un archivo por cada combinación marcada. Active **Mes** si la entrega debe subdividirse por fecha. Active **Errores** para generar un PDF destinado a indexación manual.

Si selecciona **Varios PDF** sin marcar matrícula ni mes, el sistema genera un solo PDF porque no existe un criterio para dividir el batch.

La opción **Visualizar campos** dibuja las regiones solo en la vista previa. Nunca altera los PDF exportados. **Mostrar solo columnas importantes** limita los campos visibles cuando la visualización está activa. El botón contiguo permite guardar la selección por plantilla; esa selección también define el CSV mínimo.

## 2.6 Opciones avanzadas

- **Hilos del procesador:** presupuesto total de CPU.
- **Página de referencia:** página del documento usada para calibrar la alineación.
- **Reservar un núcleo para la interfaz:** resta un hilo del presupuesto para mantener la ventana fluida.

El sistema calcula automáticamente la cantidad de procesos OCR y los hilos internos según CPU y memoria. No es necesario ajustar esta distribución en una ejecución normal.

## 2.7 Preprocesamiento de comprobación

Use **Preprocesar** cuando deba verificar geometría antes del OCR.

1. Seleccione el batch y la plantilla.
2. Active **Visualizar campos**.
3. Pulse **Preprocesar**.
4. Recorra la vista previa.
5. Confirme que los recuadros cubran los datos manuscritos.

Esta tarea aplica inclinación y alineación. No ejecuta OCR ni genera una ejecución de entrega.

## 2.8 Procesamiento normal

1. Confirme entrada, rango y plantilla.
2. Confirme la lista de flota.
3. Confirme las opciones de salida.
4. Pulse **Procesar**.
5. Vigile la barra, el contador global y el avance por archivo.
6. Espere a que el estado indique que los datos quedaron guardados.
7. Revise la tabla, los duplicados y las páginas marcadas.
8. Ajuste la separación si hace falta y pulse **Exportar**.
9. Abra la carpeta de la ejecución desde el historial o desde `output/`.

**Procesar** guarda los datos de la ejecución (CSV, JSON y estadísticas) y no
genera los PDF. La entrega se arma al pulsar **Exportar**, con la separación
que esté marcada en ese momento.

La barra cuenta páginas terminadas del batch. Las páginas pueden finalizar fuera de orden por el procesamiento paralelo; el contador nunca representa el número de la última página entregada por un proceso.

## 2.8.1 Proceso automático

**Automático**, en la misma fila que **Procesar**, hace de una sola vez toda
la cadena: procesa, exporta y, si se pidió, depura la ejecución, sube la
entrega a AirVault y la escribe allí. Cada paso arranca solo al terminar el
anterior; los botones sueltos siguen disponibles para hacer un solo tramo.

Hasta dónde llega se elige en **Automatización…**, junto a **Opciones
avanzadas** y **Indexar en AirVault…**. Se abre como el menú del botón
derecho, encima de la ventana y sin quitarle sitio a nada, y se queda
abierto entre clic y clic: marque los pasos que quiera y salga con un clic
fuera o con `Esc`. La ventana de AirVault tiene el mismo botón y el mismo
menú, con la misma elección. Lo elegido se conserva al cerrar el programa,
en el `airvault.json` de la carpeta portable.

| Paso | Se elige | Qué hace |
| --- | --- | --- |
| Procesar (OCR) | No | Siempre se hace: es de donde salen los datos. |
| Exportar CSV, JSON y PDF | No | Siempre se hace: es la entrega. |
| Depurar páginas repetidas y en blanco | Sí, suelto | Quita las apariciones sobrantes de cada bitácora repetida (nunca la primera) y las páginas en blanco, antes de exportar, así que los PDF salen ya sin ellas. Sin marcar, la ejecución se exporta entera y **Depurar** sigue disponible para revisarla a mano. |
| Subir a AirVault | Sí | Manda a Quick Upload todos los batches de la entrega en cuanto termina la exportación. |
| Esperar a que AirVault los deje listos | Sí | Es la misma casilla que **Comprobar cada** en la ventana de AirVault, donde se elige el intervalo. |
| Indexar páginas | Sí | Escribe cada batch apenas AirVault lo deja entero. |
| Completar batch | Sí | Es la misma casilla que **Completar batch** en la ventana de AirVault: marcarla en un sitio la marca en el otro. |

Los cuatro pasos de AirVault ocurren uno detrás de otro y se marcan juntos:
marcar **Indexar páginas** enciende subir y esperar, porque no se puede
indexar lo que no está arriba; apagar **Subir a AirVault** apaga los tres de
abajo, que sin la carga no tienen sobre qué trabajar. **Depurar** va suelto y
no arrastra a ninguno.

**Cancelar** corta la cadena entera, no solo el paso en curso: lo ya leído se
guarda, pero no se exporta ni se sube nada más. Un error al procesar o al
generar las salidas la corta igual, y lo dice en el estado.

La cadena solo trabaja sobre **la ejecución que está en marcha**. La ventana
de AirVault puede retomar batches que quedaron a medias en ejecuciones de
días anteriores, pero eso lo hace únicamente cuando alguien pulsa **Subir a
AirVault**: lo que arranca solo (la cadena y el reloj de comprobación) no
sale de la ejecución elegida.

### La línea de pasos

Debajo de la barra de progreso hay una línea con los siete pasos en el orden
en que ocurren:

```
Procesar › Depurar › Exportar › Subir › Esperar › Indexar › Completar
```

Cada uno se pinta según lo que le pasó: gris claro los que faltan, azul el
que está en curso, verde los terminados, rojo el que se cortó y gris apagado
los que no están marcados en **Automatización…**, que no se van a hacer. Al
pasar por encima, cada paso dice su estado con palabras.

Los cuatro últimos ocurren en la ventana de AirVault y llegan aquí desde
ella, así que la línea sigue contando aunque esa ventana esté minimizada:
sirve para saber, sin ir a buscarla, si la entrega terminó de subirse, en
qué paso se quedó o dónde se cortó. Solo refleja la ejecución que esta
ventana mandó subir; si hay varias ventanas de AirVault abiertas, el avance
de las demás no la toca.

## 2.9 Vista previa y tabla

Antes de procesar, la vista previa recorre todos los PDF como una sola secuencia. Después del OCR, conserva solo los documentos que aportaron páginas al rango. Escriba un número de página global para saltar directamente a ella. La ventana indica además el archivo y la página local.

La tabla contiene una fila por página y usa las columnas del CSV completo. El selector de vista alterna entre todas las columnas y las importantes sin cambiar el archivo guardado. El orden de una columna sigue un ciclo de tres pasos: descendente, ascendente y orden original. La fila bajo el cursor y la fila seleccionada se sombrean enteras.

La página y la tabla se reparten el ancho a medias. Arrastre el separador para darle más a una de las dos; desde entonces se respeta esa medida, también al cambiar el tamaño de la ventana.

`Ctrl+C` copia **la celda que está bajo el cursor**, no la fila entera: se pulsa una celda para llevarse ese dato (un número de bitácora, una matrícula) y pegarlo en otro sitio. El menú del botón derecho hace lo mismo con la celda sobre la que se hizo clic. Vale igual en la tabla de la ventana principal, en la del visor de CSV y en la cola de la ventana de AirVault, que son las que seleccionan por filas.

El indicador **Duplicados** cuenta las páginas marcadas desde la segunda aparición de un `log_number`; la primera queda sin marca. El detalle muestra el grupo completo. Haga doble clic en una fila para llevar la vista previa a la página correspondiente.

**Depurar**, junto a **Exportar**, quita de la ejecución las páginas repetidas o en blanco. El cuadro dice cuántas hay de cada clase antes de borrar nada; marque las que correspondan y pulse **Eliminar**. Se reescriben el CSV mínimo, el CSV completo, el JSON y `stats.json` sin ellas, y la tabla y la vista previa se rehacen en el acto. Los PDF se generan al exportar, así que salen ya sin esas páginas. El botón espera a que la ejecución esté guardada y a que no haya ninguna escritura en curso; tras una ejecución cancelada no está disponible.

Duplicada es toda aparición posterior de un mismo `log_number` (la primera se conserva) y en blanco la que el procesamiento marcó como vacía. Una página que sea las dos cosas se elimina una sola vez. La depuración no puede dejar la ejecución sin ninguna página.

## 2.10 Terminación y archivo de entrada

Cuando el OCR termina correctamente y los datos quedan guardados, los PDF que estaban directamente en `input/` pasan a `input/processed/`. Un nombre repetido recibe el sufijo `-2`, `-3`, etc. Los PDF seleccionados desde otras carpetas no se mueven.

La ventana actualiza sus referencias después del traslado. La vista previa y la reexportación continúan disponibles.

## 2.11 Cancelación

Pulse **Cancelar** para detener el batch en un límite seguro.

- Las páginas que ya están en ejecución terminan antes de la parada y se incluyen en el resultado parcial.
- Se conservan CSV, JSON y estadísticas de las páginas terminadas.
- No se generan PDF de entrega para la ejecución parcial.
- Los archivos de entrada no pasan a `input/processed/`.
- El rango no avanza en forma automática. Ajústelo antes de continuar.
- La tabla y el avance parcial se retiran de la pantalla al guardar.

Al cancelar **Preprocesar**, se conserva únicamente la geometría terminada en memoria. No se generan CSV, JSON, estadísticas ni PDF.

> **PRECAUCIÓN:** No use una ejecución cancelada como entrega ni la reexporte desde el visor histórico. El visor puede abrir sus datos parciales, pero no los convierte en una ejecución completa.

## 2.12 Exportación y reexportación

Después del OCR, elija separación y formato de PDF y pulse **Exportar**. La operación reutiliza los resultados en memoria; no ejecuta OCR otra vez. Puede exportar tantas veces como quiera.

Cambiar solo **Fecha del CSV** repuebla la tabla y reescribe automáticamente los dos CSV. No pulse **Exportar** salvo que también necesite regenerar JSON, estadísticas o PDF. En el visor histórico, la nueva política de fecha sí se aplica al exportar.

La reexportación usa la misma carpeta de ejecución. Reescribe el CSV, el JSON y `stats.json`. Conserva los PDF existentes y añade un sufijo numérico a cualquier PDF nuevo cuyo nombre ya exista.

## 2.13 Indexado en AirVault

El botón **Indexar en AirVault…**, junto a **Opciones avanzadas**, abre una
ventana aparte. Escribe en el Web Index de AirVault los datos que la
ejecución ya leyó, en lugar de teclearlos página por página.

Requiere una ejecución exportada. Si marcó **Repartir en**, la entrega sale
en varios archivos y cada uno será un batch distinto en AirVault; la ventana
los sube y los indexa todos, y cuenta el avance sobre el total.

1. Pulse **Indexar en AirVault…**. La lista muestra las últimas 25
   ejecuciones procesadas, de la más reciente a la más antigua, con sus
   páginas y lo que tienen para subir.
2. Elija la ejecución. Viene señalada la exportada más reciente. Las que
   todavía no se pueden subir salen en gris: **Sin exportar** son las que no
   tienen PDF de entrega y **Falta reexportar** las exportadas antes de que
   existiera el índice de páginas. Con **Otra ejecución…** se elige el CSV
   de una que no esté en la lista.
3. Revise el nombre en **Batch**. Lleva la fecha y la hora de la ejecución
   porque en la cola de AirVault conviven batches con nombres repetidos.
4. Si necesita reducir el peso de la carga, marque **Compresión**. La copia
   interna que se envía se rasteriza a 200 DPI y JPEG de calidad moderada;
   los PDF exportados de la ejecución no cambian. Quick Upload admite hasta
   2.048 MB por archivo y el programa comprueba ese límite antes de enviar.
5. **Sesión** se deja vacío. La primera vez que pulse **Subir a AirVault**
   se abre una ventana de Edge para que entre a AirVault con su usuario de
   Microsoft; se cierra sola al terminar y las veces siguientes no vuelve a
   aparecer. El campo es el respaldo por si eso falla: se pega ahí la cookie
   de la sesión copiada del navegador, y no se guarda.
6. Si quiere ver antes en qué va a quedar la ejecución, pulse **Vista
   previa…**, junto al título de la tabla de batches: enseña cada batch con
   el nombre que llevaría, sus páginas y sus bitácoras, y desde ahí abre la
   lista de las bitácoras que van dentro de cada uno. Mirarla no prepara ni
   sube nada, así que puede cambiar el máximo por batch y volver a mirarla.
7. Pulse **Subir a AirVault**. Manda los PDF y termina ahí. Nada se indexa
   todavía.
8. Espere. AirVault mete el batch en su cola y tarda en dejarlo indexable:
   minutos, a veces mucho más. La ventana pregunta sola cada cinco minutos
   (se cambia en **Comprobar cada**, o se apaga) y con **Comprobar ahora**
   pregunta en el momento. El estado y el resumen indican cuándo los batches
   ya están listos para indexar. Cuando no queda nada que esperar, deja de
   preguntar.
9. Pulse **Ver reporte…** y revise las páginas bloqueadas.
10. Si quiere que el batch quede cerrado y fuera de la cola, marque
   **Completar batch**.
11. Pulse **Indexar**. Escribe en los batches que estén listos. El avance sale
   por la barra de esta ventana; la principal queda libre para seguir
   trabajando.

No hace falta esperar con la ventana abierta ni con el programa parado: se
puede seguir procesando en la ventana principal, y esta pregunta sola. Si
cierra y vuelve mañana, elija la misma ejecución: los batches reaparecen en
la lista tal como quedaron.

**Completar batch** da el batch por terminado en AirVault: lo indexa y lo
manda a Web Search. **Solo lo acepta con todas las páginas en verde**: si a
una le falta un campo obligatorio (casi siempre la fecha) el batch se queda
en la cola y la ventana dice qué páginas lo impiden. Cuente ahí también las
páginas separadoras del PDF: AirVault las deja en «No Template Match», que
tampoco es verde. Sin marcar la casilla, el batch se queda en la cola para
revisarlo, que es lo normal.

Mientras trabaja, la ventana va contando cada paso en la lista de abajo,
con la hora, y el reloj de al lado dice cuánto lleva el paso actual: una
espera de AirVault se distingue de un cuelgue. En las etapas sin cuenta
(entrar a AirVault, esperar a que el batch salga de la cola) la barra va en
marcha continua.

**Cancelar** detiene lo que esté en marcha y suelta los batches que se hayan
tomado en AirVault. Está disponible siempre que haya trabajo en vuelo y
corta en el acto: deja de reintentar, no espera a que el servidor conteste
la petición en curso y abandona la ventana de acceso si estaba esperando
a que alguien entrara. Lo ya escrito se conserva y al volver a comprobar
se retoma sin repetirlo.

**Cerrar** con un trabajo en marcha lo cancela y la ventana se va en cuanto
AirVault suelte los batches. Cerrarla mientras se espera no pierde nada: al
volver a abrirla y elegir la misma ejecución, los batches vuelven a salir y
basta con **Comprobar ahora**.

Las páginas separadoras del PDF (la matrícula de cada grupo y **POSIBLES
DISCREPANCIAS**) ocupan su página en el batch y no se indexan.

Las bitácoras con matrícula o `log_number` ausentes, marcados, contradichos por otra lectura
canónica o inferidos con un solo respaldo, además de las páginas detectadas en blanco, salen en un archivo aparte y forman
su propio batch, `… REVISAR`. Ese batch se sube pero no se indexa: queda en la
cola del Web Index, a la vista, para resolverlo a mano. Una inferencia de
libro coherente respaldada por dos o más lecturas sigue en el batch automático;
las advertencias de fecha y una alineación dudosa con los campos críticos
firmes tampoco la envían por sí solas a `REVISAR`. Los libros confirmados se
recuerdan en un JSON compacto para reutilizar su matrícula en otras ejecuciones.

Los batches se llaman como la ejecución, en mayúsculas y con la fecha y hora
del procesamiento: `DP | BITS 18 AUG 2026 05 42`, y `-1`, `-2`… si la
entrega se repartió. Quick Upload envia ese nombre. Si AirVault lo pierde y
publica un `Empty-Batch` o un título incompleto, el programa solo lo corrige
despues de confirmar la cantidad exacta de paginas y su contenido mediante el
nombre interno o varios `Log Page Number`. Mientras el mismo ID no aparezca
con el título completo, el batch no se considera listo ni se indexa; tampoco
se vuelve a subir solo por llevar tiempo con el nombre provisional.

Si el trabajo se corta, vuelva a pulsar **Comprobar ahora**: las páginas ya
escritas no se repiten y el PDF no se vuelve a subir.

El botón derecho sobre una fila del historial ofrece dos eliminaciones. **Eliminar el registro de AirVault** manda a la Papelera lo que el programa recuerda de esa ejecución (sus manifiestos y el registro de batches de la entrega) para empezarla de nuevo. **Eliminar la ejecución…** manda a la Papelera su carpeta de `output/` entera, con su CSV, su JSON, sus estadísticas y sus PDF de entrega, y la quita de la lista. Ninguna de las dos modifica los batches que ya estén en AirVault. No se elimina la ejecución que se esté subiendo o indexando, ni un CSV abierto con **Otra ejecución…** que viva fuera de `output/`.

**Vista previa…**, junto al título de la tabla de batches, adelanta el reparto sin preparar ni subir nada. De cada batch se abre la lista de sus bitácoras, que se mira como el visor de CSV: la hoja escaneada a la izquierda, la tabla a la derecha, un buscador encima y las columnas ordenables con un clic en su cabecera. Las dos son ventanas aparte, con su entrada en la barra de tareas: no bloquean la ventana de AirVault, que puede seguir subiendo mientras se las mira, y se pueden tener varias abiertas comparando batches. Cerrar la ventana de AirVault las cierra. Elija una fila (o haga doble clic) para ver su hoja. La columna **Estado** dice «Por indexar» mientras falte, «Indexada» cuando ya se escribió en AirVault y «Completada» cuando además se cerró el batch; si algo bloquea la página, dice qué.

La descripción técnica está en [el manual del indexado](airvault-indexado.md).

## 2.14 Visor de CSV e historial

Pulse **Visor de CSV…** para abrir una ejecución anterior.

1. Seleccione una de las 25 ejecuciones recientes en **Historial**, o use **Seleccionar carpeta…** o **Seleccionar CSV…**.
2. Seleccione el CSV mínimo o completo.
3. Escriba en **Buscar** cualquier texto del CSV: número de bitácora, matrícula, archivo, página o parte de ellos. La búsqueda no distingue mayúsculas, recorre las columnas que muestra la tabla (con el CSV completo también las ocultas en la vista resumida) y prioriza las celdas que coinciden por completo sobre las que solo contienen el texto. Repetir la búsqueda, o pulsar ‹ y ›, recorre las coincidencias; el indicador dice en qué columna coincidió y qué página es.
4. Revise la fila y la página fuente en el panel PDF. La numeración es
   continua entre todos los PDF de origen: el límite es la suma de sus páginas
   y al terminar un archivo las flechas continúan en el siguiente.
5. Use **Exportar** solo si la ejecución conserva su JSON, plantilla y PDF fuente requeridos.

Para quitar páginas sueltas, selecciónelas en la tabla (con `Ctrl` o `Mayús` para varias) y pulse `Supr`. Para juntar páginas que no están seguidas, marque su casilla en la primera columna o pulse la barra espaciadora sobre las filas elegidas: la fila marcada queda sombreada y, mientras haya alguna, son esas las que se eliminan. Para quitar de una vez las repetidas o las vacías, pulse **Depurar**: el cuadro es el mismo de la ventana principal y escribe lo mismo. Tras confirmar, el visor reescribe el CSV mínimo, el CSV completo, el JSON y `stats.json` sin ellas. Los PDF ya exportados conservan esas páginas hasta que pulse **Exportar**. La eliminación necesita una ejecución completa con su JSON y su plantilla, y no puede dejar la ejecución sin ninguna página.

El visor puede regenerar salidas de una ejecución sin repetir el OCR. Desactiva **Exportar** si falta el JSON consolidado o si el CSV no pertenece a una carpeta de ejecución reconocible. Si falta la plantilla o un PDF fuente, lo informa después de pulsar **Exportar**.

Para localizar un original, el visor comprueba la ruta del JSON, la carpeta del CSV, la carpeta de la ejecución, `input/` e `input/processed/`. Use **Ubicar PDF…** si el documento cambió de lugar. Para reexportar una ejecución histórica, use el CSV principal en su carpeta original; su nombre base debe coincidir con el de la ejecución. También deben existir el JSON consolidado, la plantilla usada y todos los PDF fuente.

## 2.15 Adaptación a la pantalla

Las ventanas se limitan al área útil del escritorio y consideran el escalado de Windows. En pantallas bajas, la ventana principal reduce márgenes y reorganiza los grupos superiores para conservar la vista previa y la tabla. Maximizar, restaurar o mover la ventana a otro monitor vuelve a calcular la distribución.
