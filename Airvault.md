# AirVault: indexado automatico de bitacoras

Instrucciones para implementar el indexado automatico de batches de bitacoras
en AirVault desde el proyecto BITS.

Todo lo que hay aqui esta verificado contra el sistema real el 18 de agosto
de 2026, leyendo la aplicacion y sus respuestas. No hay nada supuesto salvo
lo que se marca explicitamente como pendiente de confirmar.

---

## 1. Que hay que construir

Hoy alguien recorre a mano entre 300 y 500 paginas en el Web Index de
AirVault verificando matricula, numero de bitacora y fechas, una por una.

El proyecto BITS ya lee todo eso de los escaneos y lo deja en un CSV. Falta
la pieza que empuja ese CSV a AirVault.

Requisitos fijados por el usuario:

1. Se agrega al proyecto BITS existente como funcionalidad extra.
2. Cada etapa se corre por separado o todas de corrido: preproceso,
   proceso, subida, indexado.
3. Reanudable y por partes: si ya esta subido, solo indexar; si ya esta
   procesado, subir e indexar.
4. El batch se detecta **por nombre**.
5. La matricula del avion es obligatoria y los campos automaticos se
   asignan solos.
6. Tres modos: revisar sin escribir, revisar y aprobar, o todo automatico.
7. Codigo robusto, con tests.

---

## 2. Enlaces

### Acceso

```
https://airvault.criticaltech.com/zfp/?whr=https://login.microsoftonline.com/9767f0dc-e83f-4cc1-94e1-0d5f9d287d32/wsfed
```

El `whr` (home realm) apunta a Microsoft Entra ID: **el acceso esta
federado**, no es un usuario y contrasena local. Ver la seccion 4.

### Modulos

| Modulo | URL |
|---|---|
| Home | `https://airvault.criticaltech.com/home/` |
| Web Search | `https://airvault.criticaltech.com/zfp/` |
| **Web Index** | `https://airvault.criticaltech.com/index/` |
| **Quick Upload** | `https://airvault.criticaltech.com/quickuploadex/` |
| Records Packages | `https://airvault.criticaltech.com/package/` |
| Web Admin | `https://airvault.criticaltech.com/admintools/administration.aspx` |
| Web Reports | `https://airvault.criticaltech.com/reports/` |
| Barcode Header Sheet Creator | `https://airvault.criticaltech.com/barcode/` |
| Web Scan (Encapture 4x) | `https://avscan.criticaltech.com/encapture/` |
| Workflow Inbox | `https://prodworkflowweb.criticaltech.com/workflow/` |

`https://airvault.criticaltech.com/index/?debug=true` sirve los scripts sin
minificar. Solo eso; no habilita ninguna funcion extra. Util para leer el
codigo de la aplicacion.

Version de la plataforma: **AirVault 7.2.18.0**, motor interno "iMagio
Index" (Critical Technologies). Cliente: ASP.NET MVC con jQuery 1.x,
jQuery UI, jqGrid, underscore y jwerty.

---

## 3. Datos del repositorio

| Dato | Valor |
|---|---|
| Repositorio | Copa : MX : MXDocs |
| `repoId` | **3209** |
| `accountId` | 313 |
| `domainId` | 765 |
| Paso de proceso | "Web Index", `eventId` **5** |
| Esquema de indice | "Copa DocType", `indexSchemeId` **137** |
| Campo que decide el tipo de documento | `9586` |
| Renderer del panel | `OcrWordCorrection` |
| Usuario de referencia | Luis Carlos Viloria Bastidas, `userId` 129894 |

Otros repositorios visibles: Copa : MX : Receiving Parts, Copa : MX :
Technical Publications. No se usan aqui.

### Campos del panel de indexado

El repositorio tiene 97 campos, pero el tipo de documento que aplica a las
bitacoras despliega estos 20. Los `fieldId` son estables: los define el
administrador al crear el repositorio.

| fieldId | Columna | Etiqueta | Control | Obligatorio | Quick Upload |
|---|---|---|---|---|---|
| **9586** | C_DocType | Doc Type | picklist 19589 | **si** | si |
| 9627 | C_WorkType | Work Type | picklist 20355 | no | no |
| **9633** | C_ACREG | Aircraft | picklist 19590 | **si** | si |
| 9752 | C_Description | Description | texto | no | no |
| **9699** | C_Fleet | Fleet | picklist 19642 | **si** | no |
| 9783 | C_ICN | Lessor | picklist | no | no |
| **9675** | C_LogNo | Log Page Number | texto | **si** | no |
| **9754** | C_AuditStatus | Audit Status | picklist | **si** | si |
| 9625 | C_WorkOrd | WO # | texto | no | no |
| 9624 | C_Station | Work Location | texto | no | no |
| 9594 | C_StartDate | Start Date (m/d/y) | fecha | no | no |
| **9593** | C_EndDate | End Date (m/d/y) | fecha | **si** | no |
| 9812 | C_EmergencyResponse | Emergency Response | picklist | no | si |
| 9813 | C_AirworthinessCertificate | Airworthiness Certificate | picklist | no | si |
| 12770 | C_DocumentLocation | Document Location | picklist | no | no |
| 12771 | C_SpecificDocumentLocation | Specific Document Location | texto | no | no |
| 9692 | C_ECNReason | ECN Reason | picklist | no | no |
| 9781 | C_ECNReason2 | 2nd ECN Reason | picklist | no | no |
| 9782 | C_ECNReason3 | 3rd ECN Reason | picklist | no | no |
| 9631 | C_BatchName | BatchName | texto | no | si |

Solo en Quick Upload: `9630` C_DocNo, `9750` C_SN, `9749` C_PN, `9809`
C_BUName.

**Los seis obligatorios son 9586, 9633, 9699, 9675, 9754 y 9593.** Si
alguno queda vacio, la pagina se guarda en amarillo.

### Picklists

| Campo | Valores |
|---|---|
| Audit Status | AUDIT IN PROGRESS, OBSOLETE, PUBLISHED, AUDIT REQUIRED |
| Fleet | EMB, MAX, N/A, NG |
| Work Type | ACK, CCK, PDM, SPV |
| Document Location | Access, SDS |
| Emergency Response / Airworthiness Certificate | No, Yes |
| Lessor | AEROREPUBLICA, AIRCASTLE, AMCK, AMERGIN AVIATION, AVIATION CAPITAL GROUP, AVOLON, AWAS, BBAM, BELLINGER AVIATION, CASTLELAKE, COPA, GECAS, ITOCHU, JOLCO, MC AVIATION P., MERX, N/A, ORIX AVIATION, SMBC A.C, WELLS FARGO |
| Aircraft | 162 valores: HK-4453, HK-4454, HK-4456, HK-4505, HK-4599, HP-1369CMP a HP-1857CMP, HP-1990WWP, HP-9801CMP a HP-9821CMP, HP-9901CMP a HP-9932CMP, N/A, Purge |

**Aviso sobre Doc Type.** El picklist 19589 contiene `Log Page`. **No
contiene `LOG PAGE` en mayusculas.** Sin embargo, los batches cargados hasta
hoy llevan `LOG PAGE`, que la interfaz solo conserva porque agrega al combo
cualquier valor que no reconoce. Hay que decidir con el administrador de
AirVault cual se escribe, y dejarlo configurable mientras tanto.

### Lookups automaticos

Dos lookups REST que ejecutan procedimientos almacenados en SQL Server
(`prodsql`, base `CustomerLookups`), expuestos por
`https://airvault.criticaltech.com/webapi/api/v1/lookup/execute`.

**Lookup 183, "web index acreg lookup"** (el que importa)
- Entrada: `C_ACREG` (9633)
- Salidas: `C_ICN` (9783) y `C_Fleet` (9699)
- Procedimiento: `dbo.Copa_ACREG_Fleet_Lessor_WebIndex_Lookup`

**Lookup 168, "web index wp lookup"**
- Entrada: `Barcode` (9601), campo que no aparece en el panel de bitacoras
- Salidas: unos 30 campos de workpackage
- Es el que alimenta la accion personalizada "Redo MX Lookup"

**Trampa importante:** el lookup lo dispara el JavaScript de la interfaz
cuando cambia el campo, **no el servidor cuando guarda**. Si se escribe por
API mandando solo la matricula, Fleet y Lessor quedan vacios y la pagina
cae en amarillo. Hay que resolver la flota del lado nuestro.

---

## 4. Autenticacion

El acceso esta federado con Microsoft Entra ID (tenant
`9767f0dc-e83f-4cc1-94e1-0d5f9d287d32`). Un login federado con segundo
factor **no se puede completar desde un script**, asi que el camino real es
reutilizar la cookie de sesion.

Opciones, de mas a menos practica:

1. **Cookie de sesion.** El usuario entra en el navegador y el modulo usa
   esa cookie (variable de entorno o parametro). Simple y sin secretos
   guardados. Contra: hay que renovarla cuando caduca.
2. **Leer la cookie del perfil de Edge.** Se puede sin dependencias nuevas:
   `sqlite3` de la libreria estandar para la base de cookies, `ctypes` para
   DPAPI y `pycryptodome` (ya esta en `portable/`) para el AES-GCM. Evita
   pegar nada a mano. Contra: se rompe si Microsoft cambia el formato.
3. **Formulario de usuario y contrasena.** AirVault tiene su propio
   formulario en `/signin2/` para cuentas locales que no pasan por Entra.
   Sirve de respaldo, no como camino principal.

**Playwright y cualquier navegador automatizado quedan descartados** por la
regla de portabilidad de `AGENTS.md`: nada de instalar ni descargar en
tiempo de ejecucion.

Las credenciales no se guardan en disco ni se escriben en el log, nunca.

Si la sesion caduca, el servidor responde con una redireccion a `/signin2/`
o con el texto `dosignin` / `wsignin1.0` en el cuerpo. Hay que detectarlo y
decirlo, no fallar en silencio a mitad del batch.

---

## 5. Endpoints

Todos cuelgan de `https://airvault.criticaltech.com`.

### Batches

```
GET /index/Batch/GetBatches
    repoId=-1              (-1 = todos)
    eventLabel=            ("" = todos los pasos)
    encodedFilter=         base64 del texto de "Filter by"
    encodedKeywordFilter=  base64, o "" si no hay
```

Respuesta:

```json
{"total":1,"page":1,"records":22,"rows":[
  {"id":1,"cell":["3209","<div .../>","Luis Carlos Viloria Bastidas",
   "MXDocs","003SQ7","472","DP | Bit&#225;coras varias 4",
   "2026-08-05T16:34:54Z","Web Index","2026-08-05T19:56:22",
   "3276799","313","765","129894","5","1"]}]}
```

**Dos trampas.** `cell` es un **arreglo posicional**, no un diccionario. Y
los nombres vienen **escapados en HTML** (`Bit&#225;coras`), asi que hay
que deshacerlo antes de comparar.

Orden de las columnas:

```
appid, lockeduserid, lockedusername, appname, batchid, imagecount,
userbatchname, batchreceivedate, eventlabel, lasteventdate, rights,
accountid, domainid, userid, eventid, IdentityColumn
```

```
GET /index/Batch/LockAndGetBatchInfo?repoId=&encodedBatchId=
GET /index/Batch/UnlockBatch?repoId=&encodedBatchId=
GET /index/Batch/RouteBatch
GET /index/Batch/DeleteBatch
GET /index/Batch/UpdateBatchName
GET /index/Batch/ValidateQuery
GET /index/Batch/UpdateBatchValidationQuery
```

`LockAndGetBatchInfo` abre y **bloquea** el batch. Devuelve:

```json
{"repoId":3209,"batchId":"003SRO","pageNum":1,"pageCount":393,
 "docCount":393,"eventId":5,"approvalMode":false,
 "indexRenderer":"OcrWordCorrection","lockedUserId":129894,
 "indexScheme":{...},"processParams":[...]}
```

### Indexado

```
GET /index/FormsProcessing/GetIndexFields
    encodedBatchId=  base64 del batchId
    repoId=
    page=            numero de pagina, empieza en 1
```

Respuesta: `{"RepoFields":[...], "Sequence":n, "SequenceStart":n,
"SequenceEnd":n, "Status":0}`. Cada campo trae `FieldId`, `ColumnName`,
`Value`, `Required`, `PickListId`, `MaxLength` y demas metadatos.

```
GET /index/FormsProcessing/SaveAndGetIndexFields
    encodedBatchId=
    repoId=
    page=              pagina que se guarda
    nextPageToOpen=    pagina que queda abierta despues
    encodedValues=     ver codificacion abajo
    encodedSticky=     "" si no hay campos sticky
    status=            0 = Valid
```

```
GET /index/FormsProcessing/SaveIndexFields
GET /index/FormsProcessing/UpdateForwardIndexFields
    repoId=, page=, batchId=, encodedValues=, encodedStickyIndexes=
GET /index/FormsProcessing/UpdatePageStatus
GET /index/FormsProcessing/RejectingPage
GET /index/FormsProcessing/Resequence
GET /index/FormsProcessing/UpdateRotation
GET /index/FormsProcessing/CompleteBatch
GET /index/FormsProcessing/GetBatchPages
GET /index/FormsProcessing/GetPage/?encodedBatchId=&repoId=&page=&showOrig=0&dateTime=
```

`UpdateForwardIndexFields` es el "Stickies forward": aplica los campos
marcados como sticky a **todas** las paginas siguientes del batch, de una
sola llamada. No admite rango.

### Catalogos

```
GET /index/PickList/GetPickListViews?repoId=&indexSchemeId=&dateTime=
GET /index/Lookup/GetLookups?repoId=&indexSchemeId=&dateTime=
GET /index/Profile/GetAccountProfiles
```

### Quick Upload

```
POST /quickuploadex/Home/Upload/
    multipart, trozos de 1 MB (asi lo hace la pagina)
    campos: repoId, filename, name, chunk, chunks
    archivo en el campo "file"
    limites: 2048 MB por archivo, 100 archivos por cola

POST /quickuploadex/Home/FinishUpload
    Content-Type: application/json
    {"model":{"RepoId":3209,"FileName":"lote.pdf","InputValues":[
      {"FieldId":"9586","WarnEmpty":false,"Key":"C_DocType",
       "Value":"Log Page","Valid":true,"Dirty":true,"OriginalValue":""}
    ]}}

GET /quickuploadex/Home/GetRepositories
GET /quickuploadex/Home/GetRepositoryFields
GET /quickuploadex/Home/GetPickListViews
```

`FinishUpload` se llama **una vez por archivo**, asi que a nivel de API
cada archivo puede llevar sus propios valores de indice, aunque la pantalla
use un solo formulario para todos.

**Limitacion de Quick Upload:** solo expone diez campos y entre ellos **no
estan Log Page Number, Fleet ni End Date**. Deja el batch clasificado pero
no indexado. El indexado real siempre lo tiene que hacer el Web Index.

### Codificacion de valores

```
encodedValues  = base64( "9586=Log Page\t9633=HP-1848CMP\t9675=2287325" )
encodedSticky  = base64( "9586\t9633" )   o "" si no hay ninguno
encodedBatchId = base64( "003SRO" ) = "MDAzU1JP"
encodedFilter  = base64( "DP | BIT" )
```

Separador: tabulador literal. Ningun valor de estas bitacoras lo contiene.

Lo que **no** se manda, AirVault lo conserva. Conviene mandar solo los
campos que el sistema controla, asi un indexado no pisa lo que alguien puso
a mano.

### Estados de pagina

| Codigo | Nombre | Color en el mapa |
|---|---|---|
| 0 | Valid | verde |
| 1 | No Template Match | |
| 2 | Separator | gris |
| 3 | Need Correction | amarillo |
| | pagina borrada | negro |

Al guardar, si algun campo obligatorio queda vacio o invalido, la pagina
queda en 3. Si todos son validos, en 0.

---

## 6. Reglas del dominio

### Nombre del batch

El nombre es lo unico que el sistema y AirVault comparten para reconocer un
batch. Formato acordado:

```
DP | BIT <DD MON YYYY> <HH MM>

DP | BIT 18 AUG 2026 05 42
```

Es el mismo formato de marca de tiempo que ya usa el nombre del CSV de
ejecución (`BITS 18 AUG 2026 05 42`), de modo que el batch y la carpeta de la
ejecución se cruzan de un vistazo. Debe deducirse de la ruta del CSV, con
opcion a fijar prefijo y nombre completo a mano.

**La marca de tiempo no es decoracion.** La caja "Filter by" de AirVault
manda el texto al servidor y hace **coincidencia de subcadena sin
distinguir mayusculas**. Escribir `DP | BIT` devuelve hoy 22 batches, porque
atrapa tres familias a la vez:

```
DP | Bitácoras varias 4, 5, 6, 7, 8, 9, 12, 13, 14
DP | BITS VARIAS 17, 19, 20, 21, 22, 23
DP | BIT Mix | Viernes 14 AUG            (109 paginas)
DP | BIT Mix | Viernes 14 AUG            (455 paginas)  <- repetido
DP | BIT Mix 3 | Viernes 14 AUG
DP | BIT Mix 4 | Viernes 14 AUG
DP | BIT Mix 5 | Viernes 14 AUG          (389 paginas)
DP | BIT Mix 5 | Viernes 14 AUG          (172 paginas)  <- repetido
DP | Bits varias 1 | Martes 18 de Ago
```

Con nombres repetidos no hay forma de saber en cual escribir. La marca de
tiempo los separa.

### Correspondencia entre el CSV y las paginas del batch

El CSV minimo de una ejecución tiene estas columnas:

```
file, page, log_number, dup, disc, matricula, flight_number,
pilot_signature, captain_signature, captain_license,
technician_signature, date, time_ms
```

- `matricula` va a `C_ACREG` tal cual (`HP-1848CMP`).
- `log_number` va a `C_LogNo`, siete digitos exactos.
- `date` va a `C_EndDate` **cambiando el formato**: el CSV usa
  `YYYY/MM/dd` y AirVault espera `MM/DD/YYYY`.
- Las paginas en blanco (sin log number, sin matricula y sin fecha) **no
  entran**: no llegan al PDF que se sube y si se contaran descuadrarian la
  correspondencia con las paginas del batch.
- El orden de las paginas del batch es el del PDF que se subio. Cuando el
  PDF sale de `--separar-por`, el orden lo fija
  `app/reports/organize.py`: agrupa por matricula y mes, y dentro de cada
  grupo ordena por `log_number` y despues por posicion original.

### Reglas de las bitacoras (ya documentadas en AGENTS.md)

- Cada libro fisico tiene 50 paginas y corresponde a un solo avion.
- El `log_number` tiene siete digitos; los ultimos dos indican la pagina:
  `00`-`49` forman un libro y `50`-`99` el siguiente.
- Al aumentar el `log_number` la fecha no retrocede dentro del mismo libro.

---

## 7. Rutas del proyecto

```
D:\BITS\
  AGENTS.md                        reglas no negociables del proyecto
  Airvault.md                      este documento
  airvault.json                    configuracion del modulo
  airvault_flota.json              cache matricula -> flota y arrendador
  fleet.json                       lista de matriculas de la flota
  requirements.txt                 requests>=2.31 ya declarado
  pytest.ini                       testpaths = tests
  run_cli.py                       CLI de clasificacion (no tocar)
  run_gui.py / run_editor.py       interfaz (no tocar sin preguntar)
  run_airvault.py                  CLI del indexado

  app\airvault\                    el modulo nuevo
  app\core\pipeline.py             procesamiento (no tocar)
  app\reports\csv_reporter.py      formato del CSV (no tocar sin preguntar)
  app\reports\organize.py          orden de las paginas en los PDFs
  app\reports\outputs.py           nombre de la ejecución, run_csv_name()
  app\utils\fleet.py               normalise_matricula, load_fleet
  app\utils\portable.py            ensure_portable_env()

  docs\airvault-indexado.md        documentacion de uso del modulo
  tests\                           pytest
  tools\                           utilidades sueltas

  input\                           PDFs escaneados (archivos de 700 MB)
  output\BITS <DD MON YYYY> <HH MM>\
      datos\BITS <...>.CSV         CSV minimo, el que alimenta el indexado
      datos\BITS <...>_completo.CSV
      datos\BITS <...>.json
      stats.json
  output\airvault\<job>\           estado del indexado
      manifiesto.json
      revision.csv
      revision.html

  portable\python312\tools\python.exe    interprete portable
```

Interprete a usar siempre:

```batch
portable\python312\tools\python.exe
```

Ya trae `requests 2.34.2`, `pydantic`, `loguru`, `pytest`, `pymupdf`,
`pycryptodome`. No hace falta instalar nada.

---

## 8. Diseno propuesto

### Etapas

```
input/  ->  [procesar]  ->  CSV de la ejecución
                              |
                              v
                         [preparar]   manifiesto del trabajo
                              |
                              v
                          [subir]     Quick Upload, o el usuario lo sube
                              |
                              v
                        [descubrir]   ubicar el batch por nombre
                              |
                              v
                          [plan]      dry run + reporte de revision
                              |
                              v
                        [indexar]     escribir pagina por pagina
                              |
                              v
                       [verificar]    releer y confirmar
```

### El manifiesto

`output/airvault/<job>/manifiesto.json` es la **unica fuente de verdad**.
De ahi salen la reanudacion y la ejecucion por partes:

- Cada etapa lee el manifiesto, hace lo suyo y lo vuelve a escribir.
- Cada bitacora lleva su propio estado, asi que un indexado cortado en la
  pagina 250 se retoma en la 251 sin repetir escrituras.
- Si el usuario subio el batch a mano, se marca `subir` como omitida y se
  arranca en `descubrir`.
- La escritura del archivo debe ser **atomica** (temporal y `os.replace`),
  porque se guarda despues de cada pagina y un JSON truncado dejaria el
  trabajo irrecuperable.

Contenido minimo por bitacora: posicion en el batch, archivo y pagina de
origen, matricula, log number, fecha, flota, si la flota fue inferida,
pagina asignada en el batch, estado y avisos.

### Las guardas

Es la parte critica. Escribir en la pagina equivocada dejaria una bitacora
publicada con la matricula de otro avion. Todas las comprobaciones deben
estar juntas, ser puras y ejecutarse **igual en dry run que en
automatico**:

1. **Cantidad.** El batch y el manifiesto tienen que tener la misma cantidad
   de paginas. Si no, se corta el trabajo entero: la correspondencia por
   posicion esta rota.
2. **Matricula.** Toda matricula debe existir en el picklist de AirVault.
3. **Alineacion.** Si AirVault ya trae un log number en esa pagina, tiene
   que coincidir con el del manifiesto. Es el mejor ancla que existe.
4. **No pisar.** Una pagina ya en Valid no se toca salvo que se pida
   expresamente.
5. **Obligatorios.** Ninguno de los seis puede quedar vacio.

Una pagina que falle cualquiera de las guardas 2 a 5 queda marcada como
bloqueada y no se escribe; el resto del batch sigue.

### Los modos

| Modo | Que hace |
|---|---|
| dry run | Calcula el plan completo, escribe el reporte, no toca la red |
| revisar | Igual, y espera aprobacion explicita antes de escribir |
| automatico | Escribe sin detenerse |

El reporte de revision debe ser **el mismo artefacto en los tres modos**,
para que lo que se aprueba sea exactamente lo que se envia.

### La flota

Como el lookup de AirVault lo dispara la interfaz y no el servidor, hay que
resolverla por cuenta propia, en tres niveles:

1. Cache local `airvault_flota.json`.
2. Lo que AirVault ya tiene indexado: al leer las paginas del batch se
   aprenden los pares matricula/flota/arrendador y se guardan en el cache.
   Es la fuente autoritativa y se alimenta sola.
3. Regla de prefijos como ultimo recurso, marcando la bitacora como
   `fleet_inferido` para que alguien la confirme en el reporte.

---

## 9. Estado actual

**Ya hay una implementacion en el proyecto**, escrita el 18 de agosto de
2026, con 123 tests pasando en local. Nunca ha escrito nada en AirVault.

Archivos que ya existen:

```
run_airvault.py
airvault.json
app\airvault\__init__.py    config.py     encoding.py   model.py
                manifest.py  mapping.py    guards.py     naming.py
                session.py   client.py     discovery.py  indexer.py
                report.py    uploader.py
docs\airvault-indexado.md
tools\_tmp_muestra_bitacoras.py     <- script temporal, borrar al terminar
tests\airvault_fake.py
tests\test_airvault_encoding.py     test_airvault_mapping.py
      test_airvault_guards.py       test_airvault_manifest.py
      test_airvault_indexer.py      test_airvault_discovery.py
      test_airvault_report.py       test_airvault_uploader.py
      test_airvault_session.py      test_airvault_flujo.py
      test_airvault_naming.py
requirements.txt                    <- se le agrego requests
```

`git status` los muestra sin commitear. Revisalos, quedatelos o borralos y
rehazlos: este documento tiene todo lo necesario para escribirlos de cero.

Lo que **no** esta hecho:

- Nunca se ha ejecutado una escritura contra AirVault.
- La autenticacion asume formulario local; **hay que rehacerla para Entra
  ID** segun la seccion 4.
- No hay nada en la interfaz grafica. Todo es linea de comandos.
- Falta el PDF de prueba de 20 bitacoras y la prueba de punta a punta.

---

## 10. Como implementarlo desde Claude Code

### Antes de tocar nada

1. Leer `AGENTS.md`. Sus reglas mandan sobre cualquier cosa que diga este
   documento.
2. `git status` y crear una rama; nunca commitear directo sobre `main`.
3. Correr los tests que ya existen para tener una linea base:

```batch
portable\python312\tools\python.exe -m pytest tests -q
```

### Reglas del proyecto que aplican aqui

De `AGENTS.md`, resumidas:

- **Portable.** La carpeta se copia a cualquier PC Windows sin permisos de
  administrador y funciona sin instalar nada. Nada de rutas del sistema,
  registro ni descargas en tiempo de ejecucion. Todo vive en `portable/`.
- **Solo CPU.** No aplica directamente a este modulo, pero no se introduce
  ninguna dependencia que lo viole.
- **Interfaz.** No se agregan botones, paneles, dialogos ni columnas que el
  usuario no pidio. Si hace falta algo, se propone y se espera respuesta.
  Este modulo es de linea de comandos hasta que el usuario diga otra cosa.
- **El CSV.** El output del reporte CSV no se modifica sin preguntar.
- **Commits.** En espanol, sin acentos, asunto en presente y tercera
  persona diciendo que hace el cambio, cuerpo explicando el porque. Sin
  firma ni coautoria ni marca de herramienta. Al cerrar un cambio se hace
  commit y push sin volver a preguntar.

### Orden sugerido

Cada paso deja algo que se puede probar solo.

1. **Codificacion.** `fieldId=valor` unido por tabuladores y en base64, y
   el batchId en base64. Test con el vector conocido: `003SRO` -> `MDAzU1JP`.
2. **Modelo y manifiesto.** Registros y etapas, con guardado atomico. Test
   de carga, de archivo corrupto y de version desconocida.
3. **Mapeo.** CSV a valores: formato de fecha, matricula, log number, y el
   resolutor de flota. Test de que las paginas en blanco no entran y de que
   solo viajan los campos que el sistema controla.
4. **Nombre del batch.** `DP | BIT` mas la marca de tiempo, deducida de la
   ruta del CSV. Test de que la marca sale igual a la del CSV de ejecución.
5. **Guardas.** Las cinco, puras y con un test por caso de fallo.
6. **Cliente HTTP.** Cuidado con `cell` como arreglo y con las entidades
   HTML de los nombres.
7. **Sesion.** Cookie primero, formulario como respaldo. Detectar caducidad.
8. **Descubrimiento.** Filtro server-side y desempate por cantidad de
   paginas; si sigue habiendo empate, parar y pedir el batch id.
9. **Indexador.** Leer todo el batch, aprender la flota, planificar,
   escribir guardando el manifiesto tras cada pagina.
10. **Reporte.** CSV y HTML autocontenido, sin recursos externos.
11. **Subida.** Quick Upload por trozos, desacoplada para poder saltarla.
12. **CLI.** Subcomandos `preparar`, `subir`, `descubrir`, `plan`,
    `indexar`, `verificar`, `todo`.

### Como probarlo sin tocar produccion

Hacer un cliente falso que guarde en memoria lo que se le escribe, e
inyectarlo en el indexador. Asi los tests pueden afirmar exactamente que
paginas se tocaron, con que valores y en que orden.

Los tests que no pueden faltar:

- El dry run **no hace ninguna llamada de escritura**.
- Un CSV que no corresponde al batch **no escribe nada**.
- Un corte a media escritura deja constancia y al reanudar no repite.
- Una pagina ya en Valid no se pisa.
- Cada pagina se lee una sola vez.

### Prueba de punta a punta

1. Sacar 20 bitacoras al azar de un PDF de `input\` y armar un PDF de
   prueba con su CSV esperado. Los PDFs de entrada pesan unos 700 MB, asi
   que hay que trabajarlos en la maquina, con PyMuPDF del portable.
2. Subir ese PDF a AirVault con el nombre `DP | BIT <fecha> <hora>`.
3. `preparar`, `descubrir`, `plan`. Revisar `revision.html`.
4. `indexar --revisar` y aprobar.
5. `verificar`.

Recien despues de que eso funcione, correr un batch completo.

---

## 11. Trampas conocidas

- **Un batch a la vez por usuario.** Si el batch esta abierto en otra
  pestana, `LockAndGetBatchInfo` se queda colgado indefinidamente, sin
  error. Hay que cerrar el batch antes de abrirlo desde otro lado, y poner
  tiempo limite a todas las peticiones.
- **`cell` es un arreglo**, no un diccionario, y los nombres vienen
  escapados en HTML. Leerlo mal hace que nunca se encuentre ningun batch.
- **El filtro es subcadena.** `DP | BIT` trae 22 batches de tres familias
  distintas, con nombres repetidos entre ellos.
- **El lookup de flota es del cliente.** Escribir por API solo la matricula
  deja Fleet y Lessor vacios y la pagina en amarillo.
- **`LOG PAGE` no esta en el picklist.** La interfaz lo agrega al combo
  para no perderlo, pero es un valor fuera del vocabulario controlado.
- **Edge congela las pestanas en segundo plano** y la aplicacion deja de
  responder: timers y peticiones detenidos. Solo importa si se automatiza
  con navegador, que no es el caso, pero explica comportamientos raros al
  depurar a mano.
- **Quick Upload no alcanza.** Le faltan Log Page Number, Fleet y End Date.

---

## 12. Decisiones abiertas

1. **Doc Type.** Se escribe `Log Page` (el valor del picklist) o
   `LOG PAGE` (lo que tienen los batches viejos). Preguntar al administrador
   de AirVault.
2. **Cookie de Entra ID.** Se pega a mano cada vez, o el modulo la lee del
   perfil de Edge.
3. **Interfaz grafica.** Si la etapa va a la ventana principal, hay que
   proponer antes donde y como se ve, segun las reglas de interfaz de
   `AGENTS.md`.
4. **Alcance de la subida.** Si Quick Upload se implementa del todo o el
   usuario sube siempre a mano y el sistema arranca en `descubrir`.
