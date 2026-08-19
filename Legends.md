# Legends

Registro de los cambios de comportamiento del sistema. Cada entrada indica qué hacía antes, qué hace ahora y por qué se cambió. La descripción técnica completa vive en [docs](docs/README.md).

## 2026-08-18 — Indexación automática: vuelo, fechas, matrículas sin confirmar y sección «Revisar»

Corrida de referencia: 884 páginas de `input/Image_001..003.pdf`.

### Matrículas que no existen

Con la verificación de flota activa, una lectura que no correspondía a ningún avión del catálogo se conservaba tal cual cuando dos aviones quedaban a la misma distancia o cuando la lectura no permitía comparación. Esa lectura abría su propia sección en el PDF de entrega y su propia clave en `stats.json`, de modo que la entrega contenía aviones inexistentes: `HP-1281CMP`, `HP-1375CMP` y `HP-1820CMP`.

Ahora, cuando no hay un único avión más parecido, la asignación se elimina, la lectura queda en `alternatives` y la página pasa a **REVISAR**. Ninguna matrícula sin confirmar llega al CSV, al PDF ni a las estadísticas.

### Sección «Revisar»

Las páginas sin matrícula confirmada ya no forman un grupo `sin_matricula` junto a los aviones reales.

- PDF único: cierran el documento bajo el separador **REVISAR**, con cualquier combinación de opciones y aunque no se haya marcado «Posibles discrepancias» ni la separación por matrícula.
- Varios PDF: se escriben en `revisar.pdf`.

En la corrida de referencia son 17 páginas.

### Estado de página

El estado representaba el peor campo de la página, de modo que una firma de técnico ausente —lo normal en una bitácora de vuelo— dejaba la página en `ERROR`. Con la verificación de matrículas activa el estado se recalculaba además sobre las casillas sueltas de la fecha, así que activar esa opción convertía en error a páginas que nadie había tocado. La corrida de referencia daba 689 páginas en `ERROR` sobre 884.

El estado pasa a describir la capacidad de indexación de la página: `ERROR` solo cuando una página no blanca no aporta ninguno de los datos de índice disponibles en la plantilla; `WARNING` cuando algo requiere confirmación; `OK` cuando `log_number`, matrícula y fecha salieron de la lectura directa. Las firmas, las casillas sueltas de la fecha y el número de vuelo no deciden el estado. La política vive en `app/validation/page_status.py` y la comparten la validación de plantilla y los tres correctores.

Resultado en la corrida de referencia: 0 páginas en `ERROR`, 423 en `OK` y 461 en `WARNING`. Quedan 40 páginas con algún dato de índice sin resolver para indexación manual.

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
