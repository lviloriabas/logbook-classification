# Indexado automatico en AirVault

Toma el CSV que ya produce una corrida de clasificacion y escribe esos
valores en las paginas del lote correspondiente del Web Index de AirVault,
sin que nadie tenga que teclear pagina por pagina.

## Etapas

Cada etapa se corre sola o todas de corrido. El estado vive en el
manifiesto del trabajo (`output/airvault/<job>/manifiesto.json`), asi que
se puede procesar hoy, subir manana e indexar despues sin repetir nada.

| Etapa | Que hace |
|---|---|
| `preparar` | Arma el manifiesto a partir del CSV de la corrida |
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

## Guardas

El indexado se niega a escribir si algo no cuadra. Estan todas juntas en
`app/airvault/guards.py` y se ejecutan igual en dry run que en automatico:

1. El lote y el manifiesto tienen que tener la misma cantidad de paginas.
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

```batch
set AIRVAULT_COOKIE=FedAuth=...; FedAuth1=...
```

o `--cookie "..."` en cualquier subcomando.

El login por formulario (`AIRVAULT_USER` / `AIRVAULT_PASSWORD`, o
preguntando al momento) queda implementado para las cuentas locales de
AirVault que no pasan por Entra. Las credenciales nunca se guardan en disco
ni se escriben en el log.

Si la sesion caduca, el modulo lo detecta por la redireccion a `/signin2/`
y lo dice, en vez de fallar en silencio a mitad del lote.

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

## Reanudacion

El manifiesto se guarda despues de cada pagina. Si el proceso se corta, al
volver a correr `indexar` las paginas ya escritas se saltan y se sigue
desde donde quedo. Las que fallaron quedan con el motivo anotado.

## Tests

```batch
portable\python312\tools\python.exe -m pytest tests -k airvault
```

Todo el recorrido se prueba contra un cliente falso
(`tests/airvault_fake.py`) que guarda en memoria lo que se le escribe, de
modo que los tests afirman exactamente que paginas se tocaron y con que
valores, sin tocar produccion.
