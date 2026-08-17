# Suite temporal de etiquetado de firmas

Sirve para una cosa: convertir su criterio sobre "aquí hay firma / aquí falta"
en números con los que ajustar el detector (`app/vision/signature.py`).

Son tres pasos y cada uno deja su resultado en disco, así que se pueden hacer
en días distintos.

Todo se ejecuta con el intérprete portable del proyecto, desde `d:\BITS`.

---

## 1. Sacar los recortes de los PDF

```
portable/python312/tools/python.exe tools/signature_labeling/extract.py --pages 1-60
```

Lee los PDF de `input/`, repite la geometría exacta del pipeline (corrección
de inclinación + alineación con las anclas del lote) y guarda un PNG por cada
campo de firma en `output/firmas_dataset/recortes/`.

Los recortes se guardan con más margen del que usa el detector: usted ve el
contexto al etiquetar, y el calibrador puede después probar márgenes más
estrechos sin volver a abrir los PDF.

Opciones útiles:

| Opción | Para qué |
|---|---|
| `--input input/test2.pdf` | un archivo concreto en vez de toda la carpeta |
| `--pages 1-60` | tramo de páginas de cada PDF |
| `--cada 5` | una de cada cinco páginas del tramo |
| `--max-por-pdf 80` | tope por archivo, repartido por todo el tramo |
| `--campos captain_signature` | solo un campo de firma |

Cada página aporta 5 recortes (los cinco campos de firma de la plantilla).
Con 100 páginas ya hay 500 recortes, que es más que suficiente: **entre 300 y
600 recortes** dan una calibración sólida sin que etiquetarlos se haga eterno.
Conviene que vengan de PDF distintos, porque la calidad del escaneo es lo que
más cambia entre lotes.

Volver a ejecutarlo añade lo que falte y **no borra las etiquetas ya puestas**.

## 2. Etiquetar

```
portable/python312/tools/python.exe tools/signature_labeling/label_gui.py
```

Una rejilla de recortes con el recuadro azul del campo dibujado encima, y
debajo el recorte seleccionado en grande. Todo con el teclado:

| Tecla | Qué hace |
|---|---|
| `F` | hay firma (o escritura: un número de licencia también cuenta) |
| `A` | ausente: el campo está vacío |
| `D` | dudosa: ni usted puede decidirlo |
| `←` `→` `↑` `↓` | moverse (etiquetar avanza solo) |
| `Retroceso` | quitar la etiqueta |
| `Ctrl+Z` | deshacer |
| `Re Pág` / `Av Pág` | cambiar de página de la rejilla |
| `V` | mostrar el veredicto actual del detector |

Criterio: lo que decide es si hay **escritura a mano dentro del recuadro
azul**. La escritura del campo vecino que se cuela por el borde no cuenta como
firma de este campo; lo que se sale un poco del recuadro pero es claramente de
este campo, sí. Si el recorte está quemado, movido o partido, es `D`: mejor
excluirlo que forzar una respuesta, porque `D` queda fuera de las métricas.

Deje `V` apagado mientras etiqueta. Ver la respuesta del detector antes de
decidir sesga la etiqueta, y estas etiquetas son la vara con la que después se
mide. Enciéndalo al terminar, junto con el filtro *Discrepan con el detector*,
para revisar en qué se equivoca.

Se guarda solo, en `output/firmas_dataset/labels.json`.

## 3. Calibrar

```
portable/python312/tools/python.exe tools/signature_labeling/tune.py
```

Mide cada recorte etiquetado con la función real del detector y busca los
umbrales que menos cuestan. El coste no es la tasa de acierto:

- **falso presente** (el sistema da por firmada una página sin firmar): 6 —
  esconde una falta real, y nadie va a ir a buscarla;
- **falso ausente** (reclama una firma que sí está): 4 — acusa en falso;
- **incierto**: 1 — solo cuesta una revisión manual.

El informe compara la configuración actual con la propuesta, valida con
particiones que no participaron en la búsqueda (para que la mejora no sea
memoria del conjunto), desglosa por campo y lista los recortes en los que se
seguiría equivocando, con su ruta para poder abrirlos.

```
portable/python312/tools/python.exe tools/signature_labeling/tune.py --aplicar
```

escribe los umbrales en `template/aircraft_log.json` (y no escribe nada si la
propuesta no mejora a la actual).

### El margen del recorte

El margen no está en la plantilla: son dos constantes de
`app/vision/signature.py` (`SIGNATURE_PAD_X` y `SIGNATURE_PAD_Y`). Si la mejor
configuración de todas pide otro margen, el informe la muestra como referencia
pero **`--aplicar` escribe la mejor configuración con el margen actual**, que
es la única que se puede aplicar sin tocar el código; escribir los umbrales de
una y dejar el margen de la otra daría una mezcla que nadie ha medido.

Si la diferencia de coste entre ambas compensa, edite las dos constantes a
mano y vuelva a ejecutar `tune.py`: las dos filas del informe pasarán a ser la
misma. Todo lo que va después de esas filas —validación cruzada, desglose por
campo y lista de errores— habla siempre de la configuración que se aplicaría.

Otras opciones: `--rapido` (rejilla reducida), `--por-campo` (umbrales propios
por campo), `--solo-alineadas`, `--cv 0` (sin validación cruzada) y los pesos
`--coste-falso-presente` / `--coste-falso-ausente` / `--coste-incierto`.

---

## Qué toca esta suite del código de producción

Nada, salvo una costura: `app/vision/signature.py` expone `_classify`, la
decisión a partir de los rasgos ya medidos, que antes estaba escrita dentro de
`detect_signature`. La calibración decide con esa misma función, de modo que
lo que se calibra es exactamente lo que corre en producción.
`tests/test_signature_labeling.py` comprueba que la versión vectorizada de la
búsqueda coincide fila a fila con ella, y que el recorte simulado desde el PNG
es idéntico al que saca el pipeline de la página.

## Cómo se quita

Es temporal: cuando ya no haga falta, se borran `tools/signature_labeling/`,
`tests/test_signature_labeling.py` y la carpeta `output/firmas_dataset/`. Lo
único que conviene conservar antes de borrar es `labels.json`: es trabajo
humano que no se regenera, y sirve para volver a calibrar el día que cambie el
detector.
