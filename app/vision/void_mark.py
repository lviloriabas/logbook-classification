"""Localiza letras grandes de VOID sin depender de su posicion en el formulario.

La geometria solo propone regiones. Para anular requisitos de firmas se
exigen dos lecturas de cuatro letras compatibles en la misma zona.
"""

from pathlib import Path
import re
import cv2
import numpy as np
from app.models.schemas import OcrResult

MODELO_VOID = "PP-OCRv6_medium_rec"


def candidatos(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 35, 15)
    lines = cv2.HoughLinesP(binary, 1, np.pi / 180, threshold=80,
                            minLineLength=w * .3, maxLineGap=8)
    mask = np.zeros_like(gray)
    if lines is not None:
        for x1, y1, x2, y2 in lines[:, 0]:
            if abs(y2-y1) < abs(x2-x1)*.07 or (abs(x2-x1) < abs(y2-y1)*.035 and abs(y2-y1) > h*.6):
                cv2.line(mask, (x1, y1), (x2, y2), 255, 2)
    clean = cv2.inpaint(gray, mask, 3, cv2.INPAINT_NS)
    results = []
    seen = []
    for threshold in [110, 0]:
        b = (cv2.threshold(clean, threshold, 255, cv2.THRESH_BINARY_INV)[1] if threshold else cv2.adaptiveThreshold(clean,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY_INV,55,12))
        b = cv2.morphologyEx(b, cv2.MORPH_CLOSE, np.ones((7,7),np.uint8))
        _, _, stats, _ = cv2.connectedComponentsWithStats(b)
        boxes = np.array([s[:4] for s in stats[1:] if h*.045 < s[3] < h*.45
                          and w*.006 < s[2] < w*.3 and s[4] > 40])
        if len(boxes) < 3:
            continue
        boxes = boxes[np.sort(np.argsort(-boxes[:, 3])[:60])]
        centers = boxes[:,:2] + boxes[:,2:]/2
        for i in range(len(boxes)):
            for j in range(i+1, len(boxes)):
                delta = centers[j] - centers[i]
                norm = np.linalg.norm(delta)
                if not w*.05 < norm < w*.5:
                    continue
                unit = delta / norm
                if unit[0] < 0:
                    unit = -unit
                normal = np.array([-unit[1], unit[0]])
                height = float(np.median([boxes[i,3], boxes[j,3]]))
                chosen = np.flatnonzero((abs((centers-centers[i]) @ normal) < height*.4)
                                       & (abs((centers-centers[i]) @ unit) < height*4))
                if len(chosen) < 3 or len(chosen) > 8:
                    continue
                pts = centers[chosen]
                u = pts @ unit
                v = pts @ normal
                cw = float(np.ptp(u) + height*1.8)
                ch = float(height*1.7)
                if cw < w*.15 or not 1.15 < cw/ch < 6:
                    continue
                center = unit*((u.min()+u.max())/2) + normal*((v.min()+v.max())/2)
                if any(np.linalg.norm(center-c) < height*.45 and abs(cw-oldw)<height
                       for c,oldw in seen):
                    continue
                seen.append((center,cw))
                angle = np.degrees(np.arctan2(unit[1],unit[0]))
                results.append((len(chosen)*height,center,cw,ch,angle))
    return sorted(results,key=lambda x:-x[0])[:12]


def es_lectura_void(texto, confianza):
    """Cuatro letras completas; no acepta VOD, VOID dentro de una frase ni GOLD."""
    texto = re.sub(r"\s+", "", texto).upper()
    return confianza >= .80 and re.fullmatch(r"V[O0][I1L|/]D", texto) is not None


def motor_void():
    """El modelo debe estar precargado; nunca se descarga al procesar un PDF."""
    from app.ocr.engine import PaddleOcrEngine

    carpeta = Path(__file__).resolve().parents[2] / "portable/paddlex/official_models" / MODELO_VOID
    if not all((carpeta / archivo).is_file() for archivo in (
        "inference.json", "inference.pdiparams", "inference.yml",
    )):
        raise RuntimeError("Falta precargar el modelo portable para leer VOID")
    return PaddleOcrEngine(cpu_threads=4, rec_model=MODELO_VOID)


def detectar_void(image, engine, cancelado=lambda: False):
    """Busqueda finita en toda la hoja, con cancelacion entre grupos de recortes."""
    escala = min(1., 1800 / max(image.shape[:2]))
    if escala < 1:
        image = cv2.resize(image, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)
    h, w = image.shape[:2]
    lecturas = []
    for _, center, cw, ch, angle in candidatos(image):
        if cancelado():
            return None
        crops = []
        for delta in (0, -15, 15):
            matrix = cv2.getRotationMatrix2D(tuple(center), angle + delta, 1)
            matrix[0, 2] += cw / 2 - center[0]
            matrix[1, 2] += ch / 2 - center[1]
            crop = cv2.warpAffine(image, matrix, (round(cw), round(ch)), borderValue=(255, 255, 255))
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            for threshold in (110, 145):
                ink = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY_INV)[1]
                crops.append(255 - cv2.dilate(ink, np.ones((2, 2), np.uint8)))
        for resultados in engine.recognize_lines(crops):
            for result in resultados:
                if not es_lectura_void(result.text, result.confidence):
                    continue
                cercanas = [(c, score) for c, score in lecturas
                            if np.linalg.norm(center - c) < ch * .6]
                if cercanas:
                    confianza = min(result.confidence, max(score for _, score in cercanas))
                    box = cv2.boxPoints((tuple(center), (cw, ch), angle))
                    box = (box / np.array([w, h])).tolist()
                    return OcrResult(text="VOID", confidence=confianza, box=box)
                lecturas.append((center, result.confidence))
    return None


def revisar_voids(pdf_path, pages, template, renderer, avisar, cancelado):
    """Relee posibles discrepancias antes de exigir una intervencion humana."""
    from loguru import logger
    from app.validation.discrepancias import _clasificar_pagina, Categoria
    from app.vision.pdf_loader import render_page

    pendientes = []
    for page in pages:
        if page.blank or page.void_mark:
            continue
        resultado = _clasificar_pagina(page, template)
        if resultado and resultado[1] is Categoria.MISSING:
            pendientes.append(page)
    if not pendientes or cancelado():
        return
    try:
        engine = motor_void()
    except RuntimeError as exc:
        logger.warning("[VOID] {}; se mantienen las discrepancias", exc)
        return
    for numero, page in enumerate(pendientes, start=1):
        if cancelado():
            return
        avisar(numero - 1, len(pendientes), "Comprobando marcas VOID")
        try:
            image = (renderer.render_page(page.page_number, 120) if renderer
                     else render_page(pdf_path, page.page_number, 120))
            page.void_mark = detectar_void(image, engine, cancelado)
            if page.void_mark:
                logger.info("[VOID] Pagina {}: marca confirmada; conserva su indice", page.page_number)
        except Exception as exc:
            logger.warning("[VOID] Pagina {} sin confirmacion: {}", page.page_number, exc)
        avisar(numero, len(pendientes), "Comprobando marcas VOID")
