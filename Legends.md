# Legends

Registro de los cambios de comportamiento del sistema. Cada entrada indica qué hacía antes, qué hace ahora y por qué se cambió. La descripción técnica completa vive en [docs](docs/README.md).

## 2026-08-20 — La bitácora sin fecha legible ya no bloquea el batch de AirVault

`End Date` es obligatorio en AirVault. Una bitácora cuya fecha no se dejó leer llegaba al indexado con ese campo vacío, la guarda de obligatorios bloqueaba su página y el batch se quedaba sin poder cerrarse: alguien tenía que abrirlo en el Web Index y teclear esa fecha a mano, en medio de cuatrocientas páginas que sí se habían escrito solas. Y bastaba una.

Ahora, cuando la fecha no se leyó pero el número de bitácora sí, la fecha se deduce. El número es el que ordena el libro, y dentro de un libro la fecha no retrocede al aumentar: una página sin fecha está entre la de la anterior y la de la siguiente, así que se le pone la de la bitácora fechada más cercana del mismo libro —en un empate, la posterior—, que cae dentro de ese intervalo por construcción. Pasada la última fechada ya no hay techo que respetar y va el último día de ese mes, la misma convención con la que el CSV completa un día ilegible. Si el libro entero llegó sin fechas se baja al mes dominante del avión y, en último término, al de la ejecución.

La regla no cruza libros: otro libro del mismo avión no ordena a este, solo aporta su mes. Y una bitácora **sin número legible no recibe fecha**: sin número no hay libro ni posición, esa página está bloqueada de todos modos por su propio campo obligatorio, y ponerle una fecha solo maquillaría el reporte.

La deducida no se presenta como leída. El reporte de revisión trae la columna `fecha_inferida` con la regla que la produjo, y el resumen —el de la página HTML y el de la consola— cuenta cuántas páginas la llevan, para mirarlas antes de aprobar la escritura. El CSV de la ejecución no cambia: ahí la fecha sigue siendo la que se leyó, o ninguna.

## 2026-08-19 — El indexado en AirVault se muda a su propia ventana, con el historial delante

La sección «Indexar en AirVault» colgaba de la ventana principal, desplegable junto a «Opciones avanzadas». Desplegada le quitaba alto a la vista previa y cambiaba el mínimo de la ventana: el reparto se recalculaba con la sección abierta, y en pantallas bajas eso empujaba la ventana fuera del escritorio. Y sabía subir una sola ejecución, la que acababa de exportarse; cualquier otra había que buscarla a mano por la ruta de su CSV.

Ahora es una ventana aparte, que abre el botón **Indexar en AirVault…** desde esa misma fila. Dentro va el historial de las últimas 25 ejecuciones procesadas, de la más reciente a la más antigua, con sus páginas y lo que cada una tiene para subir: se elige de ahí cuál se sube, que es como se trabaja en realidad. La ventana principal vuelve a medirse sin nada colgando y la vista previa recupera su alto.

El historial dice antes de intentarlo cuáles todavía no se pueden subir: **Sin exportar** las que no tienen PDF de entrega y **Falta reexportar** las exportadas antes de que existiera el índice de páginas. Salen en la lista igualmente, en gris —quien las busca tiene que verlas, y ver qué les falta—, pero no dejan arrancar el trabajo. Al abrirse viene señalada la ejecución exportada más reciente, no la última a secas: procesar sin exportar es normal, y abrir apuntando a una ejecución que no se puede subir no lo es.

El avance sale por la barra y la etiqueta de la propia ventana, no por las de la principal, que queda libre para seguir procesando mientras un batch se escribe. La ventana no se cierra con un batch a medias. Cerrada entre la revisión y el indexado, la revisión se conserva: al volver a abrirla se puede **Indexar** sin revisar de nuevo.

De paso, los batches que una revisión sin indexar dejaba tomados se sueltan de verdad al cerrar el programa. El cierre tenía que pedirlo y nunca lo hacía, así que el batch quedaba bloqueado en AirVault —sin error, colgando a quien lo abriera después— hasta que alguien lo reclamaba.

## 2026-08-19 — El batch de AirVault se suelta al terminar y la sesión se renueva sola

Indexar dejaba el batch tomado. Abrirlo lo bloquea a nombre de quien lo abre, y el programa nunca lo soltaba: AirVault admite un solo dueño por batch, así que a partir de la primera ejecución cualquier apertura posterior —la del programa la vez siguiente, o la de la persona que entra por el navegador— se quedaba esperando sin respuesta, porque el servidor no contesta ni da error cuando el batch está tomado. Después de tres minutos aparecía un tiempo agotado que además culpaba al navegador de un candado que había dejado el propio programa. El batch de «Revisar» era el peor caso: es el que alguien tiene que indexar a mano, y quedaba bloqueado sin que nadie escribiera en él.

Ahora el batch se suelta siempre: al terminar, al cancelar, cuando algo se corta a medias y también cuando la ventana se cierra sin llegar a indexar. El de «Revisar» se suelta en cuanto se planifica. Y cuando una apertura se queda esperando de todos modos, el programa pregunta al listado quién lo tiene tomado y lo dice con nombre, en vez de dejar una espera sin explicación.

La sesión guardada en el perfil de Edge tampoco tenía salida cuando dejaba de valer. La cookie seguía ahí y con la forma correcta, así que se daba por buena, la primera petición moría y el mensaje mandaba a copiar una cookie con F12: el camino largo, y encima el que el perfil venía a evitar. Ahora el programa vuelve a abrir la ventana para entrar, que es lo que haría una persona.

Sigue siendo un perfil propio dentro de `portable/` y no el Edge de siempre, aunque ahí la sesión ya esté abierta. No es una preferencia: el navegador ignora a propósito el puerto de depuración cuando el perfil es el de por defecto, y si Edge ya está abierto el arranque nuevo le pasa la orden al que corre y se va, sin dejar puerto al que conectarse. Un WebDriver termina en el mismo sitio, porque también maneja un navegador que arranca él. Lo que sí se arregló es que entrar sea una sola vez de verdad: la cookie de federación es de sesión, y Chromium solo la guarda en disco cuando el perfil arranca restaurando la sesión anterior.

Los motivos que se muestran cambian de tono. Un campo obligatorio vacío se nombra como se llama en la pantalla de AirVault —Aircraft, Fleet, Log Page Number— y no por su número interno. Si el batch y la ejecución no tienen las mismas páginas, el aviso dice cuántas faltan o sobran y cuál es la causa habitual. Un rechazo del servidor dice qué se pedía y qué contestó, y ya no arrastra el batch entero: un 404 de una página frena esa página, no las cuatrocientas. Y si Edge no arranca, el mensaje incluye lo que dijo Edge, que antes se tiraba.

## 2026-08-19 — La sesión de AirVault se resuelve sola

Indexar empezaba por un trámite a mano: entrar a AirVault en el navegador, abrir las herramientas de desarrollo, copiar la cookie de sesión y pegarla en el campo **Sesión**. Cada vez que se abría el programa, otra vez. El atajo que iba a evitarlo —leer la cookie del perfil de Edge— casi nunca funcionaba: hay que cerrar Edge para que suelte su base de cookies, y un Edge moderno las cifra con la identidad del navegador (`v20`), que no se deshace desde fuera.

Ahora el programa abre Edge él mismo, con un perfil propio dentro de `portable/`, apuntando al enlace de acceso federado. La primera vez se ve la ventana y alguien entra con su usuario de Microsoft y su segundo factor; en cuanto AirVault suelta sus cookies, la ventana se cierra sola. De ahí en adelante el perfil conserva la sesión, así que el navegador se abre sin ventana, entrega la cookie y se cierra. El campo **Sesión** sigue ahí, vacío, como respaldo por si eso falla.

El segundo factor lo sigue haciendo una persona: existe justamente para eso. Lo que se automatizó es lo de alrededor —encontrar la cookie, copiarla y volver a pegarla cada vez—, que no protegía nada.

Las cookies se le piden al navegador por su protocolo de depuración, no leyendo su archivo. El navegador sí sabe descifrar las suyas y por ese camino las entrega en claro, así que no hay ningún cifrado que rodear. No se instala ni se descarga nada: Edge ya viene con Windows y el perfil es una carpeta más de `portable/`, que viaja con el programa.

Se entra por el enlace federado y no por la raíz del sitio, que es el que dispara la redirección a Microsoft. Por la raíz la sesión queda a medias, con `ASP.NET_SessionId` pero sin la cookie que autentica; esa cookie sola dejó de contar como sesión abierta, porque la pone el servidor al primer contacto, antes de saber quién eres, y darla por buena arrancaba un batch que moría en la primera página.

## 2026-08-19 — Depurar duplicados y páginas en blanco desde donde se ve la ejecución

Las páginas repetidas y las que salen en blanco se conocían pero no se podían quitar en bloque. El contador **Duplicados** de la ventana principal decía cuántas eran y en qué bitácoras, y el resumen del procesamiento contaba las vacías, pero sacarlas de la ejecución era ir al visor de CSV, buscarlas una por una en la tabla y borrarlas con `Supr`. En una ejecución de 884 páginas con una veintena de repetidas eso son veinte búsquedas.

Los dos apartados que muestran a la vez el CSV y su PDF —la ventana principal y el visor de CSV— ganan un botón **Depurar**, junto al de **Exportar**. El cuadro dice cuántas páginas hay de cada clase antes de borrar nada, y solo quita las que se marquen. Se reescriben el CSV mínimo, el CSV completo, el JSON y `stats.json` sin ellas.

Duplicada es toda aparición posterior de un mismo `log_number`; la primera se conserva, que es la que se entrega, y una lectura que no tenga siete dígitos no cuenta como repetida. En blanco es la que el procesamiento marcó como vacía. Una página que sea las dos cosas se elimina una sola vez, aunque cada casilla la cuente en su total: cada número responde a «cuántas quita esta casilla».

El criterio vive en un solo sitio, `app/validation/depuracion.py`, y el cuadro en otro, para que las dos ventanas no puedan entender por «duplicada» dos cosas distintas.

Los PDF no se rehacen al depurar. Son la entrega y se componen al exportar, cuando ya no queda nada más que quitar; hacerlo antes dejaría dos entregas distintas de la misma ejecución en la carpeta. En la ventana principal el botón espera a que la ejecución esté guardada —sin su carpeta, la reescritura crearía una segunda entrega de lo mismo— y a que no haya ninguna escritura en curso. Ninguna de las dos ventanas deja la ejecución sin páginas: para deshacerse de ella entera se borra su carpeta de `output/`.

## 2026-08-19 — Indexado en AirVault desde la ventana, y los PDF se generan al exportar

### Sección «Indexar en AirVault»

Escribir los índices de un batch era teclear en el Web Index de AirVault entre 300 y 500 páginas a mano, comprobando matrícula, número de bitácora y fecha una por una, con todos los datos ya leídos y guardados en el CSV de la ejecución.

La ventana principal gana una sección desplegable, junto a «Opciones avanzadas», que hace ese recorrido: sube el PDF de la ejecución, espera a que el batch aparezca en AirVault, calcula qué escribiría en cada página y —solo después de que alguien mire el reporte— lo escribe. El avance sale por la barra y la etiqueta de estado que ya existían; no se añadieron indicadores.

Su flecha comparte fila con la de «Opciones avanzadas». Apilada debajo le costaba 15 px de alto a la ventana, que en una pantalla de 1024x768 se abría fuera del escritorio.

La misma función existe en `run_airvault.py` para la línea de comandos.

### Los separadores del PDF ya no cuentan como bitácoras

El indexado emparejaba el CSV con el batch por posición, dando por hecho que la página *n* del batch era la bitácora *n* del CSV. El PDF de entrega no cumple eso: entre las secciones lleva páginas divisorias —la matrícula o el mes de cada grupo, **POSIBLES DISCREPANCIAS**, **REVISAR**— que el CSV no tiene y que en AirVault ocupan una página igual que cualquier otra. Con un solo separador delante, todo lo que iba detrás se habría escrito una página corrido: la bitácora de la página 40 indexada con los datos de la 39.

La exportación pasa a escribir junto al CSV un índice, `<ejecución>_paginas.json`, que declara qué hay en cada página del PDF. Ese archivo, y no el CSV, fija el orden del manifiesto. Los separadores entran como registros propios —así la correspondencia por posición se sostiene— y no se les escribe nada: ni se leen del servidor, ni cuentan como omitidos, ni se espera que queden en `Valid` al verificar.

Una ejecución exportada antes de que existiera el índice sigue el orden del CSV y se avisa; si aquel PDF llevaba separadores, la guarda de cantidad detiene el trabajo antes de escribir nada.

### La sesión de AirVault sale del navegador

El acceso está federado con Microsoft Entra ID y pide segundo factor, que no se completa desde un script, así que el formulario de usuario y contraseña no servía para la cuenta con la que se trabaja.

La sesión se reutiliza del navegador: la cookie que se pega, o la del perfil de Edge cuando se deja leer. La cookie va al tarro de peticiones y no a una cabecera fija, porque el primer `Set-Cookie` del servidor se habría comido la puesta a mano y el batch habría muerto a media escritura. Antes de empezar se comprueba la sesión con una petición, para no descubrir en la página 250 de 400 que había caducado.

El atajo de Edge sirve poco en la práctica: hay que cerrar Edge para que suelte su base de cookies, y un Edge moderno las cifra con la identidad del navegador (`v20`), que no se deshace desde fuera. Cuando no se puede, se dice por qué y se sigue con la cookie pegada.

### Los PDF se generan al exportar, no al procesar

Terminado el OCR, el programa generaba siempre los PDF de entrega. Componerlos vuelve a abrir cada original y tarda, y lo pagaba también quien iba a cambiar la separación y a exportar otra vez de todos modos.

**Procesar** guarda ahora los datos —CSV, JSON y estadísticas— y nada más. La entrega se arma al pulsar **Exportar**, con la separación marcada en ese momento. Los archivos de entrada se siguen apartando a `input/processed/` al terminar, y la ventana reapunta sus resultados allí, así que exportar después encuentra las páginas originales.

## 2026-08-19 — Nombre de batch definitivo y las bitácoras sin avión en su propio batch

### El nombre

Los batches se llamaban `DP | BIT 18 AUG 2026 05 42`, y al repartir la entrega las partes salían como `(1 de 5)`. Pasan a llamarse **`DP | BITS 18 AUG 2026 05 42`**, con S, enteros en mayúsculas, y las partes con sufijo `-1`, `-2`. El sufijo del batch es el mismo que lleva su archivo, y una prueba comprueba que no se separen: si lo hicieran, el batch dejaría de poder emparejarse con el PDF que lo formó.

La marca de tiempo es la del **procesamiento**. Ya salía del nombre de la carpeta de la ejecución, pero cuando esa carpeta no lo llevaba se caía a la hora actual, que es la de la subida y no dice nada de la bitácora. Ahora se toma la hora del propio archivo, que sigue siendo la del procesamiento; la hora actual solo aparece si no hay ni archivo que mirar.

### Las bitácoras sin avión confirmado, en su propio batch

Las páginas cuya matrícula nadie pudo confirmar cerraban el PDF de entrega bajo el separador **REVISAR**, así que caían dentro del batch grande. Allí el indexado las bloqueaba —sin avión no hay dónde archivarlas— y se quedaban en medio de cuatrocientas páginas, donde nadie las encontraba.

Salen ahora en su propio archivo y, por tanto, en su propio batch: `DP | BITS 18 AUG 2026 05 42 REVISAR`. Ese batch **se sube y no se toca**: el indexado no le lee ni le escribe ninguna página, y queda marcado en la cola del Web Index para resolverlo a mano. No se numera como una parte más —no es «una de cinco», es el que queda aparte— y su manifiesto vive en `output/airvault/<ejecución>/revisar/`.

En la ejecución de referencia son 17 páginas de 884.

## 2026-08-19 — La entrega se reparte en batches, y el indexado aguanta que la red falle

### Repartir la ejecución en varios batches

Una ejecución completa —unas 900 páginas y casi dos gigas— formaba un solo batch en AirVault: incómodo de revisar, y una subida de ~1850 peticiones que si se cortaba había que rehacer entera.

La casilla **Repartir en** del cuadro «Salidas», o `--paginas-por-parte N` en la línea de comandos, escribe la entrega en varios PDF de a lo sumo esas páginas. Cada archivo es un batch propio en AirVault, con su nombre —`DP | BIT 18 AUG 2026 05 42 (2 de 5)`—, su manifiesto y sus guardas; una parte que se corte no arrastra a las demás y al volver a revisar se retoma solo lo que falta. El reporte de revisión sigue siendo uno solo para toda la ejecución.

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

## 2026-08-18 — Indexación automática: vuelo, fechas, matrículas sin confirmar y sección «Revisar»

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

El estado representaba el peor campo de la página, de modo que una firma de técnico ausente —lo normal en una bitácora de vuelo— dejaba la página en `ERROR`. Con la verificación de matrículas activa el estado se recalculaba además sobre las casillas sueltas de la fecha, así que activar esa opción convertía en error a páginas que nadie había tocado. La ejecución de referencia daba 689 páginas en `ERROR` sobre 884.

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

Revisados uno a uno los recortes de las 89 lecturas que seguían quedando vacías, aparecen dos palabras más escritas con claridad —`SUP`, en dos páginas, y `MTC`— y una constante: el reconocedor devuelve la P de `SPV` como `9`, `D`, `R` o `2`, y la T de `TCK` como `J`. Comparar el código letra a letra dejaba fuera `S9V`, `SDV`, `SRV`, `52V` y `JCK`, que en la página dicen `SPV` y `TCK`.

La comparación pasa a hacerse por clase de trazo sobre la lectura entera, con un solo trazo de diferencia admitido y con preferencia por el código de la misma longitud. No se afloja la distancia, porque a dos trazos `ZCC` —que es un `700` manuscrito— se confundiría con `CCK`.

Resultado: 782 páginas con vuelo y 91 códigos reconocidos (`TCK` 44, `SPV` 27, `SV` y sus variantes numeradas 14, `SVC` 4, `MTC` y `SUP` 1 cada uno).
