# 2. Operación de la interfaz

## 2.1 Preparación del lote

1. Compruebe que la carpeta portable esté completa.
2. Copie los PDF pendientes en `input/` o manténgalos en una carpeta de trabajo autorizada.
3. Compruebe que la plantilla corresponda al formulario escaneado.
4. Si usa verificación de flota, actualice `fleet.json` antes de procesar.
5. Compruebe que `output/` tenga espacio para la corrida y sus PDF.

> **PRECAUCIÓN:** La lista de flota debe contener todas las aeronaves vigentes. Una lista incompleta puede reclasificar una lectura válida como otra matrícula.

## 2.2 Arranque

Abra `LogbookClassification.exe`. El lanzador inicia `run_gui.py` con `portable/python312/tools/pythonw.exe` y no abre una consola.

Al iniciar, la ventana carga las plantillas disponibles y detecta automáticamente los PDF situados directamente en `input/`, ordenados por nombre.

Si el ejecutable no inicia, ejecute:

```batch
portable\python312\tools\python.exe run_gui.py
```

El mensaje de la terminal identifica una dependencia o componente portable ausente.

## 2.3 Selección de entrada

- **Seleccionar archivos…:** selecciona uno o más PDF de cualquier carpeta.
- **Detectar:** carga los PDF situados directamente en `input/`.
- **Vaciar input:** envía a la Papelera los archivos situados directamente en `input/`. No elimina `input/processed/`.
- **Vaciar output:** envía a la Papelera todo el contenido de `output/`, incluidas corridas, registros y `.performance.json`.

Las acciones de vaciado solicitan confirmación. **Vaciar output** queda bloqueado durante preprocesamiento, OCR o exportación. **Vaciar input** queda bloqueado durante OCR o exportación, pero no durante el preprocesamiento.

> **PRECAUCIÓN:** No use **Vaciar input** mientras ejecuta **Preprocesar**, aunque el mando esté disponible.

El rango **Páginas** usa una numeración continua para todo el lote. Si el primer PDF tiene 10 páginas, la página 11 del lote es la primera del segundo PDF. La interfaz cuenta previamente las páginas de todos los documentos; los que no intersectan el rango no pasan al OCR.

## 2.4 Configuración normal

Seleccione la plantilla en **Plantilla**. Use **Buscar…** para una plantilla externa o **Abrir editor** para ajustar regiones.

Mantenga activadas estas funciones durante una operación normal:

- **Corrección de inclinación:** endereza el escaneo antes de alinearlo.
- **Alineación:** ajusta cada página contra la referencia.
- **Preprocesar recortes:** localiza la tinta y escala cada región antes del OCR.
- **Verificar matrículas:** compara el resultado con `fleet.json`.

La aplicación fija PaddleOCR en CPU. La GUI trabaja a un máximo de 200 DPI para la página completa y limita la resolución al detalle nativo del PDF.

## 2.5 Configuración de salida

La entrega inicial queda configurada así:

- **Un solo PDF**;
- separación por **Matrícula**;
- sección **Posibles discrepancias** activada;
- fecha del CSV en **Día específico (si falta, fin de mes)**.

Seleccione **Varios PDF** para crear un archivo por cada combinación marcada. Active **Mes** si la entrega debe subdividirse por fecha. Active **Errores** para generar un PDF destinado a indexación manual.

Si selecciona **Varios PDF** sin marcar matrícula ni mes, el sistema genera un solo PDF porque no existe un criterio para dividir el lote.

La opción **Visualizar campos** dibuja las regiones solo en la vista previa. Nunca altera los PDF exportados. **Mostrar solo columnas importantes** limita los campos visibles cuando la visualización está activa. El botón contiguo permite guardar la selección por plantilla; esa selección también define el CSV mínimo.

## 2.6 Opciones avanzadas

- **Hilos del procesador:** presupuesto total de CPU.
- **Página de referencia:** página del documento usada para calibrar la alineación.
- **Reservar un núcleo para la interfaz:** resta un hilo del presupuesto para mantener la ventana fluida.

El sistema calcula automáticamente la cantidad de procesos OCR y los hilos internos según CPU y memoria. No es necesario ajustar esta distribución en una corrida normal.

## 2.7 Preprocesamiento de comprobación

Use **Preprocesar** cuando deba verificar geometría antes del OCR.

1. Seleccione el lote y la plantilla.
2. Active **Visualizar campos**.
3. Pulse **Preprocesar**.
4. Recorra la vista previa.
5. Confirme que los recuadros cubran los datos manuscritos.

Esta tarea aplica inclinación y alineación. No ejecuta OCR ni genera una corrida de entrega.

## 2.8 Procesamiento normal

1. Confirme entrada, rango y plantilla.
2. Confirme la lista de flota.
3. Confirme las opciones de salida.
4. Pulse **Procesar**.
5. Vigile la barra, el contador global y el avance por archivo.
6. Espere a que el estado indique que las salidas terminaron.
7. Revise la tabla, los duplicados y las páginas marcadas.
8. Abra la carpeta de la corrida desde el historial o desde `output/`.

La barra cuenta páginas terminadas del lote. Las páginas pueden finalizar fuera de orden por el procesamiento paralelo; el contador nunca representa el número de la última página entregada por un proceso.

## 2.9 Vista previa y tabla

Antes de procesar, la vista previa recorre todos los PDF como una sola secuencia. Después del OCR, conserva solo los documentos que aportaron páginas al rango. Escriba un número de página global para saltar directamente a ella. La ventana indica además el archivo y la página local.

La tabla contiene una fila por página y usa las columnas del CSV completo. El selector de vista alterna entre todas las columnas y las importantes sin cambiar el archivo guardado. El orden de una columna sigue un ciclo de tres pasos: descendente, ascendente y orden original.

El indicador **Duplicados** cuenta las páginas marcadas desde la segunda aparición de un `log_number`; la primera queda sin marca. El detalle muestra el grupo completo. Haga doble clic en una fila para llevar la vista previa a la página correspondiente.

## 2.10 Terminación y archivo de entrada

Cuando el OCR y las salidas terminan correctamente, los PDF que estaban directamente en `input/` pasan a `input/processed/`. Un nombre repetido recibe el sufijo `-2`, `-3`, etc. Los PDF seleccionados desde otras carpetas no se mueven.

La ventana actualiza sus referencias después del traslado. La vista previa y la reexportación continúan disponibles.

## 2.11 Cancelación

Pulse **Cancelar** para detener el lote en un límite seguro.

- Las páginas que ya están en ejecución terminan antes de la parada y se incluyen en el resultado parcial.
- Se conservan CSV, JSON y estadísticas de las páginas terminadas.
- No se generan PDF de entrega para la corrida parcial.
- Los archivos de entrada no pasan a `input/processed/`.
- El rango no avanza en forma automática. Ajústelo antes de continuar.
- La tabla y el avance parcial se retiran de la pantalla al guardar.

Al cancelar **Preprocesar**, se conserva únicamente la geometría terminada en memoria. No se generan CSV, JSON, estadísticas ni PDF.

> **PRECAUCIÓN:** No use una corrida cancelada como entrega ni la reexporte desde el visor histórico. El visor puede abrir sus datos parciales, pero no los convierte en una corrida completa.

## 2.12 Reexportación

Después del OCR puede cambiar separación o formato de PDF y pulsar **Exportar**. La operación reutiliza los resultados en memoria; no ejecuta OCR otra vez.

Cambiar solo **Fecha del CSV** repuebla la tabla y reescribe automáticamente los dos CSV. No pulse **Exportar** salvo que también necesite regenerar JSON, estadísticas o PDF. En el visor histórico, la nueva política de fecha sí se aplica al exportar.

La reexportación usa la misma carpeta de corrida. Reescribe el CSV, el JSON y `stats.json`. Conserva los PDF existentes y añade un sufijo numérico a cualquier PDF nuevo cuyo nombre ya exista.

## 2.13 Visor de CSV e historial

Pulse **Visor de CSV…** para abrir una corrida anterior.

1. Seleccione una de las 25 corridas recientes en **Historial**, o use **Seleccionar carpeta…** o **Seleccionar CSV…**.
2. Seleccione el CSV mínimo o completo.
3. Busque un `log_number` de siete dígitos cuando sea necesario.
4. Revise la fila y la página fuente en el panel PDF.
5. Use **Exportar** solo si la corrida conserva su JSON, plantilla y PDF fuente requeridos.

El visor puede regenerar salidas de una corrida sin repetir el OCR. Desactiva **Exportar** si falta el JSON consolidado o si el CSV no pertenece a una carpeta de corrida reconocible. Si falta la plantilla o un PDF fuente, lo informa después de pulsar **Exportar**.

Para localizar un original, el visor comprueba la ruta del JSON, la carpeta del CSV, la carpeta de la corrida, `input/` e `input/processed/`. Use **Ubicar PDF…** si el documento cambió de lugar. Para reexportar una corrida histórica, use el CSV principal en su carpeta original; su nombre base debe coincidir con el de la corrida. También deben existir el JSON consolidado, la plantilla usada y todos los PDF fuente.

## 2.14 Adaptación a la pantalla

Las ventanas se limitan al área útil del escritorio y consideran el escalado de Windows. En pantallas bajas, la ventana principal reduce márgenes y reorganiza los grupos superiores para conservar la vista previa y la tabla. Maximizar, restaurar o mover la ventana a otro monitor vuelve a calcular la distribución.
