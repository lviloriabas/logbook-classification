# Indexado automatico en AirVault

Toma el CSV que ya produce una corrida de clasificacion y escribe esos
valores en las paginas del lote correspondiente del Web Index de AirVault,
sin que nadie tenga que teclear pagina por pagina.

Se opera desde la ventana principal o desde la linea de comandos. Las dos
recorren las mismas etapas y comparten las mismas guardas.

## Desde la ventana

La seccion **Indexar en AirVault** cuelga de la fila de «Opciones
avanzadas», cerrada hasta que se despliega. Trabaja en dos tiempos:

| Control | Que hace |
|---|---|
| `Corrida:` | CSV de la corrida. Se rellena solo con la que acaba de exportarse; con `Buscar…` se elige otra. |
| `Lote:` | Nombre con el que el lote queda en AirVault. Viene propuesto con la fecha y la hora de la corrida. |
| `Sesion:` | Cookie de AirVault copiada del navegador. No se guarda en el disco. |
| `Subir y revisar` | Sube el PDF, espera a que aparezca el lote y calcula que se escribiria. No indexa nada. |
| `Indexar` | Escribe. Solo se habilita cuando ya hay una revision que mirar. |
| `Ver reporte…` | Abre el detalle pagina por pagina. |

El avance sale por la barra y la etiqueta de estado de la ventana, las
mismas que usa el procesamiento.

La corrida tiene que estar exportada con la salida en **un solo PDF**. Ese
archivo puede venir repartido en partes (ver más abajo): cada parte es un
lote distinto y la sección los recorre todos.

## Etapas

Cada etapa se corre sola o todas de corrido. El estado vive en el
manifiesto del trabajo (`output/airvault/<job>/manifiesto.json`), asi que
se puede procesar hoy, subir manana e indexar despues sin repetir nada.

| Etapa | Que hace |
|---|---|
| `preparar` | Arma el manifiesto a partir del CSV y del indice de paginas (un manifiesto por parte) |
| `subir` | Sube los PDFs por Quick Upload (opcional: se puede subir a mano) |
| `descubrir` | Ubica el lote en AirVault por su nombre |
| `plan` | Dry run: calcula todo, escribe el reporte y no toca nada |
| `indexar` | Escribe los indices |
| `verificar` | Relee el lote y confirma como quedo |
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
  tocar el lote.
- **`--auto`**: escribe sin detenerse.

El reporte es el mismo artefacto en los tres modos, asi que lo que se
aprueba es exactamente lo que se envia.

## Repartir en varios lotes

Una corrida completa son unas 900 páginas y casi dos gigas. Eso en AirVault
es un solo lote: incómodo de revisar, y una subida que si se corta hay que
rehacer entera.

Marcando **Repartir en** en el cuadro «Salidas» —o `--paginas-por-parte N`
en la línea de comandos— la entrega se escribe en varios PDF de a lo sumo
esas páginas:

```
BITS 18 AUG 2026 05 42 (1 de 5).pdf
BITS 18 AUG 2026 05 42 (2 de 5).pdf
...
```

Cada archivo es un lote propio en AirVault, con su nombre —`DP | BIT 18 AUG
2026 05 42 (2 de 5)`—, su manifiesto en `output/airvault/<corrida>/parte-02/`
y sus guardas. Una parte que falle o se corte no arrastra a las demás, y al
volver a revisar se retoma solo lo que falta.

El corte se hace **entre secciones** siempre que se pueda, para no separar
en dos lotes las bitácoras de un mismo avión. Cuando un avión tiene por sí
solo más páginas que el tope, se parte y la continuación vuelve a abrir con
su separador, de modo que ninguna parte empieza con bitácoras sueltas.

El reporte de revisión sigue siendo uno solo para toda la corrida: se
aprueba de una vez y no lote por lote.

## Separadores del PDF

El PDF de entrega no es solo bitacoras: entre las secciones lleva paginas
divisorias —la matricula o el mes de cada grupo, `POSIBLES DISCREPANCIAS`,
`REVISAR`— que el CSV no tiene. En AirVault cada una ocupa una pagina del
lote igual que cualquier otra.

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

Una corrida exportada antes de que existiera el indice no lo tiene. En ese
caso se sigue el orden del CSV y se avisa; si aquel PDF llevaba
separadores, la guarda de cantidad detiene el trabajo antes de escribir
nada.

## Guardas

El indexado se niega a escribir si algo no cuadra. Estan todas juntas en
`app/airvault/guards.py` y se ejecutan igual en dry run que en automatico:

1. El lote y el manifiesto tienen que tener la misma cantidad de paginas,
   contando los separadores.
2. Toda matricula debe existir en el picklist de AirVault.
3. Si AirVault ya trae un log number en esa pagina, tiene que coincidir con
   el del manifiesto. Es el mejor ancla de alineacion que existe.
4. Una pagina ya validada no se pisa salvo con `--sobrescribir`.
5. Ningun campo obligatorio puede quedar vacio.

Una pagina que falle cualquiera de estas queda marcada como bloqueada en el
reporte y no se escribe; el resto del lote sigue. La primera guarda es la
unica que corta el trabajo entero, porque si sobran o faltan paginas la
correspondencia por posicion esta rota y cualquier escritura caeria en la
bitacora de al lado.

## Campos

De los veinte campos del panel, el sistema controla seis y deja el resto
intacto. Lo que no se manda, AirVault lo conserva, asi que un indexado no
pisa lo que alguien haya puesto a mano.

| Campo | Origen |
|---|---|
| Doc Type | valor del trabajo (`airvault.json`) |
| Aircraft | columna `matricula` del CSV |
| Fleet | se deduce de la matricula |
| Log Page Number | columna `log_number` del CSV |
| Audit Status | valor del trabajo |
| End Date | columna `date` del CSV, convertida a `MM/DD/YYYY` |

**La flota.** AirVault la resuelve con un procedimiento almacenado a partir
de la matricula, pero ese lookup lo dispara la interfaz al escribir el
campo, no el servidor al guardar. Por eso el modulo la resuelve por su
cuenta con tres niveles: primero el cache local
(`airvault_flota.json`), que se alimenta solo de lo que AirVault ya tiene
indexado en el propio lote; si no hay dato, una regla de prefijos de
respaldo, y en ese caso la bitacora queda marcada como `fleet_inferido` en
el reporte para que alguien la confirme.

## Autenticacion

El programa es portable, asi que no hay navegador ni dependencias nuevas:
la sesion se resuelve con `requests` y la libreria estandar.

**Este acceso esta federado con Microsoft Entra ID**
(`login.microsoftonline.com/9767f0dc-.../wsfed`), asi que el camino
realista es la cookie de sesion, no el usuario y la contrasena: un login
federado con segundo factor no se puede completar desde un script.

La sesion se toma de la primera fuente disponible, en este orden:

1. La cookie que se pasa a mano: el campo `Sesion:` de la ventana,
   `--cookie "..."` en cualquier subcomando, o la variable de entorno.

   ```batch
   set AIRVAULT_COOKIE=FedAuth=...; FedAuth1=...
   ```

2. La misma cookie leida del perfil de Edge, para no tener que copiarla.
   Es un atajo con dos condiciones que hoy casi nunca se cumplen: **Edge
   tiene que estar cerrado**, porque mientras corre no suelta su base de
   cookies, y las cookies no pueden ir cifradas con la identidad del
   navegador (prefijo `v20`, la clave `app_bound_encrypted_key`), que es lo
   que hace un Edge moderno. Cuando no se puede, se dice por que y se sigue
   con la cookie pegada; nunca se intenta rodear ese cifrado. Con
   `--sin-edge` no se mira el navegador.

3. El formulario propio de AirVault (`AIRVAULT_USER` / `AIRVAULT_PASSWORD`,
   o `--usuario`), que solo sirve para las cuentas locales que no pasan por
   Entra ID.

La cookie va al tarro de peticiones y no a una cabecera fija: en cuanto el
servidor devuelve su primera cookie, `requests` reconstruye la cabecera
desde el tarro y se comeria cualquier valor puesto a mano, dejando el lote
a medio escribir.

Ni las cookies ni las contrasenas se guardan en disco ni se escriben en el
log: de una cookie solo se registra el nombre y cuanto mide.

Antes de empezar se comprueba la sesion con una peticion, para no descubrir
en la pagina 250 de 400 que habia caducado. Si caduca a mitad, se detecta
por la redireccion a `/signin2/` o a `login.microsoftonline.com` y se dice,
en vez de fallar en silencio.

## Subida

`subir` usa Quick Upload, que crea el lote y lo deja en la cola de Web
Index. Hay una limitacion del lado de AirVault: ese modulo solo expone diez
campos y entre ellos **no** estan Log Page Number, Fleet ni End Date. Por
eso la subida deja el lote clasificado pero no indexado, y el indexado real
lo hace `indexar` despues. Si el administrador habilita esos campos para
Quick Upload, la subida podria cerrarlo todo de una vez.

La etapa esta desacoplada a proposito: si el lote se sube a mano, se salta
`subir` y se arranca en `descubrir`.

## Nombre del lote

El nombre es lo unico que el sistema y AirVault comparten para reconocer un
lote, asi que se arma solo, con el prefijo mas la marca de tiempo de la
corrida, en el mismo formato que ya usa el nombre del CSV:

```
DP | BIT 18 AUG 2026 05 42
```

`preparar` lo deduce de la ruta del CSV, de modo que el lote se llama igual
que la corrida que lo produjo y los dos se cruzan de un vistazo. Con
`--prefijo` se cambia el prefijo y con `--lote` se fija el nombre completo a
mano.

**El lote hay que subirlo a AirVault con ese mismo nombre.** Es lo que
`descubrir` va a buscar.

La marca de tiempo no es decoracion. El filtro "Filter by" de AirVault es
una coincidencia de subcadena sin distinguir mayusculas, asi que escribir
`DP | BIT` devuelve hoy 22 lotes: los `DP | BITS VARIAS`, los
`DP | Bitacoras varias` y los `DP | BIT Mix`. Y entre ellos hay nombres
repetidos, dos `DP | BIT Mix | Viernes 14 AUG` y dos
`DP | BIT Mix 5 | Viernes 14 AUG`, con los que no habria forma de saber en
cual escribir. La marca de tiempo los separa.

## Deteccion del lote

`descubrir` manda `DP | BIT ...` como filtro al servidor, el mismo que
aplica la caja "Filter by" de la pantalla, y despues compara el nombre
completo sin distinguir mayusculas ni separadores.
Contempla que Quick Upload deja el nombre como `<lote> - usuario@dominio`.
Si hay mas de un candidato desempata por cantidad de paginas, y si aun asi
queda mas de uno se detiene y pide el batch id a mano: escribir en el lote
equivocado es peor que preguntar.

Con `--esperar` sondea hasta que el lote aparezca, porque un lote recien
subido tarda en pasar por el procesamiento del servidor.

## Cuando algo falla

Un lote son cientos de peticiones y una subida completa casi dos mil. A esa
escala los tropiezos dejan de ser raros, así que cada uno tiene una
respuesta decidida de antemano.

| Qué pasa | Qué hace el indexado |
|---|---|
| Se corta la red, vence el tiempo o el servidor responde que está ocupado (408, 429, 5xx) | Reintenta, esperando más en cada intento. Por defecto tres intentos con 5 s, 10 s. |
| Se agotan los reintentos | Corta y dice qué pasó. Lo escrito queda anotado. |
| El servidor responde 404 o 403 | No reintenta: insistir devuelve lo mismo. |
| Una página del lote no carga | Bloquea **esa** página y sigue con el resto. Sin poder leerla no se puede comprobar que el lote y el manifiesto hablan de la misma bitácora, así que no se escribe. |
| Caduca la cookie a media escritura | Corta el lote entero. Lo que no se llegó a intentar queda **pendiente**, no fallido: al volver a revisar se retoma sin repetir lo escrito. |
| Falla el guardado de una página concreta | Se marca esa página con el motivo. Con `--continuar-con-errores` el resto del lote sigue. |
| Un trozo de la subida se pierde | Se reenvía ese trozo. Reenviar el mismo índice es inocuo: el servidor arma el archivo por posición. |

La petición que abre el lote merece mención aparte: si el lote está abierto
en el navegador, AirVault **no contesta y no da error**, deja la petición
esperando. Por eso todas las peticiones llevan tiempo límite y el mensaje
lo dice: hay que cerrar el lote en el navegador antes de indexarlo.

La comprobación de sesión se hace antes de empezar, no a mitad: descubrir
en la página 250 de 400 que la cookie había caducado cuesta mucho más que
descubrirlo al principio.

## Reanudacion

El manifiesto se guarda despues de cada pagina. Si el proceso se corta, al
volver a correr `indexar` las paginas ya escritas se saltan y se sigue
desde donde quedo. Las que fallaron quedan con el motivo anotado.

## Prueba de punta a punta

Antes de soltar el indexado sobre un lote real conviene probarlo con una
muestra. `tools/muestra_bitacoras.py` arma una corrida de prueba con unas
pocas bitácoras sacadas al azar de una corrida ya hecha:

```batch
portable\python312\tools\python.exe tools\muestra_bitacoras.py ^
    --csv "output\BITS 18 AUG 2026 05 42\datos\BITS 18 AUG 2026 05 42.CSV" ^
    --pdf "input\Image_001.pdf" ^
    --cuantas 20
```

Deja en `output\PRUEBA INDEXADO\` una carpeta con la misma forma que una
corrida de verdad —CSV, índice de páginas y PDF de entrega con sus
separadores—, porque la entrega la escribe la exportación real. Veinte
bitácoras de dieciséis aviones distintos salen en unas 36 páginas y 40 MB.

La semilla se imprime al terminar: con `--semilla N` se repite exactamente
la misma muestra. Con `--paginas-por-parte` se prueba además el reparto en
varios lotes.

Después:

1. Apuntar la sección de AirVault a `output\PRUEBA INDEXADO\datos\PRUEBA INDEXADO.CSV`.
2. **Subir y revisar**, y mirar `revision.html`.
3. **Indexar**, y comprobar en el Web Index que las páginas separadoras
   quedaron sin tocar y las bitácoras con sus datos.

Nada de esto se commitea: `output/` está fuera del repositorio y los PDF de
entrada pesan cientos de megas.

## Tests

```batch
portable\python312\tools\python.exe -m pytest tests -k airvault
```

Todo el recorrido se prueba contra un cliente falso
(`tests/airvault_fake.py`) que guarda en memoria lo que se le escribe, de
modo que los tests afirman exactamente que paginas se tocaron y con que
valores, sin tocar produccion.
