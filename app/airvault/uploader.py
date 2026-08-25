"""Subida de archivos a AirVault por Quick Upload.

Quick Upload crea un batch nuevo a partir de los archivos que se le envian y
lo deja en la cola de Web Index. La subida va por trozos, como la hace la
propia pagina, y despues se confirma cada archivo con sus valores de
indice.

Aviso importante: Quick Upload solo expone los campos marcados para ese
modulo, y entre ellos no estan Log Page Number, Fleet ni End Date. Por eso
la subida deja el batch clasificado pero no indexado, y el indexado real lo
hace :mod:`app.airvault.indexer` despues. Si el administrador habilita esos
campos para Quick Upload, esta etapa podria cerrarlo todo de una vez.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from loguru import logger

# Campos que Quick Upload acepta hoy en el repositorio MXDocs.
CAMPOS_QUICK_UPLOAD = {
    9586: "C_DocType",
    9754: "C_AuditStatus",
    9633: "C_ACREG",
    9630: "C_DocNo",
    9750: "C_SN",
    9749: "C_PN",
    9631: "C_BatchName",
    9812: "C_EmergencyResponse",
    9813: "C_AirworthinessCertificate",
    9809: "C_BUName",
}

TROZO_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ResultadoSubida:
    archivo: str
    ok: bool
    detalle: str = ""


def valores_quick_upload(valores: Mapping[int, str]) -> List[Dict[str, Any]]:
    """Arma el ``InputValues`` que espera ``Home/FinishUpload``.

    Solo viajan los campos que el modulo admite; el resto se descarta en
    silencio porque el servidor los rechazaria.
    """
    salida: List[Dict[str, Any]] = []
    for field_id, columna in CAMPOS_QUICK_UPLOAD.items():
        valor = str(valores.get(field_id, "") or "")
        salida.append({
            "FieldId": str(field_id),
            "WarnEmpty": False,
            "Key": columna,
            "Value": valor,
            "Valid": True,
            "Dirty": bool(valor),
            "OriginalValue": "",
        })
    return salida


def trozos(ruta: Path, tamano: int = TROZO_BYTES):
    """Parte el archivo tal como lo hace el cargador de la pagina."""
    total = max(1, -(-ruta.stat().st_size // tamano))
    with ruta.open("rb") as handle:
        for indice in range(total):
            yield indice, total, handle.read(tamano)


class SubidorQuickUpload:
    """Sube archivos y confirma sus indices."""

    def __init__(self, sesion, repo_id: int):
        self.sesion = sesion
        self.repo_id = repo_id

    def subir(
        self, ruta: Path | str, valores: Mapping[int, str],
        avisar: Optional[Callable[[str, int, int], None]] = None,
    ) -> ResultadoSubida:
        """Sube un archivo y confirma sus valores de indice.

        Los PDF de una ejecución completa pesan casi dos gigas y viajan en
        trozos de un mega, asi que sin ``avisar`` la subida parece colgada
        durante media hora.
        """
        archivo = Path(ruta)
        if not archivo.is_file():
            return ResultadoSubida(str(archivo), False, "no existe")
        for indice, total, datos in trozos(archivo):
            if avisar is not None:
                avisar(f"Subiendo {archivo.name}", indice, total)
            # Reenviar un trozo con el mismo indice es inocuo: el servidor
            # arma el archivo por posicion, no por orden de llegada.
            self.sesion.post(
                "/quickuploadex/Home/Upload/",
                data={
                    "repoId": self.repo_id,
                    "filename": archivo.name,
                    "name": archivo.name,
                    "chunk": indice,
                    "chunks": total,
                },
                files={"file": (archivo.name, datos,
                                "application/octet-stream")},
            )
        self.sesion.post(
            "/quickuploadex/Home/FinishUpload",
            json={"model": {
                "RepoId": self.repo_id,
                "FileName": archivo.name,
                "InputValues": valores_quick_upload(valores),
            }},
        )
        logger.info("Subido {}", archivo.name)
        return ResultadoSubida(archivo.name, True)

    def subir_varios(
        self, rutas: Sequence[Path], valores: Mapping[int, str]
    ) -> List[ResultadoSubida]:
        return [self.subir(ruta, valores) for ruta in rutas]
