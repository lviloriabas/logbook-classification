"""Cliente de los endpoints del Web Index de AirVault.

Es una capa fina sobre :class:`SesionAirVault`: traduce nombres en espanol a
las rutas reales y devuelve estructuras de Python. No decide nada; toda la
logica de si algo se escribe o no vive en el indexador y en las guardas,
que se pueden probar sin red.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence

from loguru import logger

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


@dataclass(frozen=True)
class PaginaDelLote:
    """Como ve AirVault una pagina del lote, sin traerse sus valores.

    Es el mapa del lote entero en una sola peticion. Sirve para saber que
    paginas quedaron en verde sin releerlas una por una, que es lo que hay
    que mirar antes de dar el lote por terminado.
    """

    pagina: int
    estado: int
    # Primera pagina del documento al que pertenece. AirVault agrupa varias
    # paginas en un documento y el estado que cuenta es el de la primera.
    inicio_documento: int
    borrada: bool = False

    @property
    def encabeza_documento(self) -> bool:
        return self.pagina == self.inicio_documento

    @property
    def valida(self) -> bool:
        return self.estado == 0


def codificar_texto(texto: str) -> str:
    """Base64 del filtro, vacio cuando no hay filtro."""
    import base64

    limpio = str(texto or "")
    if not limpio:
        return ""
    return base64.b64encode(limpio.encode("utf-8")).decode("ascii")


class RespuestaInesperada(RuntimeError):
    """AirVault contesto algo con otra forma de la que se esperaba."""


def _describir(datos: Any) -> str:
    """Describe una respuesta rara sin volcarla entera en el mensaje."""
    if datos is None:
        return "una respuesta vacia"
    if isinstance(datos, (list, tuple)):
        return f"una lista de {len(datos)} elementos"
    texto = str(datos).strip().replace("\n", " ")
    if not texto:
        return "una respuesta vacia"
    return f"«{texto[:120]}»" + ("…" if len(texto) > 120 else "")


class ClienteAirVault(Protocol):
    """Contrato minimo que necesita el indexador.

    Existe para que los tests inyecten un cliente falso y se pueda probar
    todo el recorrido de un lote sin tocar produccion.
    """

    def listar_lotes(self, filtro: str = "") -> List[ResumenLote]: ...

    def abrir_lote(self, batch_id: str) -> Mapping[str, Any]: ...

    def cerrar_lote(self, batch_id: str) -> Mapping[str, Any]: ...

    def renombrar_lote(self, batch_id: str, nombre: str) -> bool: ...

    def leer_pagina(self, batch_id: str, pagina: int) -> PaginaIndexada: ...

    def guardar_pagina(
        self, batch_id: str, pagina: int, valores: Mapping[int, str],
        estado: int, pagina_siguiente: Optional[int] = None,
    ) -> Mapping[str, Any]: ...

    def picklist_matriculas(self) -> List[str]: ...

    def paginas_del_lote(self, batch_id: str) -> List[PaginaDelLote]: ...

    def borrar_pagina(self, batch_id: str, pagina: int,
                      borrada: bool = True) -> bool: ...

    def completar_lote(self, batch_id: str) -> Mapping[str, Any]: ...


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
        """Suelta el lote que abrio :meth:`abrir_lote`.

        Hay que llamarlo siempre, tambien cuando el indexado se corta a
        medias: AirVault admite un solo dueno por lote, asi que un lote que
        queda bloqueado deja colgada la siguiente apertura —la del propio
        programa o la de la persona que lo abre en el navegador— sin decir
        por que.
        """
        return self.sesion.get(
            "/index/Batch/UnlockBatch",
            {"repoId": self.config.repo_id,
             "encodedBatchId": codificar_batch_id(batch_id)},
        )

    def renombrar_lote(self, batch_id: str, nombre: str) -> bool:
        """Le pone al lote el nombre con el que se le va a reconocer.

        Hace falta porque Quick Upload no admite ninguno: todo lo que sube
        el programa llega a la cola como «Empty-Batch», y asi nadie
        distingue en la pantalla un lote de otro. Es la misma accion
        «Rename» que ofrece el Web Index.

        Devuelve si el servidor lo acepto. No se levanta error: el lote ya
        esta subido y encontrado, y quedarse sin nombre bonito no es razon
        para tirar el trabajo.
        """
        limpio = str(nombre or "").strip()
        if not limpio:
            return False
        try:
            self.sesion.post(
                "/index/Batch/UpdateBatchName",
                data={"repoId": self.config.repo_id, "batchId": batch_id,
                      "encodedBatchName": codificar_texto(limpio)},
            )
        except Exception as exc:  # noqa: BLE001 - el trabajo sigue igual
            logger.info("No se pudo renombrar el lote {}: {}", batch_id, exc)
            return False
        logger.info("El lote {} quedo como {!r}", batch_id, limpio)
        return True

    def borrar_pagina(self, batch_id: str, pagina: int,
                      borrada: bool = True) -> bool:
        """Quita una pagina del lote, o la devuelve.

        Es la papelera de la tira de paginas del Web Index. No borra el
        archivo: marca la pagina, que deja de contar como documento y deja
        de estorbar para dar el lote por terminado. Se puede deshacer
        mientras el lote siga en la cola, con ``borrada=False``.

        Devuelve si AirVault lo acepto. Quitar paginas pide un permiso
        aparte —«Delete Batch Image»— que no toda cuenta tiene, y quedarse
        sin el no es motivo para tirar el trabajo: lo que sigue es mirar
        las paginas otra vez y decir que el lote no se puede cerrar.
        """
        try:
            self.sesion.post(
                "/index/FormsProcessing/MarkPageDeleted",
                data={"repoId": self.config.repo_id, "batchId": batch_id,
                      "page": int(pagina),
                      "markDeleted": "true" if borrada else "false"},
            )
        except Exception as exc:  # noqa: BLE001 - el lote sigue entero
            logger.info(
                "No se pudo {} la pagina {} del lote {}: {}",
                "quitar" if borrada else "devolver", pagina, batch_id, exc,
            )
            return False
        return True

    def paginas_del_lote(self, batch_id: str) -> List[PaginaDelLote]:
        """El mapa del lote entero en una sola peticion.

        Es lo que dibuja la tira de paginas del Web Index, y trae el estado
        de cada una sin sus valores. Se usa para saber si el lote esta
        entero en verde, que es la condicion para darlo por terminado: con
        una peticion por pagina eso serian cuatrocientas.
        """
        datos = self.sesion.get(
            "/index/FormsProcessing/GetBatchPages",
            {"repoId": self.config.repo_id,
             "encodedBatchId": codificar_batch_id(batch_id)},
        )
        filas = (
            datos.get("fpisForWebIndex") if isinstance(datos, Mapping)
            else datos
        )
        if not isinstance(filas, (list, tuple)):
            raise RespuestaInesperada(
                f"AirVault no devolvio las paginas del lote {batch_id}, sino "
                f"{_describir(datos)}."
            )
        paginas: List[PaginaDelLote] = []
        for fila in filas:
            if not isinstance(fila, Mapping):
                continue
            def entero(clave: str, defecto: int = 0) -> int:
                try:
                    return int(fila.get(clave, defecto) or defecto)
                except (TypeError, ValueError):
                    return defecto

            secuencia = entero("Sequence")
            paginas.append(PaginaDelLote(
                pagina=secuencia,
                estado=entero("Status"),
                inicio_documento=entero("SequenceStart", secuencia) or secuencia,
                borrada=bool(entero("Deleted")),
            ))
        return paginas

    def completar_lote(self, batch_id: str) -> Mapping[str, Any]:
        """Da el lote por terminado y lo saca de la cola del Web Index.

        Es el boton «Complete» de la pantalla. AirVault solo lo acepta con
        el lote entero en verde; por eso quien lo llama mira antes las
        paginas y no lo intenta a ciegas.
        """
        respuesta = self.sesion.get(
            "/index/FormsProcessing/CompleteBatch",
            {"repoId": self.config.repo_id,
             "encodedBatchId": codificar_batch_id(batch_id)},
        )
        if isinstance(respuesta, Mapping) and respuesta.get("IsError"):
            raise RespuestaInesperada(
                f"AirVault no dio por terminado el lote {batch_id}: "
                f"{respuesta.get('Message') or 'sin motivo'}"
            )
        return respuesta if isinstance(respuesta, Mapping) else {}

    # ── paginas ────────────────────────────────────────────────────

    def leer_pagina(self, batch_id: str, pagina: int) -> PaginaIndexada:
        datos = self.sesion.get(
            "/index/FormsProcessing/GetIndexFields",
            {"encodedBatchId": codificar_batch_id(batch_id),
             "repoId": self.config.repo_id, "page": pagina},
        )
        if not isinstance(datos, Mapping):
            raise RespuestaInesperada(
                f"AirVault no devolvio los campos de la pagina {pagina} del "
                f"lote {batch_id}, sino {_describir(datos)}. Suele ser que "
                f"el lote ya no tenga esa pagina."
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
        """Guarda los valores de una pagina y deja abierta la siguiente.

        Va por POST, que es como lo manda el Web Index. Por GET la ruta ni
        siquiera existe: ASP.NET contesta «The resource cannot be found»
        con un 404, que se leia como «esa pagina ya no esta en el lote».
        """
        return self.sesion.post_json(
            "/index/FormsProcessing/SaveAndGetIndexFields",
            data={
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
