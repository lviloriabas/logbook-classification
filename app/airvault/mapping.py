"""Del CSV de la corrida a los valores de indice de AirVault.

Aqui viven las tres traducciones que hacen falta: el formato de fecha, la
matricula contra el picklist de AirVault y la flota que AirVault deduce de
la matricula con su propio lookup.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence

from app.airvault.config import (
    CAMPO_AUDIT_STATUS,
    CAMPO_BATCH_NAME,
    CAMPO_DESCRIPCION,
    CAMPO_DOC_TYPE,
    CAMPO_END_DATE,
    CAMPO_FLEET,
    CAMPO_LESSOR,
    CAMPO_LOG_NUMBER,
    CAMPO_MATRICULA,
)
from app.airvault.fechas import fechas_inferidas
from app.airvault.model import Registro

FLOTA_CACHE_FILENAME = "airvault_flota.json"

_FECHA_CSV_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})$")
_MATRICULA_RE = re.compile(r"^(HP|HK)-\d{4}(CMP|WWP)?$")
_LOG_NUMBER_RE = re.compile(r"^\d{7}$")

# Regla de respaldo cuando la matricula no esta en el cache. AirVault
# resuelve la flota con un procedimiento almacenado contra su base, asi que
# esto es solo una aproximacion: lo que salga de aqui se marca como
# inferido y el reporte de revision lo muestra aparte para que alguien lo
# confirme antes de escribir.
_PREFIJOS_FLOTA = (
    ("HK-", "EMB"),
    ("HP-98", "MAX"),
    ("HP-99", "MAX"),
)
_FLOTA_POR_DEFECTO = "NG"


def fecha_airvault(fecha_csv: str) -> str:
    """Convierte ``YYYY/MM/dd`` del CSV al ``MM/DD/YYYY`` de AirVault.

    Devuelve cadena vacia si la fecha no viene o no tiene el formato del
    CSV: es preferible que la guarda posterior acuse el campo obligatorio
    vacio a mandar una fecha inventada.
    """
    match = _FECHA_CSV_RE.match(str(fecha_csv or "").strip())
    if not match:
        return ""
    anio, mes, dia = match.groups()
    return f"{mes}/{dia}/{anio}"


def normalizar_matricula(valor: str) -> str:
    """Deja la matricula como la escribe el picklist de AirVault."""
    limpio = str(valor or "").strip().upper().replace(" ", "")
    return limpio if _MATRICULA_RE.fullmatch(limpio) else ""


def normalizar_log_number(valor: str) -> str:
    """El log number son siete digitos exactos; cualquier otra cosa no va."""
    limpio = re.sub(r"\D", "", str(valor or ""))
    return limpio if _LOG_NUMBER_RE.fullmatch(limpio) else ""


class ResolutorFlota:
    """Traduce matricula a flota y arrendador.

    Primero mira el cache local, que se alimenta de lo que AirVault ya tiene
    indexado. Solo si no hay dato aplica la regla de prefijos, y en ese caso
    avisa de que el valor es inferido.
    """

    def __init__(self, cache: Mapping[str, Mapping[str, str]] | None = None):
        self._cache: Dict[str, Dict[str, str]] = {
            normalizar_matricula(k): {
                "fleet": str(v.get("fleet", "")),
                "lessor": str(v.get("lessor", "")),
            }
            for k, v in (cache or {}).items()
            if normalizar_matricula(k)
        }

    @classmethod
    def load(cls, path: Path | str) -> "ResolutorFlota":
        ruta = Path(path)
        if not ruta.is_file():
            return cls()
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if isinstance(datos, Mapping):
            return cls(datos.get("matriculas", datos))
        return cls()

    def guardar(self, path: Path | str) -> Path:
        ruta = Path(path)
        ruta.write_text(
            json.dumps(
                {"version": 1, "matriculas": self._cache},
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return ruta

    def aprender(self, matricula: str, fleet: str, lessor: str = "") -> None:
        """Guarda un par visto en AirVault para no volver a inferirlo."""
        clave = normalizar_matricula(matricula)
        if not clave or not fleet:
            return
        actual = self._cache.setdefault(clave, {"fleet": "", "lessor": ""})
        actual["fleet"] = fleet
        if lessor:
            actual["lessor"] = lessor

    def resolver(self, matricula: str) -> tuple[str, str, bool]:
        """Devuelve ``(fleet, lessor, inferido)`` para la matricula."""
        clave = normalizar_matricula(matricula)
        if not clave:
            return "", "", False
        conocido = self._cache.get(clave)
        if conocido and conocido.get("fleet"):
            return conocido["fleet"], conocido.get("lessor", ""), False
        for prefijo, flota in _PREFIJOS_FLOTA:
            if clave.startswith(prefijo):
                return flota, "", True
        return _FLOTA_POR_DEFECTO, "", True

    def conocidas(self) -> int:
        return len(self._cache)


def leer_csv_corrida(path: Path | str) -> List[dict]:
    """Lee el CSV minimo de una corrida respetando el BOM que escribe Excel."""
    ruta = Path(path)
    with ruta.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def registros_desde_csv(
    filas: Iterable[Mapping[str, str]],
    resolutor: ResolutorFlota | None = None,
    orden: Sequence[tuple[str, int]] | None = None,
) -> List[Registro]:
    """Construye los registros del manifiesto a partir del CSV.

    Args:
        filas: filas del CSV de la corrida.
        resolutor: traductor de matricula a flota.
        orden: pares ``(archivo, pagina)`` en el orden exacto en que las
            paginas quedaron en el PDF que se sube. Sin esta lista se asume
            que el lote conserva el orden del CSV, que solo es cierto
            cuando el PDF se genero sin separar ni reordenar.

    Las paginas en blanco del CSV (sin log number ni matricula) se
    descartan: no llegan al PDF que se sube, asi que incluirlas descuadraria
    la correspondencia con las paginas del lote.
    """
    resolutor = resolutor or ResolutorFlota()
    filas = list(filas)
    inferidas = fechas_inferidas(filas)
    por_clave: Dict[tuple[str, int], Mapping[str, str]] = {}
    secuencia: List[Mapping[str, str]] = []
    for fila in filas:
        archivo = str(fila.get("file", "")).strip()
        try:
            pagina = int(str(fila.get("page", "")).strip())
        except ValueError:
            pagina = 0
        por_clave[(archivo, pagina)] = fila
        secuencia.append(fila)

    if orden is not None:
        elegidas = [por_clave[c] for c in orden if c in por_clave]
    else:
        elegidas = secuencia

    registros: List[Registro] = []
    seq = 0
    for fila in elegidas:
        if _en_blanco(fila):
            continue
        seq += 1
        registros.append(_registro_de_fila(seq, fila, resolutor, inferidas))
    return registros


def _en_blanco(fila: Mapping[str, str]) -> bool:
    """La pagina no aporta ningun dato de indice, asi que no llega al PDF."""
    return not any((
        normalizar_matricula(fila.get("matricula", "")),
        normalizar_log_number(fila.get("log_number", "")),
        str(fila.get("date", "")).strip(),
    ))


def _registro_de_fila(
    seq: int,
    fila: Mapping[str, str],
    resolutor: ResolutorFlota,
    inferidas: Mapping[tuple[str, int], tuple[str, str]] | None = None,
) -> Registro:
    """Traduce una fila del CSV al registro que viaja en el manifiesto.

    ``inferidas`` trae las fechas deducidas para las bitacoras que llegaron
    sin ella (ver :mod:`app.airvault.fechas`). Solo se usa cuando la fila no
    trae una fecha propia: una lectura nunca se pisa con una deduccion.
    """
    matricula = normalizar_matricula(fila.get("matricula", ""))
    fleet, lessor, inferido = resolutor.resolver(matricula)
    archivo = str(fila.get("file", "")).strip()
    pagina = int(str(fila.get("page", "0")).strip() or 0)
    fecha = str(fila.get("date", "")).strip()
    fecha_inferida = ""
    if not _FECHA_CSV_RE.match(fecha):
        fecha, fecha_inferida = (inferidas or {}).get(
            (archivo, pagina), (fecha, "")
        )
    return Registro(
        seq=seq,
        archivo_origen=archivo,
        pagina_origen=pagina,
        matricula=matricula,
        log_number=normalizar_log_number(fila.get("log_number", "")),
        flight_number=str(fila.get("flight_number", "")).strip().upper(),
        fecha=fecha,
        fecha_inferida=fecha_inferida,
        fleet=fleet if matricula else "",
        lessor=lessor,
        fleet_inferido=inferido and bool(matricula),
        duplicado=str(fila.get("dup", "")).strip().lower() == "true",
        discrepancia=str(fila.get("disc", "")).strip().lower() == "true",
    )


def leer_indice_paginas(path: Path | str) -> List[dict]:
    """Lee el indice de la entrega que escribe la corrida.

    Devuelve una entrada por archivo de entrega, ``{"pdf", "paginas"}``, con
    las paginas en el orden en que estan dentro de ese archivo. Cada archivo
    es un lote distinto en AirVault, asi que el reparto importa tanto como
    el orden.

    Sin indice devuelve una lista vacia y quien llame decide si puede seguir
    sin el.
    """
    ruta = Path(path)
    if not ruta.is_file():
        return []
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(datos, Mapping):
        return []
    if datos.get("partes") is not None:
        crudas = datos.get("partes") or []
    else:
        # Version 1: un unico archivo, con las paginas en la raiz.
        crudas = [{"pdf": datos.get("pdf", ""),
                   "paginas": datos.get("paginas") or []}]
    partes: List[dict] = []
    for parte in crudas:
        if not isinstance(parte, Mapping):
            continue
        paginas = [
            p for p in (parte.get("paginas") or []) if isinstance(p, Mapping)
        ]
        if paginas:
            partes.append({"pdf": str(parte.get("pdf", "")),
                           "revisar": bool(parte.get("revisar", False)),
                           "paginas": paginas})
    return partes


def registros_desde_entrega(
    filas: Iterable[Mapping[str, str]],
    indice: Sequence[Mapping[str, object]],
    resolutor: ResolutorFlota | None = None,
) -> List[Registro]:
    """Construye los registros siguiendo el PDF que se sube, no el CSV.

    El lote de AirVault tiene una pagina por cada pagina del PDF, y el PDF
    lleva separadores que el CSV no tiene. Recorrer el indice en vez del
    CSV es lo que mantiene ``seq`` igual a la pagina del lote: los
    separadores ocupan su sitio y quedan marcados para que nadie les
    escriba nada.
    """
    resolutor = resolutor or ResolutorFlota()
    filas = list(filas)
    # Las fechas se deducen con el CSV entero, no con las paginas de esta
    # parte: una ejecucion repartida en varios lotes sigue siendo un solo
    # juego de libros, y las anclas de un libro pueden haber caido en otra
    # parte.
    inferidas = fechas_inferidas(filas)
    por_clave: Dict[tuple[str, int], Mapping[str, str]] = {}
    for fila in filas:
        archivo = str(fila.get("file", "")).strip()
        try:
            pagina = int(str(fila.get("page", "")).strip())
        except ValueError:
            continue
        por_clave[(archivo, pagina)] = fila

    registros: List[Registro] = []
    for seq, entrada in enumerate(indice, start=1):
        etiqueta = str(entrada.get("separador", "") or "").strip()
        if etiqueta:
            registros.append(Registro(seq=seq, separador=etiqueta))
            continue
        archivo = str(entrada.get("archivo", "") or "").strip()
        try:
            pagina = int(str(entrada.get("pagina", 0)))
        except (TypeError, ValueError):
            pagina = 0
        fila = por_clave.get((archivo, pagina))
        if fila is None:
            registros.append(Registro(
                seq=seq, archivo_origen=archivo, pagina_origen=pagina,
                avisos=[
                    f"[sin_fila] la pagina {pagina} de {archivo} esta en el "
                    f"PDF pero no en el CSV"
                ],
            ))
            continue
        registros.append(_registro_de_fila(seq, fila, resolutor, inferidas))
    return registros


def valores_de_indice(
    registro: Registro,
    doc_type: str,
    audit_status: str,
    nombre_batch: str = "",
) -> Dict[int, str]:
    """Diccionario ``{fieldId: valor}`` que se manda al guardar la pagina.

    Solo se incluyen los campos que el sistema controla. Los demas se dejan
    fuera a proposito: lo que no se manda, AirVault lo conserva tal como
    estaba, asi que un indexado no pisa datos que alguien haya puesto a
    mano.
    """
    valores: Dict[int, str] = {
        CAMPO_DOC_TYPE: doc_type,
        CAMPO_MATRICULA: registro.matricula,
        CAMPO_FLEET: registro.fleet,
        CAMPO_LOG_NUMBER: registro.log_number,
        CAMPO_AUDIT_STATUS: audit_status,
        CAMPO_END_DATE: fecha_airvault(registro.fecha),
    }
    if registro.lessor:
        valores[CAMPO_LESSOR] = registro.lessor
    if registro.flight_number:
        # El vuelo de la bitacora, en Description. Solo cuando la lectura
        # lo trajo: mandarlo vacio borraria lo que alguien haya escrito a
        # mano, y este campo no es de los que el sistema controla siempre.
        valores[CAMPO_DESCRIPCION] = registro.flight_number
    if nombre_batch:
        valores[CAMPO_BATCH_NAME] = nombre_batch
    return valores
