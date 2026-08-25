# Legends

Registro de los cambios de comportamiento del sistema. Cada entrada indica qué hacía antes, qué hace ahora y por qué se cambió. La descripción técnica completa vive en [docs](docs/README.md).

## 2026-08-25 - La tabla del visor deja de copiarse celda a celda, y la fila entera se sombrea

El visor de CSV guardaba un `QTableWidgetItem` por celda. Una ejecución de dos mil cuatrocientas páginas por ochenta y seis columnas son doscientas siete mil celdas: abrir el CSV costaba más de cuatro segundos y cada clic en una cabecera movía esas doscientas siete mil celdas de sitio, medio segundo con la ventana muerta en el que el orden parecía no aplicarse. Ahora la tabla no guarda nada: lee del CSV que ya está en memoria y pinta solo lo que se ve. Ordenar es reordenar una lista de índices, así que el mismo clic tarda veinticinco milisegundos, y abrir la ejecución baja de cuatro segundos y medio a menos de uno.

Lo que sobraba en la apertura eran dos cosas más. El JSON de la ejecución se interpretaba cinco veces seguidas (los PDF de cada fila, los documentos, sus páginas, los estados de campo y el botón de exportar piden lo mismo); ahora se interpreta una vez y se recuerda mientras el archivo no cambie de tamaño ni de fecha, de modo que reescribirlo al eliminar páginas lo invalida solo. Y el mapa de estados del JSON, que es el respaldo del color cuando se abre el CSV mínimo, se armaba siempre: el CSV completo trae sus columnas `_status` y no lo consulta nunca.

La casilla con la que se juntan páginas sueltas vivía dentro de la primera columna de datos, compartiendo celda con el nombre del archivo. Ahora es una columna propia, estrecha, sin rótulo y que la vista resumida no oculta: marcar páginas no puede depender de qué columnas se estén mostrando. La fila marcada queda sombreada de azul de un extremo al otro, no solo donde está la casilla, y la barra espaciadora marca de una vez todas las filas resaltadas.

El cursor sombrea también la fila entera. El estilo nativo de Windows resalta la celda que tiene debajo, así que recorrer una tabla ancha dejaba un cuadro azul suelto en vez de la línea que se está leyendo. Vale en todas las tablas de la aplicación. Una celda con color de estado (el verde de lo correcto, el rojo del error) no lo pierde al pasar el cursor: se tiñe, porque ese color es un dato y no un realce.

## 2026-08-25 - La bitácora se ve entera en la ventana principal y en la vista previa de batches

La vista previa de la ventana principal se quedaba con 350 px de ancho de los 1447 de la ventana. El reparto lo decidía el mínimo del panel de la tabla, y ese mínimo no lo ponían las columnas (que se recorren de lado) sino la frase de ayuda del buscador, que pedía de ancho la frase entera. Ahora esa frase se recorta y la página y la tabla se reparten el ancho a medias, que en una ventana normal son unos 720 px para cada una. Mover el separador sigue mandando: desde que alguien lo ajusta, no se le vuelve a tocar.

La lista de bitácoras de un batch se mira ahora como el visor de CSV, en compacto: la hoja escaneada a la izquierda, la tabla a la derecha, un buscador encima y las columnas ordenables con un clic. Elegir una fila abre su hoja en el visor y el doble clic la vuelve a traer, que es lo que hace falta cuando se ha navegado el PDF por otro lado. Comprobar que una bitácora concreta cae donde se espera deja de exigir abrir el PDF por fuera y contar páginas. La lista de batches gana el mismo buscador y el mismo orden por columna.

La columna de estado decía «Escrita» de una bitácora ya indexada y «Por escribir» de la que falta. Escribir no es lo que se hace: se indexa, y así se llama en el resto de la ventana. Ahora dicen «Indexada» y «Por indexar», y cuando el batch ya se completó (se cerró y se mandó a Web Search) sus bitácoras dicen «Completada», que es lo que las distingue de las que siguen en la cola esperando revisión.

## 2026-08-25 - Una ejecución se elimina desde el historial de AirVault

Eliminar el registro local de AirVault era un botón que actuaba siempre sobre la ejecución abierta: para olvidar el de otra había que cargarla primero, con lo que eso arrastra (soltar los batches tomados, parar la vigilancia, releer los manifiestos). Y de la ejecución en sí no había forma de deshacerse desde el programa: la lista crecía con todo lo procesado y limpiarla era ir a `output/` con el explorador.

Ahora el clic derecho sobre una fila del historial ofrece las dos cosas por separado. **Eliminar el registro de AirVault** hace lo mismo que el botón, pero sobre la fila elegida: si no es la ejecución abierta, la ventana ni se mueve. **Eliminar la ejecución…** manda a la Papelera su carpeta de `output/` entera (CSV, JSON, estadísticas y PDF de entrega) junto con su registro local, y la fila desaparece de la lista. Va a la Papelera y no al vacío porque una ejecución son horas de proceso. Los batches que ya estén en AirVault no se tocan en ninguno de los dos casos: eso no vive aquí. No se elimina la ejecución que se esté subiendo o indexando en ese momento, ni un CSV abierto con **Otra ejecución…** que viva fuera de `output/`.

## 2026-08-25 - La vista previa dice en qué batches queda la ejecución antes de subirla

La tabla de batches solo tenía algo que enseñar después de subir: hasta que se pulsaba **Subir a AirVault** no había manifiestos, porque el reparto se decide al preparar los archivos. Con el máximo por batch delante había que elegir un número a ciegas, subir, y solo entonces ver que la ejecución quedaba en siete batches y no en cinco. Ahora **Vista previa…**, junto al título de la tabla, adelanta ese mismo reparto: el nombre que llevaría cada batch, sus páginas (con los separadores que se repiten al cortar a mitad de una aeronave) y sus bitácoras. Los que ya están en AirVault salen con su estado, así que la lista es a la vez lo que falta por subir y lo que está esperando en la cola.

El cálculo es el de `preparar_partes` sin sus efectos: lee el índice de páginas y el CSV, y no crea manifiestos, no divide PDF, no rasteriza y no crea siquiera la carpeta de trabajo. Lo único que puede escribir es la reparación que ya hace cargar la lista de batches: un manifiesto de una carpeta portable que se movió guarda su ruta nueva. Por eso se puede abrir, cambiar el máximo y volver a abrirla las veces que haga falta. Las bitácoras que ya viajaron en batches subidos se descuentan igual que en el reparto de verdad, de modo que lo previsto es exactamente lo que se subiría.

De cada batch se abre además la lista de sus bitácoras: la página que ocupa cada una dentro del batch (la misma con la que se la busca en Web Index), matrícula, Log Page Number, fecha, vuelo, de qué página de la ejecución salió y qué la bloquea si algo la bloquea. Se llega desde la vista previa y con el botón derecho sobre una fila de la tabla de batches, que es la vía para un batch que ya está subido. Los separadores no entran en la lista, porque no son documentos que indexar, y se cuentan aparte.

La bitácora de la ventana tenía 110 px fijos de alto. Un motivo de fallo o la lista de páginas que faltan para completar un batch se envuelve en cuatro o cinco líneas, así que se leía a trozos moviendo la barra. Ahora parte de 160 px, no tiene tope y se queda con el alto que sobre al crecer la ventana: las dos tablas siguen con el suyo, y la ventana se abre pidiendo 800 px de alto en vez de 720 para que ese sitio exista desde el principio. Lo que no quepa lo sigue recortando la pantalla.

## 2026-08-25 - Las dos bitácoras que chocan se marcan como duplicadas

La columna `dup` y el color de la tabla señalaban solo la aparición posterior de un `log_number` repetido. La primera salía limpia, así que al ver una fila marcada había que recorrer el CSV buscando con cuál chocaba, y en una ejecución larga eso es media entrega. Ahora se marcan todas las apariciones del grupo: las dos filas del choque se ven a la vez.

Estar repetida y sobrar dejan de ser lo mismo, porque para borrar hacen falta las dos ideas por separado. Depurar duplicados sigue conservando la primera aparición de cada grupo, igual que antes; si el descarte hubiera seguido a la marca, la bitácora repetida habría desaparecido entera de la ejecución. El detalle del contador dice ahora cuál es la que se conserva, que con todas marcadas ya no se deducía de la lista.

## 2026-08-25 - Depurar deja elegir qué aparición se va, y la tabla del visor conserva su orden

El cuadro de depurar daba dos casillas y un número: se borraba la segunda aparición de cada bitácora sin enseñar cuál era ni permitir quedarse con otra. Cuando la buena era la segunda no había forma de decirlo. Ahora cada criterio trae su lista, los duplicados agrupados por bitácora con todas sus apariciones y las páginas en blanco una a una. Se marca la sobrante por omisión y se puede cambiar cuál se va, página por página. El borrado pasa a hacerse por las páginas marcadas y no por el criterio entero, que era lo que perdía esa elección al recontar.

En el visor de CSV, la tabla volvía siempre al orden del archivo después de quitar páginas, porque la ejecución se reescribe y el CSV se vuelve a leer entero. Ahora conserva el criterio de orden que estuviera puesto; cambiar de archivo sí lo descarta, porque el criterio era de la ejecución anterior. El cuadro de confirmación nombra además las bitácoras que se van, no solo cuántas páginas son: el número solo no dice cuáles, y una selección hecha sobre la tabla ordenada abarca filas que quedaron fuera de la vista.

Las casillas de la primera columna permiten juntar páginas sueltas sin sostener Ctrl mientras se recorre media ejecución. Mientras haya alguna marcada, son esas las que se eliminan.

Abrir un CSV grande deja de calcular el campo lógico de cada columna una vez por celda. Ese cálculo armaba un conjunto con todas las columnas cada vez, y en una ejecución de dos mil páginas por sesenta columnas era el coste dominante de la carga: veintitrés veces más lento que consultarlo en un mapa hecho una sola vez por archivo.

## 2026-08-25 - Cancelar y cerrar se pueden cortar sin esperar a las páginas en vuelo

Cancelar y cerrar pedían la parada y esperaban a que terminaran las páginas que ya se estaban leyendo. Con páginas grandes esa espera son minutos, y como la ventana no ofrecía ninguna salida parecía colgada. Ahora la segunda pulsación de Cancelar, y el segundo intento de cerrar, ofrecen cortar sin esperarlas, avisando de que se pierden las que estaban en curso y de que las ya leídas se conservan.

El corte no destruye ningún hilo. Matar un `QThread` en marcha aborta el proceso entero, que es el fallo `0xC0000409` que el cierre ordenado venía a evitar. Lo que se rompe son los pools de OCR, que es lo que tiene esperando a los hilos, y entonces terminan solos en milisegundos. Los procesos hijos se matan antes de soltar el pool para que no sigan gastando CPU leyendo una página cuyo resultado ya no va a recoger nadie.

La ventana principal gana el buscador de bitácoras que solo tenía el visor de CSV: mismo campo, mismos botones y el mismo criterio de que la coincidencia exacta va delante de la mención de paso, buscando solo en las columnas que la tabla está mostrando. Los dos visores de PDF comparten ya los atajos, flechas para pasar de página y Ctrl con más y menos para acercar, que hasta ahora solo valían en la vista previa principal.

Las tablas dejan además de ser texto que solo se puede leer: Ctrl+C y el menú contextual copian las celdas elegidas con tabuladores y saltos de línea, para pegarlas enteras en una hoja de cálculo. Vale en toda superficie donde aparezca el CSV, y una tabla que ya define su propio menú de clic derecho conserva el suyo.

## 2026-08-25 - La ventana de AirVault se queda dentro de la pantalla y adelanta el reparto

La ventana se abría con 780 px de ancho, pero su contenido exigía un mínimo de 1269. Qt aplica ese mínimo por encima del tamaño con el que se abrió, así que crecía sola: en una pantalla de 1366 quedaba pegada a los bordes y por debajo se salía, con los botones fuera del alcance del ratón. La culpa no era de las tablas, cuyo mínimo se queda en 66 px con o sin contenido, sino de los controles puestos en fila: la hilera de botones de abajo pide 1247 px ella sola, y la frase de arriba pedía 481 por no ajustar la línea. Ahora esa frase ajusta y el mínimo se acota a lo que da el escritorio, recortando lo que no quepa antes que mandar media ventana a donde no se llega. Comprobado a 1920, 1366, 1280 y 1024.

La columna «Entrega» del historial contaba los PDF exportados, que no es lo que se sube: un archivo de entrega se parte en varios batches según el máximo elegido. Ahora dice también en cuántos queda repartido, y se rehace al cambiar ese máximo. El número sale del mismo reparto que después se ejecuta y no de dividir páginas entre el límite, porque un batch que empieza a mitad de una aeronave repite el separador de su sección y esa página repetida ocupa sitio: con doce páginas y máximo dos salen ocho batches, no seis.

## 2026-08-25 - La raya queda prohibida en todo el proyecto

El guion largo aparecía en comentarios, docstrings, mensajes de estado, documentación y pruebas. Se eliminaron las 312 que había en el código y los textos del programa, y la prohibición queda escrita en `AGENTS.md` con la equivalencia de cada uso, para que no vuelva a entrar en el primer comentario que alguien redacte: paréntesis o comas para un inciso, dos puntos para introducir una explicación, y un guion normal para separar dos datos en una línea de estado.

Quedan fuera las definiciones de herramientas de `.agents` y `.claude`, cuyo texto puede formar parte de cómo se disparan, y la propia regla de `AGENTS.md`, que nombra el carácter que prohíbe.

Las cadenas de progreso cambian de forma visible, que ahora se leen «Archivo 1/3: a.pdf - Procesando páginas 29/30».

## 2026-08-25 - La orden de subir a mano se obedece, y las paginas amarillas se avisan en vez de prohibirse

Mandar subir un batch desde la tabla era una orden que el programa discutia. La accion solo se ofrecia sobre dos estados, y cuando se ofrecia pasaba por la comprobacion larga: recorrer la cola entera de AirVault, abrir cada batch candidato, leer sus paginas y contrastar los Log Page Number. Eso tarda minutos, y bastantes veces terminaba sin subir nada, porque la propia comprobacion vetaba la carga si el estado no era exactamente «sin subir». Visto desde fuera, el programa se ponia a revisar mucho y el archivo no salia nunca.

Ahora la orden se obedece. Vale sobre cualquier fila que no tenga un batch confirmado, sea cual sea el motivo por el que no lo tiene (que la carga no llegara, que lleve dos revisiones de tres, que aparezca descuadrada o que el reloj de espera no haya vencido), porque sin batch confirmado no hay nada que duplicar. Y antes de mandar el archivo se hace una sola lectura de la cola, no la comprobacion completa: si AirVault lo publico con su titulo entre la ultima revision y el clic, se recupera el ID y no se sube. Lo caro de la comprobacion nunca fue el listado, sino el contraste batch por batch, asi que esta version corta responde en el acto y conserva la unica proteccion que importaba.

Esa lectura cubre tambien el caso que de verdad se da: AirVault publica cargas como `Empty-Batch` aunque Quick Upload reciba el nombre, y entonces la persona que mira Web Index tampoco las ve. Sobre la misma lista se busca un `Empty-Batch` que no estuviera en la foto de la cola tomada antes de la carga anterior y cuyas paginas cuadren. Si aparece exactamente uno, es esta carga y no se sube; si aparecen varios, tampoco, porque elegir mal seria peor. Y no se miran siquiera si el trabajo nunca llego a subirse: un `Empty-Batch` ajeno del tamano justo habria bloqueado una primera subida para siempre, que es peor que el duplicado (el duplicado se ve y se borra, el bloqueo es silencioso).

De paso se corrigio un fallo de orden en el camino anterior: la ventana olvidaba el ID local antes de lanzar el hilo, y ese olvido borra tambien la foto de la cola. La comprobacion posterior llegaba sin con que reconocer un `Empty-Batch` propio, de modo que la accion pensada para no duplicar podia crear justo el duplicado. Ahora ese reinicio ocurre donde debe, despues de comprobar y justo antes de enviar.

La otra mitad es la guarda de paginas amarillas. AirVault llama asi a la pagina con un campo obligatorio sin rellenar, y basta una para que el batch no se deje cerrar. El programa lo sabe leyendo el manifiesto, sin preguntar nada al servidor, y hasta ahora se negaba a subir y ofrecia una sola salida: volver a exportar. Reexportar rehace el PDF de entrega completo, cientos de paginas, para arreglar a veces una. La guarda estaba eligiendo entre dos trabajos cuyo coste solo conoce quien indexa, y le cobraba el mas caro sin ensenarle la cuenta. Ahora la prohibicion es una pregunta: se dicen cuantas paginas son y que campos les faltan, y decide la persona.

Quien contesta que si asume tres cosas: que el batch no se cerrara solo mientras esas paginas sigan amarillas, que hay que completarlas a mano en Web Index, y que el programa no las pierde de vista, porque el detalle de la etapa de cierre las nombra una a una.

Lo que no cambia, y es la mitad que evita leer esto como una relajacion: evitarlas sigue siendo lo correcto, y el reparto normal manda las bitacoras dudosas al batch REVISAR, que existe para eso; REVISAR no pregunta nunca, porque ahi se sube sabiendo que se indexa a mano; la automatizacion no puede autorizar nada, solo una persona desde la ventana; y la autorizacion queda anotada en el manifiesto, asi que es de ese batch y no general, y un reintento no vuelve a preguntar lo mismo.

## 2026-08-24 - La cola admite varios batches a la vez y no exige esperar a que termine

La cola nació resolviendo una fila cada vez y con la ventana parada: el menú del botón derecho desactivaba todas sus acciones mientras había algo en vuelo, así que elegir un batch mientras subía otro no hacía nada. Ahora se eligen varias filas con Ctrl o Mayúsculas y la acción vale para todas (cada una hace lo que le corresponde, y el menú dice a cuántas se aplicaría antes de pulsarla); y lo que se pida mientras el programa trabaja no se pierde: queda apuntado y arranca solo en cuanto el hilo queda libre. Cancelar un batch descarta además lo que estuviera esperando turno para él, que si no acabaría subiéndose igual un rato después.

La banda azul de la fila seleccionada se decidía con el estado que llegaba en cada celda. Bastaba que una no lo recibiera (una celda sin contenido, como el ID de un batch que todavía no lo tiene) para que la banda saliera cortada justo ahí. Ahora quien decide es la selección de la vista: en una tabla que selecciona por filas, se pinta la fila entera tenga ítems o no.

Y «Completar batch» vuelve a ser una sola casilla. Había dos que decidían lo mismo (una en el menú principal, que se recuerda entre ejecuciones, y otra en Automatización, que además forzaba a la primera), de modo que cuál mandaba dependía de cuál se hubiera tocado la última. Queda la del menú principal.

## 2026-08-24 - La tabla de batches es una cola de trabajo, con su registro durable detrás

Tres cosas dejaban batches parados sin forma de sacarlos de ahí.

La primera era un error propio. Al repartir una entrega se comprueba que ninguna bitácora viaje en dos batches, y la identidad de una bitácora es el archivo del que salió y su página. Esa identidad solo es única **dentro** de su entrega: el escáner nombra igual sus archivos en cada ejecución, así que `Image_001.pdf` página 1 existe en todas. La ventana retoma los pendientes de días anteriores junto a los de hoy, de modo que en la misma tabla conviven batches de entregas distintas; la comprobación los daba por repetidos entre sí y abortaba la subida completa (también la de los que no chocaban con nada). El resultado era una tabla llena de filas «Sin subir» que no se subían nunca. Ahora cada entrega se comprueba por separado, y la guarda sigue en pie donde importa: dentro de una misma entrega.

La segunda era que el programa se rendía. Una carga dada por perdida se reenviaba como mucho dos veces y después se dejaba de insistir, lo que dejaba ese batch fuera de AirVault indefinidamente: un archivo que no llegó no se arregla por dejar de mandarlo. Ahora no hay tope. Lo que crece es el margen entre un intento y el siguiente (la espera configurada multiplicada por los reenvíos ya hechos: media hora, una hora, hora y media), de modo que una cola que solo va lenta no recibe el mismo archivo cada vuelta del reloj y una carga realmente perdida acaba subiendo igual.

La tercera es que no había forma de intervenir sobre un batch concreto: los botones actuaban sobre la ejecución entera. La tabla es ahora la cola de trabajo y el botón derecho sobre una fila ofrece subirla, comprobarla, indexarla, cerrarla, copiar su nombre o su ID, y sacarla de la cola. Subir a mano es una orden expresa y no pasa por la regla que decide sola si una carga se dio por perdida: se olvida lo que el programa creía haber subido y el archivo vuelve a Quick Upload, donde la búsqueda previa a la carga lo encuentra si estaba publicado de verdad. Cancelar no deshace nada (el batch conserva su ID y lo que ya se le escribió): deja de subirse, de buscarse y de indexarse hasta que alguien lo reanude.

Detrás de todo eso hay ahora un registro durable por entrega, `registro-de-batches.json`, junto a los manifiestos. Anota qué bitácoras lleva cada batch y cuáles llegaron a AirVault, y esa memoria sobrevive a los cambios de configuración: hasta ahora la respuesta a «qué falta por subir» se sacaba juntando los manifiestos que hubiera en disco en ese momento, así que al rehacer un reparto (donde los manifiestos viejos se apartan) se iba con ellos lo único que sabía qué estaba ya en AirVault. Guarda además los últimos diez repartos descartados, por si hay que mirar o rehacer uno, y se borra entero cuando se elimina el registro local de la ejecución, junto con los manifiestos apartados que antes se quedaban ahí.

## 2026-08-24 - Una carga que AirVault no publicó se da por perdida en cuanto se sabe, y no cuando vence un reloj

La única señal de que una carga aceptada por Quick Upload no iba a aparecer era un reloj, y el reloj empezaba tarde y en el momento equivocado. Se agotaban tres ciclos de identificación, se guardaba ahí la marca de espera y solo media hora después de esa marca se permitía reenviar. Como la marca se estrena cuando el programa se da cuenta, una ejecución que se retomaba días más tarde volvía a esperar media hora entera por un archivo que llevaba días perdido. Y agotados los dos reenvíos, la parte se quedaba en «Subido pendiente confirmación» para siempre, con las demás partes de la entrega ya terminadas y sin nada que la sacara de ahí.

Ahora una carga se da por perdida por cualquiera de dos motivos, y el primero no depende de ningún reloj: si las partes que se enviaron **después** ya aparecieron en Web Index y quedaron indexadas, la cola pasó de largo. No es que AirVault vaya lento, es que esa carga no está. El segundo motivo sigue siendo el tiempo, pero contado desde la fecha del batch (el momento en que Quick Upload aceptó el archivo), no desde que el programa miró. Una ejecución retomada días después reconoce la carga vieja en la primera comprobación en vez de estrenar la espera.

Se añade un tercer motivo, y es el que resuelve el caso de los batches borrados. Cuando los títulos esperados no aparecen, el programa recorre la cola entera de AirVault buscándolos por nombre visible, por nombre embebido, por cantidad de páginas y por los Log Page Number de dentro. Si esa búsqueda tampoco los encuentra, no queda ningún nombre bajo el que puedan estar escondidos; y si además el archivo lleva subido más de lo que AirVault tarda en publicar, tampoco viene en camino. Antes ese hallazgo no contaba para nada: la misma búsqueda se repetía dos veces más y después empezaba la espera. Ahora se resuelve en la primera comprobación y el archivo vuelve a Quick Upload.

Esa vía queda atada a la edad de la carga a propósito. Sobre un batch recién subido la misma búsqueda no demuestra nada (que AirVault aún no lo haya publicado es lo normal, tarda minutos y a veces horas), así que una carga reciente conserva el camino de siempre: tres revisiones y después la espera. Volver a subirla antes publicaría el mismo archivo dos veces.

Lo demás no cambia: los tres ciclos de identificación por nombre, páginas y contenido se siguen agotando antes de dar nada por perdido (la edad de un archivo no es motivo para saltarse la búsqueda de un batch publicado con otro nombre), el reenvío sigue topado en dos veces para no acabar publicando lo mismo varias veces, y la decisión de qué falta por subir sigue viviendo en un solo sitio, de modo que el botón y la comprobación periódica deciden igual. Esa regla recibe ahora la ejecución entera, porque dar una carga por perdida depende de en qué van las demás.

Los batches que el programa cierra por su cuenta al terminar de indexarlos quedan como **Terminado por el programa**, separados de los que ya estaban cerrados en AirVault cuando se encontraron. Antes ambos casos se pintaban igual y no había forma de saber, mirando la lista, qué había cerrado el programa sin que nadie interviniera.

## 2026-08-24 - Cambiar el reparto de una ejecución ya subida conserva sus batches y solo reparte lo que falta

Cambiar el máximo de páginas por batch no tenía efecto sobre una ejecución que ya estaba preparada: el reparto de disco se devolvía tal cual y el número nuevo se ignoraba en silencio. La ventana lo tapaba bloqueando el control en cuanto había batches, así que la única forma de aplicar otro reparto era borrar el registro local, y entonces la ejecución se repartía entera desde cero, sin saber nada de los batches que ya estaban en AirVault. Los batches viejos seguían ahí con sus páginas, los nuevos volvían a traer esas mismas bitácoras, y cada una acababa publicada dos veces. El mismo agujero se abría solo cuando el juego de manifiestos quedaba incompleto por cualquier motivo, porque ese caso también terminaba en un reparto desde cero.

Ahora el reparto se rehace, pero lo que ya viajó manda. Cada bitácora se identifica por el archivo del que salió y su página dentro de él, que es lo único estable entre un reparto y otro. Los batches que Quick Upload ya aceptó se conservan intactos (su PDF, su nombre, su ID y sus páginas) y solo se reparten, con el número nuevo, las bitácoras que ningún batch subido se llevó. Las partes nuevas siguen la numeración desde la última que ya existe: un número que viajó con su nombre no se reutiliza aunque su hueco haya quedado libre. Si el corte deja una aeronave partida, el batch siguiente abre con una copia de su separador, igual que en un reparto normal.

El reparto que solo estaba en disco sí se rehace entero, porque no hay nada que respetar. Su manifiesto no se borra: se aparta como `manifiesto-reemplazado-<fecha>.json`, de modo que deja de ofrecerse como un batch pendiente de subir (que era otra vía para acabar cargando dos veces lo mismo) pero sigue ahí por si hay que mirarlo.

Antes de mandar nada a Quick Upload se comprueba que ningún batch repita una bitácora que ya viaja en otro. Es la última red y cubre lo único que no tiene arreglo cómodo: una vez publicada dos veces, hay que deshacerlo en AirVault a mano. Si al empezar ya hubiera bitácoras en dos batches subidos, no se reparte nada y se dice cuáles son.

Con esto el control de páginas por batch deja de estar bloqueado: se puede cambiar con la ejecución a medio subir, y la ventana dice cuántos batches conserva y cuántas bitácoras vuelve a repartir.

## 2026-08-24 - Los batches se reparten con la cantidad exacta de páginas y lo que no llegó a AirVault se reenvía solo

El **Máximo por batch** de la ventana de AirVault no era la cantidad que se subía. El reparto cortaba entre aeronaves y nunca partía una: una sección que no cabía entera cerraba el batch antes de tiempo, así que con el mismo número elegido salían batches de tamaños distintos y bastante por debajo de él. Con 200 páginas por batch y aviones de 30, un batch se cerraba en 180 y el siguiente en 150; el reparto que se había pedido y el que se subía no se parecían, y las cuentas que dependen de esa cantidad dejaban de cuadrar.

Ahora el número es exacto. Todos los batches llevan las páginas elegidas y solo el último se queda con el resto. Cortar por cantidad puede dejar una aeronave repartida entre dos batches: cuando pasa, el siguiente abre con una copia del separador de esa aeronave (que ocupa una de sus páginas) para que ninguno empiece con bitácoras sueltas. Es la misma regla que ya se aplicaba a una sección que por sí sola superaba el límite, ahora aplicada en todos los cortes. La entrega exportada y el CSV siguen intactos: el reparto solo produce los PDF internos que van a Quick Upload.

El otro cambio es el de las cargas que se quedaban paradas. La comprobación periódica solo preguntaba: un archivo que no había llegado a subirse (porque la subida falló, porque venía pendiente de otra ejecución) se quedaba en la lista mientras el reloj seguía consultando al servidor por él, cada tantos minutos, indefinidamente. Y una carga que Quick Upload aceptó pero AirVault nunca publicó terminaba su espera pidiendo que alguien pulsara **Subir a AirVault**, con la comprobación automática ya apagada.

Ahora, con **Comprobar cada** marcado, cada vuelta del reloj vuelve a enviar lo que falte y continúa la cadena hasta el indexado, sin que nadie pulse nada. La regla de qué falta por subir vive en un solo sitio, así que el botón y el reloj deciden lo mismo. Una carga dada por perdida se reenvía como máximo dos veces: insistir siempre contra una cola atascada acabaría publicando el mismo archivo varias veces. Agotadas, el programa deja de insistir, lo dice en el resumen y pide revisar el batch en AirVault. Antes de cada envío se vuelve a buscar el nombre en la cola, de modo que un batch que apareció entre medias recupera su ID en vez de subirse dos veces. Una subida que falla no se reintenta hasta la vuelta siguiente del reloj: comprobar y subir se llamarían el uno al otro sin parar.

## 2026-08-20 - La bitácora sin fecha legible ya no bloquea el batch de AirVault

`End Date` es obligatorio en AirVault. Una bitácora cuya fecha no se dejó leer llegaba al indexado con ese campo vacío, la guarda de obligatorios bloqueaba su página y el batch se quedaba sin poder cerrarse: alguien tenía que abrirlo en el Web Index y teclear esa fecha a mano, en medio de cuatrocientas páginas que sí se habían escrito solas. Y bastaba una.

Ahora, cuando la fecha no se leyó pero el número de bitácora sí, la fecha se deduce. El número es el que ordena el libro, y dentro de un libro la fecha no retrocede al aumentar: una página sin fecha está entre la de la anterior y la de la siguiente, así que se le pone la de la bitácora fechada más cercana del mismo libro (en un empate, la posterior), que cae dentro de ese intervalo por construcción. Pasada la última fechada ya no hay techo que respetar y va el último día de ese mes, la misma convención con la que el CSV completa un día ilegible. Si el libro entero llegó sin fechas se baja al mes dominante del avión y, en último término, al de la ejecución.

La regla no cruza libros: otro libro del mismo avión no ordena a este, solo aporta su mes. Y una bitácora **sin número legible no recibe fecha**: sin número no hay libro ni posición, esa página está bloqueada de todos modos por su propio campo obligatorio, y ponerle una fecha solo maquillaría el reporte.

La deducida no se presenta como leída. El reporte de revisión trae la columna `fecha_inferida` con la regla que la produjo, y el resumen (el de la página HTML y el de la consola) cuenta cuántas páginas la llevan, para mirarlas antes de aprobar la escritura. El CSV de la ejecución no cambia: ahí la fecha sigue siendo la que se leyó, o ninguna.

## 2026-08-19 - El indexado en AirVault se muda a su propia ventana, con el historial delante

La sección «Indexar en AirVault» colgaba de la ventana principal, desplegable junto a «Opciones avanzadas». Desplegada le quitaba alto a la vista previa y cambiaba el mínimo de la ventana: el reparto se recalculaba con la sección abierta, y en pantallas bajas eso empujaba la ventana fuera del escritorio. Y sabía subir una sola ejecución, la que acababa de exportarse; cualquier otra había que buscarla a mano por la ruta de su CSV.

Ahora es una ventana aparte, que abre el botón **Indexar en AirVault…** desde esa misma fila. Dentro va el historial de las últimas 25 ejecuciones procesadas, de la más reciente a la más antigua, con sus páginas y lo que cada una tiene para subir: se elige de ahí cuál se sube, que es como se trabaja en realidad. La ventana principal vuelve a medirse sin nada colgando y la vista previa recupera su alto.

El historial dice antes de intentarlo cuáles todavía no se pueden subir: **Sin exportar** las que no tienen PDF de entrega y **Falta reexportar** las exportadas antes de que existiera el índice de páginas. Salen en la lista igualmente, en gris (quien las busca tiene que verlas, y ver qué les falta), pero no dejan arrancar el trabajo. Al abrirse viene señalada la ejecución exportada más reciente, no la última a secas: procesar sin exportar es normal, y abrir apuntando a una ejecución que no se puede subir no lo es.

El avance sale por la barra y la etiqueta de la propia ventana, no por las de la principal, que queda libre para seguir procesando mientras un batch se escribe. La ventana no se cierra con un batch a medias. Cerrada entre la revisión y el indexado, la revisión se conserva: al volver a abrirla se puede **Indexar** sin revisar de nuevo.

De paso, los batches que una revisión sin indexar dejaba tomados se sueltan de verdad al cerrar el programa. El cierre tenía que pedirlo y nunca lo hacía, así que el batch quedaba bloqueado en AirVault (sin error, colgando a quien lo abriera después) hasta que alguien lo reclamaba.

## 2026-08-19 - El batch de AirVault se suelta al terminar y la sesión se renueva sola

Indexar dejaba el batch tomado. Abrirlo lo bloquea a nombre de quien lo abre, y el programa nunca lo soltaba: AirVault admite un solo dueño por batch, así que a partir de la primera ejecución cualquier apertura posterior (la del programa la vez siguiente, o la de la persona que entra por el navegador) se quedaba esperando sin respuesta, porque el servidor no contesta ni da error cuando el batch está tomado. Después de tres minutos aparecía un tiempo agotado que además culpaba al navegador de un candado que había dejado el propio programa. El batch de «Revisar» era el peor caso: es el que alguien tiene que indexar a mano, y quedaba bloqueado sin que nadie escribiera en él.

Ahora el batch se suelta siempre: al terminar, al cancelar, cuando algo se corta a medias y también cuando la ventana se cierra sin llegar a indexar. El de «Revisar» se suelta en cuanto se planifica. Y cuando una apertura se queda esperando de todos modos, el programa pregunta al listado quién lo tiene tomado y lo dice con nombre, en vez de dejar una espera sin explicación.

La sesión guardada en el perfil de Edge tampoco tenía salida cuando dejaba de valer. La cookie seguía ahí y con la forma correcta, así que se daba por buena, la primera petición moría y el mensaje mandaba a copiar una cookie con F12: el camino largo, y encima el que el perfil venía a evitar. Ahora el programa vuelve a abrir la ventana para entrar, que es lo que haría una persona.

Sigue siendo un perfil propio dentro de `portable/` y no el Edge de siempre, aunque ahí la sesión ya esté abierta. No es una preferencia: el navegador ignora a propósito el puerto de depuración cuando el perfil es el de por defecto, y si Edge ya está abierto el arranque nuevo le pasa la orden al que corre y se va, sin dejar puerto al que conectarse. Un WebDriver termina en el mismo sitio, porque también maneja un navegador que arranca él. Lo que sí se arregló es que entrar sea una sola vez de verdad: la cookie de federación es de sesión, y Chromium solo la guarda en disco cuando el perfil arranca restaurando la sesión anterior.

Los motivos que se muestran cambian de tono. Un campo obligatorio vacío se nombra como se llama en la pantalla de AirVault (Aircraft, Fleet, Log Page Number) y no por su número interno. Si el batch y la ejecución no tienen las mismas páginas, el aviso dice cuántas faltan o sobran y cuál es la causa habitual. Un rechazo del servidor dice qué se pedía y qué contestó, y ya no arrastra el batch entero: un 404 de una página frena esa página, no las cuatrocientas. Y si Edge no arranca, el mensaje incluye lo que dijo Edge, que antes se tiraba.

## 2026-08-19 - La sesión de AirVault se resuelve sola

Indexar empezaba por un trámite a mano: entrar a AirVault en el navegador, abrir las herramientas de desarrollo, copiar la cookie de sesión y pegarla en el campo **Sesión**. Cada vez que se abría el programa, otra vez. El atajo que iba a evitarlo (leer la cookie del perfil de Edge) casi nunca funcionaba: hay que cerrar Edge para que suelte su base de cookies, y un Edge moderno las cifra con la identidad del navegador (`v20`), que no se deshace desde fuera.

Ahora el programa abre Edge él mismo, con un perfil propio dentro de `portable/`, apuntando al enlace de acceso federado. La primera vez se ve la ventana y alguien entra con su usuario de Microsoft y su segundo factor; en cuanto AirVault suelta sus cookies, la ventana se cierra sola. De ahí en adelante el perfil conserva la sesión, así que el navegador se abre sin ventana, entrega la cookie y se cierra. El campo **Sesión** sigue ahí, vacío, como respaldo por si eso falla.

El segundo factor lo sigue haciendo una persona: existe justamente para eso. Lo que se automatizó es lo de alrededor (encontrar la cookie, copiarla y volver a pegarla cada vez), que no protegía nada.

Las cookies se le piden al navegador por su protocolo de depuración, no leyendo su archivo. El navegador sí sabe descifrar las suyas y por ese camino las entrega en claro, así que no hay ningún cifrado que rodear. No se instala ni se descarga nada: Edge ya viene con Windows y el perfil es una carpeta más de `portable/`, que viaja con el programa.

Se entra por el enlace federado y no por la raíz del sitio, que es el que dispara la redirección a Microsoft. Por la raíz la sesión queda a medias, con `ASP.NET_SessionId` pero sin la cookie que autentica; esa cookie sola dejó de contar como sesión abierta, porque la pone el servidor al primer contacto, antes de saber quién eres, y darla por buena arrancaba un batch que moría en la primera página.

## 2026-08-19 - Depurar duplicados y páginas en blanco desde donde se ve la ejecución

Las páginas repetidas y las que salen en blanco se conocían pero no se podían quitar en bloque. El contador **Duplicados** de la ventana principal decía cuántas eran y en qué bitácoras, y el resumen del procesamiento contaba las vacías, pero sacarlas de la ejecución era ir al visor de CSV, buscarlas una por una en la tabla y borrarlas con `Supr`. En una ejecución de 884 páginas con una veintena de repetidas eso son veinte búsquedas.

Los dos apartados que muestran a la vez el CSV y su PDF (la ventana principal y el visor de CSV) ganan un botón **Depurar**, junto al de **Exportar**. El cuadro dice cuántas páginas hay de cada clase antes de borrar nada, y solo quita las que se marquen. Se reescriben el CSV mínimo, el CSV completo, el JSON y `stats.json` sin ellas.

Duplicada es toda aparición posterior de un mismo `log_number`; la primera se conserva, que es la que se entrega, y una lectura que no tenga siete dígitos no cuenta como repetida. En blanco es la que el procesamiento marcó como vacía. Una página que sea las dos cosas se elimina una sola vez, aunque cada casilla la cuente en su total: cada número responde a «cuántas quita esta casilla».

El criterio vive en un solo sitio, `app/validation/depuracion.py`, y el cuadro en otro, para que las dos ventanas no puedan entender por «duplicada» dos cosas distintas.

Los PDF no se rehacen al depurar. Son la entrega y se componen al exportar, cuando ya no queda nada más que quitar; hacerlo antes dejaría dos entregas distintas de la misma ejecución en la carpeta. En la ventana principal el botón espera a que la ejecución esté guardada (sin su carpeta, la reescritura crearía una segunda entrega de lo mismo) y a que no haya ninguna escritura en curso. Ninguna de las dos ventanas deja la ejecución sin páginas: para deshacerse de ella entera se borra su carpeta de `output/`.

## 2026-08-19 - Indexado en AirVault desde la ventana, y los PDF se generan al exportar

### Sección «Indexar en AirVault»

Escribir los índices de un batch era teclear en el Web Index de AirVault entre 300 y 500 páginas a mano, comprobando matrícula, número de bitácora y fecha una por una, con todos los datos ya leídos y guardados en el CSV de la ejecución.

La ventana principal gana una sección desplegable, junto a «Opciones avanzadas», que hace ese recorrido: sube el PDF de la ejecución, espera a que el batch aparezca en AirVault, calcula qué escribiría en cada página y (solo después de que alguien mire el reporte) lo escribe. El avance sale por la barra y la etiqueta de estado que ya existían; no se añadieron indicadores.

Su flecha comparte fila con la de «Opciones avanzadas». Apilada debajo le costaba 15 px de alto a la ventana, que en una pantalla de 1024x768 se abría fuera del escritorio.

La misma función existe en `run_airvault.py` para la línea de comandos.

### Los separadores del PDF ya no cuentan como bitácoras

El indexado emparejaba el CSV con el batch por posición, dando por hecho que la página *n* del batch era la bitácora *n* del CSV. El PDF de entrega no cumple eso: entre las secciones lleva páginas divisorias (la matrícula o el mes de cada grupo, **POSIBLES DISCREPANCIAS**, **REVISAR**) que el CSV no tiene y que en AirVault ocupan una página igual que cualquier otra. Con un solo separador delante, todo lo que iba detrás se habría escrito una página corrido: la bitácora de la página 40 indexada con los datos de la 39.

La exportación pasa a escribir junto al CSV un índice, `<ejecución>_paginas.json`, que declara qué hay en cada página del PDF. Ese archivo, y no el CSV, fija el orden del manifiesto. Los separadores entran como registros propios (así la correspondencia por posición se sostiene) y no se les escribe nada: ni se leen del servidor, ni cuentan como omitidos, ni se espera que queden en `Valid` al verificar.

Una ejecución exportada antes de que existiera el índice sigue el orden del CSV y se avisa; si aquel PDF llevaba separadores, la guarda de cantidad detiene el trabajo antes de escribir nada.

### La sesión de AirVault sale del navegador

El acceso está federado con Microsoft Entra ID y pide segundo factor, que no se completa desde un script, así que el formulario de usuario y contraseña no servía para la cuenta con la que se trabaja.

La sesión se reutiliza del navegador: la cookie que se pega, o la del perfil de Edge cuando se deja leer. La cookie va al tarro de peticiones y no a una cabecera fija, porque el primer `Set-Cookie` del servidor se habría comido la puesta a mano y el batch habría muerto a media escritura. Antes de empezar se comprueba la sesión con una petición, para no descubrir en la página 250 de 400 que había caducado.

El atajo de Edge sirve poco en la práctica: hay que cerrar Edge para que suelte su base de cookies, y un Edge moderno las cifra con la identidad del navegador (`v20`), que no se deshace desde fuera. Cuando no se puede, se dice por qué y se sigue con la cookie pegada.

### Los PDF se generan al exportar, no al procesar

Terminado el OCR, el programa generaba siempre los PDF de entrega. Componerlos vuelve a abrir cada original y tarda, y lo pagaba también quien iba a cambiar la separación y a exportar otra vez de todos modos.

**Procesar** guarda ahora los datos (CSV, JSON y estadísticas) y nada más. La entrega se arma al pulsar **Exportar**, con la separación marcada en ese momento. Los archivos de entrada se siguen apartando a `input/processed/` al terminar, y la ventana reapunta sus resultados allí, así que exportar después encuentra las páginas originales.

## 2026-08-19 - Nombre de batch definitivo y las bitácoras sin avión en su propio batch

### El nombre

Los batches se llamaban `DP | BIT 18 AUG 2026 05 42`, y al repartir la entrega las partes salían como `(1 de 5)`. Pasan a llamarse **`DP | BITS 18 AUG 2026 05 42`**, con S, enteros en mayúsculas, y las partes con sufijo `-1`, `-2`. El sufijo del batch es el mismo que lleva su archivo, y una prueba comprueba que no se separen: si lo hicieran, el batch dejaría de poder emparejarse con el PDF que lo formó.

La marca de tiempo es la del **procesamiento**. Ya salía del nombre de la carpeta de la ejecución, pero cuando esa carpeta no lo llevaba se caía a la hora actual, que es la de la subida y no dice nada de la bitácora. Ahora se toma la hora del propio archivo, que sigue siendo la del procesamiento; la hora actual solo aparece si no hay ni archivo que mirar.

### Las bitácoras sin avión confirmado, en su propio batch

Las páginas cuya matrícula nadie pudo confirmar cerraban el PDF de entrega bajo el separador **REVISAR**, así que caían dentro del batch grande. Allí el indexado las bloqueaba (sin avión no hay dónde archivarlas) y se quedaban en medio de cuatrocientas páginas, donde nadie las encontraba.

Salen ahora en su propio archivo y, por tanto, en su propio batch: `DP | BITS 18 AUG 2026 05 42 REVISAR`. Ese batch **se sube y no se toca**: el indexado no le lee ni le escribe ninguna página, y queda marcado en la cola del Web Index para resolverlo a mano. No se numera como una parte más (no es «una de cinco», es el que queda aparte) y su manifiesto vive en `output/airvault/<ejecución>/revisar/`.

En la ejecución de referencia son 17 páginas de 884.

## 2026-08-19 - La entrega se reparte en batches, y el indexado aguanta que la red falle

### Repartir la ejecución en varios batches

Una ejecución completa (unas 900 páginas y casi dos gigas) formaba un solo batch en AirVault: incómodo de revisar, y una subida de ~1850 peticiones que si se cortaba había que rehacer entera.

La casilla **Repartir en** del cuadro «Salidas», o `--paginas-por-parte N` en la línea de comandos, escribe la entrega en varios PDF de a lo sumo esas páginas. Cada archivo es un batch propio en AirVault, con su nombre (`DP | BIT 18 AUG 2026 05 42 (2 de 5)`), su manifiesto y sus guardas; una parte que se corte no arrastra a las demás y al volver a revisar se retoma solo lo que falta. El reporte de revisión sigue siendo uno solo para toda la ejecución.

El corte se hace entre secciones, así que las bitácoras de un mismo avión no quedan repartidas entre dos batches. Cuando un avión solo tiene más páginas que el tope, se parte y la continuación repite su separador, para que ninguna parte empiece con bitácoras sueltas.

El nombre lleva el número de parte porque los batches se localizan por nombre: dos iguales no habría forma de distinguirlos, que es justo lo que ya pasa en la cola de AirVault con los batches cargados a mano.

El control se puso en la fila del formato de salida y no debajo: apilado, el cuadro crecía y la ventana dejaba de caber en 1280x720; con el texto largo, además, empujaba el reparto en dos columnas fuera de alcance y la ventana volvía a estirarse a lo alto.

### Que la red falle deja de tirar el trabajo

Un batch son cientos de peticiones y una subida completa casi dos mil. A esa escala un corte momentáneo dejó de ser raro, y hasta ahora cualquiera de ellos tiraba el trabajo entero: la subida no reintentaba nada, y una página que no cargaba cortaba la planificación del batch completo.

- **Se reintenta lo que puede arreglarse solo**: un tiempo agotado, una conexión cortada, un servidor que responde que está ocupado (408, 429, 5xx). Tres intentos, esperando más en cada uno. Un 404 no se reintenta, porque insistir devuelve lo mismo.
- **Cada trozo de la subida se reintenta por separado.** Reenviar un trozo con el mismo índice es inocuo: el servidor arma el archivo por posición.
- **Una página que no carga bloquea solo a esa página.** Sin poder leerla no se puede comprobar que el batch y el manifiesto hablan de la misma bitácora, así que no se escribe; el resto del batch sigue.
- **Si la cookie caduca a media escritura, se corta el batch entero.** Lo que no se llegó a intentar queda pendiente, no fallido: seguir habría marcado como fallidas cientos de páginas que nadie tocó, y al retomar no se sabría cuáles reintentar. La sesión se comprueba además antes de empezar.
- **El batch abierto en el navegador** hace que AirVault no conteste y tampoco dé error. Todas las peticiones llevan tiempo límite y el mensaje dice qué cerrar.

### Muestra para probar antes de un batch real

`tools/muestra_bitacoras.py` arma un PDF de prueba con veinte páginas al azar de las que haya en `input/`. Es una entrada de verdad, así que se procesa, se exporta y se indexa como cualquier otra y la prueba recorre lo mismo que un batch real: 40 MB y veinte páginas en vez de 1.9 GB y 884.

## 2026-08-18 - Indexación automática: vuelo, fechas, matrículas sin confirmar y sección «Revisar»

Ejecución de referencia: 884 páginas de `input/Image_001..003.pdf`.

### Matrículas que no existen

Con la verificación de flota activa, una lectura que no correspondía a ningún avión del catálogo se conservaba tal cual cuando dos aviones quedaban a la misma distancia o cuando la lectura no permitía comparación. Esa lectura abría su propia sección en el PDF de entrega y su propia clave en `stats.json`, de modo que la entrega contenía aviones inexistentes: `HP-1281CMP`, `HP-1375CMP` y `HP-1820CMP`.

Ahora, cuando no hay un único avión más parecido, la asignación se elimina, la lectura queda en `alternatives` y la página pasa a **REVISAR**. Ninguna matrícula sin confirmar llega al CSV, al PDF ni a las estadísticas.

### Sección «Revisar»

Las páginas sin matrícula confirmada ya no forman un grupo `sin_matricula` junto a los aviones reales.

- PDF único: cierran el documento bajo el separador **REVISAR**, con cualquier combinación de opciones y aunque no se haya marcado «Posibles discrepancias» ni la separación por matrícula.
- Varios PDF: se escriben en `revisar.pdf`.

En la ejecución de referencia son 17 páginas.

### Estado de página

El estado representaba el peor campo de la página, de modo que una firma de técnico ausente (lo normal en una bitácora de vuelo) dejaba la página en `ERROR`. Con la verificación de matrículas activa el estado se recalculaba además sobre las casillas sueltas de la fecha, así que activar esa opción convertía en error a páginas que nadie había tocado. La ejecución de referencia daba 689 páginas en `ERROR` sobre 884.

El estado pasa a describir la capacidad de indexación de la página: `ERROR` solo cuando una página no blanca no aporta ninguno de los datos de índice disponibles en la plantilla; `WARNING` cuando algo requiere confirmación; `OK` cuando `log_number`, matrícula y fecha salieron de la lectura directa. Las firmas, las casillas sueltas de la fecha y el número de vuelo no deciden el estado. La política vive en `app/validation/page_status.py` y la comparten la validación de plantilla y los tres correctores.

Resultado en la ejecución de referencia: 0 páginas en `ERROR`, 423 en `OK` y 461 en `WARNING`. Quedan 40 páginas con algún dato de índice sin resolver para indexación manual.

### Fecha

Un mes o un año sin resolver marcaban el campo como `ERROR`, y el día que no se leía se dejaba sin valor: 176 páginas terminaban sin fecha aunque el resto de la bitácora se hubiera leído entera.

Se añaden dos pasos al corrector por libro y se cambia el estado de lo no resuelto a `WARNING`:

- **Relleno por consenso.** Si todas las lecturas confiables del libro coinciden en el mes (o en el año), no hay otro valor posible para las páginas que no se dejaron leer y se completa con ese. Las anclas se fotografían antes de interpolar, porque la interpolación descarta como ancla la lectura que contradice un intervalo y un libro dejaría de parecer discrepante sin serlo.
- **Recuperación del día.** El día leído nunca se sustituye. El que no se leyó recibe el último día que cabe en la secuencia del libro: acotado por la página anterior y la siguiente del mismo mes y, sin página posterior, el último día del mes. Queda en `WARNING`, con `source=inferred` e `inference_method=month_end_fallback`, así que en el CSV se distingue de un día leído.

Resultado: 141 días completados y 27 páginas sin fecha, frente a 176.

### Número de vuelo

El casillero admitía cualquier lectura de hasta tres letras seguidas de cifras, así que llegaban al CSV valores que no existen en la bitácora: `SYZ`, `BSO`, `FOS`, `BOB`, `YO3`, `CMP472`, `CN364`. Eran 166 de 884 páginas.

La normalización pasa a ajustar la lectura a las formas que de verdad se escriben en el casillero, comprobadas contra los escaneos:

- vuelo numerado de una a cuatro cifras;
- vuelo con prefijo: cualquier letra junto a tres cifras se normaliza a `CM`, y la `A` se conserva;
- código de mantenimiento `TCK`, `CCK`, `SPV`, `SVC` o `SV`, ajustado al más parecido a una letra de distancia, sin resolver empates.

Las letras que el reconocedor devuelve en lugar de una cifra manuscrita se recuperan cuando el tramo conserva evidencia numérica (`7S8` es 758, `CMIO3` es CM103). Un `CM` leído entero sostiene por sí solo la lectura de lo que va detrás (`CMPlOS` es CM105). Lo que no encaja en ninguna forma se deja vacío.

Resultado: 775 páginas con vuelo (antes 737) y ninguna con un valor fuera de esas formas.

### Vocabulario del casillero de vuelo

Revisados uno a uno los recortes de las 89 lecturas que seguían quedando vacías, aparecen dos palabras más escritas con claridad (`SUP`, en dos páginas, y `MTC`) y una constante: el reconocedor devuelve la P de `SPV` como `9`, `D`, `R` o `2`, y la T de `TCK` como `J`. Comparar el código letra a letra dejaba fuera `S9V`, `SDV`, `SRV`, `52V` y `JCK`, que en la página dicen `SPV` y `TCK`.

La comparación pasa a hacerse por clase de trazo sobre la lectura entera, con un solo trazo de diferencia admitido y con preferencia por el código de la misma longitud. No se afloja la distancia, porque a dos trazos `ZCC` (que es un `700` manuscrito) se confundiría con `CCK`.

Resultado: 782 páginas con vuelo y 91 códigos reconocidos (`TCK` 44, `SPV` 27, `SV` y sus variantes numeradas 14, `SVC` 4, `MTC` y `SUP` 1 cada uno).
