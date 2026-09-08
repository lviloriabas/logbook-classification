"""Que hay que corregir de cada excepcion de Log Page Audit, y como.

El reporte dice lo que esta mal pero no lo arregla, y arreglarlo a mano son
dos pantallas por caso: buscar la bitacora, mirar sus apariciones y borrar
las que sobran, o abrir la mal indexada y cambiarle el avion. Este modulo
parte ese trabajo en dos mitades que conviene no mezclar.

**El plan** (:func:`planificar`) sale entero del propio reporte y no toca
nada. Una mal indexada ya trae las dos matriculas: la del libro al que
pertenece la bitacora y aquella bajo la que quedo archivada, asi que no hay
nada que adivinar. Una duplicada trae cuantas copias hay, y de ahi salen
cuantas sobran. Lo que el reporte no diga con esas palabras no se deduce:
queda como caso a revisar, con el motivo escrito.

**La aplicacion** (:class:`CorrectorLogPageAudit`) conduce el cliente de
AirVault por el mismo camino que una persona, con el navegador del perfil
portable, igual que :mod:`app.airvault.web_reports` conduce el visor de
SSRS. Va por la pantalla y no por peticiones sueltas a proposito: por la
pantalla valen los permisos de la cuenta y las validaciones del
repositorio, asi que una cuenta sin permiso para borrar no encuentra el
boton y el caso se queda como estaba, en vez de mandar media operacion por
una ruta que nadie documento.

Tres reglas gobiernan la aplicacion, y las tres estan para lo mismo: que un
caso que no se entiende no se toque.

* **Se comprueba antes de escribir.** Cada caso se contrasta con lo que la
  pantalla ensena ahora: que la duplicada siga teniendo las copias que
  decia el reporte y que la mal indexada siga estando donde decia. El
  reporte se genero en su momento; si desde entonces alguien lo corrigio,
  actuar sobre el plan viejo borraria lo que ya estaba bien.
* **No se elige a ciegas cual sobra.** De un grupo de copias se conserva la
  mas antigua, y para eso hace falta una columna de fecha que se pueda
  leer. Sin ella no se sabe cual es la primera, y entonces no se borra
  ninguna.
* **Un control que no aparece detiene ese caso, no la corrida.** Se busca
  por lo que el control dice, no por identificadores copiados de una
  instalacion. Si no esta, ese caso se informa sin tocarlo y se sigue con
  el siguiente.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from app.airvault.config import AirVaultConfig
from app.airvault.navegador import PERFIL_POR_DEFECTO, SesionDeNavegador
from app.airvault.web_reports import (
    TIPO_DUPLICADA,
    TIPO_MAL_INDEXADA,
    ConsultaCancelada,
    ExcepcionLogPageAudit,
    _Pagina,
)

ACCION_BORRAR = "Borrar copias"
ACCION_REINDEXAR = "Reindexar"
ACCION_REVISAR = "Revisar a mano"

# Lo que delata a la columna de fecha dentro de la rejilla de resultados. Es
# la que decide cual de las copias es la primera, y por eso se reconoce por
# el nombre de la columna y no por su posicion: el orden de las columnas lo
# define quien monta la busqueda en AirVault.
_COLUMNA_FECHA = re.compile(
    r"scan|creat|receiv|import|fecha|date", re.IGNORECASE
)
# Y a la del avion, que es la que se reescribe en una mal indexada.
_COLUMNA_MATRICULA = re.compile(r"aircraft|acn|matr", re.IGNORECASE)

_MATRICULA = re.compile(r"\b(?:HP|HK)-\d{4}(?:CMP|WWP)\b")

# Como se llama el control en cada idioma en el que AirVault puede estar
# instalado. Se busca por lo que dice porque los identificadores del cliente
# cambian entre versiones y no hay ninguno documentado.
TEXTOS_BORRAR = ("delete", "borrar", "eliminar", "remove")
TEXTOS_GUARDAR = ("save", "guardar", "apply", "aplicar")

_FORMATOS_FECHA = (
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


class ControlNoEncontrado(RuntimeError):
    """La pantalla no traia el control que hacia falta para este caso."""


@dataclass(frozen=True)
class Correccion:
    """Lo que hay que hacerle a una excepcion, ya decidido."""

    accion: str
    excepcion: ExcepcionLogPageAudit
    sobran: int = 0
    matricula_actual: str = ""
    matricula_correcta: str = ""
    motivo: str = ""

    @property
    def log_number(self) -> str:
        return self.excepcion.log_number

    @property
    def url_busqueda(self) -> str:
        return self.excepcion.url_busqueda

    @property
    def descripcion(self) -> str:
        """Una linea que dice lo que va a pasar, en el idioma de la ventana."""
        if self.accion == ACCION_BORRAR:
            return (
                f"Bitácora {self.log_number}: conservar la más antigua de "
                f"{self.sobran + 1} copias y borrar las {self.sobran} "
                "restantes."
            )
        if self.accion == ACCION_REINDEXAR:
            return (
                f"Bitácora {self.log_number}: pasarla de "
                f"{self.matricula_actual} a {self.matricula_correcta}."
            )
        return f"Bitácora {self.log_number}: {self.motivo}"


@dataclass
class Resultado:
    """Como quedo un caso despues de intentarlo."""

    correccion: Correccion
    hecho: bool = False
    detalle: str = ""

    @property
    def log_number(self) -> str:
        return self.correccion.log_number


def planificar(
    excepciones: Iterable[ExcepcionLogPageAudit],
) -> list[Correccion]:
    """Que hacer con cada excepcion, sin consultar nada ni tocar nada.

    Las duplicadas van primero porque quitan documentos: si una bitacora
    esta ademas mal indexada, puede que la copia que sobra sea justo la mal
    archivada y que al borrarla se arreglen las dos. Por eso la mal indexada
    de una bitacora que tambien esta repetida no se toca en esta pasada y se
    deja dicho que hay que volver a consultar el reporte.
    """
    excepciones = list(excepciones)
    duplicadas = [
        excepcion
        for excepcion in excepciones
        if excepcion.tipo == TIPO_DUPLICADA
    ]
    mal_indexadas = [
        excepcion
        for excepcion in excepciones
        if excepcion.tipo == TIPO_MAL_INDEXADA
    ]
    repetidas = {
        excepcion.log_number
        for excepcion in duplicadas
        if (excepcion.copias or 0) >= 2
    }

    plan: list[Correccion] = []
    for excepcion in duplicadas:
        copias = excepcion.copias or 0
        if copias < 2:
            plan.append(
                Correccion(
                    ACCION_REVISAR,
                    excepcion,
                    motivo=(
                        "el reporte no dice cuántas copias hay, así que no "
                        "se sabe cuántas sobran"
                    ),
                )
            )
            continue
        plan.append(Correccion(ACCION_BORRAR, excepcion, sobran=copias - 1))

    for excepcion in mal_indexadas:
        actual = (excepcion.destino or "").strip()
        correcta = (excepcion.matricula_libro or "").strip()
        if not actual or not correcta:
            motivo = (
                "el reporte no dice de qué matrícula a cuál hay que moverla"
            )
        elif actual.casefold() == correcta.casefold():
            motivo = "el reporte la señala en la matrícula en la que ya está"
        elif excepcion.log_number in repetidas:
            motivo = (
                "esa bitácora además está repetida: primero se quitan las "
                "copias que sobran y después se vuelve a consultar"
            )
        else:
            plan.append(
                Correccion(
                    ACCION_REINDEXAR,
                    excepcion,
                    matricula_actual=actual,
                    matricula_correcta=correcta,
                )
            )
            continue
        plan.append(Correccion(ACCION_REVISAR, excepcion, motivo=motivo))

    return plan


def resumen_del_plan(plan: Sequence[Correccion]) -> str:
    """Lo que se le enseña a quien tiene que autorizar la corrección."""
    borrar = [c for c in plan if c.accion == ACCION_BORRAR]
    reindexar = [c for c in plan if c.accion == ACCION_REINDEXAR]
    revisar = [c for c in plan if c.accion == ACCION_REVISAR]
    copias = sum(c.sobran for c in borrar)

    partes: list[str] = []
    if borrar:
        partes.append(
            f"borrar {copias} copias sobrantes de {len(borrar)} bitácoras"
        )
    if reindexar:
        partes.append(f"reindexar {len(reindexar)} páginas mal archivadas")
    if not partes:
        texto = "No hay nada que corregir automáticamente."
    else:
        texto = "Se va a " + " y ".join(partes) + "."
    if revisar:
        texto += (
            f" Quedan {len(revisar)} casos para revisar a mano; esos no se "
            "tocan."
        )
    return texto


# ── leer la pantalla ────────────────────────────────────────────────

# La rejilla de resultados de Web Search. Se toma la tabla con mas filas de
# la pagina, que es la de los documentos: el cliente monta ademas tablas de
# maquetacion, y todas ellas son pequenas.
#
# De cada fila se guarda su identificador ademas de su texto. AirVault monta
# sus rejillas con jqGrid, que le pone a cada ``<tr>`` el identificador del
# registro, y eso es lo que hay que apuntar: la **posicion** de una fila deja
# de valer en cuanto se borra la de encima. Con indices, borrar la segunda de
# tres copias corria las de abajo y la siguiente orden caia sobre otro
# documento, que es exactamente lo que no puede pasar aqui.
_LEER_REJILLA = r"""(function(){
  var mejor = null, mayor = 0;
  Array.from(document.querySelectorAll('table')).forEach(function(tabla){
    var filas = tabla.querySelectorAll('tr').length;
    if (filas > mayor) { mayor = filas; mejor = tabla; }
  });
  if (!mejor) return null;
  var salida = [];
  mejor.querySelectorAll('tr').forEach(function(fila){
    var celdas = [];
    Array.from(fila.children).forEach(function(celda){
      celdas.push((celda.innerText || '').replace(/\s+/g, ' ').trim());
    });
    if (celdas.length) salida.push({id: fila.id || '', celdas: celdas});
  });
  return salida;
})()"""


def _fecha_de(texto: str) -> datetime | None:
    """La fecha de una celda, si esa celda trae una que se pueda leer."""
    limpio = " ".join(str(texto or "").split())
    if not limpio:
        return None
    for formato in _FORMATOS_FECHA:
        try:
            return datetime.strptime(limpio, formato)
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class Copia:
    """Una aparicion de la bitacora en la rejilla de resultados."""

    fila: int
    identificador: str
    celdas: tuple[str, ...]
    cuando: datetime | None
    matricula: str


def _normalizar(rejilla: Sequence[object]) -> list[tuple[str, list[str]]]:
    """El identificador y el texto de cada fila, venga como venga.

    La pantalla las manda con identificador; una fila escrita a mano (las de
    las pruebas, o una rejilla sin identificadores) es solo la lista de sus
    celdas. Las dos formas se leen igual a partir de aqui.
    """
    normalizadas: list[tuple[str, list[str]]] = []
    for fila in rejilla:
        if isinstance(fila, Mapping):
            identificador = str(fila.get("id", "") or "")
            celdas = fila.get("celdas") or ()
        else:
            identificador = ""
            celdas = fila
        normalizadas.append(
            (
                identificador,
                [" ".join(str(celda or "").split()) for celda in celdas],
            )
        )
    return normalizadas


def copias_en(rejilla: Sequence[object], log_number: str) -> list[Copia]:
    """Las filas de la rejilla que son ese numero de bitacora.

    La cabecera dice que columna es la fecha y cual el avion; se busca por
    el nombre porque el orden de las columnas lo decide quien monto la
    busqueda en AirVault, no este programa.
    """
    numero = str(log_number).strip()
    if not numero:
        return []
    leidas = _normalizar(rejilla)
    columna_fecha = -1
    columna_matricula = -1
    for _identificador, fila in leidas:
        for indice, celda in enumerate(fila):
            if columna_fecha < 0 and _COLUMNA_FECHA.search(celda):
                columna_fecha = indice
            if columna_matricula < 0 and _COLUMNA_MATRICULA.search(celda):
                columna_matricula = indice
        if columna_fecha >= 0 or columna_matricula >= 0:
            break

    copias: list[Copia] = []
    for indice, (identificador, fila) in enumerate(leidas):
        if not any(numero == celda for celda in fila):
            continue
        cuando = None
        if 0 <= columna_fecha < len(fila):
            cuando = _fecha_de(fila[columna_fecha])
        if cuando is None:
            for celda in fila:
                cuando = _fecha_de(celda)
                if cuando is not None:
                    break
        matricula = ""
        if 0 <= columna_matricula < len(fila):
            hallada = _MATRICULA.search(fila[columna_matricula])
            matricula = hallada.group(0) if hallada else ""
        if not matricula:
            for celda in fila:
                hallada = _MATRICULA.search(celda)
                if hallada:
                    matricula = hallada.group(0)
                    break
        copias.append(
            Copia(
                fila=indice,
                identificador=identificador,
                celdas=tuple(fila),
                cuando=cuando,
                matricula=matricula,
            )
        )
    return copias


def por_antiguedad(
    copias: Sequence[Copia],
) -> tuple[Copia | None, list[Copia]]:
    """La copia que se queda y las que sobran, de mas antigua a mas nueva.

    Si alguna no trae fecha legible no se devuelve ninguna. Sin fecha no se
    sabe cual es la primera, y borrar «las demas» sin saber cual se queda es
    exactamente lo que no puede hacer sola una corrección automática.
    """
    if len(copias) < 2 or any(copia.cuando is None for copia in copias):
        return None, []
    ordenadas = sorted(copias, key=lambda copia: (copia.cuando, copia.fila))
    return ordenadas[0], list(ordenadas[1:])


class CorrectorLogPageAudit:
    """Aplica el plan en el cliente de AirVault, caso por caso."""

    def __init__(self, config: AirVaultConfig) -> None:
        self.config = config

    def aplicar(
        self,
        plan: Sequence[Correccion],
        avisar: Callable[[str], None] | None = None,
        cancelar: Callable[[], bool] | None = None,
        ensayo: bool = True,
    ) -> list[Resultado]:
        """Recorre el plan. Con ``ensayo`` comprueba pero no escribe nada.

        El ensayo no es un modo de prueba: es la primera mitad de cada caso
        y se hace igual en los dos. Abre la busqueda de esa bitacora, lee la
        rejilla y contrasta lo que hay ahora con lo que decia el reporte. Lo
        unico que cambia es si despues se toca algo.
        """
        notificar = avisar or (lambda _texto: None)
        esta_cancelado = cancelar or (lambda: False)
        pendientes = [
            correccion
            for correccion in plan
            if correccion.accion in (ACCION_BORRAR, ACCION_REINDEXAR)
        ]
        resultados: list[Resultado] = [
            Resultado(
                correccion,
                detalle="No se intentó: queda para revisar a mano.",
            )
            for correccion in plan
            if correccion.accion == ACCION_REVISAR
        ]
        if not pendientes:
            return resultados

        perfil = (
            Path(self.config.perfil_navegador)
            if self.config.perfil_navegador
            else PERFIL_POR_DEFECTO
        )
        notificar("Abriendo AirVault en Edge")
        with SesionDeNavegador(perfil, visible=False) as navegador:
            version = navegador.abrir(
                self.config.base_url, espera_s=self.config.espera_login_s
            )
            for numero, correccion in enumerate(pendientes, start=1):
                if esta_cancelado():
                    raise ConsultaCancelada()
                notificar(
                    f"Bitácora {correccion.log_number} "
                    f"({numero} de {len(pendientes)})"
                )
                resultados.append(
                    self._un_caso(
                        navegador, version, correccion,
                        esta_cancelado, ensayo,
                    )
                )
        return resultados

    def _un_caso(
        self,
        navegador: SesionDeNavegador,
        version: dict,
        correccion: Correccion,
        cancelar: Callable[[], bool],
        ensayo: bool,
    ) -> Resultado:
        """Un caso entero, con su pestana propia y sin dejarla abierta."""
        pagina: _Pagina | None = None
        try:
            target_id = navegador.abrir_pestana(
                correccion.url_busqueda, version=version
            )
            pagina = _Pagina(version, target_id, cancelar)
            rejilla = self._rejilla(pagina)
            copias = copias_en(rejilla, correccion.log_number)
            if not copias:
                return Resultado(
                    correccion,
                    detalle=(
                        "La búsqueda no devolvió esa bitácora. Puede que ya "
                        "esté corregida; no se tocó nada."
                    ),
                )
            if correccion.accion == ACCION_BORRAR:
                return self._borrar(pagina, correccion, copias, ensayo)
            return self._reindexar(pagina, correccion, copias, ensayo)
        except ConsultaCancelada:
            raise
        except ControlNoEncontrado as exc:
            return Resultado(correccion, detalle=str(exc))
        except Exception as exc:  # noqa: BLE001 - llega a la interfaz
            return Resultado(
                correccion,
                detalle=f"No se pudo corregir: {exc}. No se tocó nada.",
            )
        finally:
            if pagina is not None:
                pagina.cerrar()

    @staticmethod
    def _rejilla(pagina: _Pagina) -> list[list[str]]:
        if not pagina.esperar("document.querySelectorAll('tr').length > 1", 90.0):
            raise ControlNoEncontrado(
                "La búsqueda no llegó a mostrar resultados. No se tocó nada."
            )
        leidas = pagina.evaluar(_LEER_REJILLA)
        if not isinstance(leidas, list):
            raise ControlNoEncontrado(
                "No se reconoció la lista de resultados de AirVault. No se "
                "tocó nada."
            )
        return [
            [str(celda) for celda in fila]
            for fila in leidas
            if isinstance(fila, list)
        ]

    def _borrar(
        self,
        pagina: _Pagina,
        correccion: Correccion,
        copias: Sequence[Copia],
        ensayo: bool,
    ) -> Resultado:
        """Deja una sola copia: la mas antigua."""
        esperadas = correccion.sobran + 1
        if len(copias) != esperadas:
            return Resultado(
                correccion,
                detalle=(
                    f"El reporte contaba {esperadas} copias y ahora hay "
                    f"{len(copias)}. Cambió desde que se consultó, así que "
                    "no se tocó nada: vuelva a consultar."
                ),
            )
        se_queda, sobran = por_antiguedad(copias)
        if se_queda is None:
            return Resultado(
                correccion,
                detalle=(
                    "Las copias no traen una fecha que se pueda leer, así "
                    "que no se sabe cuál es la más antigua. No se borró "
                    "ninguna."
                ),
            )
        if ensayo:
            return Resultado(
                correccion,
                detalle=(
                    f"Se conservaría la del {se_queda.cuando:%d/%m/%Y} y se "
                    f"borrarían {len(sobran)}."
                ),
            )
        for copia in sobran:
            self._borrar_fila(pagina, copia)
        # Se vuelve a mirar la pantalla en vez de dar por hecho que la orden
        # surtio efecto. Un control que se pulsa pero no borra (permisos,
        # una confirmacion que nadie contesto) dejaria si no una corrida que
        # dice haber limpiado lo que sigue repetido.
        quedan = copias_en(self._rejilla(pagina), correccion.log_number)
        if len(quedan) != 1:
            return Resultado(
                correccion,
                detalle=(
                    f"Se pidió borrar {len(sobran)} copias y después de "
                    f"hacerlo siguen {len(quedan)}. AirVault no aceptó el "
                    "borrado: revíselo a mano."
                ),
            )
        return Resultado(
            correccion,
            hecho=True,
            detalle=(
                f"Borradas {len(sobran)} copias; queda la del "
                f"{se_queda.cuando:%d/%m/%Y}."
            ),
        )

    def _reindexar(
        self,
        pagina: _Pagina,
        correccion: Correccion,
        copias: Sequence[Copia],
        ensayo: bool,
    ) -> Resultado:
        """Le pone a la pagina el avion del libro al que pertenece."""
        if len(copias) != 1:
            return Resultado(
                correccion,
                detalle=(
                    f"Esa bitácora aparece {len(copias)} veces, así que no "
                    "está claro cuál hay que mover. No se tocó nada."
                ),
            )
        copia = copias[0]
        actual = copia.matricula
        if actual and actual.casefold() != (
            correccion.matricula_actual.casefold()
        ):
            return Resultado(
                correccion,
                detalle=(
                    f"El reporte la daba en {correccion.matricula_actual} y "
                    f"ahora está en {actual}. Cambió desde que se consultó, "
                    "así que no se tocó nada: vuelva a consultar."
                ),
            )
        if ensayo:
            return Resultado(
                correccion,
                detalle=(
                    f"Se pasaría de {correccion.matricula_actual} a "
                    f"{correccion.matricula_correcta}."
                ),
            )
        self._escribir_matricula(
            pagina, copia, correccion.matricula_correcta
        )
        return Resultado(
            correccion,
            hecho=True,
            detalle=(
                f"Reindexada en {correccion.matricula_correcta}."
            ),
        )

    # ── los controles de la pantalla ────────────────────────────────
    #
    # Se localizan por lo que dicen y se comprueba que existan antes de
    # pulsarlos. Un identificador copiado de una instalacion concreta se
    # rompe en la siguiente y, peor, un identificador que ya no existe hace
    # que la orden no llegue a ninguna parte sin que nadie se entere: la
    # corrida diria que borro y no habria borrado nada.

    @staticmethod
    def _borrar_fila(pagina: _Pagina, copia: Copia) -> None:
        """Marca esa copia y pulsa borrar.

        La fila se busca por su identificador y solo se cae en la posicion
        cuando la rejilla no traia ninguno: al borrar de arriba abajo las
        posiciones se corren, y la segunda orden acabaria sobre el documento
        equivocado.
        """
        hecho = pagina.evaluar(
            """(function(identificador, indice, textos){
              var mejor = null, mayor = 0;
              Array.from(document.querySelectorAll('table')).forEach(
                function(tabla){
                  var cuantas = tabla.querySelectorAll('tr').length;
                  if (cuantas > mayor) { mayor = cuantas; mejor = tabla; }
                });
              if (!mejor) return 'sin rejilla';
              var filas = Array.from(mejor.querySelectorAll('tr'));
              var fila = null;
              if (identificador) {
                filas.forEach(function(otra){
                  if (!fila && otra.id === identificador) fila = otra;
                });
                if (!fila) return 'la fila ya no está';
              } else {
                fila = filas[indice];
              }
              if (!fila) return 'sin fila';
              var casilla = fila.querySelector('input[type=checkbox]');
              if (!casilla) return 'sin casilla';
              if (!casilla.checked) casilla.click();
              var pulsable = null;
              Array.from(document.querySelectorAll(
                'button, a, input[type=button], input[type=submit]'
              )).forEach(function(control){
                if (pulsable) return;
                var texto = (
                  control.innerText || control.value || control.title || ''
                ).trim().toLowerCase();
                if (!texto) return;
                textos.forEach(function(quiza){
                  if (!pulsable && texto === quiza) pulsable = control;
                });
              });
              if (!pulsable) return 'sin boton';
              pulsable.click();
              return 'OK';
            })(%s, %s, %s)"""
            % (
                json.dumps(copia.identificador),
                json.dumps(copia.fila),
                json.dumps(list(TEXTOS_BORRAR)),
            )
        )
        if hecho != "OK":
            raise ControlNoEncontrado(
                "La pantalla de AirVault no dejó borrar esa copia "
                f"({hecho}). No se borró ninguna."
            )

    @staticmethod
    def _escribir_matricula(
        pagina: _Pagina, copia: Copia, matricula: str
    ) -> None:
        hecho = pagina.evaluar(
            """(function(identificador, indice, valor, textos){
              var mejor = null, mayor = 0;
              Array.from(document.querySelectorAll('table')).forEach(
                function(tabla){
                  var cuantas = tabla.querySelectorAll('tr').length;
                  if (cuantas > mayor) { mayor = cuantas; mejor = tabla; }
                });
              if (!mejor) return 'sin rejilla';
              var filas = Array.from(mejor.querySelectorAll('tr'));
              var fila = null;
              if (identificador) {
                filas.forEach(function(otra){
                  if (!fila && otra.id === identificador) fila = otra;
                });
              } else {
                fila = filas[indice];
              }
              if (!fila) return 'sin fila';
              fila.click();
              var campo = null;
              Array.from(document.querySelectorAll(
                'input[type=text], select, textarea'
              )).forEach(function(control){
                if (campo) return;
                var nombre = (
                  control.name || control.id || control.title ||
                  control.getAttribute('aria-label') || ''
                ).toLowerCase();
                if (/aircraft|acn/.test(nombre)) campo = control;
              });
              if (!campo) return 'sin campo';
              campo.value = valor;
              campo.dispatchEvent(new Event('input', {bubbles: true}));
              campo.dispatchEvent(new Event('change', {bubbles: true}));
              var guardar = null;
              Array.from(document.querySelectorAll(
                'button, a, input[type=button], input[type=submit]'
              )).forEach(function(control){
                if (guardar) return;
                var texto = (
                  control.innerText || control.value || control.title || ''
                ).trim().toLowerCase();
                if (!texto) return;
                textos.forEach(function(quiza){
                  if (!guardar && texto === quiza) guardar = control;
                });
              });
              if (!guardar) return 'sin boton';
              guardar.click();
              return 'OK';
            })(%s, %s, %s, %s)"""
            % (
                json.dumps(copia.identificador),
                json.dumps(copia.fila),
                json.dumps(matricula),
                json.dumps(list(TEXTOS_GUARDAR)),
            )
        )
        if hecho != "OK":
            raise ControlNoEncontrado(
                "La pantalla de AirVault no traía el campo del avión o el "
                f"de guardar ({hecho}). No se reindexó nada."
            )
