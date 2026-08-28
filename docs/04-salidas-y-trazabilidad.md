# 4. Salidas y trazabilidad

## 4.1 Carpeta de ejecución

Cada ejecución nueva crea una carpeta con fecha y hora:

```text
output/
└── BITS DD MON YYYY HH MM/
    ├── datos/
    │   ├── BITS DD MON YYYY HH MM.CSV
    │   ├── BITS DD MON YYYY HH MM_completo.CSV
    │   ├── BITS DD MON YYYY HH MM.json
    │   └── BITS DD MON YYYY HH MM_paginas.json
    ├── stats.json
    ├── BITS DD MON YYYY HH MM.pdf
    └── otros PDF opcionales
```

Si el nombre ya existe, el sistema añade `-2`, `-3`, etc. No sobrescribe otra ejecución.

## 4.2 CSV mínimo

El CSV principal contiene una fila por página. Incluye siempre `file` y `page`, más las columnas seleccionadas como importantes. La selección se guarda por plantilla en `important_fields.json`.

Si la selección guardada está vacía, el generador recupera las columnas mínimas predeterminadas; no produce un archivo limitado únicamente a `file` y `page`.

El archivo usa UTF-8 con marca BOM para abrir correctamente en Excel. Los valores críticos solo se escriben cuando cumplen su formato canónico. Una lectura no válida queda vacía en ambos CSV. El CSV completo conserva su confianza, estado, comentario y fuente; el texto original y las alternativas permanecen únicamente en el JSON.

> **NOTA:** Cambiar las columnas importantes guarda la preferencia y actualiza la vista. El CSV mínimo adopta la selección en la siguiente generación o reexportación; el CSV completo conserva todas las columnas.

## 4.3 CSV completo

El archivo `_completo.CSV` es la referencia de auditoría. Para cada campo contiene:

- valor;
- confianza (`_conf`);
- estado (`_status`), excepto en firmas;
- comentario (`_comment`), excepto en firmas;
- fuente (`_source`).

Además contiene:

- `dup`: `true` desde la segunda aparición de un `log_number`; la primera queda en `false`;
- `disc`: `true` cuando la página presenta una posible discrepancia de firmas;
- `date`: fecha normalizada `YYYY/MM/DD`;
- `time_ms`: parte proporcional del tiempo real de la ejecución.

## 4.4 Política de fecha del CSV

La política afecta la representación del CSV, no el resultado OCR ni el JSON.

- **Día específico:** conserva un día válido. Si falta el día pero existen mes y año, usa el último día calendario del mes.
- **Último día del mes:** representa todas las fechas resueltas con el cierre calendario del mes.

Cuando la política sustituye el día, el CSV identifica la fuente como `csv_date_policy` y deja el motivo en el comentario.

## 4.5 JSON consolidado

El JSON tiene el mismo nombre base que la ejecución. Contiene:

- nombre y fecha de generación;
- cantidad de documentos;
- un reporte por PDF;
- resultados de cada página y campo;
- valores originales, alternativas, confianza, estado, fuente y método de inferencia;
- resumen de cada documento.

Los metadatos exclusivos de la vista previa no se guardan.

## 4.6 Estadísticas

`stats.json` resume:

- documentos y páginas procesadas;
- páginas en blanco y válidas;
- conteo por matrícula y por mes;
- páginas sin matrícula o sin fecha;
- posibles discrepancias, categorías y razones;
- distribución de páginas entre PDF, cuando se aplicó separación.

El campo histórico `total_bitacoras` cuenta reportes de entrada, es decir, PDF fuente. No representa el número de libros físicos cuando un libro cruza archivos o un PDF contiene más de un libro.

El bloque de separación comprueba que cada página esté distribuida, excluida por discrepancia confirmada o destinada a revisión. `paginas_fuera` debe ser cero y `completa` debe ser `true`.

## 4.7 PDF único

Sin criterios de separación, el sistema crea un PDF plano con las páginas ordenadas por `log_number`.

Con separación por matrícula o mes, crea un solo PDF con páginas divisorias blancas y horizontales. Las secciones siguen este orden:

1. matrícula ascendente;
2. mes cronológico dentro de la matrícula;
3. **POSIBLES DISCREPANCIAS**, si hay páginas marcadas con `disc=true`;
4. **REVISAR**, si existen páginas sin datos críticos seguros o detectadas en blanco.

Las dos últimas secciones son el mismo batch manual: se escriben juntas, primero las discrepancias y después el resto.

Las páginas fuente se insertan directamente desde el PDF original. No se rasterizan ni reciben anotaciones de BITS. Las páginas en blanco se conservan bajo **REVISAR**.

## 4.8 Varios PDF

Los nombres dependen de los criterios seleccionados:

| Criterio | Nombre |
|---|---|
| Matrícula | `HP-XXXXCMP.pdf` |
| Mes | `YYYY-MMM.pdf` |
| Matrícula y mes | `HP-XXXXCMP_YYYY-MMM.pdf` |
| Mes no resuelto | `sf.pdf` o sufijo `_sf` |
| Matrícula ausente, débil o en conflicto | `revisar.pdf` |
| Marcada como discrepancia (`disc=true`) | `discrepancias.pdf`, con la opción marcada |

Las páginas de cada archivo se ordenan por libro y `log_number`. Las páginas con número ilegible quedan al final, en su orden de entrada.

## 4.9 Posibles discrepancias

Toda página marcada con `disc=true` sale de las secciones normales: tanto la que tiene una ausencia de firma confirmada como aquella cuya firma no se pudo leer con seguridad. Ambas necesitan que alguien mire la página, que es lo que la sección declara.

- En PDF único, abren la parte manual bajo **POSIBLES DISCREPANCIAS**, delante del separador **REVISAR** que encabeza al resto.
- En modo de varios PDF, con la opción **Posibles discrepancias** marcada van en `discrepancias.pdf`, que abre con la misma página divisoria, y por eso no se repiten en `revisar.pdf`. Sin marcar, se quedan en `revisar.pdf` junto al resto.

El orden es global por `log_number`. La sección no se subdivide por matrícula ni mes.

Al indexar, estas páginas se escriben con el `Audit Status` de discrepancia (`AUDIT REQUIRED` de fábrica, configurable en `airvault.json`) en lugar del `Audit Status` normal del trabajo. Es lo único que las distingue en AirVault del resto del batch.

## 4.10 Páginas para revisar

Una página marcada con `disc=true` va también a este batch, en su propia sección. Una página sin matrícula o `log_number` confirmados no se asigna a un avión supuesto. Tampoco se asigna automáticamente cuando su propia lectura canónica contradice al consenso del libro, cuando un campo crítico quedó en `WARNING` o cuando una inferencia solo tiene una lectura de respaldo. Una inferencia coherente respaldada por dos o más lecturas, incluida una asociación fuerte recordada de otra ejecución, conserva la lógica normal. La alineación dudosa no basta para apartar una página si matrícula y `log_number` quedaron firmes. Las páginas detectadas en blanco también van a **REVISAR**.

Las advertencias de fecha no deciden esta separación. Mes, año y día pueden inferirse con las reglas del libro, incluido el último día del mes cuando corresponde.

- En PDF único, cierra el documento bajo **REVISAR**.
- En modo de varios PDF, se incorpora a `revisar.pdf`.

Esta regla se aplica aunque no se haya seleccionado separación por matrícula.

Si la misma página también debe ir a **REVISAR**, esa sección tiene prioridad y la página aparece una sola vez. No se duplica en `discrepancias.pdf`.

## 4.11 Salidas opcionales

| Salida | Contenido |
|---|---|
| `errores.pdf` | Páginas con matrícula, fecha o `log_number` sin resolver para indexación manual. |
| `debug.pdf` | Páginas fuente procesadas, limpias y sin recuadros. Se solicita con `--debug`. |
| `recortes_firmas/` | Recortes de auditoría separados por campo y veredicto. Disponible en CLI. |

## 4.12 Registros

Los registros se llaman `app_YYYY-MM-DD.log`, rotan a diario y se conservan siete días.

- GUI y editor: `output/logs/`.
- CLI: `logs/` dentro de la carpeta de la ejecución.

El archivo registra detalle de depuración aunque la terminal muestre solo información normal.

## 4.13 Reexportación

Una reexportación usa resultados existentes y no modifica el OCR.

- Reescribe el CSV mínimo, el CSV completo, el JSON y `stats.json`.
- Conserva todos los PDF anteriores.
- Si un PDF nuevo repite un nombre, añade `-2`, `-3`, etc.
- Limpia y regenera otros artefactos derivados, como los recortes de auditoría.

> **PRECAUCIÓN:** No use una reexportación como sustituto de un OCR nuevo cuando cambió la plantilla, la flota o el contenido del PDF fuente. Esos cambios requieren procesar otra ejecución.
