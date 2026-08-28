"""Recorrido del batch: comprueba, escribe y deja constancia.

El indexador es deliberadamente aburrido. Antes de tocar nada verifica el
batch completo; despues escribe pagina por pagina guardando el manifiesto
tras cada una, de modo que una interrupcion no obliga a repetir trabajo ni
deja dudas sobre que se alcanzo a escribir.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from loguru import logger

from app.airvault.config import (
    CAMPO_DESCRIPCION,
    CAMPO_FLEET,
    CAMPO_LESSOR,
    CAMPO_LOG_NUMBER,
    CAMPO_MATRICULA,
    CAMPO_WORK_LOCATION,
    ESTADO_NECESITA_CORRECCION,
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
from app.airvault.session import (
    ErrorDeAirVault,
    ErrorDeConexion,
    ErrorDeSesion,
)

# Fallos que no son de una pagina sino del camino entero: insistir con la
# siguiente solo sirve para marcar cuatrocientas paginas con el mismo error.
FALLOS_DE_CAMINO = (ErrorDeSesion, ErrorDeConexion)

# Estos avisos describen calidad incompleta, no una correspondencia rota: se
# envia lo disponible y AirVault deja la pagina pendiente de revision.
#
# «obligatorio_vacio» no esta en la lista y no puede estarlo, ni siquiera en
# REVISAR: AirVault no acepta la pagina y contesta 500 «Field <campo> value
# is required». Mandarla no la deja amarilla, la rechaza, y el rechazo se
# llevaba por delante el resto del batch.
AVISOS_DE_REVISION = {
    "matricula_vacia",
    "matricula_desconocida",
    "log_duplicado",
}


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
        return not self.bloqueos and not self.registro.es_separador

    @property
    def bloqueos(self) -> List[Aviso]:
        return [a for a in self.avisos if a.codigo not in AVISOS_DE_REVISION]

    @property
    def requiere_revision(self) -> bool:
        return self.escribible and bool(self.avisos)

    @property
    def queda_incompleta(self) -> bool:
        """AirVault no puede validarla porque falta un campo obligatorio."""
        return any(a.codigo == "obligatorio_vacio" for a in self.avisos)


@dataclass
class Plan:
    """Plan completo del batch: lo que se escribiria y lo que no."""

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
            # Paginas cuya End Date no se leyo y se dedujo del libro. No
            # bloquean, pero son las primeras que hay que mirar en el
            # reporte antes de aprobar la escritura.
            "fechas_inferidas": sum(
                1 for p in self.paginas if p.registro.fecha_inferida
            ),
        }


@dataclass
class Resultado:
    """Resultado de una escritura real."""

    escritas: int = 0
    omitidas: int = 0
    fallidas: int = 0
    separadores_borrados: int = 0
    separadores_pendientes: int = 0
    detalles: List[str] = field(default_factory=list)
    # Motivo por el que se corto el batch entero, si se corto. Lo que queda
    # sin escribir sigue pendiente en el manifiesto y se retoma despues.
    interrumpido: str = ""


class Indexador:
    """Aplica un manifiesto sobre un batch de AirVault."""

    def __init__(
        self,
        cliente,
        manifiesto: Manifiesto,
        picklist_matriculas: Sequence[str] = (),
        sobrescribir: bool = False,
        al_guardar: Optional[Callable[[Manifiesto], None]] = None,
        resolutor: Optional[ResolutorFlota] = None,
        permitir_log_distinto: bool = False,
    ):
        self.cliente = cliente
        self.manifiesto = manifiesto
        self.picklist = list(picklist_matriculas)
        self.sobrescribir = sobrescribir
        self.permitir_log_distinto = permitir_log_distinto
        self._al_guardar = al_guardar
        # El resolutor aprende del propio batch: AirVault ya trae la flota
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
            raise ErrorDeGuarda("El manifiesto no tiene batch asignado")

        registros = self.manifiesto.registros
        verificar_cantidad(
            registros, paginas_lote,
            self.manifiesto.separadores_borrados(),
        )

        globales: List[Aviso] = []
        globales.extend(verificar_matriculas(registros, self.picklist))
        globales.extend(verificar_duplicados(registros))

        # Primero se lee todo el batch y se aprende la flota que AirVault ya
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
                # poder leerla no se puede comprobar que el batch y el
                # manifiesto hablan de la misma bitacora, asi que no se
                # escribe; el resto del batch no tiene por que esperarla.
                ilegibles[registro.seq] = str(exc)
                logger.warning(
                    "No se pudo leer la pagina {} del batch {}: {}",
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
                self.manifiesto.audit_status_discrepancia,
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
                avisos.extend(verificar_alineacion(
                    registro, remota.valores, self.permitir_log_distinto,
                    remota.estado,
                ))
                work_location = str(
                    remota.valores.get(CAMPO_WORK_LOCATION, "") or ""
                ).strip()
                # Incluso una pagina Valid se vuelve a guardar si AirVault
                # lleno Work Location. Es el unico caso en que se toca una
                # pagina verde sin pedir sobrescritura: el flujo exige ese
                # campo vacio y el payload conserva el resto de sus datos.
                if not (remota.estado == ESTADO_VALIDO and work_location):
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
                    remota is not None
                    and remota.estado == ESTADO_VALIDO
                    and not str(
                        remota.valores.get(CAMPO_WORK_LOCATION, "") or ""
                    ).strip()
                ),
            ))

        plan.avisos_globales = [
            a for a in globales if a.seq not in {p.seq for p in plan.paginas}
        ]
        self._anotar_plan(plan)
        return plan

    def _anotar_plan(self, plan: Plan) -> None:
        """Deja en la bitacora por que no se va a escribir cada pagina.

        Sin esto, un batch que no escribe nada solo se nota en AirVault: el
        indexado dice «omitidas 400» y hay que abrir el manifiesto para
        saber si fue una guarda, cual, o si AirVault ya las daba por buenas.
        Una linea por batch con el recuento y las primeras paginas basta
        para no volver a adivinarlo.
        """
        bitacoras = len(plan.paginas) - len(plan.separadores)
        bloqueadas = plan.bloqueadas
        logger.info(
            "Batch {}: {} bitacoras, {} por escribir, {} bloqueadas, "
            "{} divisorias",
            plan.batch_id, bitacoras, len(plan.escribibles),
            len(bloqueadas), len(plan.separadores),
        )
        if not bloqueadas:
            return
        motivos = Counter(
            aviso.codigo for entrada in bloqueadas
            for aviso in entrada.bloqueos
        )
        paginas = [str(entrada.pagina_batch) for entrada in bloqueadas[:8]]
        if len(bloqueadas) > len(paginas):
            paginas.append("...")
        logger.warning(
            "Batch {}: {} de {} bitacoras no se pueden escribir ({}). "
            "Paginas {}. La primera: {}",
            plan.batch_id, len(bloqueadas), bitacoras,
            ", ".join(f"{codigo} x{cuantas}"
                      for codigo, cuantas in motivos.most_common()),
            ", ".join(paginas),
            bloqueadas[0].bloqueos[0],
        )

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

        Solo se saltan las paginas con bloqueos de correspondencia o de
        seguridad. Los avisos de calidad permiten enviar los datos
        confirmados y dejar la pagina en amarillo para su revision.

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
                # Una pagina que AirVault ya confirma en Valid no se
                # degrada en el manifiesto solo porque la guarda contra
                # sobreescritura la bloqueo al volver a planificar.
                if not entrada.ya_indexada:
                    registro.estado = EstadoRegistro.OMITIDA
                    registro.avisos = [str(a) for a in entrada.avisos]
                resultado.omitidas += 1
                continue
            if (
                registro.estado is EstadoRegistro.ESCRITA
                and entrada.ya_indexada
            ):
                resultado.omitidas += 1
                continue
            try:
                valores = dict(entrada.valores)
                # La marca identifica exclusivamente la escritura por API.
                # Con vuelo va despues de el; sin vuelo es todo el contenido.
                # No forma parte del CSV ni del plan/reporte local. Las
                # bitacoras de REVISAR se terminan a mano, asi que no la
                # llevan: su Description queda con el vuelo leido, y sin
                # vuelo el campo ni se manda para no borrar lo escrito.
                if not self.manifiesto.solo_subir:
                    vuelo = registro.flight_number.strip()
                    valores[CAMPO_DESCRIPCION] = (
                        f"{vuelo} AUTO INDEX" if vuelo else "AUTO INDEX"
                    )
                # Work Location no se usa en este flujo. Se envia de forma
                # explicita para limpiar cualquier valor que AirVault haya
                # heredado o completado por su cuenta.
                valores[CAMPO_WORK_LOCATION] = ""
                estado = (
                    ESTADO_NECESITA_CORRECCION
                    if self.manifiesto.solo_subir or entrada.queda_incompleta
                    else ESTADO_VALIDO
                )
                self.cliente.guardar_pagina(
                    plan.batch_id,
                    entrada.pagina_batch,
                    valores,
                    estado,
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
            except ErrorDeAirVault as exc:
                # AirVault contesto y dijo que no acepta *esta* pagina. No
                # es el camino: la siguiente puede escribirse sin problema,
                # asi que se anota y se sigue aunque se haya pedido parar en
                # el primer error. Una bitacora que el servidor rechaza no
                # puede dejar sin indexar a las cuatrocientas de atras.
                registro.estado = EstadoRegistro.ERROR
                registro.avisos = [f"[rechazada] {exc}"]
                resultado.fallidas += 1
                resultado.detalles.append(
                    f"pagina {entrada.pagina_batch}: {exc}"
                )
                logger.error(
                    "AirVault rechazo la pagina {}: {}",
                    entrada.pagina_batch, exc,
                )
                self._persistir()
                continue
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

        # Las divisorias conservaron hasta aqui la numeracion con la que se
        # leyeron y escribieron las bitacoras. Ya no son documentos utiles:
        # se marcan como borradas en el batch automatico aunque el operador
        # no haya pedido completarlo. REVISAR se conserva entero.
        if not resultado.interrumpido and not self.manifiesto.solo_subir:
            for entrada in plan.separadores:
                try:
                    borrada = self.cliente.borrar_pagina(
                        plan.batch_id, entrada.pagina_batch, True
                    )
                except FALLOS_DE_CAMINO as exc:
                    resultado.interrumpido = str(exc)
                    resultado.detalles.append(
                        f"pagina {entrada.pagina_batch}: no se pudo borrar "
                        f"el separador ({exc})"
                    )
                    self._persistir()
                    break
                except Exception as exc:  # noqa: BLE001 - se anota y sigue
                    borrada = False
                    logger.warning(
                        "No se pudo borrar el separador {} del batch {}: {}",
                        entrada.pagina_batch, plan.batch_id, exc,
                    )
                if borrada:
                    resultado.separadores_borrados += 1
                else:
                    resultado.separadores_pendientes += 1
                    resultado.detalles.append(
                        f"pagina {entrada.pagina_batch}: no se pudo borrar "
                        "el separador en AirVault"
                    )
        return resultado

    def _persistir(self) -> None:
        if self._al_guardar is not None:
            self._al_guardar(self.manifiesto)


def verificar_lote(
    cliente, manifiesto: Manifiesto
) -> tuple[int, int, List[str]]:
    """Relee el batch y cuenta cuantas paginas quedaron validas.

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
        work_location = str(
            remota.valores.get(CAMPO_WORK_LOCATION, "") or ""
        ).strip()
        log_remoto = str(
            remota.valores.get(CAMPO_LOG_NUMBER, "") or ""
        ).strip()
        matricula_remota = str(
            remota.valores.get(CAMPO_MATRICULA, "") or ""
        ).strip().upper()
        identidad_correcta = True
        if log_remoto != registro.log_number:
            identidad_correcta = False
            problemas.append(
                f"pagina {pagina}: AirVault tiene el log "
                f"{log_remoto or '(vacio)'} y se esperaba "
                f"{registro.log_number or '(vacio)'}"
            )
        if matricula_remota != registro.matricula.upper():
            identidad_correcta = False
            problemas.append(
                f"pagina {pagina}: AirVault tiene la matricula "
                f"{matricula_remota or '(vacia)'} y se esperaba "
                f"{registro.matricula or '(vacia)'}"
            )
        if remota.estado == ESTADO_VALIDO:
            if work_location:
                problemas.append(
                    f"pagina {pagina}: Work Location no quedo vacio"
                )
            if not work_location and identidad_correcta:
                validas += 1
        else:
            problemas.append(
                f"pagina {pagina}: estado {remota.estado}"
            )
    return validas, len(bitacoras), problemas
