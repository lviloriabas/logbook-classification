"""Configuracion del indexado en AirVault.

Todo lo que cambia entre instalaciones vive en ``airvault.json``, junto al
programa. Nada de credenciales: eso se resuelve en ``app.airvault.session``
y no se guarda en disco.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

AIRVAULT_FILENAME = "airvault.json"
_CONFIG_WRITE_LOCK = threading.Lock()

# Identificadores de campo del repositorio MXDocs (repoId 3209). Son
# estables: los asigna el administrador de AirVault al definir el
# repositorio, no cambian entre batches.
CAMPO_DOC_TYPE = 9586
CAMPO_WORK_LOCATION = 9624
CAMPO_WORK_TYPE = 9627
CAMPO_MATRICULA = 9633
CAMPO_DESCRIPCION = 9752
CAMPO_FLEET = 9699
CAMPO_LESSOR = 9783
CAMPO_LOG_NUMBER = 9675
CAMPO_AUDIT_STATUS = 9754
CAMPO_START_DATE = 9594
CAMPO_END_DATE = 9593
CAMPO_BATCH_NAME = 9631

# Los seis obligatorios del tipo de documento por defecto. Si alguno queda
# vacio la pagina se guarda en amarillo (estado 3, "Need Correction"), asi
# que el indexador se niega a escribir antes de llegar a eso.
CAMPOS_OBLIGATORIOS = (
    CAMPO_DOC_TYPE,
    CAMPO_MATRICULA,
    CAMPO_FLEET,
    CAMPO_LOG_NUMBER,
    CAMPO_AUDIT_STATUS,
    CAMPO_END_DATE,
)

ESTADO_VALIDO = 0
ESTADO_SIN_PLANTILLA = 1
ESTADO_SEPARADOR = 2
ESTADO_NECESITA_CORRECCION = 3

NOMBRE_ESTADO = {
    ESTADO_VALIDO: "Valid",
    ESTADO_SIN_PLANTILLA: "No Template Match",
    ESTADO_SEPARADOR: "Separator",
    ESTADO_NECESITA_CORRECCION: "Need Correction",
}

# Como se llama cada campo en la pantalla de AirVault. Un aviso que dice
# «el campo 9633 quedaria vacio» no se puede leer sin abrir el codigo; el
# mismo aviso con el nombre se resuelve mirando la bitacora.
NOMBRE_CAMPO = {
    CAMPO_DOC_TYPE: "Doc Type",
    CAMPO_WORK_LOCATION: "Work Location",
    CAMPO_WORK_TYPE: "Work Type",
    CAMPO_MATRICULA: "Aircraft",
    CAMPO_DESCRIPCION: "Description",
    CAMPO_FLEET: "Fleet",
    CAMPO_LESSOR: "Lessor",
    CAMPO_LOG_NUMBER: "Log Page Number",
    CAMPO_AUDIT_STATUS: "Audit Status",
    CAMPO_START_DATE: "Start Date",
    CAMPO_END_DATE: "End Date",
    CAMPO_BATCH_NAME: "Batch Name",
}


def nombre_campo(field_id: int) -> str:
    """Nombre legible de un campo; el numero solo si no se conoce."""
    return NOMBRE_CAMPO.get(field_id, f"campo {field_id}")


@dataclass(frozen=True)
class AirVaultConfig:
    """Parametros de conexion y valores por defecto del indexado."""

    base_url: str = "https://airvault.criticaltech.com"
    # Enlace de acceso federado. Es el que dispara la redireccion a Entra ID;
    # entrar por la raiz deja la sesion sin la cookie de federacion.
    url_sso: str = (
        "https://airvault.criticaltech.com/zfp/?whr="
        "https://login.microsoftonline.com/"
        "9767f0dc-e83f-4cc1-94e1-0d5f9d287d32/wsfed"
    )
    repo_id: int = 3209
    index_scheme_id: int = 137
    # El picklist de Doc Type contiene "Log Page"; los batches cargados hasta
    # hoy llevan "LOG PAGE", que no existe en ese picklist y la interfaz
    # solo conserva porque lo agrega al combo. Se deja configurable para no
    # decidir por el administrador, con el valor valido como defecto.
    doc_type: str = "Log Page"
    audit_status: str = "PUBLISHED"
    # Preferencia portable compartida por todos los controles de reparto.
    # El valor inicial vive en airvault.json y cada cambio reemplaza ese valor.
    paginas_por_batch: int | None = None
    # Segundos de espera entre sondeos al buscar el batch por nombre.
    espera_descubrimiento_s: float = 20.0
    espera_maxima_s: float = 900.0
    # Quick Upload puede tardar en publicar un batch; antes de media hora no
    # se reenvia para evitar duplicar una carga todavia en proceso.
    espera_reenvio_s: float = 1800.0
    # Tiempo limite de cada peticion. El servidor cuelga la peticion de
    # apertura cuando el mismo usuario tiene el batch abierto en otra sesion,
    # asi que sin limite el proceso se queda esperando para siempre.
    timeout_s: float = 60.0
    reintentos: int = 3
    # Espera antes de reintentar, multiplicada por el numero de intento. Un
    # corte de red o un servidor ocupado no se arreglan reintentando al
    # instante; darle aire evita convertir un tropiezo en un fallo.
    espera_reintento_s: float = 5.0
    # Cuanto se espera a que alguien entre a AirVault en la ventana que abre
    # el programa. Cinco minutos dan de sobra para un segundo factor.
    espera_login_s: float = 300.0
    # Perfil de Edge propio del programa. Vacio usa el de portable/.
    perfil_navegador: str = ""
    usuario: str = ""

    @classmethod
    def load(cls, path: Path | str) -> "AirVaultConfig":
        """Carga la configuracion; si no existe el archivo, usa defectos."""
        ruta = Path(path)
        if not ruta.is_file():
            return cls()
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(datos, Mapping):
            return cls()
        return cls.from_mapping(datos)

    @classmethod
    def from_mapping(cls, datos: Mapping[str, Any]) -> "AirVaultConfig":
        campos = {f.name for f in cls.__dataclass_fields__.values()}
        limpio = {k: v for k, v in datos.items() if k in campos}
        return cls(**limpio)

    def with_overrides(self, **cambios: Any) -> "AirVaultConfig":
        """Copia con los valores que llegan por linea de comandos."""
        utiles = {k: v for k, v in cambios.items() if v is not None}
        return replace(self, **utiles) if utiles else self

    def url(self, ruta: str) -> str:
        return f"{self.base_url.rstrip('/')}/{ruta.lstrip('/')}"


def guardar_paginas_por_batch(path: Path | str, cantidad: int) -> bool:
    """Guarda la última cantidad elegida sin perder las demás opciones."""
    ruta = Path(path)
    try:
        with _CONFIG_WRITE_LOCK:
            if ruta.is_file():
                datos = json.loads(ruta.read_text(encoding="utf-8"))
                if not isinstance(datos, Mapping):
                    return False
            else:
                datos = {}
            valor = int(cantidad)
            if valor <= 0:
                return False
            datos["paginas_por_batch"] = valor
            ruta.parent.mkdir(parents=True, exist_ok=True)
            temporal = ruta.with_name(f"{ruta.name}.tmp")
            temporal.write_text(
                json.dumps(datos, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporal, ruta)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return True
