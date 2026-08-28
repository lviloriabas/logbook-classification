# Indexado automatico en AirVault

Toma el CSV que ya produce una ejecución de clasificacion y escribe esos
valores en las paginas del batch correspondiente del Web Index de AirVault,
sin que nadie tenga que teclear pagina por pagina.

Se opera desde la ventana principal o desde la linea de comandos. Las dos
recorren las mismas etapas y comparten las mismas guardas.

## Desde la ventana

El boton **Indexar en AirVault…**, en la fila de «Opciones avanzadas», abre
una ventana aparte. Trabaja en **tres tiempos**, y estan separados porque
duran cosas muy distintas: subir tarda lo que tarde la red; que AirVault
procese lo subido tarda lo que quiera el servidor, y eso no se espera
delante.

| Control                 | Que hace                                                                                                                                                      |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Seleccionar ejecucion` | Desplegable con las ultimas 25 ejecuciones procesadas por su nombre, de la mas reciente a la mas antigua. Es el mismo control que el del visor de CSV y abre en «Seleccionar ejecucion»; viene señalada la exportada mas reciente, y las que todavia no se pueden subir salen en gris. Al posarse sobre una salen sus paginas y lo que tiene para subir. Con el boton derecho ofrece eliminar el registro de AirVault de la elegida, o la ejecucion entera. |
| `Ejecucion:`            | CSV de la ejecucion elegida arriba. No se teclea.                                                                                                             |
| `Otra ejecucion…`       | Elige el CSV de una ejecucion que no este en la lista.                                                                                                        |
| `Batch:`                | Nombre con el que el batch queda en AirVault. Viene propuesto con la fecha y la hora de la ejecucion.                                                         |
| `Sesion:`               | Respaldo, normalmente vacio: la sesion la resuelve el navegador. Lo que se pegue aqui no se guarda en el disco.                                               |
| `Comprobar cada N min`  | Le pregunta solo a AirVault, sin que nadie pulse. Viene marcado, cada 2 minutos, y solo deja de preguntar cuando no queda nada por esperar; un fallo suelto del servidor no lo para (hacen falta tres seguidos). No decide si se espera (eso va dentro de `Subir a AirVault`, en **Automatización…**) sino cada cuanto se pregunta, y vale mientras la ventana este abierta: apagarlo deja la cadena parada ahi, y la linea de pasos de la ventana principal lo enseña en rojo. |
| `Comprobar ahora`       | La misma pregunta, en el momento.                                                                                                                             |
| `Subir a AirVault`      | Manda los PDF de la entrega. Termina cuando termina la subida. Es lo unico que ademas retoma los batches que quedaron a medias en ejecuciones anteriores: lo que arranca solo se ciñe a la ejecucion elegida. |
| `Completar batch`       | Al terminar de escribir, da el batch por terminado en AirVault: lo indexa y lo manda a Web Search (ver mas abajo). Sin marcar, el batch se queda en la cola para revisarlo. Es la misma casilla que la de **Automatización…** en la ventana principal. |
| `Automatizacion…`       | Menu con los pasos que se encadenan solos: subir a AirVault, indexar y completar el batch, mas depurar la ejecucion. Es la misma eleccion que en la ventana principal y se conserva al cerrar el programa. |
| `Continuar pendiente`   | Consulta AirVault y sigue desde el primer paso que no haya terminado, sin repetir paginas que ya esten en verde.                                              |
| `Reiniciar paso incompleto` | Reinicia el estado local del batch elegido, o de todos los incompletos si no hay ninguno elegido. No borra nada en AirVault.                              |
| `Indexar`               | Escribe en los batches que ya estan listos.                                                                                                                   |
| `Buscar:`               | Pregunta por una bitacora (Log Page Number, matricula, vuelo, fecha o archivo de origen) y resalta los batches de la cola que la llevan. Puede estar en varios a la vez. |
| `Vista previa…`         | Enseña en cuantos batches quedaria repartida la ejecucion, con sus paginas y sus bitacoras. No prepara ni sube nada.                                          |
| `Cancelar`              | Detiene en el acto lo que esté en marcha (sin esperar al servidor) y suelta los batches tomados.                                                                                                   |

### Ver lo que va en cada batch

Hasta que se sube no hay ningun batch en la lista, porque el reparto se
decide al preparar los archivos. **Vista previa…**, junto al titulo de la
tabla, lo adelanta: calcula el mismo reparto que hara **Subir a AirVault**
con el maximo de paginas elegido y enseña cada batch con el nombre que
llevaria, sus paginas (separadores incluidos) y sus bitacoras. Los que ya
estan en AirVault salen con su estado; los demas son los que se crearian.
Solo lee el indice de paginas y el CSV: no escribe manifiestos, no divide
PDF y no sube nada, asi que se puede cambiar el maximo y volver a mirarla
las veces que haga falta.

De cada batch se abre la lista de las bitacoras que lleva dentro, con la
pagina que ocupa cada una (la misma que muestra Web Index), su matricula,
su Log Page Number, su fecha, su vuelo, de que pagina de la ejecucion
salio y que la bloquea si algo la bloquea. Se llega desde la vista previa
y con el boton derecho sobre una fila de la tabla de batches, que es la
via para un batch que ya esta subido. Los separadores no aparecen: ocupan
pagina en el batch pero no son documentos que indexar, y se cuentan
aparte en la linea de arriba.

### La cola: el clic derecho

Cada fila de la tabla de batches se maneja por separado, y con Ctrl o
Mayusculas se eligen varias a la vez: el menu del boton derecho actua sobre
todas las elegidas y hace en cada una lo que corresponda, asi que una
seleccion de batches mezclados sube los que faltan por subir e indexa los que
estan listos sin tocar a los demas. Lo que no vale para ninguna sale
desactivado en vez de desaparecer, para que la fila diga siempre de que es
capaz. Trabajando tambien se puede elegir: la accion se pone en cola y
arranca en cuanto termine lo que hay en vuelo.

**Cancelar en la cola** y **Eliminar el batch…** no son lo mismo:

* **Cancelar** deja el batch donde esta. Conserva su ID y lo que ya se le
  escribio, y solo deja de subirse, buscarse e indexarse hasta que alguien lo
  reanude. Sus bitacoras siguen anotadas como enviadas, asi que ningun
  reparto posterior las vuelve a mandar.
* **Eliminar** borra la memoria local del batch. Su manifiesto se va a la
  Papelera, su anotacion sale del registro de la entrega y sus bitacoras
  vuelven a quedar libres: el proximo reparto de la ejecucion las repartira
  otra vez, en otro batch. Una entrega repartida deja cada batch en su propia
  carpeta (`parte-02`, `revisar`) y ahi se va la carpeta entera con el PDF que
  se preparo para subirlo; sin repartir, el batch vive en la carpeta de la
  ejecucion junto al registro que es de la entrega entera, y ahi solo se va su
  manifiesto.

Lo que ya este en AirVault no se toca en ninguno de los dos casos: el batch
remoto se queda donde estaba, y eliminarlo aqui solo pierde el rastro de que
fue este trabajo el que lo subio. No se elimina nada mientras la ventana esta
subiendo o indexando —primero hay que cancelar el trabajo— ni de carpetas que
no cuelguen de la de trabajos del programa.

### Buscar una bitacora en la cola

El campo **Buscar**, junto al titulo de la tabla de batches, responde en
que batch esta una bitacora. Se busca por Log Page Number, matricula,
vuelo, fecha (en el formato del CSV o en el de AirVault) o archivo de
origen; los separadores se saltan, porque no son documentos que indexar.
La pagina que se dice es la del batch, la misma que muestra Web Index, y
no la del PDF del que salio la bitacora.

La respuesta puede ser mas de un batch, y no es raro: una bitacora dudosa
viaja en su parte y en el batch REVISAR, una ejecucion subida dos veces la
deja en el batch anterior y en el nuevo, y la cola conserva los pendientes
de ejecuciones anteriores. Se resaltan a la vez todas las filas que la
llevan, y ‹ y › pasan de una a la siguiente sin soltar el resaltado. Se
buscan todos los batches de la tabla en cualquier estado, terminados y
cancelados incluidos, porque saber que una bitacora ya viajo en un batch
cerrado es lo que explica que no haya que volver a subirla.

Una coincidencia exacta manda sobre las parciales: pegar un Log Page
Number completo responde por esa bitacora y no por los archivos que lo
lleven dentro del nombre. La busqueda se rehace sola cada vez que la tabla
se repinta, asi que la respuesta sigue siendo la de la cola de ahora y no
la de hace dos minutos.

### Los tres tiempos

1. **Subir y ubicar.** Manda primero todos los archivos pendientes. Solo
   después empieza a buscarlos y nombrarlos en AirVault; ningún indexado se
   inicia mientras quede una subida por intentar.
2. **Esperar a AirVault.** El batch entra en la cola del servidor y tarda en
   quedar indexable: aparece antes de tener todas sus paginas. Mientras le
   falte alguna no esta listo, porque escribir con las paginas corridas
   dejaria cada dato en la bitacora de al lado. La ventana pregunta cada
   dos minutos (o cuando se pulse **Comprobar ahora**) y va pasando los
   batches a **Listo para indexar** segun quedan. Solo deja de preguntar
   cuando ya no queda nada que esperar: mientras haya un batch sin terminar
   sigue mirando, tambien despues de dar una carga por perdida, porque
   AirVault publica cargas horas despues de aceptarlas y preguntar no
   escribe nada.
3. **Indexar.** Con la automatizacion inicial, en cuanto un batch aparece
   entero empieza a escribirse en un carril paralelo mientras se buscan las
   partes restantes. Para entonces ya terminaron todas las subidas. Si se
   desmarca **Indexar paginas**, solo se calcula el plan y se espera la
   aprobacion manual.

Estados que puede tener un batch en la lista:

| Estado                         | Que significa                                                                                                |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| Sin subir                      | Todavia no se ha mandado                                                                                     |
| Subido pendiente confirmación  | Mandado, pero el servidor aun no lo saca en la cola                                                          |
| Procesandose en AirVault       | Ya esta en la cola, con menos paginas de las que lleva el PDF                                                |
| Cantidad de paginas incorrecta | Tiene mas paginas de las posibles; se detiene porque AirVault junto cargas o el PDF no corresponde al indice |
| Listo para indexar             | Entero y libre: se puede escribir                                                                            |
| Abierto por otra persona       | Alguien lo tiene tomado; AirVault no lo entrega a nadie mas                                                  |
| Para revisar a mano            | Es el batch REVISAR, que no se indexa                                                                        |
| Indexado / Terminado           | Ya escrito, y cerrado si se pidio                                                                            |

Cuando AirVault devuelve el batch en el índice, la fila empieza por **Subido
confirmado** y conserva después el estado operativo correspondiente.

El avance sale por la barra y la etiqueta de estado de esta ventana, no por
las de la principal: mientras un batch se escribe se sigue procesando.

Va aparte y no empotrada en la ventana principal. Colgando de ella, el
indexado le quitaba alto a la vista previa y al desplegarse cambiaba el
minimo de la ventana, que en pantallas bajas la sacaba del escritorio;
ademas daba a entender que solo se sube la ejecución recien exportada, cuando
lo normal es elegir cual del historial.

La ejecución tiene que estar exportada. La lista lo dice antes de intentarlo:
**Sin exportar** cuando no hay PDF de entrega y **Falta reexportar** cuando
se exporto antes de que existiera el indice de paginas. Ese archivo puede
venir repartido en partes (ver más abajo): cada parte es un batch distinto y
la ventana los recorre todos.

## Etapas

Cada etapa se corre sola o todas de corrido. El estado vive en el
manifiesto del trabajo (`output/airvault/<job>/manifiesto.json`), asi que
se puede procesar hoy, subir manana e indexar despues sin repetir nada.

| Etapa       | Que hace                                                                                         |
| ----------- | ------------------------------------------------------------------------------------------------ |
| `preparar`  | Arma el manifiesto a partir del CSV y del indice de paginas (uno por parte, y otro para REVISAR) |
| `subir`     | Sube los PDFs por Quick Upload (opcional: se puede subir a mano)                                 |
| `descubrir` | Ubica el batch en AirVault por su nombre                                                         |
| `plan`      | Dry run: calcula todo, escribe el reporte y no toca nada                                         |
| `indexar`   | Escribe los indices                                                                              |
| `verificar` | Relee el batch y confirma como quedo                                                             |
| `completar` | Da el batch por terminado en AirVault, si lo acepta (`indexar --completar`)                      |
| `todo`      | Descubrir, indexar y verificar de corrido                                                        |

```batch
portable\python312\tools\python.exe run_airvault.py preparar  --job varias24 --csv "output\BITS 18 AUG 2026 05 42\datos\BITS 18 AUG 2026 05 42.CSV" --lote "DP | BITS VARIAS 24"
portable\python312\tools\python.exe run_airvault.py descubrir --job varias24 --esperar
portable\python312\tools\python.exe run_airvault.py plan      --job varias24
portable\python312\tools\python.exe run_airvault.py indexar   --job varias24 --revisar
portable\python312\tools\python.exe run_airvault.py verificar --job varias24
```

## Modos

- **Sin bandera**: dry run. Calcula el plan completo, escribe
  `revision.csv` y `revision.html` en la carpeta del trabajo y no manda
  nada al servidor.
- **`--revisar`**: lo mismo, y despues pide confirmacion escrita antes de
  tocar el batch.
- **`--auto`**: escribe sin detenerse.

El reporte es el mismo artefacto en los tres modos. Conserva el vuelo leido
tal como aparece en el CSV; la marca `AUTO INDEX` se agrega solo al enviar
la pagina a AirVault, y solo en los batches automaticos.

## Repartir en varios batches

Una ejecución completa son unas 900 páginas y casi dos gigas. Eso en AirVault
es un solo batch: incómodo de revisar, y una subida que si se corta hay que
rehacer entera.

Marcando **Repartir en** en el cuadro «Salidas» (o `--paginas-por-parte N`
en la línea de comandos) la entrega se escribe en varios PDF de a lo sumo
esas páginas:

```
BITS 18 AUG 2026 05 42 (1 de 5).pdf
BITS 18 AUG 2026 05 42 (2 de 5).pdf
...
```

Cada archivo es un batch propio en AirVault, con su nombre (`DP | BIT 18 AUG
2026 05 42 (2 de 5)`), su manifiesto en `output/airvault/<corrida>/parte-02/`
y sus guardas. Una parte que falle o se corte no arrastra a las demás, y al
volver a revisar se retoma solo lo que falta.

El corte se hace **entre secciones** siempre que se pueda, para no separar
en dos batches las bitácoras de un mismo avión. Cuando un avión tiene por sí
solo más páginas que el tope, se parte y la continuación vuelve a abrir con
su separador, de modo que ninguna parte empieza con bitácoras sueltas.

El reporte de revisión sigue siendo uno solo para toda la ejecución: se
aprueba de una vez y no batch por batch.

La ventana **Indexar en AirVault** aplica además un máximo propio justo
antes de Quick Upload. La instalación portable empieza en **200 páginas por
batch** y después recuerda la última cantidad elegida, tanto aquí como en
**Repartir en**. Si un PDF exportado supera ese valor, se copia en
tramos consecutivos dentro de `output/airvault/<corrida>/cargas/`; el PDF de
la entrega, su índice y el CSV no se modifican. El mismo límite protege al
batch `REVISAR`; si hace falta más de uno, se nombran `REVISAR -1`,
`REVISAR -2`, etc.

Si Quick Upload aceptó un archivo pero el batch no aparece con el nombre
esperado, la ventana hace primero **tres revisiones completas** de la cola.
En cada una contrasta también nombres distintos, cantidad de páginas, Batch
Name interno y Log Page Number para corregir el mismo ID sin resubir. Solo si
las tres terminan sin identificarlo empieza la espera de **30 minutos**. Al
final de esa espera avisa de que AirVault no publicó la carga y ofrece
**Subir a AirVault**, pero no la manda otra vez sola: eso lo decide quien
mire Web Index. Antes de reenviar vuelve a comprobar la cola. Los batches
que aparecieron o pudieron corregirse no se repiten.

Cada ejecución activa vive en su propia ventana y su propio hilo. Mientras
una sube, espera o indexa, se puede elegir otra ejecución en el historial o
con **Otra ejecución…**; se abre aparte y ambas continúan simultáneamente.

Si se activa **Compresión**, cada PDF de entrega se rasteriza una sola vez a
200 DPI antes de formar esos tramos. Los batches se copian desde ese archivo
interno ya comprimido, para no repetir la compresión por cada parte.

Los batches automáticos se numeran `-1`, `-2`, etc. La correspondencia con el
tramo que se subió queda en cada manifiesto y en el índice de páginas; no
depende del nombre interno del PDF de carga.

## El batch REVISAR

Las bitácoras cuya matrícula no permite asignarlas con seguridad no se
indexan automáticamente: una matrícula ausente o marcada, una lectura
canónica que contradice al consenso del libro, una alineación dudosa o una
inferencia con menos de dos lecturas de respaldo. La inferencia se conserva
para ayudar a quien revise, pero la página no se coloca bajo ese separador.
Una inferencia coherente y bien respaldada sí continúa por el flujo normal;
`REVISAR` no es un destino general para toda lectura inferida.

Ahora salen en su **propio archivo**, y por tanto en su propio batch:

```
DP | BITS 18 AUG 2026 05 42 REVISAR
```

Ese batch **se sube y no se toca**. El indexado no le lee ni le escribe
ninguna página; queda en la cola del Web Index, marcado y a la vista, para
que alguien lo resuelva a mano. En el reporte sus páginas aparecen con el
aviso `revisar_a_mano`.

No se numera como una parte automática más: lleva su propia cuenta. En el
caso normal su manifiesto vive en `output/airvault/<corrida>/revisar/`; si
también supera el máximo de Quick Upload, usa `revisar-01/`, `revisar-02/`,
etc.

En la ejecución de referencia son 17 páginas de 884.

## Separadores del PDF

El PDF de entrega no es solo bitacoras: entre las secciones lleva paginas
divisorias (la matricula o el mes de cada grupo, `POSIBLES DISCREPANCIAS`,
`REVISAR`) que el CSV no tiene. En AirVault cada una ocupa una pagina del
batch igual que cualquier otra.

Contarlas mal no deja un hueco: desplaza todo lo que va detras, y la
bitacora de la pagina 40 terminaria indexada con los datos de la 39.

Por eso la exportacion escribe junto al CSV un indice de paginas,
`<corrida>_paginas.json`, que declara que hay en cada pagina del PDF:

```json
{
  "version": 1,
  "pdf": "BITS 18 AUG 2026 05 42.pdf",
  "paginas": [
    { "separador": "HP-1848CMP" },
    { "archivo": "Image_001.pdf", "pagina": 12 },
    { "separador": "REVISAR" }
  ]
}
```

Ese archivo, y no el CSV, es el que fija el orden del manifiesto. Los
separadores entran como registros propios para que la correspondencia por
posicion siga en pie, quedan marcados, y **nunca se les escribe nada**: ni
se leen del servidor, ni cuentan como omitidos, ni se espera que queden en
`Valid` al verificar. En AirVault se quedan como estan.

Una ejecución exportada antes de que existiera el indice no lo tiene. En ese
caso se sigue el orden del CSV y se avisa; si aquel PDF llevaba
separadores, la guarda de cantidad detiene el trabajo antes de escribir
nada.

## Guardas

El indexado se niega a escribir si algo no cuadra. Estan todas juntas en
`app/airvault/guards.py` y se ejecutan igual en dry run que en automatico:

1. El batch y el manifiesto tienen que tener la misma cantidad de paginas,
   contando los separadores.
2. Toda matricula debe existir en el picklist de AirVault.
3. Si AirVault ya trae un log number en esa pagina, tiene que coincidir con
   el del manifiesto. Es el mejor ancla de alineacion que existe.
4. Una pagina ya validada no se pisa salvo con `--sobrescribir`.
5. Ningun campo obligatorio puede quedar vacio. La fecha, que era la que
   mas veces faltaba, se deduce antes de llegar aqui (ver «Campos»).

Una pagina que falle cualquiera de estas queda marcada como bloqueada en el
reporte y no se escribe; el resto del batch sigue. La primera guarda es la
unica que corta el trabajo entero, porque si sobran o faltan paginas la
correspondencia por posicion esta rota y cualquier escritura caeria en la
bitacora de al lado.

## Que nada se suba dos veces

Publicar la misma bitacora dos veces es el unico error de este modulo que no
se deshace desde el programa: hay que ir a borrar la copia a mano en Web
Index. Hay cinco defensas, y estan puestas en cascada porque cada una ve
algo que las otras no.

1. **El reparto de la entrega.** Cambiar el maximo de paginas por batch de
   una ejecucion que ya tiene batches subidos conserva esos batches y
   reparte solo las bitacoras que ninguno se llevo (ver «Repartir en varios
   batches»).
2. **El registro de la entrega** (`registro-de-batches.json`, en la carpeta
   de la ejecucion). Recuerda que bitacoras se llevo cada batch aunque su
   manifiesto se aparte al rehacer el reparto.
3. **La cola de Web Index.** Antes de cada carga se busca el batch por su
   nombre, por su cantidad de paginas y por los Log Page Number de dentro,
   incluidos los `Empty-Batch` sin nombre (ver «Deteccion del batch»).
4. **El libro de envios** (`bitacoras-enviadas.json`, en `output/airvault/`).
   Es la memoria de la instalacion, no de una ejecucion: no la borra
   **Eliminar el registro local** ni desaparece al reprocesar los escaneos en
   otra carpeta. Anota cada bitacora que salio hacia AirVault, por su numero.
5. **Web Search.** Antes de cada carga se consultan tres bitacoras del batch,
   repartidas de principio a fin, para ver si ya estan publicadas.

Las tres primeras miran la **cola** de Web Index, y ahi estaba el hueco:
completar un batch lo saca de la cola y lo manda a Web Search, asi que desde
ese momento ninguna consulta a la cola lo encuentra. Si ademas se pierde la
memoria local, nada impedia volver a subirlo. Las dos ultimas cubren ese
caso: el libro sabe lo que mando este programa y Web Search sabe lo que hay
publicado, lo subiera quien lo subiera.

### La identidad de una bitacora

Cambia segun con que se compare, y no es un detalle:

* Dentro de **la misma entrega**, una bitacora es su pagina de origen (el
  archivo escaneado y el numero de pagina). Dos paginas distintas pueden
  traer el mismo numero mal leido, y darlas por la misma dejaria sin subir
  una pagina que nadie subio.
* Entre **entregas distintas**, esa pagina de origen no significa nada: los
  mismos escaneos procesados otra vez producen otros archivos y otra
  numeracion. Ahi lo unico que identifica al documento es su **numero de
  bitacora**, que es tambien por lo que pregunta Web Search.

### La consulta a Web Search

AirVault no documenta su API de busqueda, asi que la ruta se descubre en
ejecucion: se lee la portada de `/zfp/`, se sacan las rutas que aparecen en
ella y en sus scripts, se prueban las que parecen de busqueda y la que
funciona se conserva en `airvault.json` (`ruta_websearch` y
`parametros_websearch`). Borrar esas dos claves obliga a descubrirla otra
vez, que es lo que hay que hacer si AirVault cambia.

**Una ruta sin probar no sirve para decir que una bitacora no esta**: una
ruta equivocada tambien contesta que no hay nada. Por eso solo se acepta la
que encuentra un **control positivo**, un numero de bitacora que el propio
programa completo antes y que por lo tanto tiene que estar publicado. Los
controles salen del libro de envios. Mientras no haya ninguno (una
instalacion nueva, que todavia no ha completado nada), la consulta contesta
que no puede responder y no autoriza nada; las otras cuatro defensas siguen
en pie.

Una consulta que no sale (sin red, sin ruta) tampoco inventa un motivo: no
es un «no esta». No frena la carga, pero tampoco la respalda.

### «Posible duplicado»

Cuando el libro o Web Search encuentran que las bitacoras del batch ya estan
en AirVault, el batch **no se sube**, se anota el motivo en su manifiesto y
la fila queda en la cola con el estado `Posible duplicado` y el motivo al
lado. Mientras la marca este puesta, ese batch:

* no se sube ni se reenvia, ni solo ni por orden dada desde la tabla;
* **no se completa**, que es lo que lo archivaria por segunda vez.

Es una sospecha y no una certeza a proposito: lo que se demuestra es que el
documento esta arriba, no que este batch sea el que lo subio (pudo llegar por
otro batch, por otra persona o por una carga anterior). Decidirlo es de quien
mira AirVault, asi que la marca se quita a mano: clic derecho sobre la fila y
**No es duplicado: volver a permitirlo**. Lo que el programa no hace es
seguir solo mientras la duda este puesta.

### Ninguna carga se reenvia sola

Una carga que Quick Upload acepto y AirVault nunca publico **no se vuelve a
mandar sola**, por mucho que tarde. Las tres senales que la dan por perdida
(la cola entera sin encontrarla, las partes siguientes ya indexadas, o el
tiempo de espera vencido) pueden dispararse mientras AirVault todavia la
esta procesando, y mandar el mismo PDF otra vez es como aparecen dos copias
del mismo batch el dia que la cola las publica todas. El programa no
distingue una carga perdida de una cola lenta; quien mira Web Index si.

Lo que hace solo es avisar: el resumen dice que batches no aparecieron y por
que motivo, y la orden queda a mano en la cola (clic derecho sobre la fila,
**Subir a AirVault ahora**), que si sube en el acto.

Buscarla si la sigue buscando: la comprobacion periodica mira la cola por si
AirVault acaba publicando la carga, y si aparece se indexa sola. Mirar la
cola no escribe nada. Lo unico que necesita una mano es volver a mandarla.

Lo que si sale sin pedirlo es lo que **nunca llego** a Quick Upload
(`partes_por_subir`): un archivo que no se subio no puede estar duplicado.

### El PDF que se manda

Los tramos que van a Quick Upload se guardan en `cargas/` y se reaprovechan,
que es lo que evita volver a cortar (y a comprimir) la entrega en cada
intento. Se reconocen por la ruta del PDF de origen, por que paginas se
pidieron y por **el tamaño y la fecha de ese PDF**. Los dos primeros no
bastaban: al depurar y volver a exportar, el archivo conserva el nombre y las
paginas se numeran otra vez desde uno, asi que el tramo viejo encajaba y se
subia tal cual, con paginas que no eran las suyas.

## Campos

De los veinte campos del panel, el sistema controla seis fijos y dos mas
cuando hay dato, y deja el resto intacto. Lo que no se manda, AirVault lo
conserva, asi que un indexado no pisa lo que alguien haya puesto a mano.

| Campo           | Origen                                                                     |
| --------------- | -------------------------------------------------------------------------- |
| Doc Type        | valor del trabajo (`airvault.json`)                                        |
| Aircraft        | columna `matricula` del CSV                                                |
| Fleet           | se deduce de la matricula                                                  |
| Log Page Number | columna `log_number` del CSV                                               |
| Audit Status    | valor del trabajo                                                          |
| End Date        | columna `date` del CSV en `MM/DD/YYYY`; si no se leyo, se deduce del libro |
| Description     | `<flight_number> AUTO INDEX`, o solo `AUTO INDEX` si no se leyó vuelo; en REVISAR, el vuelo sin marca |
| Lessor          | del cache de flota, solo si lo trae                                        |

**El vuelo.** `Description` lleva el vuelo de esa bitacora, pagina por
pagina: un vuelo numerado (`703`, `CM137`) o un codigo de mantenimiento
(`TCK`, `SPV`), seguido por la marca `AUTO INDEX`. Cuando no se pudo leer
el vuelo, `Description` lleva solamente `AUTO INDEX`. La marca se agrega
solo al payload que se guarda en AirVault: el CSV y el reporte de revision
conservan el vuelo tal como lo dejo la lectura. El batch de REVISAR se
termina a mano y por eso no la lleva: manda el vuelo leido tal cual, y
cuando no hay vuelo ni siquiera manda el campo, para no borrar lo que
alguien haya escrito. Es un campo por pagina, no
del batch: el batch no lo lleva (Quick Upload ni siquiera expone
`Description`).

**La fecha.** `End Date` es obligatorio: una bitacora sin fecha deja su
pagina bloqueada, y basta una para que el batch no se pueda cerrar. Cuando la
lectura no dejo fecha pero si el log number (que es el que ordena el libro)
se deduce con las mismas reglas que el corrector de fechas del
procesamiento, de la que mas evidencia tiene a la que menos:

| Que hay                                                    | Que fecha se pone                                |
| ---------------------------------------------------------- | ------------------------------------------------ |
| La misma bitacora repetida, y una de las dos si trae fecha | esa                                              |
| Bitacoras fechadas antes y despues en el libro             | la de la mas cercana; en un empate, la posterior |
| Solo fechadas antes                                        | el ultimo dia de ese mes                         |
| Solo fechadas despues                                      | la de la primera de ellas                        |
| Ninguna en el libro                                        | el ultimo dia del mes dominante del avion        |
| El avion entero sin fechas                                 | el ultimo dia del mes dominante de la ejecucion  |

Dentro de un libro la fecha no retrocede al aumentar el log number, asi que
una pagina sin fecha esta entre la de la anterior y la de la siguiente: lo
que se le pone cae siempre dentro de ese intervalo. Pasada la ultima
fechada ya no hay techo, y ahi va el fin de mes, la misma convencion con la
que el CSV completa un dia ilegible. La regla no cruza libros: otro libro
del mismo avion no ordena a este, solo aporta su mes.

Una bitacora **sin log number legible no recibe fecha**: sin numero no hay
libro ni posicion, la pagina esta bloqueada de todos modos por ese campo
obligatorio, y ponerle una fecha solo maquillaria el reporte.

La deducida no se presenta como leida. El reporte trae la columna
`fecha_inferida` con la regla que la produjo y el resumen cuenta cuantas
paginas la llevan, para mirarlas antes de aprobar la escritura.

**La flota.** AirVault la resuelve con un procedimiento almacenado a partir
de la matricula, pero ese lookup lo dispara la interfaz al escribir el
campo, no el servidor al guardar. Por eso el modulo la resuelve por su
cuenta con tres niveles: primero el cache local
(`airvault_flota.json`), que se alimenta solo de lo que AirVault ya tiene
indexado en el propio batch; si no hay dato, una regla de prefijos de
respaldo, y en ese caso la bitacora queda marcada como `fleet_inferido` en
el reporte para que alguien la confirme.

## Autenticacion

**Este acceso esta federado con Microsoft Entra ID**
(`login.microsoftonline.com/9767f0dc-.../wsfed`) y pide segundo factor. Eso
no se automatiza, ni se debe: el segundo factor existe justamente para que
lo haga una persona. Lo que si se automatiza es todo lo que viene despues.

El programa **abre Edge con un perfil propio**, dentro de `portable/`,
apuntando al enlace de acceso federado. La primera vez se ve la ventana y
alguien entra con su usuario de Microsoft; en cuanto AirVault suelta sus
cookies, la ventana se cierra sola. De ahi en adelante el perfil conserva
la sesion, asi que el navegador se abre **sin ventana**, entrega la cookie
y se cierra. Nadie copia nada ni teclea nada.

Nada de esto instala ni descarga nada: Edge ya viene con Windows y el
perfil es una carpeta mas dentro de `portable/`, que viaja con el programa.

### Por que un perfil propio y no el Edge de siempre

Lo ideal seria colgarse de la ventana de Edge donde la persona ya esta
dentro de AirVault, sin entrar una segunda vez. No se puede, y no por
comodidad:

- **Chromium ignora el puerto de depuracion cuando el perfil es el de por
  defecto.** Es una proteccion deliberada del navegador, no un ajuste: sin
  ese puerto no hay forma de pedirle las cookies.
- **Si Edge ya esta abierto, lanzarlo otra vez no arranca nada**: el
  proceso nuevo le pasa la orden al que ya corre y se va, asi que no queda
  ningun puerto al que conectarse.
- **Un WebDriver no cambia nada de lo anterior.** `msedgedriver` maneja un
  navegador que arranca el mismo, con su propio perfil; llega al mismo
  punto que este modulo y con una dependencia mas que empaquetar. Solo
  valdria la pena si evitara el acceso, y no lo evita.
- **Copiar el perfil de la persona tampoco sirve** y ademas no se hace: las
  cookies estan cifradas contra el navegador, la copia se haria con Edge
  abierto y, sobre todo, la cookie de federacion es de sesion y muchas
  veces ni siquiera esta en el disco. Leer el almacen de cookies del
  navegador de alguien no es algo que este programa haga.

Por eso el trato es otro: **se entra una sola vez** en el perfil del
programa y esa sesion se queda. Lo que lo sostiene es
`--restore-last-session`, que no esta para reabrir pestanas: Chromium solo
guarda en disco las cookies **de sesion** (y la de federacion lo es) cuando
el perfil arranca restaurando la sesion anterior. Sin esa bandera habria
que entrar con segundo factor en cada ejecución.

### El navegador que quedo abierto de la vez anterior

Ese mismo «si Edge ya esta abierto, lanzarlo otra vez no arranca nada» vale
tambien para el perfil del programa. Si una ejecucion deja el navegador vivo
—se cierra el programa a media faena, o el puerto no llega a contestar y
nadie cierra el navegador—, el Edge que se lanza despues le entrega la orden
y se va sin abrir nada: su puerto de depuracion, que es otro cada vez, no
contesta jamas. El perfil se queda tomado y el acceso moria en «Edge no
llego a arrancar» ejecucion tras ejecucion, con todo lo que va detras
parado.

Por eso el programa **anota el puerto** dentro del propio perfil
(`bits-puerto-depuracion`) en cuanto lanza el navegador, antes de saber si
va a contestar. La ejecucion siguiente lo lee, y si ahi hay un navegador:

- **Si solo hacen falta las cookies**, habla con el que ya esta en vez de
  lanzar otro: son las cookies del mismo perfil, y visitar el enlace
  federado renueva la sesion igual. Al terminar lo cierra, que es lo que
  suelta el perfil.
- **Si hace falta la ventana para entrar**, no sirve —el que quedo vivo
  puede ser uno sin ventana, donde nadie puede teclear nada—, asi que le
  pide el cierre y abre la propia.
- **Si esta colgado** —contesta el `/json/version` pero no su protocolo,
  que es como termina un navegador olvidado durante horas— se le cierra a
  la fuerza: se pregunta por el puerto quien lo tiene abierto (`netstat`) y
  se cierra ese proceso. Se busca por el puerto y no por el nombre del
  programa, porque cerrar «msedge» a secas se llevaria por delante el
  navegador de la persona.

La anotacion se borra al cerrar el navegador, pero solo cuando de verdad se
fue. Pedirle el cierre no siempre basta, y darlo por muerto dejaba a la
ejecucion siguiente sin saber por donde buscarlo: si sigue en pie se cierra
a la fuerza, y si ni asi se va, la anotacion se queda. Una que sobreviva a
un apagon manda a un puerto mudo: se tira y se abre un navegador nuevo.

Cuando no hay anotacion y aun asi el perfil esta tomado —un navegador que
dejo una version anterior del programa, o uno que arranco sin llegar a
anotarse— se le pregunta a Windows que Edge hay abiertos y con que linea de
ordenes. El perfil es lo unico que distingue al navegador del programa del
de la persona: por el nombre son el mismo `msedge.exe`. Con el que aparezca
se hace lo de siempre, aprovecharlo o cerrarlo, y se vuelve a intentar una
vez. Es la unica salida cuando el puerto no se sabe; sin ella el acceso se
quedaba muerto hasta que alguien buscaba ese proceso sin ventana a mano en
el administrador de tareas.

Cuando la sesion guardada deja de valer, el programa **vuelve a abrir la
ventana para entrar**, en vez de quedarse pidiendo que alguien copie una
cookie con F12.

Las cookies se le piden al navegador por su protocolo de depuracion
(`Storage.getCookies`), no leyendo su archivo. Un Edge moderno las cifra
con la identidad del navegador (prefijo `v20`, clave
`app_bound_encrypted_key`), que no se deshace desde fuera, y ademas
mantiene su base abierta en exclusiva mientras corre. El navegador si sabe
descifrar las suyas, y por el protocolo las entrega ya en claro; nunca se
intenta rodear ese cifrado.

Se entra por el enlace federado y no por la raiz del sitio: es el que
dispara la redireccion a Microsoft. Por la raiz la sesion queda a medias,
con `ASP.NET_SessionId` pero sin la cookie que autentica. Por eso esa
cookie sola no se da por buena: la pone el servidor al primer contacto,
antes de saber quien eres, y darla por buena hacia arrancar un batch que
moria en la primera pagina.

**La cookie que autentica en esta instalacion se llama `Critical`.** Se
midio pidiendo el listado de batches con cada cookie por separado: `Critical`
sola abre la sesion y ninguna de las otras seis (`ProdSSO`, `ProdSSO1`,
`AirVaultContext`, `SessionInfo_AV`, `Production-AirVaultAntiForgery`,
`ASP.NET_SessionId`) lo hace. Antes solo se reconocian los nombres
habituales de ASP.NET (`FedAuth`, `.ASPXAUTH`), que aqui no aparecen: el
programa esperaba cinco minutos con la sesion ya abierta delante y despues
acusaba a la ventana de haberse quedado en la pagina de Microsoft. Los dos
nombres de ASP.NET se siguen aceptando por si otra instalacion los usa.

Y no se toma la primera cookie que aparece, sino la primera que **sirve**:
recien abierto, el navegador todavía va y viene de Microsoft, y lo que hay
en el perfil en ese instante es lo de la vez anterior. Se prueba contra el
servidor hasta que una funciona. Eso es tambien lo que renueva la sesion
sola, sin ventana ni segundo factor: si AirVault contesta a mitad del
trabajo que hay que volver a entrar (un 401, o el 440 «Login Timeout» de
IIS, que llega con la pagina de error generica y antes se leia como un
rechazo del sitio), se vuelve a leer el perfil y se repite la peticion.

## El token del sitio

Toda escritura lleva la cabecera `AntiForgery`, con el token que el propio
sitio publica en su portada (`data-root-antiforgery`). Se guarda uno por
aplicacion (`/index/`, `/quickuploadex/`), porque cada una sirve el suyo, y
se vuelve a leer cuando una escritura falla.

Sin esa cabecera el servidor **no** contesta 403 ni dice que falte nada:
devuelve un 500 con su pagina de error generica. Y como un 500 se reintenta
por transitorio, el fallo llegaba disfrazado de «la red o AirVault
ocupado». Asi murio la subida durante dias, siempre en `FinishUpload` y
siempre despues de haber mandado el archivo entero.

La sesion se toma de la primera fuente disponible, en este orden:

1. La cookie pasada a mano, si la hay: el campo `Sesion:` de la ventana,
   `--cookie "..."` en cualquier subcomando, o la variable de entorno.

   ```batch
   set AIRVAULT_COOKIE=FedAuth=...; FedAuth1=...
   ```

   Es el respaldo por si el navegador no puede; el camino normal es dejarlo
   vacio.

2. El navegador, como acaba de describirse. Con `--sin-edge` no se abre;
   con `--perfil-edge` se usa otra carpeta de perfil.

3. El formulario propio de AirVault (`AIRVAULT_USER` / `AIRVAULT_PASSWORD`,
   o `--usuario`), que solo sirve para las cuentas locales que no pasan por
   Entra ID.

Si nadie entra en la ventana dentro del plazo (cinco minutos, `espera_login_s`)
se dice y se puede reintentar o pegar la cookie. Si en la maquina no hay
Edge, tambien se dice y se sigue a mano.

La cookie va al tarro de peticiones y no a una cabecera fija: en cuanto el
servidor devuelve su primera cookie, `requests` reconstruye la cabecera
desde el tarro y se comeria cualquier valor puesto a mano, dejando el batch
a medio escribir.

Ni las cookies ni las contrasenas se guardan en disco ni se escriben en el
log: de una cookie solo se registra el nombre y cuanto mide. Lo unico que
queda guardado es el perfil del navegador, igual que cualquier sesion
abierta en un navegador.

Antes de empezar se comprueba la sesion con una peticion, para no descubrir
en la pagina 250 de 400 que habia caducado. Si caduca a mitad, se detecta
por la redireccion a `/signin2/` o a `login.microsoftonline.com` y se dice,
en vez de fallar en silencio.

## Subida

`subir` usa Quick Upload, que crea el batch y lo deja en la cola de Web
Index. Hay una limitacion del lado de AirVault: ese modulo solo expone diez
campos y entre ellos **no** estan Log Page Number, Fleet ni End Date. Por
eso la subida deja el batch clasificado pero no indexado, y el indexado real
lo hace `indexar` despues. Si el administrador habilita esos campos para
Quick Upload, la subida podria cerrarlo todo de una vez.

La etapa esta desacoplada a proposito: si el batch se sube a mano, se salta
`subir` y se arranca en `descubrir`.

Los valores que se mandan salen de la **primera bitacora**, no de la
primera pagina: la primera suele ser un separador, sin avion, y `Aircraft`
es obligatorio en Quick Upload. Son solo la clasificacion inicial del
archivo; lo de cada pagina lo escribe `indexar` despues.

Primero se terminan todas las subidas. La busqueda y el indexado empiezan
despues, para que las consultas del Web Index no retrasen Quick Upload. Cada
archivo lleva su propio `Batch Name` y su manifiesto conserva la cola previa
a esa carga.

Toda escritura lleva la cabecera `AntiForgery`; sin ella `FinishUpload`
contesta 500 despues de haber recibido el archivo entero.

## Nombre del batch

El nombre es lo unico que el sistema y AirVault comparten para reconocer un
batch, asi que se arma solo, con el prefijo mas la marca de tiempo de la
ejecución, en el mismo formato que ya usa el nombre del CSV, y **entero en
mayusculas**:

```
DP | BITS 18 AUG 2026 05 42            entrega sin repartir
DP | BITS 18 AUG 2026 05 42 -1         primera parte
DP | BITS 18 AUG 2026 05 42 -2         segunda parte
DP | BITS 18 AUG 2026 05 42 REVISAR    bitacoras sin avion confirmado
```

**La marca es la del procesamiento, no la de la subida.** Sale del nombre de
la carpeta de la ejecución; si esa carpeta no lo lleva, se toma la hora del
propio archivo, que sigue siendo la del procesamiento. La hora actual es el
ultimo recurso y solo aparece cuando no hay ni archivo que mirar: el batch
tiene que decir cuando se leyo la bitacora, no cuando alguien se acordo de
subirla.

Con `--prefijo` se cambia el prefijo y con `--lote` se fija el nombre
completo a mano; los dos se pasan a mayusculas igual.

Quick Upload envia `Batch Name` y el caso normal aparece en la cola con ese
titulo. Algunas cargas anómalas pierden el nombre y figuran como
`Empty-Batch`; también puede dejar el título truncado. En esos casos se usa
`Batch/UpdateBatchName`: antes se exige el mismo repositorio, la cantidad
exacta de paginas y una coincidencia unica de contenido. Se prioriza el
`Batch Name` interno; si falta, se contrastan varios `Log Page Number`
repartidos por el batch y se refuerzan con matricula y fecha cuando AirVault
las leyo. Un OCR vacio no contradice; un valor distinto si descarta el
candidato. El orden de llegada o el tamaño por si solos nunca autorizan un
renombrado.

El programa vuelve a consultar la cola y solo guarda el ID o empieza a
indexar cuando ese mismo ID aparece con el titulo esperado. Si el renombrado
falla, se detiene ese batch con un mensaje que incluye su ID; **Revisar en
AirVault** vuelve a intentar la identificacion y el renombrado.
Si el ID no corresponde al PDF esperado, se confirma en Web Index, se elimina
alli y se manda otra vez con **Subir a AirVault ahora** desde su fila.

La marca de tiempo no es decoracion. El filtro "Filter by" de AirVault es
una coincidencia de subcadena sin distinguir mayusculas, asi que escribir
`DP | BIT` devuelve hoy 22 batches: los `DP | BITS VARIAS`, los
`DP | Bitacoras varias` y los `DP | BIT Mix`. Y entre ellos hay nombres
repetidos, dos `DP | BIT Mix | Viernes 14 AUG` y dos
`DP | BIT Mix 5 | Viernes 14 AUG`, con los que no habria forma de saber en
cual escribir. La marca de tiempo los separa.

## Deteccion del batch

Hay dos caminos, en este orden:

1. **Por nombre**, incluido el que envia Quick Upload y un batch que alguien
   subio a mano poniendoselo.
   `descubrir` manda `DP | BIT ...` como filtro al servidor, el mismo que
   aplica la caja "Filter by" de la pantalla, y despues compara el nombre
   completo sin distinguir mayusculas ni separadores. Contempla el sufijo
   `<batch> - usuario@dominio` que dejan algunas subidas.

2. **Por contenido**, para un `Empty-Batch`. Justo antes de cada subida se
   anota qué IDs habia en la cola. Los nuevos solo son candidatos: cantidad
   exacta de paginas y huella interna deciden cuál corresponde.

Esa anotacion solo distingue si hay una sola carga sin identificar. AirVault
junta las subidas que llegan seguidas y las publica como varios
`Empty-Batch` a la vez: la diferencia contra la cola devuelve entonces mas
de un candidato y no se renombra ninguno. Por eso las cargas van de una en
una: se manda un PDF, se espera a que AirVault lo publique, se le pone su
titulo, y solo entonces sale el siguiente. Asi la foto de la cola de cada
subida ya incluye todos los batches anteriores.

Si queda más de una coincidencia con la misma evidencia, se detiene sin
renombrar ni escribir: la siguiente comprobacion vuelve a intentarlo.
Mientras exista una carga provisional compatible, **Revisar en AirVault** no
la considera ausente ni la vuelve a subir por antigüedad; sigue intentando
confirmar y completar el nombre del mismo ID.

Con `--esperar` sondea hasta que el batch aparezca, porque un batch recien
subido tarda en pasar por el procesamiento del servidor.

## Cuando algo falla

Un batch son cientos de peticiones y una subida completa casi dos mil. A esa
escala los tropiezos dejan de ser raros, así que cada uno tiene una
respuesta decidida de antemano.

| Qué pasa                                                                                 | Qué hace el indexado                                                                                                                                                 |
| ---------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Se corta la red, vence el tiempo o el servidor responde que está ocupado (408, 429, 5xx) | Reintenta, esperando más en cada intento. Por defecto tres intentos con 5 s, 10 s.                                                                                   |
| Se agotan los reintentos                                                                 | Corta y dice qué pasó. Lo escrito queda anotado.                                                                                                                     |
| Alguien cancela mientras hay una petición esperando                                       | Deja de reintentar, corta la espera y cierra el pool de conexiones, que es lo único que aborta la petición en vuelo.  |
| El servidor responde 404 o 403                                                           | No reintenta: insistir devuelve lo mismo.                                                                                                                            |
| Una página del batch no carga                                                            | Bloquea **esa** página y sigue con el resto. Sin poder leerla no se puede comprobar que el batch y el manifiesto hablan de la misma bitácora, así que no se escribe. |
| Caduca la cookie a media escritura                                                       | Corta el batch entero. Lo que no se llegó a intentar queda **pendiente**, no fallido: al volver a revisar se retoma sin repetir lo escrito.                          |
| Falla el guardado de una página concreta                                                 | Se marca esa página con el motivo. Con `--continuar-con-errores` el resto del batch sigue.                                                                           |
| Un trozo de la subida se pierde                                                          | Se reenvía ese trozo. Reenviar el mismo índice es inocuo: el servidor arma el archivo por posición.                                                                  |

### El batch se abre y se cierra

AirVault admite **un solo dueño por batch**. Abrirlo lo bloquea a nombre de
quien lo abre, y mientras siga bloqueado cualquier otra apertura (la del
programa la próxima vez, o la de la persona que lo abre en el navegador)
se queda esperando: el servidor **no contesta y no da error**. Por eso
todas las peticiones llevan tiempo límite.

De ahí que el batch se tome **lo menos posible**. Leerlo para calcular el
plan lo suelta en cuanto acaba, y escribirlo lo vuelve a tomar y lo suelta
al terminar. Antes se quedaba tomado entre revisar y escribir, y entre una
cosa y otra puede pasar un rato largo (o no pulsarse nunca **Indexar**):
todo ese tiempo nadie más podía abrirlo, ni la persona que iba a revisarlo
ni el propio programa al volver. Un batch sin nada que escribir ni se toma,
que es el caso del de «Revisar».

Se suelta siempre: salga bien, se cancele o se corte a medias, y también
cuando la ventana se cierra con algo en vuelo.

Cuando aun así una apertura se queda esperando, el programa pregunta al
listado quién lo tiene tomado y lo dice con nombre y apellido, en vez de
dejar un tiempo agotado sin explicación.

La comprobación de sesión se hace antes de empezar, no a mitad: descubrir
en la página 250 de 400 que la cookie había caducado cuesta mucho más que
descubrirlo al principio.

## Cerrar el batch: «Completar batch»

Indexar deja el batch escrito, pero **en la cola** del Web Index. Darlo por
terminado (el boton «Complete» de la pantalla) lo saca de ahi y lo manda al
repositorio. Eso es lo que hace la casilla **Completar batch**, o
`indexar --completar` en la linea de comandos.

**AirVault solo cierra un batch con todas sus paginas en verde.** Basta una
a la que le falte un campo obligatorio para que no lo deje. Por eso el
programa mira antes el mapa del batch (`FormsProcessing/GetBatchPages`, una
sola peticion para todas las paginas) y aplica la misma regla que la
pantalla: cuenta la pagina que encabeza cada documento, salvo las borradas.
Si alguna no esta en verde **no se intenta**: se dice cuales son y el batch
se queda donde estaba.

Los cuatro estados de una pagina son los de AirVault:

| Estado | Nombre en AirVault | Cierra el batch |
| ------ | ------------------ | --------------- |
| 0      | Valid              | si              |
| 1      | No Template Match  | no              |
| 2      | Separator          | no              |
| 3      | Need Correction    | no              |

**Las divisorias del PDF cuentan.** Medido en el batch `003SUS`: sus trece
paginas separadoras quedaron en estado 1, «No Template Match», y para
AirVault eso pesa igual que una bitacora incompleta. Al indexar un batch
automatico, BITS las marca como borradas mediante la misma operacion de
AirVault que se ejecuta con `Ctrl+Supr`; no depende de completar el batch.
El batch `REVISAR` no se indexa automaticamente y conserva todas sus
paginas para que una persona lo resuelva.

Lo otro que bloquea son las bitacoras que quedaron en amarillo. Antes de
**subir un batch automatico**, BITS exige todos los campos obligatorios; si
falta alguno, detiene la carga para que la pagina se reexporte en `REVISAR`.
La fecha puede venir de
las reglas del libro, incluido el ultimo dia del mes. Despues relee el batch:
si AirVault aun deja una pagina en «Need Correction», reintenta el indexado y
no declara el batch automatico terminado ni intenta completarlo mientras no
queden todas las bitacoras en verde.

## Reanudacion

El manifiesto se guarda despues de cada pagina. Si el proceso se corta, al
volver a correr `indexar` las paginas ya escritas se saltan y se sigue
desde donde quedo. Las que fallaron quedan con el motivo anotado.

## Prueba de punta a punta

Antes de soltar el indexado sobre un batch real conviene probarlo con una
muestra. `tools/muestra_bitacoras.py` arma un PDF de prueba con unas pocas
páginas al azar de las que haya en `input/`:

```batch
portable\python312\tools\python.exe tools\muestra_bitacoras.py
```

Deja `input\MUESTRA.pdf` con veinte páginas (unos 40 MB, frente a los
setecientos de un escaneo entero) y dice de dónde salió cada una. Es una
entrada de verdad, no una ejecución reconstruida: se procesa desde la ventana
como cualquier otro PDF, así que la prueba pasa por el mismo OCR, la misma
exportación y el mismo indexado que un batch de verdad.

Las páginas salen al azar a propósito. Entre ellas caen bitácoras buenas,
alguna en blanco y alguna que el OCR no va a poder leer, que es justo lo
que hay que ver antes.

La semilla se imprime al terminar: con `--semilla N` se repite exactamente
la misma muestra. `--cuantas` cambia el tamaño y `--pdf` saca todas las
páginas de un solo escaneo.

Después:

1. Procesar `MUESTRA.pdf` y exportar con la salida en **un solo PDF**.
2. Abrir **Indexar en AirVault…** y pulsar **Subir a AirVault**.
3. Esperar a que el batch pase a **Listo para indexar** (se comprueba solo
   cada dos minutos).
4. **Indexar**, y comprobar en el Web Index que las páginas separadoras
   quedaron sin tocar y las bitácoras con sus datos.

Nada de esto se commitea: `input/` y `output/` están fuera del repositorio.

## Tests

```batch
portable\python312\tools\python.exe -m pytest tests -k airvault
```

Todo el recorrido se prueba contra un cliente falso
(`tests/airvault_fake.py`) que guarda en memoria lo que se le escribe, de
modo que los tests afirman exactamente que paginas se tocaron y con que
valores, sin tocar produccion.
