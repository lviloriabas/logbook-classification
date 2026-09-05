# BITS: instrucciones de operación

BITS prepara bitácoras aeronáuticas escaneadas para su revisión, entrega e indexado en AirVault. Lee cada página, valida sus datos, separa los casos dudosos y genera los archivos de salida. El OCR trabaja localmente en Windows y solo usa CPU.

Los detalles de arquitectura, tecnología y mantenimiento están en [TECNICO.md](TECNICO.md).

## 1. Antes de empezar

Compruebe lo siguiente:

- La carpeta del programa está completa, incluida `portable/`.
- Los originales son PDF y se pueden abrir.
- La plantilla corresponde al formato de las páginas.
- `fleet.json` contiene la flota vigente si se verifican matrículas.
- Hay espacio suficiente en `output/`.
- Los PDF no se moverán ni cambiarán durante el proceso.

Para usar AirVault también necesita conexión, Microsoft Edge y una cuenta autorizada. No hace falta conexión para el OCR normal.

## 2. Preparar los PDF

1. Copie los PDF en `input/` o selecciónelos desde otra carpeta.
2. Abra `BITS.exe`.
3. Pulse **Detectar** para usar `input/` o **Seleccionar archivos...** para elegirlos manualmente.
4. Revise el orden. El rango de páginas se cuenta de forma continua sobre todos los PDF.
5. Si solo necesita una parte, indique la primera y la última página.

Los archivos tomados directamente de `input/` pasan a `input/processed/` cuando el proceso termina. Los archivos elegidos desde otra carpeta no se mueven.

## 3. Configurar la lectura

1. Elija la plantilla correcta. La normal es `template/aircraft_log.json`.
2. Active **Verificar matrículas** para comparar contra `fleet.json`.
3. Mantenga activadas **Corrección de inclinación**, **Alineación** y **Preprocesar recortes**.
4. Seleccione una cantidad de hilos adecuada. **Reservar un núcleo** deja capacidad para otras tareas.
5. Use como referencia una página completa y nítida.

Si el escaneo tiene una geometría dudosa, active **Visualizar campos** y pulse **Preprocesar**. Revise varias páginas y confirme que los recuadros cubren los datos correctos. Este paso no ejecuta OCR ni crea una salida.

## 4. Elegir la salida

- **Un solo PDF** crea una entrega continua y puede insertar separadores por matrícula o mes. Es la opción necesaria para AirVault.
- **Varios PDF** crea archivos separados por matrícula, mes o ambos.
- **Repartir en** limita las páginas de cada parte de una entrega única.
- **Posibles discrepancias** reúne páginas con firmas faltantes o inciertas.
- **Errores** crea un PDF auxiliar con páginas cuyos datos principales no se resolvieron.
- **Fecha del CSV** viene en **Fin de mes**, que es la fecha con la que se indexa; **Día exacto** conserva el día leído y solo cae al fin de mes cuando falta.
  - En **Fin de mes** la ejecución no lee el día: no llega a ninguna salida, y saltárselo la acelera un 13 % (medido sobre 21 páginas). A cambio, esa ejecución ya no puede volver a representarse con el día exacto sin procesarla otra vez.
  - En **Día exacto** se lee todo, y al indexar todavía se puede elegir escribir el fin de mes. Cambiar la opción reescribe el CSV sin volver a leer las páginas.
- **Campos importantes** define las columnas del CSV principal.

Los recuadros de **Visualizar campos** solo aparecen en pantalla. No se imprimen en la entrega.

## 5. Procesar

### Proceso manual

1. Confirme archivos, rango, plantilla y opciones.
2. Pulse **Procesar**.
3. Vigile el avance y no mueva los originales.
4. Espere el mensaje **Procesamiento terminado**.
5. Revise los resultados antes de exportar.

El proceso guarda CSV, JSON y estadísticas. Los PDF de entrega se crean al exportar.

### Proceso automático

1. Abra **Automatización...**.
2. Elija si también desea subir, indexar y completar los batches.
3. Pulse **Automático**.
4. Siga la línea de pasos hasta que todo termine.

Procesar y exportar siempre forman parte de la cadena. Las opciones de AirVault se ejecutan en orden. **Cancelar** detiene toda la cadena y conserva lo que ya se haya guardado.

## 6. Revisar

Haga doble clic en una fila para ver su página. Los estados significan:

| Estado | Qué hacer |
|---|---|
| `OK` | Los datos principales están resueltos. |
| `WARNING` | Revise un dato incompleto, débil o inferido. |
| `ERROR` | Los datos principales no están resueltos. |

Revise como mínimo:

- `log_number`, que debe tener siete dígitos;
- matrícula y fecha de cada libro;
- cambios de fecha, que no deben retroceder dentro del libro;
- `dup=true`, que indica una aparición repetida;
- `disc=true`, que indica una posible discrepancia de firmas;
- las páginas incluidas en **REVISAR** y en `errores.pdf`.

Cada libro físico tiene 50 páginas y una sola aeronave. Los finales `00` a `49` pertenecen a un libro y `50` a `99` al siguiente. La fecha puede repetirse, pero no retroceder al aumentar `log_number` dentro del mismo libro.

## 7. Depurar

1. Pulse **Depurar**.
2. Marque **Duplicados**, **Páginas en blanco** o ambas.
3. Revise la selección y confirme.
4. Exporte otra vez si ya había creado los PDF.

Depurar es siempre manual: no se ofrece dentro del proceso automático. De cada `log_number` repetido hay que ver las dos apariciones para saber cuál sobra, así que el cuadro las lista con su matrícula, su fecha y su vuelo, y marca la primera. Se conserva una aparición de cada bitácora aunque se marquen todas. También puede retirar las páginas en blanco de CSV, JSON y estadísticas. Nunca deja la ejecución sin páginas.

## 8. Exportar

1. Confirme la organización de la salida.
2. Pulse **Exportar**.
3. Abra la carpeta indicada y revise los PDF.

Exportar de nuevo no repite OCR ni sobrescribe los PDF anteriores. Las nuevas copias reciben un sufijo como `-2` o `-3`.

La estructura normal es:

```text
output/
└── BITS DD MON YYYY HH MM/
    ├── datos/
    │   ├── <ejecución>.CSV
    │   ├── <ejecución>_completo.CSV
    │   ├── <ejecución>.json
    │   └── <ejecución>_paginas.json
    ├── stats.json
    └── <PDF de entrega>
```

El archivo `_paginas.json` se crea con **Un solo PDF** y es necesario para relacionar la entrega con las páginas de AirVault.

## 9. Indexar en AirVault

1. Exporte con **Un solo PDF**.
2. Confirme que el CSV incluya matrícula, `log_number` y fecha.
3. Abra **Indexar en AirVault...**.
4. Seleccione la ejecución.
5. Revise el nombre, el máximo de páginas por batch y la **fecha**: **Fin de mes** escribe el último día del mes en todas las bitácoras. **Día exacto** solo está disponible si la ejecución leyó el día.
6. Deje **Sesión** vacía. Si se abre Edge, complete el inicio de sesión y el segundo factor.
7. Use **Vista previa...** para comprobar el reparto antes de subir.
8. Pulse **Subir a AirVault**.
9. Si trabaja de forma manual, revise el plan y pulse **Indexar** cuando los datos sean correctos.
10. Compruebe el estado final de todos los batches.

BITS sube un archivo a la vez y espera a identificarlo antes de continuar. No sobrescribe páginas que ya están válidas. Si una carga aceptada no aparece, no la vuelve a enviar automáticamente, porque podría crear un duplicado. Use el menú de la fila solo después de comprobar el caso en AirVault.

Las páginas dudosas forman un batch terminado en `REVISAR`. Ese batch se conserva para clasificación manual. Las páginas normales reciben los campos configurados de documento, aeronave, flota, número de bitácora, estado de auditoría, fecha y nombre del batch. El número de vuelo se usa en la descripción cuando está disponible.

Una diferencia en la cantidad de páginas detiene el batch. Un dato obligatorio vacío, un duplicado o una diferencia con un valor remoto bloquea la página afectada.

Un libro tiene una sola aeronave. Si AirVault ya tiene una página del mismo libro en verde con otra matrícula, la página no se escribe: publicar una bitácora a nombre de otro avión no se corrige con comodidad. Esa comprobación sale de la misma lectura del batch y no cuesta ninguna consulta adicional.

## 10. Editar una plantilla

1. Abra **Herramientas → Editor de plantillas…**, o ejecute `run_editor.py` con el Python portable.
2. Abra un PDF representativo y la plantilla que desea modificar.
3. Ajuste las regiones sin cambiar los identificadores de los campos.
4. Guarde una copia.
5. Pruebe **Preprocesar** en varias páginas y después procese un rango pequeño.

No use una plantilla nueva en producción hasta revisar su geometría y sus resultados.

## 11. Usar la consola

La interfaz es el medio normal. La consola sirve para diagnóstico o trabajos controlados:

```powershell
portable\python312\tools\python.exe run_cli.py --help
portable\python312\tools\python.exe run_airvault.py --help
```

Ejemplo de OCR sobre una carpeta:

```powershell
portable\python312\tools\python.exe run_cli.py `
  --input-dir input `
  --template template\aircraft_log.json `
  --output-dir output `
  --un-solo-pdf `
  --verificar-flota
```

En AirVault, ejecute primero `plan`. Use `indexar --revisar` para pedir confirmación o `--auto` solo cuando el plan ya fue comprobado. No use `--sobrescribir` salvo que deba reemplazar datos remotos válidos de forma intencional.

Para contrastar con AirVault lo que el sistema recuerda de cada libro:

```powershell
portable\python312\tools\python.exe run_airvault.py memoria
portable\python312\tools\python.exe run_airvault.py memoria --aplicar
```

Sin `--aplicar` solo informa de las diferencias. Esta comprobación consulta Web Search; la que va sola en cada `plan` e `indexar` no consulta nada de más y no hay que pedirla.

## 12. Contingencias

- **Cancelar OCR:** pulse **Cancelar** una vez y espere. No use una ejecución cancelada como entrega final.
- **Original no localizado:** abra **Visor de CSV...**, seleccione la ejecución y use **Ubicar PDF...**.
- **Reexportación:** necesita el JSON, la plantilla y todos los PDF fuente.
- **Cierre con trabajo activo:** solicite la cancelación y espere antes de cerrar.
- **Sesión de AirVault vencida:** vuelva a iniciar sesión en Edge y reanude. El manifiesto evita repetir páginas confirmadas.

## 13. Comprobación final

Antes de entregar o indexar, confirme:

1. rango y cantidad de páginas;
2. matrícula, `log_number` y fecha por libro;
3. duplicados, blancos y discrepancias;
4. contenido del batch `REVISAR`;
5. PDF final;
6. estado de todos los batches en AirVault.
