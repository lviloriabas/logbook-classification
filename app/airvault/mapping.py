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
    CAMPO_DOC_TYPE,
    CAMPO_END_DATE,
    CAMPO_FLEET,
    CAMPO_LESSOR,
    CAMPO_LOG_NUMBER,
    CAMPO_MATRICULA,
)
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
        matricula = normalizar_matricula(fila.get("matricula", ""))
        log_number = normalizar_log_number(fila.get("log_number", ""))
        fecha = str(fila.get("date", "")).strip()
        if not matricula and not log_number and not fecha:
            continue
        seq += 1
        fleet, lessor, inferido = resolutor.resolver(matricula)
        registros.append(
            Registro(
                seq=seq,
                archivo_origen=str(fila.get("file", "")).strip(),
                pagina_origen=int(str(fila.get("page", "0")).strip() or 0),
                matricula=matricula,
                log_number=log_number,
                fecha=fecha,
                fleet=fleet if matricula else "",
                lessor=lessor,
                fleet_inferido=inferido and bool(matricula),
                duplicado=str(fila.get("dup", "")).strip().lower() == "true",
                discrepancia=(
                    str(fila.get("disc", "")).strip().lower() == "true"
                ),
            )
        )
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
    if nombre_batch:
        valores[CAMPO_BATCH_NAME] = nombre_batch
    return valores
