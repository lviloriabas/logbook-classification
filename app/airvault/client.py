"""Cliente de los endpoints del Web Index de AirVault.

Es una capa fina sobre :class:`SesionAirVault`: traduce nombres en espanol a
las rutas reales y devuelve estructuras de Python. No decide nada; toda la
logica de si algo se escribe o no vive en el indexador y en las guardas,
que se pueden probar sin red.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence

from app.airvault.config import AirVaultConfig
from app.airvault.encoding import (
    codificar_batch_id,
    codificar_sticky,
    codificar_valores,
)
from app.airvault.naming import limpiar_nombre_remoto

# Orden de las columnas que devuelve GetBatches. La respuesta es una lista
# posicional, no un diccionario, asi que el orden es parte del contrato.
COLUMNAS_LOTE = (
    "appid", "lockeduserid", "lockedusername", "appname", "batchid",
    "imagecount", "userbatchname", "batchreceivedate", "eventlabel",
    "lasteventdate", "rights", "accountid", "domainid", "userid", "eventid",
    "IdentityColumn",
)


@dataclass(frozen=True)
class ResumenLote:
    """Una fila del listado de lotes de AirVault."""

    batch_id: str
    nombre: str
    paginas: int
    repo_id: int
    repositorio: str
    paso: str
    bloqueado_por: str
    recibido: str

    @classmethod
    def desde_celdas(cls, celdas: Sequence[Any]) -> "ResumenLote":
        """Arma el resumen desde la lista posicional que manda el servidor."""
        valores = dict(zip(COLUMNAS_LOTE, list(celdas)))
        return cls.desde_fila(valores)

    @classmethod
    def desde_fila(cls, fila: Mapping[str, Any]) -> "ResumenLote":
        def entero(valor: Any) -> int:
            try:
                return int(str(valor).strip())
            except (TypeError, ValueError):
                return 0

        return cls(
            batch_id=str(fila.get("batchid", "")).strip(),
            nombre=limpiar_nombre_remoto(fila.get("userbatchname", "")),
            paginas=entero(fila.get("imagecount")),
            repo_id=entero(fila.get("appid")),
            repositorio=str(fila.get("appname", "")).strip(),
            paso=str(fila.get("eventlabel", "")).strip(),
            bloqueado_por=str(fila.get("lockedusername", "")).strip(),
            recibido=str(fila.get("batchreceivedate", "")).strip(),
        )


@dataclass(frozen=True)
class PaginaIndexada:
    """Lo que AirVault tiene guardado hoy en una pagina."""

    pagina: int
    estado: int
    valores: Dict[int, str]
    columnas: Dict[str, str]


def codificar_texto(texto: str) -> str:
    """Base64 del filtro, vacio cuando no hay filtro."""
    import base64

    limpio = str(texto or "")
    if not limpio:
        return ""
    return base64.b64encode(limpio.encode("utf-8")).decode("ascii")


class ClienteAirVault(Protocol):
    """Contrato minimo que necesita el indexador.

    Existe para que los tests inyecten un cliente falso y se pueda probar
    todo el recorrido de un lote sin tocar produccion.
    """

    def listar_lotes(self, filtro: str = "") -> List[ResumenLote]: ...

    def abrir_lote(self, batch_id: str) -> Mapping[str, Any]: ...

    def leer_pagina(self, batch_id: str, pagina: int) -> PaginaIndexada: ...

    def guardar_pagina(
        self, batch_id: str, pagina: int, valores: Mapping[int, str],
        estado: int, pagina_siguiente: Optional[int] = None,
    ) -> Mapping[str, Any]: ...

    def picklist_matriculas(self) -> List[str]: ...


class ClienteHttp:
    """Implementacion real contra el servidor."""

    def __init__(self, sesion, config: AirVaultConfig):
        self.sesion = sesion
        self.config = config

    # ── lotes ──────────────────────────────────────────────────────

    def listar_lotes(self, filtro: str = "") -> List[ResumenLote]:
        """Lista los lotes de la cola, opcionalmente filtrados por nombre.

        El filtro lo aplica el servidor como subcadena sin distinguir
        mayusculas, asi que mandarlo evita traerse la cola entera cuando
        solo interesan los lotes de un dia.
        """
        datos = self.sesion.get(
            "/index/Batch/GetBatches",
            {"repoId": -1, "eventLabel": "",
             "encodedFilter": codificar_texto(filtro),
             "encodedKeywordFilter": "",
             "_search": "false", "rows": 1000, "page": 1, "sidx": "",
             "sord": "asc"},
        )
        filas = datos.get("rows", datos) if isinstance(datos, Mapping) else datos
        resultado: List[ResumenLote] = []
        for fila in filas or []:
            celda = fila.get("cell", fila) if isinstance(fila, Mapping) else fila
            if isinstance(celda, Mapping):
                resultado.append(ResumenLote.desde_fila(celda))
            elif isinstance(celda, (list, tuple)):
                resultado.append(ResumenLote.desde_celdas(celda))
        return resultado

    def abrir_lote(self, batch_id: str) -> Mapping[str, Any]:
        """Bloquea el lote y devuelve su informacion.

        Si el mismo usuario tiene el lote abierto en un navegador, el
        servidor deja la peticion colgada. El tiempo limite de la sesion es
        lo que evita que el proceso se quede esperando indefinidamente.
        """
        return self.sesion.get(
            "/index/Batch/LockAndGetBatchInfo",
            {"repoId": self.config.repo_id,
             "encodedBatchId": codificar_batch_id(batch_id)},
        )

    def cerrar_lote(self, batch_id: str) -> Mapping[str, Any]:
        return self.sesion.get(
            "/index/Batch/UnlockBatch",
            {"repoId": self.config.repo_id,
             "encodedBatchId": codificar_batch_id(batch_id)},
        )

    # ── paginas ────────────────────────────────────────────────────

    def leer_pagina(self, batch_id: str, pagina: int) -> PaginaIndexada:
        datos = self.sesion.get(
            "/index/FormsProcessing/GetIndexFields",
            {"encodedBatchId": codificar_batch_id(batch_id),
             "repoId": self.config.repo_id, "page": pagina},
        )
        valores: Dict[int, str] = {}
        columnas: Dict[str, str] = {}
        for campo in (datos.get("RepoFields") or []):
            try:
                field_id = int(campo.get("FieldId"))
            except (TypeError, ValueError):
                continue
            valor = campo.get("Value")
            valores[field_id] = "" if valor is None else str(valor)
            columna = str(campo.get("ColumnName", "")).strip()
            if columna:
                columnas[columna] = valores[field_id]
        try:
            estado = int(datos.get("Status", 0))
        except (TypeError, ValueError):
            estado = 0
        return PaginaIndexada(
            pagina=pagina, estado=estado, valores=valores, columnas=columnas
        )

    def guardar_pagina(
        self, batch_id: str, pagina: int, valores: Mapping[int, str],
        estado: int, pagina_siguiente: Optional[int] = None,
    ) -> Mapping[str, Any]:
        """Guarda los valores de una pagina y deja abierta la siguiente."""
        return self.sesion.get(
            "/index/FormsProcessing/SaveAndGetIndexFields",
            {
                "encodedBatchId": codificar_batch_id(batch_id),
                "repoId": self.config.repo_id,
                "page": pagina,
                "nextPageToOpen": (
                    pagina if pagina_siguiente is None else pagina_siguiente
                ),
                "encodedValues": codificar_valores(valores),
                "encodedSticky": codificar_sticky([]),
                "status": estado,
            },
        )

    # ── catalogos ──────────────────────────────────────────────────

    def picklist_matriculas(self) -> List[str]:
        """Valores validos del campo Aircraft, tal como los define el admin."""
        from app.airvault.config import CAMPO_MATRICULA

        datos = self.sesion.get(
            "/index/PickList/GetPickListViews",
            {"repoId": self.config.repo_id,
             "indexSchemeId": self.config.index_scheme_id,
             "dateTime": 0},
        )
        return _valores_de_picklist(datos, CAMPO_MATRICULA)


def _valores_de_picklist(datos: Any, field_id: int) -> List[str]:
    """Extrae los valores del picklist asociado a un campo.

    La respuesta viene como una lista de vistas y el formato exacto depende
    de la version del servidor, asi que se recorre buscando la vista que
    mencione el campo y de ahi se sacan las cadenas.
    """
    vistas = datos.values() if isinstance(datos, Mapping) else datos
    for vista in vistas or []:
        if not isinstance(vista, Mapping):
            continue
        referencias = {
            str(vista.get(clave, "")) for clave in
            ("FieldId", "fieldId", "PickListId", "picklistId")
        }
        if str(field_id) not in referencias:
            continue
        valores: List[str] = []
        for item in (vista.get("Items") or vista.get("items") or []):
            if isinstance(item, Mapping):
                texto = item.get("Value") or item.get("value") or item.get(
                    "Text"
                )
            else:
                texto = item
            if texto:
                valores.append(str(texto).strip())
        if valores:
            return valores
    return []
