"""Recorrido del lote: comprueba, escribe y deja constancia.

El indexador es deliberadamente aburrido. Antes de tocar nada verifica el
lote completo; despues escribe pagina por pagina guardando el manifiesto
tras cada una, de modo que una interrupcion no obliga a repetir trabajo ni
deja dudas sobre que se alcanzo a escribir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from loguru import logger

from app.airvault.config import (
    CAMPO_FLEET,
    CAMPO_LESSOR,
    CAMPO_MATRICULA,
    ESTADO_VALIDO,
)
from app.airvault.guards import (
    Aviso,
    ErrorDeGuarda,
    verificar_alineacion,
    verificar_cantidad,
    verificar_duplicados,
    verificar_matriculas,
    verificar_no_pisar,
    verificar_obligatorios,
)
from app.airvault.mapping import ResolutorFlota, valores_de_indice
from app.airvault.model import EstadoRegistro, Manifiesto, Registro
from app.airvault.session import ErrorDeConexion, ErrorDeSesion

# Fallos que no son de una pagina sino del camino entero: insistir con la
# siguiente solo sirve para marcar cuatrocientas paginas con el mismo error.
FALLOS_DE_CAMINO = (ErrorDeSesion, ErrorDeConexion)


@dataclass
class PlanPagina:
    """Lo que se haria en una pagina concreta."""

    seq: int
    pagina_batch: int
    registro: Registro
    valores: Dict[int, str]
    avisos: List[Aviso] = field(default_factory=list)
    ya_indexada: bool = False

    @property
    def escribible(self) -> bool:
        """Una divisoria nunca se escribe: no es un documento que indexar."""
        return not self.avisos and not self.registro.es_separador


@dataclass
class Plan:
    """Plan completo del lote: lo que se escribiria y lo que no."""

    batch_id: str
    paginas: List[PlanPagina] = field(default_factory=list)
    avisos_globales: List[Aviso] = field(default_factory=list)

    @property
    def escribibles(self) -> List[PlanPagina]:
        return [p for p in self.paginas if p.escribible]

    @property
    def bloqueadas(self) -> List[PlanPagina]:
        return [p for p in self.paginas
                if not p.escribible and not p.registro.es_separador]

    @property
    def separadores(self) -> List[PlanPagina]:
        return [p for p in self.paginas if p.registro.es_separador]

    def resumen(self) -> Dict[str, int]:
        return {
            "total": len(self.paginas),
            "escribibles": len(self.escribibles),
            "bloqueadas": len(self.bloqueadas),
            "separadores": len(self.separadores),
            "avisos_globales": len(self.avisos_globales),
        }


@dataclass
class Resultado:
    """Resultado de una escritura real."""

    escritas: int = 0
    omitidas: int = 0
    fallidas: int = 0
    detalles: List[str] = field(default_factory=list)
    # Motivo por el que se corto el lote entero, si se corto. Lo que queda
    # sin escribir sigue pendiente en el manifiesto y se retoma despues.
    interrumpido: str = ""


class Indexador:
    """Aplica un manifiesto sobre un lote de AirVault."""

    def __init__(
        self,
        cliente,
        manifiesto: Manifiesto,
        picklist_matriculas: Sequence[str] = (),
        sobrescribir: bool = False,
        al_guardar: Optional[Callable[[Manifiesto], None]] = None,
        resolutor: Optional[ResolutorFlota] = None,
    ):
        self.cliente = cliente
        self.manifiesto = manifiesto
        self.picklist = list(picklist_matriculas)
        self.sobrescribir = sobrescribir
        self._al_guardar = al_guardar
        # El resolutor aprende del propio lote: AirVault ya trae la flota
        # resuelta por su lookup en las paginas preindexadas, y eso vale
        # mucho mas que la regla de prefijos que usamos de respaldo.
        self.resolutor = resolutor or ResolutorFlota()

    # ── planificacion ──────────────────────────────────────────────

    def planificar(self, paginas_lote: int) -> Plan:
        """Construye el plan sin escribir nada.

        Es exactamente el mismo camino que sigue :meth:`aplicar`, asi que lo
        que muestra el dry run es lo que se va a enviar, no una version
        simplificada.
        """
        batch_id = self.manifiesto.batch_id or ""
        if not batch_id:
            raise ErrorDeGuarda("El manifiesto no tiene lote asignado")

        registros = self.manifiesto.registros
        verificar_cantidad(registros, paginas_lote)

        globales: List[Aviso] = []
        globales.extend(verificar_matriculas(registros, self.picklist))
        globales.extend(verificar_duplicados(registros))

        # Primero se lee todo el lote y se aprende la flota que AirVault ya
        # tiene resuelta; asi los registros cuya flota veniamos infiriendo se
        # corrigen antes de construir los valores que se van a escribir.
        remotas: Dict[int, object] = {}
        ilegibles: Dict[int, str] = {}
        for indice, registro in enumerate(registros, start=1):
            if registro.es_separador:
                # Una divisoria no se lee: no tiene indices que aprender ni
                # con que contrastar, y son peticiones de mas contra el
                # servidor.
                continue
            pagina = registro.pagina_batch or indice
            try:
                remotas[registro.seq] = self.cliente.leer_pagina(
                    batch_id, pagina
                )
            except FALLOS_DE_CAMINO:
                # La sesion o la red se cayeron: leer las demas no va a ir
                # mejor y el mensaje que importa es este.
                raise
            except Exception as exc:  # noqa: BLE001 - se anota y se sigue
                # Una pagina que no carga bloquea solo a esa pagina. Sin
                # poder leerla no se puede comprobar que el lote y el
                # manifiesto hablan de la misma bitacora, asi que no se
                # escribe; el resto del lote no tiene por que esperarla.
                ilegibles[registro.seq] = str(exc)
                logger.warning(
                    "No se pudo leer la pagina {} del lote {}: {}",
                    pagina, batch_id, exc,
                )
        self._aprender_flota(remotas.values())
        self._corregir_flota_inferida()

        plan = Plan(batch_id=batch_id)
        por_seq: Dict[int, List[Aviso]] = {}
        for aviso in globales:
            por_seq.setdefault(aviso.seq, []).append(aviso)

        for indice, registro in enumerate(registros, start=1):
            pagina = registro.pagina_batch or indice
            if registro.es_separador:
                plan.paginas.append(PlanPagina(
                    seq=registro.seq, pagina_batch=pagina, registro=registro,
                    valores={}, avisos=[], ya_indexada=False,
                ))
                continue
            valores = valores_de_indice(
                registro,
                self.manifiesto.doc_type,
                self.manifiesto.audit_status,
                self.manifiesto.nombre_batch,
            )
            avisos = list(por_seq.get(registro.seq, ()))
            avisos.extend(verificar_obligatorios(registro, valores))

            remota = remotas.get(registro.seq)
            if remota is None:
                avisos.append(Aviso(
                    registro.seq, "no_cargo",
                    f"AirVault no devolvio la pagina {pagina}: "
                    f"{ilegibles.get(registro.seq, 'sin respuesta')}",
                ))
            else:
                avisos.extend(verificar_alineacion(registro, remota.valores))
                avisos.extend(verificar_no_pisar(
                    registro, remota.estado, self.sobrescribir
                ))

            plan.paginas.append(PlanPagina(
                seq=registro.seq,
                pagina_batch=pagina,
                registro=registro,
                valores=valores,
                avisos=avisos,
                ya_indexada=(
                    remota is not None and remota.estado == ESTADO_VALIDO
                ),
            ))

        plan.avisos_globales = [
            a for a in globales if a.seq not in {p.seq for p in plan.paginas}
        ]
        return plan

    def _aprender_flota(self, remotas) -> None:
        """Guarda los pares matricula/flota que AirVault ya tiene puestos."""
        for remota in remotas:
            matricula = str(remota.valores.get(CAMPO_MATRICULA, "")).strip()
            fleet = str(remota.valores.get(CAMPO_FLEET, "")).strip()
            lessor = str(remota.valores.get(CAMPO_LESSOR, "")).strip()
            if matricula and fleet:
                self.resolutor.aprender(matricula, fleet, lessor)

    def _corregir_flota_inferida(self) -> None:
        """Reemplaza la flota adivinada por la que AirVault confirma."""
        for registro in self.manifiesto.registros:
            if registro.es_separador:
                continue
            if not registro.fleet_inferido or not registro.matricula:
                continue
            fleet, lessor, inferido = self.resolutor.resolver(
                registro.matricula
            )
            if not inferido and fleet:
                registro.fleet = fleet
                registro.lessor = registro.lessor or lessor
                registro.fleet_inferido = False

    # ── escritura ──────────────────────────────────────────────────

    def aplicar(
        self, plan: Plan, detener_en_error: bool = True,
        al_avanzar: Optional[Callable[[int, int], None]] = None,
    ) -> Resultado:
        """Escribe las paginas escribibles del plan.

        Las paginas con avisos se saltan siempre: el plan ya decidio que no
        se pueden tocar y aqui no se vuelve a opinar.

        ``al_avanzar`` recibe cuantas paginas se llevan escritas de cuantas
        habia previstas, para que la interfaz pueda mover la barra sin que
        el indexador sepa que existe una interfaz.
        """
        resultado = Resultado()
        previstas = len(plan.escribibles)
        for entrada in plan.paginas:
            registro = entrada.registro
            if registro.es_separador:
                # No cuenta como omitida: nunca hubo nada que escribirle.
                continue
            if not entrada.escribible:
                registro.estado = EstadoRegistro.OMITIDA
                registro.avisos = [str(a) for a in entrada.avisos]
                resultado.omitidas += 1
                continue
            if registro.estado is EstadoRegistro.ESCRITA:
                resultado.omitidas += 1
                continue
            try:
                self.cliente.guardar_pagina(
                    plan.batch_id,
                    entrada.pagina_batch,
                    entrada.valores,
                    ESTADO_VALIDO,
                    entrada.pagina_batch,
                )
            except FALLOS_DE_CAMINO as exc:
                # Se cayo la sesion o la red. Seguir escribiendo marcaria
                # como fallidas paginas que nadie llego a intentar; se para
                # y lo que queda sigue pendiente para retomarlo.
                resultado.interrumpido = str(exc)
                resultado.detalles.append(
                    f"pagina {entrada.pagina_batch}: {exc}"
                )
                logger.error(
                    "Se corto el indexado en la pagina {}: {}",
                    entrada.pagina_batch, exc,
                )
                self._persistir()
                break
            except Exception as exc:  # noqa: BLE001 - se anota y se sigue
                registro.estado = EstadoRegistro.ERROR
                registro.avisos = [f"[error_escritura] {exc}"]
                resultado.fallidas += 1
                resultado.detalles.append(
                    f"pagina {entrada.pagina_batch}: {exc}"
                )
                logger.error(
                    "Fallo al escribir la pagina {}: {}",
                    entrada.pagina_batch, exc,
                )
                self._persistir()
                if detener_en_error:
                    break
                continue
            registro.estado = EstadoRegistro.ESCRITA
            registro.pagina_batch = entrada.pagina_batch
            registro.avisos = []
            resultado.escritas += 1
            self._persistir()
            if al_avanzar is not None:
                al_avanzar(resultado.escritas, previstas)
        return resultado

    def _persistir(self) -> None:
        if self._al_guardar is not None:
            self._al_guardar(self.manifiesto)


def verificar_lote(
    cliente, manifiesto: Manifiesto
) -> tuple[int, int, List[str]]:
    """Relee el lote y cuenta cuantas paginas quedaron validas.

    Devuelve ``(validas, revisadas, problemas)``. Se usa despues de escribir
    para confirmar contra el servidor, no contra lo que creemos haber
    hecho.
    """
    batch_id = manifiesto.batch_id or ""
    validas = 0
    problemas: List[str] = []
    bitacoras = [r for r in manifiesto.registros if not r.es_separador]
    for registro in bitacoras:
        pagina = registro.pagina_batch or registro.seq
        try:
            remota = cliente.leer_pagina(batch_id, pagina)
        except FALLOS_DE_CAMINO:
            raise
        except Exception as exc:  # noqa: BLE001 - se anota y se sigue
            # Comprobar es leer: una pagina que no carga se cuenta como no
            # comprobada, no como mal escrita.
            problemas.append(f"pagina {pagina}: no se pudo leer ({exc})")
            continue
        if remota.estado == ESTADO_VALIDO:
            validas += 1
        else:
            problemas.append(
                f"pagina {pagina}: estado {remota.estado}"
            )
    return validas, len(bitacoras), problemas
