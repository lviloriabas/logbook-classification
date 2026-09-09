# Revision de bitacoras, septiembre de 2026

## Hallazgos

El problema de la revision automatica tenia dos partes distintas. Se descartaba
el guardado entero cuando faltaba un campo obligatorio, aunque el logpage y otros
datos fueran utilizables. Ademas, la comprobacion final exigia estado Valid a las
paginas de REVISAR que el propio indexador guardaba en Need Correction. Una
incidencia humana pendiente parecia entonces un indexado que nunca terminaba.

Se estudiaron 18 resultados guardados, 15 con paginas. El analisis reconstruye
la clasificacion y la separacion con las reglas actuales y la fecha de cada
ejecucion. No repite el OCR ni demuestra la exactitud de los campos.

| Ejecucion | Paginas | A revisar | Indice completo entre las revisadas |
| --- | ---: | ---: | ---: |
| 21 AUG 2026 06 43 | 2409 | 227 | 193 |
| 25 AUG 2026 23 29 | 498 | 156 | 49 |
| 27 AUG 2026 18 01 | 122 | 8 | 8 |
| 05 SEP 2026 20 13 | 328 | 31 | 30 |
| 06 SEP 2026 11 56 | 280 | 14 | 12 |

Un indice completo no elimina una falta real de firma o una contradiccion de
fecha. Tampoco se deben sumar las ejecuciones: algunas contienen los mismos
escaneos. Las reglas de firmas que ya estaban en el repositorio reducen, al
recalcular la corrida de 2409 paginas, las discrepancias guardadas de 324 a 106.
Esa diferencia no se atribuye a los cambios de esta tarea ni constituye una
medicion de falsos positivos.

## Cambios

- REVISAR envia los campos disponibles y omite los obligatorios sin lectura.
  Nunca manda un obligatorio explicitamente vacio para conseguir ese guardado.
  Cada guardado se relee antes de confirmarlo. Si AirVault exige tambien el campo
  omitido, el fallo permanece visible y las otras paginas continuan.
- La etapa automatica acaba al comprobar los valores enviados, aunque una
  incidencia humana conserve la pagina amarilla. Al reanudar, los guardados
  equivalentes ya confirmados se omiten. Un dato perdido sigue pendiente.
- La causa viaja por pagina en el indice de entrega. Una pagina completa sin
  incidencia independiente puede quedar Valid. Los manifiestos antiguos sin
  esa evidencia conservan su revision.
- Se muestran paginas comprobadas y avance durante la verificacion de REVISAR.
  Los reintentos se refieren a paginas sin confirmar. El batch de revision
  conserva sus separadores y no se completa ni publica automaticamente.
- Se conserva el formato del CSV. Las causas estructuradas viajan en el JSON
  de entrega y en el manifiesto; los resumenes generados de entregas anteriores
  tambien se pueden traducir al catalogo de AirVault.

## ECN Reason

Consultado en Edge, con el perfil de trabajo y la sesion del usuario, en
MX:MXDocs, busqueda Key Fields Log Page, el 8 de septiembre de 2026.
Solo se escribe ECN Reason (9692). No se escriben 2nd ECN Reason ni 3rd ECN Reason.

| Falta confirmada | Valor exacto |
| --- | --- |
| Firma o licencia de capitan | DISCREPANCY NOTE: MISSING SIGNATURE AND/OR LICENSE NUMBER OF THE CAPTAIN |
| Firma de piloto | DISCREPANCY NOTE: MISSING SIGNATURE AND/OR LICENSE NUMBER OF THE PILOT |
| Firma de tecnico | DISCREPANCY NOTE: MISSING TECHNICIAN SIGNATURE |
| Licencia de tecnico | DISCREPANCY NOTE: MISSING LICENSE NUMBER |

Siempre se elige una sola categoria por pagina, con la prioridad solicitada:
capitan, piloto y tecnico. Firma y licencia de capitan comparten categoria.
Si al tecnico le faltan firma y licencia, se elige la firma; si solo falta la
licencia, se usa su categoria. Se conserva una anotacion que ya exista en ECN
Reason. Los campos secundarios existentes no se modifican. Una lectura incierta
no genera una categoria de falta confirmada.

## VOID

La ausencia de firmas no identifica una VOID. Las imagenes mostraron VOID
grandes sobre paginas que todavia conservan vuelos, firmas y otras anotaciones.
La marca debe buscarse por toda la pagina, sin depender de una casilla fija.

El detector propone regiones por el tamano de los trazos, prueba orientaciones
y contrastes y exige dos lecturas compatibles de las cuatro letras en la misma
zona. Rechaza palabras incompletas, frases y lecturas de baja puntuacion.
Una marca confirmada elimina el reclamo de firmas, pero conserva el avion,
logpage y la fecha para el procedimiento habitual de lectura o inferencia.
No resuelve por si sola una identidad contradictoria o una fecha desconocida.

La muestra visual dirigida contiene cinco VOID y siete paginas sin VOID:
el detector confirma dos de las cinco y no marca ninguna de las siete negativas.
Cuatro de las VOID tenian firmas que el clasificador reclamaba; se corrigen
automaticamente dos de esos cuatro casos en esta muestra. No es una estimacion
representativa del porcentaje global de revision. Permanecen sin reconocer
ejemplos de letras muy separadas, trazo fino y recortes cuya orientacion confunde
al reconocedor. No se rebaja el requisito de evidencia para ocultarlos.

La busqueda esta acotada a doce regiones y seis variantes por region. Solo se
aplica antes de confirmar posibles discrepancias, conserva resultados positivos
y admite cancelacion entre grupos. En esta maquina la muestra tardo aproximadamente
4 a 21 segundos por pagina tras la primera carga. Es un coste adicional real;
conviene mejorar la localizacion antes de extender la busqueda a todas las hojas.

El reconocedor de marcas grandes es PP-OCRv6_medium_rec, separado del OCR
habitual de los campos. Se precarga con tools/precache_paddle.py, trabaja solo
en CPU y se verifico con las conexiones de red bloqueadas. Si faltan sus
archivos portables, no intenta descargarlos al procesar: mantiene la discrepancia
y deja constancia del motivo.

## Reproduccion y limites

La version aislada de los cambios previos del usuario pasa 1888 pruebas y
16 subpruebas. Tambien se comprobo la convivencia con esas ediciones locales.

Ejecutar desde la raiz:

```powershell
.\portable\python312\tools\python.exe -X utf8 tools/estudiar_revision.py --salida output/estudio_revision
.\portable\python312\tools\python.exe -m pytest tests -q
```

El estudio genera estudio.md y causas.json con motivos, archivo, pagina y
logpage. La evaluacion local de imagenes queda en
output/estudio_revision/muestra_void.json. No se modificaron entregas historicas
ni se hicieron guardados reales en AirVault durante esta tarea. El guardado
parcial esta cubierto con clientes simulados que aceptan y rechazan omisiones;
su aceptacion por la configuracion real del servidor necesita comprobarse en
una carga operativa. Nunca se da por guardada una pagina solo porque se envio.
