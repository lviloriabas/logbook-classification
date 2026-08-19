# 5. Plantillas, libros y flota

## 5.1 Finalidad de la plantilla

La plantilla define qué regiones se leen y qué reglas se aplican. Cada región usa coordenadas relativas entre 0 y 1; por ello, la misma plantilla puede trabajar a distintas resoluciones.

La plantilla predeterminada es `template/aircraft_log.json`. El administrador de plantillas carga los archivos `*.json` situados directamente en `template/`.

## 5.2 Estructura

```json
{
  "name": "Aircraft Log",
  "version": "1.0",
  "page_size": [1641, 1275],
  "fields": [
    {
      "id": "log_number",
      "type": "ocr",
      "required": true,
      "x": 0.8097,
      "y": 0.0356,
      "w": 0.1419,
      "h": 0.0330,
      "regex": "^\\d{7}$",
      "postprocess": "digits",
      "ocr_mode": "line"
    }
  ]
}
```

`page_size` sirve como referencia del editor. El proceso convierte `x`, `y`, `w` y `h` al tamaño real de cada página.

## 5.3 Tipos de campo

| Tipo | Tratamiento |
|---|---|
| `ocr` | Lectura de texto y confianza. |
| `text` | Misma ruta de lectura; uso semántico para texto libre. |
| `date` | Misma ruta de lectura; la función de fecha depende de `postprocess`. |
| `signature` | Presencia, ausencia o resultado incierto por análisis de tinta. |
| `checkbox` | Estado marcado o vacío por cobertura de tinta. |

La plantilla de producción no contiene campos `checkbox`.

## 5.4 Propiedades de campo

| Propiedad | Función |
|---|---|
| `id` | Identificador único en resultados y reportes. |
| `required` | Exige un valor para la regla del campo. |
| `regex` | Define el formato aceptado. |
| `min_length`, `max_length` | Limitan la longitud. |
| `postprocess` | Selecciona la normalización: matrícula, dígitos, fecha, carácter o vuelo. |
| `localize` | Con `ink`, ajusta el recorte a la tinta localizada. |
| `ocr_mode` | `line` para una sola línea; `detect` para localizar texto primero. |
| `min_ink_ratio`, `max_ink_ratio` | Límites generales de tinta. |
| `min_components` | Cantidad mínima de trazos o componentes. |
| `ink_delta` | Diferencia mínima entre tinta y papel local. |
| `min_ink_peak`, `max_empty_peak` | Umbrales de firma presente y vacía. |
| `min_ink_span` | Extensión horizontal mínima de escritura. |
| `sig_present_conf`, `sig_absent_conf` | Confianza exigida para presencia o ausencia firme. |

Al cargar, el sistema rechaza identificadores duplicados, expresiones regulares inválidas y rectángulos que salgan de la página.

## 5.5 Campos de la plantilla de producción

| Grupo | Campos |
|---|---|
| Índice | `log_number`, `matricula`, `flight_number` |
| Fecha principal | `day`, `month`, `year` |
| Celdas de fecha | `day_1`, `day_2`, `month_1` a `month_3`, `year_1`, `year_2` |
| Vuelo | `pilot_signature`, `captain_signature`, `captain_license` |
| Mantenimiento | `technician_signature`, `technician_license` |

Son obligatorios `log_number`, `matricula`, día, mes, año y las firmas de piloto, capitán y técnico. La lógica de discrepancias decide qué firmas corresponden al tipo de entrada; el atributo `required` por sí solo no realiza esa clasificación.

Todos los campos OCR de la plantilla de producción usan `line`. Una lectura que no supera su formato se repite automáticamente con detección completa.

## 5.6 Editor visual

Inicie el editor desde **Abrir editor** o con:

```batch
portable\python312\tools\python.exe run_editor.py
```

Procedimiento:

1. Pulse **Abrir PDF**.
2. Sitúe la página de referencia.
3. Pulse **Cargar plantilla** si va a modificar una existente.
4. Seleccione un campo en la lista.
5. Dibuje un rectángulo sobre el dato.
6. Mueva o redimensione el rectángulo hasta cubrir la región correcta.
7. Repita para cada campo requerido.
8. Pulse **Guardar plantilla** y asigne nombre y ruta.

El editor trabaja sobre una página renderizada a 150 DPI y guarda las coordenadas con cuatro decimales. Para los identificadores incorporados, vuelve a aplicar los presets de tipo, obligatoriedad, expresión regular, postproceso, localización y parámetros básicos de tinta. Conserva desde el campo cargado las longitudes, `ocr_mode` y los umbrales avanzados que el editor no reemplaza. Los campos adicionales se mantienen, pero su propiedad `localize` puede volver a `null`.

> **PRECAUCIÓN:** El editor está destinado principalmente a geometría. Si una plantilla contiene reglas personalizadas, compare el JSON antes y después de guardarla.

> **PRECAUCIÓN:** Cambiar de página vuelve a renderizar el lienzo y retira los rectángulos visibles. Configure y guarde la geometría en una sola página de referencia antes de navegar.

Atajos principales:

- `Ctrl+O`: abrir PDF;
- `Ctrl+S`: guardar plantilla;
- flechas: cambiar de página;
- `Supr`: retirar el campo seleccionado;
- `Ctrl++` y `Ctrl+-`: ajustar zoom.

## 5.7 Control de geometría

Después de guardar una plantilla:

1. selecciónela en la ventana principal;
2. active **Visualizar campos**;
3. ejecute **Preprocesar** sobre páginas representativas;
4. compruebe las regiones en escaneos desplazados e inclinados;
5. corrija la plantilla si un campo queda cortado o incluye otro dato.

No ajuste una plantilla únicamente contra una página limpia. Incluya páginas con variación real de posición.

## 5.8 Lista de flota

`fleet.json` tiene esta forma:

```json
{
  "version": 1,
  "matriculas": ["HP-1534CMP", "HP-1990WWP"]
}
```

Use **Editar lista de matrículas…** para agregar o retirar aeronaves. El editor normaliza, elimina duplicados y guarda la lista en la raíz del programa.

La lista solo afecta una corrida cuando está activa **Verificar matrículas** o cuando la CLI usa `--verificar-flota`. La CLI la ignora por defecto.

> **PRECAUCIÓN:** Trate `fleet.json` como el catálogo completo. Una matrícula canónica que no aparezca allí se considera inexistente para la corrida.

## 5.9 Identificación de libros

El libro se identifica con:

```text
(primeros cinco dígitos de log_number, tramo 00–49 o 50–99)
```

La agrupación atraviesa límites de PDF. Todas las páginas con la misma clave quedan juntas y se ordenan por `log_number`.

Si falta `log_number`:

- con un solo libro conocido, la página se agrega a ese libro;
- con varios libros posibles, las páginas desconocidas quedan en un grupo separado y no se asignan por posición del PDF.

> **PRECAUCIÓN:** En un lote con varios libros, revise las páginas sin `log_number` antes de aceptar inferencias. Al quedar juntas en el grupo no identificado, pueden compartir evidencia que no pertenece al mismo libro físico.

## 5.10 Datos manuscritos de fecha

La forma normal es `DD|MMM|AA`. Los separadores verticales pertenecen al formulario, no al valor. El mes puede aparecer como abreviatura española o inglesa y, en casos raros, como número.

La geometría se detecta por página. Las siete celdas individuales sirven como evidencia adicional para resolver caracteres, confirmar un mes numérico y evitar que una coordenada fija corte la escritura.
