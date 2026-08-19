"""Cliente falso de AirVault para probar el indexado sin tocar el servidor.

Guarda en memoria lo que le escriben, de modo que un test puede afirmar
exactamente que paginas se tocaron, con que valores y en que orden.
"""

from __future__ import annotations

from typing import Dict, List, Mapping, Optional

from app.airvault.client import PaginaIndexada, ResumenLote


class ClienteFalso:
    def __init__(
        self,
        paginas: Optional[Dict[int, PaginaIndexada]] = None,
        lotes: Optional[List[ResumenLote]] = None,
        picklist: Optional[List[str]] = None,
        page_count: int = 0,
        fallar_en: Optional[set[int]] = None,
    ):
        self.paginas = paginas or {}
        self.lotes = lotes or []
        self.picklist = picklist or []
        self.page_count = page_count or len(self.paginas)
        self.fallar_en = fallar_en or set()
        self.escrituras: List[tuple[int, Dict[int, str], int]] = []
        self.filtros: List[str] = []
        self.lecturas: List[int] = []
        self.abiertos: List[str] = []
        self.cerrados: List[str] = []

    # ── contrato que usa el indexador ──────────────────────────────

    def listar_lotes(self, filtro: str = "") -> List[ResumenLote]:
        self.filtros.append(filtro)
        if not filtro:
            return list(self.lotes)
        objetivo = filtro.strip().lower()
        return [l for l in self.lotes if objetivo in l.nombre.lower()]

    def abrir_lote(self, batch_id: str) -> Mapping[str, object]:
        self.abiertos.append(batch_id)
        return {"pageCount": self.page_count, "batchId": batch_id}

    def cerrar_lote(self, batch_id: str) -> Mapping[str, object]:
        self.cerrados.append(batch_id)
        return {"ok": True}

    def leer_pagina(self, batch_id: str, pagina: int) -> PaginaIndexada:
        self.lecturas.append(pagina)
        return self.paginas.get(
            pagina,
            PaginaIndexada(pagina=pagina, estado=3, valores={}, columnas={}),
        )

    def guardar_pagina(self, batch_id, pagina, valores, estado,
                       pagina_siguiente=None):
        if pagina in self.fallar_en:
            raise RuntimeError(f"fallo simulado en la pagina {pagina}")
        self.escrituras.append((pagina, dict(valores), estado))
        self.paginas[pagina] = PaginaIndexada(
            pagina=pagina, estado=estado, valores=dict(valores), columnas={}
        )
        return {"ok": True}

    def picklist_matriculas(self) -> List[str]:
        return list(self.picklist)


def pagina(numero: int, estado: int = 3,
           valores: Optional[Mapping[int, str]] = None) -> PaginaIndexada:
    """Pagina tal como la devolveria AirVault.

    Los valores llegan en un diccionario y no como argumentos con nombre
    porque las claves son identificadores numericos de campo.
    """
    return PaginaIndexada(
        pagina=numero, estado=estado,
        valores={int(k): str(v) for k, v in (valores or {}).items()},
        columnas={},
    )


def lote(batch_id: str, nombre: str, paginas: int = 0,
         repo_id: int = 3209, bloqueado_por: str = "") -> ResumenLote:
    return ResumenLote(
        batch_id=batch_id, nombre=nombre, paginas=paginas, repo_id=repo_id,
        repositorio="MXDocs", paso="Web Index",
        bloqueado_por=bloqueado_por, recibido="",
    )
