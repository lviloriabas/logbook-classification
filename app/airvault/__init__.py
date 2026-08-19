"""Indexado automatico de lotes de bitacoras en AirVault.

El modulo toma el CSV que ya produce la corrida de clasificacion y escribe
esos valores en las paginas del batch correspondiente de AirVault, sin que
nadie tenga que teclear pagina por pagina.

Las etapas son independientes y reanudables. El estado vive en un unico
manifiesto por trabajo (``app.airvault.manifest``), asi que se puede
procesar hoy, subir manana y indexar despues sin repetir nada.
"""

from __future__ import annotations

__all__ = [
    "AirVaultConfig",
    "Manifiesto",
    "Registro",
]

from app.airvault.config import AirVaultConfig
from app.airvault.model import Manifiesto, Registro
