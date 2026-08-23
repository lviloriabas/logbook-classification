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

| Control | Que hace |
|---|---|
| Historial | Ultimas 25 ejecuciones procesadas, de la mas reciente a la mas antigua, con sus paginas y lo que tienen para subir. Viene señalada la exportada mas reciente. |
| `Ejecucion:` | CSV de la ejecucion elegida arriba. No se teclea. |
| `Otra ejecucion…` | Elige el CSV de una ejecucion que no este en la lista. |
| `Batch:` | Nombre con el que el batch queda en AirVault. Viene propuesto con la fecha y la hora de la ejecucion. |
| `Sesion:` | Respaldo, normalmente vacio: la sesion la resuelve el navegador. Lo que se pegue aqui no se guarda en el disco. |
| **Batches en AirVault** | Una fila por batch de esta ejecucion, con sus paginas y en que va. Es donde se ve cual ya se puede indexar. |
| `Comprobar cada N min` | Le pregunta solo a AirVault, sin que nadie pulse. Viene marcado, cada 5 minutos, y deja de preguntar cuando no queda nada por esperar. |
| `Comprobar ahora` | La misma pregunta, en el momento. |
| `Subir a AirVault` | Manda los PDF de la entrega. Termina cuando termina la subida. |
| `Completar batch` | Al terminar de escribir, da el batch por terminado en AirVault (ver mas abajo). Sin marcar, el batch se queda en la cola para revisarlo. |
| `Indexar` | Escribe en los batches que ya estan listos. |
| `Ver reporte…` | Abre el detalle pagina por pagina de lo que se escribiria. |
| `Cancelar` | Detiene lo que este en marcha y suelta los batches tomados. |

### Los tres tiempos

1. **Subir y ubicar.** Manda un archivo y espera a que aparezca y quede
   nombrado antes de mandar el siguiente. Esa barrera es necesaria porque
   AirVault junta en un mismo batch los archivos que le llegan seguidos.
2. **Esperar a AirVault.** El batch entra en la cola del servidor y tarda en
   quedar indexable: aparece antes de tener todas sus paginas. Mientras le
   falte alguna no esta listo, porque escribir con las paginas corridas
   dejaria cada dato en la bitacora de al lado. La ventana pregunta cada
   cinco minutos —o cuando se pulse **Comprobar ahora**— y va pasando los
   batches a **Listo para indexar** segun quedan. Cuando ya no queda nada que
   esperar deja de preguntar sola.
3. **Indexar.** Con la automatizacion inicial, en cuanto un batch aparece
   entero se asigna su ID en la tabla y empieza a escribirse en un carril
   paralelo, mientras la subida sigue buscando las partes restantes. Si se
   desmarca **Indexar paginas**, solo se calcula el plan y se espera la
   aprobacion manual.

Estados que puede tener un batch en la lista:

| Estado | Que significa |
|---|---|
| Sin subir | Todavia no se ha mandado |
| Subido pendiente confirmación | Mandado, pero el servidor aun no lo saca en la cola |
| Procesandose en AirVault | Ya esta en la cola, con menos paginas de las que lleva el PDF |
| Cantidad de paginas incorrecta | Tiene mas paginas de las posibles; se detiene porque AirVault junto cargas o el PDF no corresponde al indice |
| Listo para indexar | Entero y libre: se puede escribir |
| Abierto por otra persona | Alguien lo tiene tomado; AirVault no lo entrega a nadie mas |
| Para revisar a mano | Es el batch REVISAR, que no se indexa |
| Indexado / Terminado | Ya escrito, y cerrado si se pidio |

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

| Etapa | Que hace |
|---|---|
| `preparar` | Arma el manifiesto a partir del CSV y del indice de paginas (uno por parte, y otro para REVISAR) |
| `subir` | Sube los PDFs por Quick Upload (opcional: se puede subir a mano) |
| `descubrir` | Ubica el batch en AirVault por su nombre |
| `plan` | Dry run: calcula todo, escribe el reporte y no toca nada |
| `indexar` | Escribe los indices |
| `verificar` | Relee el batch y confirma como quedo |
| `completar` | Da el batch por terminado en AirVault, si lo acepta (`indexar --completar`) |
| `todo` | Descubrir, indexar y verificar de corrido |

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
la pagina a AirVault.

## Repartir en varios batches

Una ejecución completa son unas 900 páginas y casi dos gigas. Eso en AirVault
es un solo batch: incómodo de revisar, y una subida que si se corta hay que
rehacer entera.

Marcando **Repartir en** en el cuadro «Salidas» —o `--paginas-por-parte N`
en la línea de comandos— la entrega se escribe en varios PDF de a lo sumo
esas páginas:

```
BITS 18 AUG 2026 05 42 (1 de 5).pdf
BITS 18 AUG 2026 05 42 (2 de 5).pdf
...
```

Cada archivo es un batch propio en AirVault, con su nombre —`DP | BIT 18 AUG
2026 05 42 (2 de 5)`—, su manifiesto en `output/airvault/<corrida>/parte-02/`
y sus guardas. Una parte que falle o se corte no arrastra a las demás, y al
volver a revisar se retoma solo lo que falta.

El corte se hace **entre secciones** siempre que se pueda, para no separar
en dos batches las bitácoras de un mismo avión. Cuando un avión tiene por sí
solo más páginas que el tope, se parte y la continuación vuelve a abrir con
su separador, de modo que ninguna parte empieza con bitácoras sueltas.

El reporte de revisión sigue siendo uno solo para toda la ejecución: se
aprueba de una vez y no batch por batch.

La ventana **Indexar en AirVault** aplica además un máximo propio justo
antes de Quick Upload. Comparte la última cantidad elegida con **Repartir
en** y no define otro valor fijo en el código. Si un PDF exportado supera esa
preferencia guardada, se copia en
tramos consecutivos dentro de `output/airvault/<corrida>/cargas/`; el PDF de
la entrega, su índice y el CSV no se modifican. El mismo límite protege al
batch `REVISAR`; si hace falta más de uno, se nombran `REVISAR -1`,
`REVISAR -2`, etc.

Los batches automáticos se numeran `-1`, `-2`, etc. La correspondencia con el
tramo que se subió queda en cada manifiesto y en el índice de páginas; no
depende del nombre interno del PDF de carga.

Quick Upload publica inicialmente el archivo como `Empty-Batch`. El programa
lee el campo `Batch Name` dentro de la primera página, lo combina con la
instantánea previa, el repositorio y la cantidad de páginas, renombra el ID y
vuelve a consultar la cola. Incluso varios `Empty-Batch` del mismo tamaño se
resuelven así sin intervención. Mientras ese mismo ID no tenga el título
esperado, no indexa ni envía el siguiente PDF; al confirmarlo continúa solo
con las cargas pendientes. Si una carga sigue ausente 30 minutos, la
consulta automática la reenvía una sola vez y vuelve a exigir esa confirmación;
después continúa consultando sin volver a duplicarla.

Cada ejecución activa usa su propia ventana y su propio hilo. Elegir otra
ejecución durante una subida abre una segunda ventana y ambas continúan.

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
divisorias —la matricula o el mes de cada grupo, `POSIBLES DISCREPANCIAS`,
`REVISAR`— que el CSV no tiene. En AirVault cada una ocupa una pagina del
batch igual que cualquier otra.

Contarlas mal no deja un hueco: desplaza todo lo que va detras, y la
bitacora de la pagina 40 terminaria indexada con los datos de la 39.

Por eso la exportacion escribe junto al CSV un indice de paginas,
`<corrida>_paginas.json`, que declara que hay en cada pagina del PDF:

```json
{"version": 1, "pdf": "BITS 18 AUG 2026 05 42.pdf", "paginas": [
  {"separador": "HP-1848CMP"},
  {"archivo": "Image_001.pdf", "pagina": 12},
  {"separador": "REVISAR"}
]}
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

## Campos

De los veinte campos del panel, el sistema controla seis fijos y dos mas
cuando hay dato, y deja el resto intacto. Lo que no se manda, AirVault lo
conserva, asi que un indexado no pisa lo que alguien haya puesto a mano.

| Campo | Origen |
|---|---|
| Doc Type | valor del trabajo (`airvault.json`) |
| Aircraft | columna `matricula` del CSV |
| Fleet | se deduce de la matricula |
| Log Page Number | columna `log_number` del CSV |
| Audit Status | valor del trabajo |
| End Date | columna `date` del CSV en `MM/DD/YYYY`; si no se leyo, se deduce del libro |
| Description | `<flight_number> AUTO INDEX`, o solo `AUTO INDEX` si no se leyó vuelo |
| Lessor | del cache de flota, solo si lo trae |

**El vuelo.** `Description` lleva el vuelo de esa bitacora, pagina por
pagina: un vuelo numerado (`703`, `CM137`) o un codigo de mantenimiento
(`TCK`, `SPV`), seguido por la marca `AUTO INDEX`. Cuando no se pudo leer
el vuelo, `Description` lleva solamente `AUTO INDEX`. La marca se agrega
solo al payload que se guarda en AirVault: el CSV y el reporte de revision
conservan el vuelo tal como lo dejo la lectura. Es un campo por pagina, no
del batch: el batch no lo lleva —Quick Upload ni siquiera expone
`Description`—.

**La fecha.** `End Date` es obligatorio: una bitacora sin fecha deja su
pagina bloqueada, y basta una para que el batch no se pueda cerrar. Cuando la
lectura no dejo fecha pero si el log number —que es el que ordena el libro—
se deduce con las mismas reglas que el corrector de fechas del
procesamiento, de la que mas evidencia tiene a la que menos:

| Que hay | Que fecha se pone |
|---|---|
| La misma bitacora repetida, y una de las dos si trae fecha | esa |
| Bitacoras fechadas antes y despues en el libro | la de la mas cercana; en un empate, la posterior |
| Solo fechadas antes | el ultimo dia de ese mes |
| Solo fechadas despues | la de la primera de ellas |
| Ninguna en el libro | el ultimo dia del mes dominante del avion |
| El avion entero sin fechas | el ultimo dia del mes dominante de la ejecucion |

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
guarda en disco las cookies **de sesion** —y la de federacion lo es— cuando
el perfil arranca restaurando la sesion anterior. Sin esa bandera habria
que entrar con segundo factor en cada ejecución.

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
sola abre la sesion y ninguna de las otras seis —`ProdSSO`, `ProdSSO1`,
`AirVaultContext`, `SessionInfo_AV`, `Production-AirVaultAntiForgery`,
`ASP.NET_SessionId`— lo hace. Antes solo se reconocian los nombres
habituales de ASP.NET (`FedAuth`, `.ASPXAUTH`), que aqui no aparecen: el
programa esperaba cinco minutos con la sesion ya abierta delante y despues
acusaba a la ventana de haberse quedado en la pagina de Microsoft. Los dos
nombres de ASP.NET se siguen aceptando por si otra instalacion los usa.

Y no se toma la primera cookie que aparece, sino la primera que **sirve**:
recien abierto, el navegador todavia va y viene de Microsoft, y lo que hay
en el perfil en ese instante es lo de la vez anterior. Se prueba contra el
servidor hasta que una funciona. Eso es tambien lo que renueva la sesion
sola, sin ventana ni segundo factor: si AirVault contesta a mitad del
trabajo que hay que volver a entrar —un 401, o el 440 «Login Timeout» de
IIS, que llega con la pagina de error generica y antes se leia como un
rechazo del sitio—, se vuelve a leer el perfil y se repite la peticion.

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

**Entre una parte y la siguiente se espera a que la anterior aparezca en
la cola.** AirVault junta en un mismo batch los archivos que le llegan
seguidos: subiendo la entrega y la parte de Revisar una detras de otra
quedaron las dos en un solo batch de 33 paginas, y dos partes en el mismo
batch no se pueden indexar por separado, que es justo para lo que se
reparten.

La espera solo llega hasta que cada archivo tiene un ID inequívoco y un
nombre. Que AirVault haya terminado de procesar todas sus paginas es otra
cosa; eso puede tardar mucho mas y se sigue comprobando mientras el carril
de indexado trabaja los batches que ya estan enteros.

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

**Quick Upload no admite nombre de batch.** Todo lo que sube el programa
llega a la cola como `Empty-Batch`, igual para todos; el valor del campo
`Batch Name` viaja en las paginas pero no nombra el batch. Asi que el nombre
se le pone **despues de encontrarlo**, con la misma accion «Rename» del Web
Index (`Batch/UpdateBatchName`). El nombre se confirma volviendo a leer el
mismo ID. Si el renombrado falla, no se indexa ese batch y se detienen las
siguientes subidas de esa ejecucion: continuar crearia mas `Empty-Batch`. El
programa reintenta el nombre automáticamente y, cuando lo confirma, reanuda
los PDF pendientes. Las otras ejecuciones abiertas conservan sus propios
hilos y no se detienen.

La marca de tiempo no es decoracion. El filtro "Filter by" de AirVault es
una coincidencia de subcadena sin distinguir mayusculas, asi que escribir
`DP | BIT` devuelve hoy 22 batches: los `DP | BITS VARIAS`, los
`DP | Bitacoras varias` y los `DP | BIT Mix`. Y entre ellos hay nombres
repetidos, dos `DP | BIT Mix | Viernes 14 AUG` y dos
`DP | BIT Mix 5 | Viernes 14 AUG`, con los que no habria forma de saber en
cual escribir. La marca de tiempo los separa.

## Deteccion del batch

Hay dos caminos, en este orden:

1. **Por nombre**, para un batch que alguien subio a mano poniendoselo.
   `descubrir` manda `DP | BIT ...` como filtro al servidor, el mismo que
   aplica la caja "Filter by" de la pantalla, y despues compara el nombre
   completo sin distinguir mayusculas ni separadores. Contempla el sufijo
   `<batch> - usuario@dominio` que dejan algunas subidas.

2. **Por lo que aparecio despues de subir**, que es el caso normal cuando
   sube el programa. Justo antes de subir se anota que batches habia en la
   cola; el que no estaba es el propio. Es exacto y no depende del nombre,
   que aqui no distingue nada.

En los dos, si hay mas de un candidato desempata por cantidad de paginas, y
si aun asi queda mas de uno se detiene y pide el batch id a mano: escribir
en el batch equivocado es peor que preguntar.

Con `--esperar` sondea hasta que el batch aparezca, porque un batch recien
subido tarda en pasar por el procesamiento del servidor.

## Cuando algo falla

Un batch son cientos de peticiones y una subida completa casi dos mil. A esa
escala los tropiezos dejan de ser raros, así que cada uno tiene una
respuesta decidida de antemano.

| Qué pasa | Qué hace el indexado |
|---|---|
| Se corta la red, vence el tiempo o el servidor responde que está ocupado (408, 429, 5xx) | Reintenta, esperando más en cada intento. Por defecto tres intentos con 5 s, 10 s. |
| Se agotan los reintentos | Corta y dice qué pasó. Lo escrito queda anotado. |
| El servidor responde 404 o 403 | No reintenta: insistir devuelve lo mismo. |
| Una página del batch no carga | Bloquea **esa** página y sigue con el resto. Sin poder leerla no se puede comprobar que el batch y el manifiesto hablan de la misma bitácora, así que no se escribe. |
| Caduca la cookie a media escritura | Corta el batch entero. Lo que no se llegó a intentar queda **pendiente**, no fallido: al volver a revisar se retoma sin repetir lo escrito. |
| Falla el guardado de una página concreta | Se marca esa página con el motivo. Con `--continuar-con-errores` el resto del batch sigue. |
| Un trozo de la subida se pierde | Se reenvía ese trozo. Reenviar el mismo índice es inocuo: el servidor arma el archivo por posición. |

### El batch se abre y se cierra

AirVault admite **un solo dueño por batch**. Abrirlo lo bloquea a nombre de
quien lo abre, y mientras siga bloqueado cualquier otra apertura —la del
programa la próxima vez, o la de la persona que lo abre en el navegador—
se queda esperando: el servidor **no contesta y no da error**. Por eso
todas las peticiones llevan tiempo límite.

De ahí que el batch se tome **lo menos posible**. Leerlo para calcular el
plan lo suelta en cuanto acaba, y escribirlo lo vuelve a tomar y lo suelta
al terminar. Antes se quedaba tomado entre revisar y escribir, y entre una
cosa y otra puede pasar un rato largo —o no pulsarse nunca **Indexar**—:
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
terminado —el boton «Complete» de la pantalla— lo saca de ahi y lo manda al
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
|---|---|---|
| 0 | Valid | si |
| 1 | No Template Match | no |
| 2 | Separator | no |
| 3 | Need Correction | no |

**Las divisorias del PDF cuentan.** Medido en el batch `003SUS`: sus trece
paginas separadoras quedaron en estado 1, «No Template Match», y para
AirVault eso pesa igual que una bitacora incompleta. Al indexar un batch
automatico, BITS las marca como borradas mediante la misma operacion de
AirVault que se ejecuta con `Ctrl+Supr`; no depende de completar el batch.
El batch `REVISAR` no se indexa automaticamente y conserva todas sus
paginas para que una persona lo resuelva.

Lo otro que bloquea son las bitacoras que quedaron en amarillo. Antes de
escribir, BITS exige todos los campos obligatorios; la fecha puede venir de
las reglas del libro, incluido el ultimo dia del mes. Despues relee el batch:
si AirVault aun deja una pagina en «Need Correction», no intenta completar
el batch y dice cual fue.

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

Deja `input\MUESTRA.pdf` con veinte páginas —unos 40 MB, frente a los
setecientos de un escaneo entero— y dice de dónde salió cada una. Es una
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
3. Esperar a que el batch pase a **Listo para indexar** —se comprueba solo
   cada cinco minutos— y mirar `revision.html`.
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
