"""Fondo del libro: la casilla vacía, aprendida de las propias páginas.

Una bitácora son 50 páginas del mismo formulario, y el pipeline ya las alinea
todas contra la misma referencia. La **mediana píxel a píxel** de un campo a lo
largo del libro es, por tanto, ese campo *vacío*: el recuadro impreso, el
rótulo, la línea de firma, el sello fijo y el gris de la fotocopia, todo lo
que se repite. Lo que no se repite, la escritura de cada página, desaparece de
la mediana porque en cada página cae en un sitio distinto.

Restar ese fondo deja exactamente lo que se escribió en *esta* página. Es la
misma pregunta que responde ``_paper_level`` en ``signature.py`` (¿cuál sería
el blanco del papel debajo de esta tinta?) pero contestada con evidencia del
documento en lugar de con morfología: el cierre morfológico no puede saber que
"(XII) CAPTAIN SIGNATURE" está impreso, y la mediana del libro sí.

Medido sobre 200 recortes etiquetados de una bitácora real: cuatro de los
cinco campos de firma quedan separados sin zona incierta, con márgenes entre
0.07 y 0.20 de densidad. Con el detector sin fondo, la ventana de umbral que
sirve a los cinco campos a la vez mide 0.0018.

El fondo no siempre está disponible (hacen falta varias páginas del mismo
libro y que se hayan podido alinear), así que todo aquí devuelve ``None``
cuando no hay evidencia suficiente y quien llama se queda con el detector
clásico.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import cv2
import numpy as np

# Páginas mínimas para fiarse de la mediana. Con menos de esto un solo trazo
# repetido (dos páginas firmadas igual) puede colarse en el fondo y borrarse a
# sí mismo de todas las demás.
MIN_BACKGROUND_PAGES = 8

# Cuánto más oscuro que el fondo del libro tiene que ser un píxel para contar
# como tinta escrita en esta página.
#
# El valor sale de barrer 40..115 sobre 200 recortes etiquetados. Importa más
# de lo que parece: por debajo de 70, las bandas verticales pálidas que deja el
# escáner (y los dobleces del papel, que son propios de cada página y por tanto
# no están en el fondo) cuentan como tinta e inflan la densidad de las casillas
# vacías hasta el nivel de una firma floja. A 85 la banda desaparece y el trazo
# de bolígrafo sigue entero, y es el único valor del barrido con el que los
# cinco campos separan firmadas de vacías.
INK_DELTA = 85.0


def _ink_channel(region: np.ndarray) -> np.ndarray:
    """Canal donde la tinta es más oscura, sea del color que sea.

    Mismo criterio que ``signature._ink_channel``: el mínimo de los tres
    canales conserva el trazo de un bolígrafo azul claro, que el gris aplana.
    """
    if region.ndim != 3:
        return region
    return np.min(region, axis=2)


# DPI en el que están calibrados ``INK_DELTA`` y ``MIN_BAND_WIDTH``, el mismo
# que usa el detector clásico. El fondo se construye siempre a esta escala y
# cada recorte se ajusta a él al medirlo, así que el veredicto no depende del
# DPI de la ejecución. Sin esto, los umbrales medidos a 200 dpi se quedaban
# cortos en la ejecución real del CLI, que renderiza a 150: los trazos salen más
# finos, la apertura morfológica se come una parte y todas las densidades
# bajan casi a la mitad.
REFERENCE_DPI = 200


def _fit(gray: np.ndarray, shape: Sequence[int]) -> np.ndarray:
    """Lleva un recorte a la forma canónica del campo.

    Las páginas de un PDF escaneado no siempre miden lo mismo al píxel, así que
    el mismo campo sale con recortes que difieren en dos o tres píxeles. Se
    reescalan en lugar de recortarse: el contenido es el mismo y lo que importa
    es que se superpongan. Y como el fondo está a la escala canónica, este
    ajuste es también el que normaliza el DPI de la ejecución.
    """
    if gray.shape[:2] == tuple(shape):
        return gray
    enlarging = shape[0] * shape[1] > gray.shape[0] * gray.shape[1]
    return cv2.resize(
        gray, (int(shape[1]), int(shape[0])),
        interpolation=cv2.INTER_CUBIC if enlarging else cv2.INTER_AREA,
    )


def build_background(
    crops: Sequence[np.ndarray], dpi: int = REFERENCE_DPI
) -> Optional[np.ndarray]:
    """Casilla vacía de un campo: mediana por píxel de todas sus páginas.

    Args:
        crops: Recortes del *mismo* campo en páginas ya alineadas del libro.
        dpi: Resolución a la que se renderizaron. El fondo se construye a
            ``REFERENCE_DPI`` para que los umbrales signifiquen lo mismo en
            cualquier ejecución.

    Returns:
        Imagen en grises con el fondo, o None si no hay páginas suficientes.
    """
    usable = [_ink_channel(crop) for crop in crops
              if crop is not None and crop.size]
    if len(usable) < MIN_BACKGROUND_PAGES:
        return None
    scale = REFERENCE_DPI / max(dpi, 1)
    shape = (int(np.median([crop.shape[0] for crop in usable]) * scale),
             int(np.median([crop.shape[1] for crop in usable]) * scale))
    if shape[0] < 4 or shape[1] < 4:
        return None
    stack = np.stack([_fit(crop, shape).astype(np.float32)
                      for crop in usable])
    return np.median(stack, axis=0).astype(np.uint8)


def ink_mask(region: np.ndarray, background: np.ndarray,
             delta: float = INK_DELTA) -> np.ndarray:
    """Tinta escrita en esta página: lo que oscurece el fondo del libro.

    No hace falta borrar reglas ni rótulos como en el detector clásico: si
    están impresos, están en el fondo y el residuo ya los ignora.
    """
    gray = _fit(_ink_channel(region), background.shape)
    gray = cv2.medianBlur(gray, 3)
    residual = cv2.subtract(background, gray)
    # Una página entera más oscura que el resto del libro (la fotocopia salió
    # cargada, el escáner cambió de exposición) tiene residuo positivo en todos
    # sus píxeles y se leería como una casilla llena de tinta. La mediana del
    # residuo es ese desnivel global (la mayoría de los píxeles son papel, no
    # tinta), y restarla deja la medida donde debe estar: en lo que sobresale
    # del papel de esta página, no en cómo de oscura salió la página.
    residual = cv2.subtract(residual, float(np.median(residual)))
    mask = (residual >= delta).astype(np.uint8)
    # Los speckles sueltos del escáner sobreviven a la resta igual que al
    # umbral clásico, y se quitan igual.
    return cv2.morphologyEx(
        mask, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    )


# Una componente que cruza la casilla de lado a lado y deja su recuadro casi
# vacío es una raya que atraviesa la página (la X de "anulado", el trazo de un
# tachón), no una firma. Los dos números tienen holgura de sobra: sobre dos
# bitácoras reales el resultado no cambia entre relleno 0.10 y 0.18, ni entre
# cruce 0.55 y 0.75, lo que dice que las dos poblaciones están lejos y no se
# está afinando un parámetro contra los datos.
CROSSING_SPAN = 0.55
CROSSING_FILL = 0.18


def drop_crossing_strokes(mask: np.ndarray) -> np.ndarray:
    """Quita las rayas que solo pasan por la casilla.

    Una firma llena su recuadro: aunque se extienda a lo ancho, sus trazos se
    cruzan y vuelven sobre sí mismos. Una raya que atraviesa la hoja lo cruza
    y se va, dejando un rectángulo enorme casi vacío. Esa diferencia (cuánto
    del recuadro ocupa la componente) separa las dos cosas sin tener que
    reconocer la escritura.

    Probado a la inversa también: filtrar además los fragmentos sueltos (motas
    y restos de sello) no mejoraba el resultado y en cambio se comía dígitos
    de números de licencia escritos flojo, así que se quedó solo esto.
    """
    height, width = mask.shape
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    kept = mask.copy()
    for index in range(1, count):
        box_width = stats[index, cv2.CC_STAT_WIDTH]
        box_height = stats[index, cv2.CC_STAT_HEIGHT]
        area = stats[index, cv2.CC_STAT_AREA]
        crosses = (box_width >= width * CROSSING_SPAN
                   or box_height >= height * 0.95)
        if crosses and area / max(1, box_width * box_height) < CROSSING_FILL:
            kept[labels == index] = 0
    return kept


def peak_density(mask: np.ndarray) -> float:
    """Máxima fracción de tinta en una ventana del alto del recorte.

    Es el mismo estadístico que usa el detector clásico
    (``signature._peak_density``), para que los dos caminos hablen de lo mismo
    y se puedan comparar recorte a recorte.
    """
    if mask.size == 0:
        return 0.0
    height, width = mask.shape
    window = max(9, min(height, width))
    density = cv2.boxFilter(
        mask.astype(np.float32), -1, (window, window),
        normalize=True, borderType=cv2.BORDER_CONSTANT,
    )
    return float(density.max())


# Páginas resueltas de cada clase que hacen falta para fiarse de la franja.
MIN_CONFIDENT_PAGES = 3

# Páginas extremas que se pueden descartar de cada clase para encontrar la
# franja. Una sola firma casi invisible (o una casilla vacía con un borrón)
# basta para que las dos poblaciones se toquen y el libro se quede sin franja,
# desperdiciando la evidencia de las otras treinta páginas (medido: pasaba en
# ``technician_license`` de test3, donde la firma confirmada más floja daba
# 0.0095 y una vacía llegaba a 0.0595). El límite es bajo a propósito: si hay
# que tirar más de esto, las poblaciones se solapan de verdad y lo correcto es
# no opinar.
MAX_OUTLIERS = 2

# Ancho mínimo que se le exige a la franja de duda. Una franja pegada ("por
# encima de 0.0095 es firma, por debajo de 0.0059 no") resuelve más casos,
# pero deja el veredicto a merced de un borrón: cualquier mancha que pase de
# 0.0095 pasaría por firma. Cuando la franja sale más estrecha que esto se
# descartan más extremos para ensancharla, que es preferible aunque cueste
# alguna resolución. Las franjas sanas medidas en dos bitácoras reales van de
# 0.04 a 0.08, así que este mínimo no molesta a ninguna de ellas.
MIN_BAND_WIDTH = 0.03


def confident_band(
    peaks: Sequence[float], verdicts: Sequence[str]
) -> Optional[tuple]:
    """Franja de duda del libro, aprendida de las páginas que sí se resolvieron.

    Buscar la frontera a ciegas en la distribución no funciona: en un campo
    con sellos y rayas que lo cruzan, el hueco interno del grupo de páginas
    vacías llega a ser más ancho que el que de verdad separa firmado de vacío
    (medido en ``technician_signature``). Hace falta supervisión, y el propio
    detector clásico la da gratis: en el 97% de las páginas se moja, y ahí
    acierta.

    Así que las páginas que el clásico resolvió etiquetan las dos poblaciones
    en la escala del residuo, y de ellas sale la franja: por debajo del máximo
    de las vacías, es vacío; por encima del mínimo de las firmadas, es firma.
    Lo de en medio (donde no cae ninguna página resuelta) sigue siendo duda,
    que es justo lo que impide convertir una duda honesta en un error grave.

    Args:
        peaks: Densidad de tinta sobre el fondo, por página.
        verdicts: Veredicto del detector clásico de esas mismas páginas
            ("true", "false" o "unclear"), en el mismo orden.

    Returns:
        ``(techo_de_las_vacías, suelo_de_las_firmadas)``, o None si el libro
        no da para aprenderla: pocas páginas resueltas de alguna de las dos
        clases, o las dos poblaciones solapadas (señal de que el residuo no
        está midiendo lo que se cree y más vale no opinar).
    """
    signed: List[float] = []
    empty: List[float] = []
    for peak, verdict in zip(peaks, verdicts):
        if peak is None or np.isnan(peak):
            continue
        if verdict == "true":
            signed.append(float(peak))
        elif verdict == "false":
            empty.append(float(peak))
    if len(signed) < MIN_CONFIDENT_PAGES or len(empty) < MIN_CONFIDENT_PAGES:
        return None
    signed.sort()
    empty.sort()
    # Se prueban primero las poblaciones enteras y solo se van descartando
    # extremos si hace falta: porque se tocan, o porque la franja que dejan es
    # tan estrecha que no protege de nada. Con las poblaciones ya separadas y
    # holgadas (el caso normal) esto no descarta nada.
    widest: Optional[tuple] = None
    for dropped in range(2 * MAX_OUTLIERS + 1):
        for from_signed in range(min(dropped, MAX_OUTLIERS) + 1):
            from_empty = dropped - from_signed
            if from_empty > MAX_OUTLIERS:
                continue
            if (len(signed) - from_signed < MIN_CONFIDENT_PAGES
                    or len(empty) - from_empty < MIN_CONFIDENT_PAGES):
                continue
            floor = signed[from_signed]
            ceiling = empty[len(empty) - 1 - from_empty]
            if floor <= ceiling:
                continue
            if floor - ceiling >= MIN_BAND_WIDTH:
                return (ceiling, floor)
            if widest is None or floor - ceiling > widest[1] - widest[0]:
                widest = (ceiling, floor)
    # Ninguna combinación llega al ancho mínimo: se devuelve la más holgada de
    # las que separan, que sigue siendo mejor evidencia que ninguna.
    return widest
