"""Consulta de Web Reports en el Edge de trabajo de AirVault.

El reporte Log Page Audit no ofrece una API documentada. Esta pieza conduce
el visor de SSRS por su DOM, siempre en el perfil portable que ya usa el
indexado, y devuelve datos legibles para la interfaz. Solo consulta: no abre
la edicion, no reindexa y no elimina documentos.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Sequence
from urllib.parse import quote

from app.airvault.config import AirVaultConfig
from app.airvault.navegador import (
    PERFIL_POR_DEFECTO,
    SesionDeNavegador,
    _WebSocket,
)

LOG_PAGE_AUDIT_URL = (
    "https://sql2014dw.criticaltech.com/ReportServer_2014/Pages/"
    "ReportViewer.aspx?/Reports2014/AVLogPageAudit"
    "&rc:Stylesheet=AVTheme-reports"
)

TIPO_MAL_INDEXADA = "Mal indexada"
TIPO_DUPLICADA = "Duplicada"
FILTRO_MAL_INDEXADAS = "8"
FILTRO_DUPLICADAS = "10"

_CONTROL = "ReportViewerControl_ctl04_"
_AREA_REPORTE = "ReportViewerControl_ctl09"
_ESPERA_REPORTE = "ReportViewerControl_AsyncWait"
_FORMULARIO_LISTO = (
    f"document.getElementById('{_CONTROL}ctl03_ddValue')"
    f" && !document.getElementById('{_CONTROL}ctl03_ddValue').disabled"
)
_FECHA_LISTA = (
    f"document.getElementById('{_CONTROL}ctl07_txtValue')"
    f" && !document.getElementById('{_CONTROL}ctl07_txtValue').disabled"
)

_DUPLICADA = re.compile(
    r"\bDUPLICATED\s+(?P<log>\d{7})\s*\((?P<copias>\d+)x\)",
    re.IGNORECASE,
)
_MAL_INDEXADA = re.compile(
    r"\b(?P<log>\d{7})\s+MIS-INDEX(?:ED)?\s+to\s+ACN\s*"
    r"\[(?P<destino>[^\]]+)\]",
    re.IGNORECASE,
)


class ConsultaCancelada(RuntimeError):
    """La consulta fue detenida desde la interfaz."""


@dataclass(frozen=True)
class ExcepcionLogPageAudit:
    """Una pagina señalada por Log Page Audit."""

    tipo: str
    matricula_libro: str
    tipo_libro: str
    rango_libro: str
    rango_fechas: str
    faltantes: str
    duplicadas: str
    fecha_sospechosa: str
    detalle: str
    log_number: str
    destino: str = ""
    copias: int | None = None
    url_busqueda: str = ""


def url_busqueda_log(config: AirVaultConfig, log_number: str) -> str:
    """Enlace estable de Web Search para las apariciones de una pagina."""
    numero = str(log_number).strip()
    parametros = f"1=LOG PAGE\t3={numero}\t4={numero}"
    return (
        f"{config.base_url.rstrip('/')}/zfp/client/unencryptedparamssearch/"
        f"?repoId={config.repo_id}&searchId=15504"
        f"&searchParams={quote(parametros, safe='=')}"
        "&maxHits=200&behavior=lockdown=false"
    )


def _texto(celda: object) -> str:
    if isinstance(celda, dict):
        valor = celda.get("t", "")
    else:
        valor = celda
    return " ".join(str(valor or "").split())


def parsear_filas(
    filas: Iterable[Sequence[object]], config: AirVaultConfig
) -> list[ExcepcionLogPageAudit]:
    """Convierte las filas planas de SSRS en excepciones sin duplicarlas."""
    salida: list[ExcepcionLogPageAudit] = []
    vistos: set[tuple[str, str, str, str, int | None]] = set()
    for fila in filas:
        celdas = [_texto(celda) for celda in fila]
        # El diseño del reporte lleva dos celdas separadoras. Las filas de
        # datos tienen diez columnas; cabeceras y pies se descartan por no
        # contener ninguna excepcion reconocible.
        if len(celdas) < 10:
            continue
        detalle = celdas[9]
        coincidencias: list[tuple[str, re.Match[str]]] = []
        coincidencias.extend(
            (TIPO_DUPLICADA, hallada)
            for hallada in _DUPLICADA.finditer(detalle)
        )
        coincidencias.extend(
            (TIPO_MAL_INDEXADA, hallada)
            for hallada in _MAL_INDEXADA.finditer(detalle)
        )
        for tipo, hallada in coincidencias:
            numero = hallada.group("log")
            destino = (
                hallada.group("destino").strip()
                if tipo == TIPO_MAL_INDEXADA
                else ""
            )
            copias = (
                int(hallada.group("copias"))
                if tipo == TIPO_DUPLICADA
                else None
            )
            clave = (tipo, numero, celdas[0], destino, copias)
            if clave in vistos:
                continue
            vistos.add(clave)
            salida.append(
                ExcepcionLogPageAudit(
                    tipo=tipo,
                    matricula_libro=celdas[0],
                    tipo_libro=celdas[1],
                    rango_libro=celdas[3],
                    rango_fechas=celdas[4],
                    faltantes=celdas[5],
                    duplicadas=celdas[6],
                    fecha_sospechosa=celdas[7],
                    detalle=detalle,
                    log_number=numero,
                    destino=destino,
                    copias=copias,
                    url_busqueda=url_busqueda_log(config, numero),
                )
            )
    return salida


def _puerto_de(version: dict) -> int:
    hallado = re.search(
        r"://[^:]+:(\d+)/", str(version.get("webSocketDebuggerUrl", ""))
    )
    if hallado is None:
        raise RuntimeError("Edge no informó su puerto de control")
    return int(hallado.group(1))


def _objetivos(puerto: int) -> list[dict]:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{puerto}/json/list", timeout=10
    ) as respuesta:
        datos = json.load(respuesta)
    return datos if isinstance(datos, list) else []


class _Pagina:
    """Pestaña del visor controlada por el protocolo local de Edge."""

    def __init__(
        self,
        version: dict,
        target_id: str,
        cancelar: Callable[[], bool],
        timeout: float = 120.0,
    ) -> None:
        self._cancelar = cancelar
        self._version = version
        self._target_id = target_id
        self.ws: _WebSocket | None = None
        limite = time.monotonic() + 45.0
        while time.monotonic() < limite:
            self._comprobar_cancelacion()
            iguales = [
                objetivo
                for objetivo in _objetivos(_puerto_de(version))
                if objetivo.get("type") == "page"
                and objetivo.get("id") == target_id
            ]
            if iguales:
                self.ws = _WebSocket(
                    iguales[-1]["webSocketDebuggerUrl"], timeout=timeout
                )
                return
            self._dormir(0.5)
        raise RuntimeError("No apareció la pestaña de Log Page Audit en Edge")

    def _comprobar_cancelacion(self) -> None:
        if self._cancelar():
            raise ConsultaCancelada()

    def _dormir(self, segundos: float) -> None:
        limite = time.monotonic() + segundos
        while time.monotonic() < limite:
            self._comprobar_cancelacion()
            time.sleep(min(0.2, max(0.0, limite - time.monotonic())))

    def evaluar(self, expresion: str):
        self._comprobar_cancelacion()
        if self.ws is None:
            raise RuntimeError("La pestaña de Log Page Audit ya no está abierta")
        respuesta = self.ws.pedir(
            "Runtime.evaluate",
            expression=expresion,
            returnByValue=True,
            awaitPromise=True,
        )
        if respuesta.get("exceptionDetails"):
            raise RuntimeError("El visor de Log Page Audit rechazó la consulta")
        return respuesta.get("result", {}).get("value")

    def esperar(
        self, condicion: str, segundos: float, cada: float = 0.5
    ) -> bool:
        limite = time.monotonic() + segundos
        while time.monotonic() < limite:
            self._comprobar_cancelacion()
            try:
                if self.evaluar(f"!!({condicion})"):
                    return True
            except (OSError, RuntimeError, ValueError):
                self._comprobar_cancelacion()
            self._dormir(cada)
        return False

    def cerrar(self) -> None:
        if self.ws is not None:
            self.ws.cerrar()
            self.ws = None
        try:
            navegador = _WebSocket(
                self._version["webSocketDebuggerUrl"], timeout=5.0
            )
            try:
                navegador.pedir("Target.closeTarget", targetId=self._target_id)
            finally:
                navegador.cerrar()
        except (KeyError, OSError, RuntimeError, ValueError):
            pass


class ClienteLogPageAudit:
    """Ejecuta uno o varios filtros de Log Page Audit en una sesión."""

    def __init__(self, config: AirVaultConfig) -> None:
        self.config = config

    def consultar(
        self,
        desde: date,
        hasta: date,
        filtros: Sequence[str],
        avisar: Callable[[str], None] | None = None,
        cancelar: Callable[[], bool] | None = None,
    ) -> list[ExcepcionLogPageAudit]:
        if desde > hasta:
            raise ValueError("La fecha inicial no puede ser posterior a la final")
        if not filtros:
            return []
        notificar = avisar or (lambda _texto: None)
        esta_cancelado = cancelar or (lambda: False)
        perfil = (
            Path(self.config.perfil_navegador)
            if self.config.perfil_navegador
            else PERFIL_POR_DEFECTO
        )
        notificar("Abriendo Log Page Audit en Edge")
        pagina: _Pagina | None = None
        with SesionDeNavegador(perfil, visible=False) as navegador:
            version = navegador.abrir(
                LOG_PAGE_AUDIT_URL, espera_s=self.config.espera_login_s
            )
            try:
                target_id = self._abrir_pestana_limpia(version)
                pagina = _Pagina(version, target_id, esta_cancelado)
                if not pagina.esperar(
                    _FORMULARIO_LISTO, self.config.espera_login_s
                ):
                    raise RuntimeError(
                        "Log Page Audit no quedó listo. Complete el acceso "
                        "en Edge con la cuenta de trabajo y vuelva a intentar."
                    )
                self._elegir_repositorio(pagina)
                if not pagina.esperar(_FECHA_LISTA, 120.0):
                    raise RuntimeError(
                        "El repositorio de producción no habilitó las fechas"
                    )

                resultado: list[ExcepcionLogPageAudit] = []
                for filtro in filtros:
                    nombre = (
                        "mal indexadas"
                        if filtro == FILTRO_MAL_INDEXADAS
                        else "duplicadas"
                    )
                    notificar(f"Consultando páginas {nombre}")
                    filas = self._correr_reporte(pagina, desde, hasta, filtro)
                    resultado.extend(parsear_filas(filas, self.config))
                return self._sin_repetidos(resultado)
            finally:
                if pagina is not None:
                    pagina.cerrar()

    @staticmethod
    def _abrir_pestana_limpia(version: dict) -> str:
        """Abre una pestaña identificable y quita visores restaurados.

        Edge conserva las pestañas del perfil entre sesiones. Elegir una
        solo por su URL terminaba conduciendo al azar una copia antigua y
        dejando la recién abierta sin tocar. El perfil es exclusivo del
        programa, así que se cierran únicamente las copias de este reporte y
        se crea una cuyo identificador conocemos.
        """
        control = _WebSocket(version["webSocketDebuggerUrl"])
        try:
            creado = control.pedir(
                "Target.createTarget", url=LOG_PAGE_AUDIT_URL
            )
            target_id = str(creado.get("targetId", ""))
            if not target_id:
                raise RuntimeError("Edge no pudo abrir Log Page Audit")
            for objetivo in _objetivos(_puerto_de(version)):
                if (
                    objetivo.get("type") == "page"
                    and objetivo.get("id") != target_id
                    and "ReportViewer.aspx?/Reports2014/AVLogPageAudit"
                    in str(objetivo.get("url", ""))
                ):
                    control.pedir(
                        "Target.closeTarget", targetId=objetivo["id"]
                    )
        finally:
            control.cerrar()
        return target_id

    @staticmethod
    def _elegir_repositorio(pagina: _Pagina) -> None:
        elegido = pagina.evaluar(
            f"""(function(){{
              var control = document.getElementById('{_CONTROL}ctl03_ddValue');
              if (!control) return false;
              if (control.value !== '1') {{
                control.value = '1';
                if (control.onchange) control.onchange();
                else control.dispatchEvent(new Event('change'));
              }}
              return true;
            }})()"""
        )
        if elegido is not True:
            raise RuntimeError(
                "El formulario de Log Page Audit no mostró el repositorio"
            )

    def _correr_reporte(
        self, pagina: _Pagina, desde: date, hasta: date, filtro: str
    ) -> list[list[object]]:
        valores = {
            f"{_CONTROL}ctl07_txtValue": self._fecha_ssrs(desde),
            f"{_CONTROL}ctl09_txtValue": self._fecha_ssrs(hasta),
            f"{_CONTROL}ctl17_ddValue": filtro,
            f"{_CONTROL}ctl21_ddValue": "2",
            f"{_CONTROL}ctl23_ddValue": "1",
        }
        lanzado = pagina.evaluar(
            """(function(valores, areaId, botonId){
              for (var id in valores) {
                var control = document.getElementById(id);
                if (!control) return 'Falta ' + id;
                control.value = valores[id];
              }
              var area = document.getElementById(areaId);
              if (area) area.innerHTML = '';
              var boton = document.getElementById(botonId);
              if (!boton) return 'Falta ' + botonId;
              boton.click();
              return 'OK';
            })(%s, %s, %s)"""
            % (
                json.dumps(valores),
                json.dumps(_AREA_REPORTE),
                json.dumps(f"{_CONTROL}ctl00"),
            )
        )
        if lanzado != "OK":
            raise RuntimeError(
                f"El formulario de Log Page Audit cambió: {lanzado}"
            )
        self._esperar_reporte(pagina)
        crudas = pagina.evaluar(
            r"""(function(){
              var area = document.getElementById(%s);
              if (!area) return [];
              var salida = [];
              area.querySelectorAll('tr').forEach(function(fila){
                var celdas = [];
                Array.from(fila.children).forEach(function(celda){
                  if (celda.tagName !== 'TD') return;
                  celdas.push({
                    t: (celda.innerText || '').replace(/\s+/g, ' ').trim()
                  });
                });
                if (celdas.length) salida.push(celdas);
              });
              return salida;
            })()"""
            % json.dumps(_AREA_REPORTE)
        )
        return crudas if isinstance(crudas, list) else []

    @staticmethod
    def _esperar_reporte(pagina: _Pagina) -> None:
        inicio = time.monotonic()
        limite = inicio + 1800.0
        while time.monotonic() < limite:
            estado = pagina.evaluar(
                """(function(){
                  var area = document.getElementById(%s);
                  var espera = document.getElementById(%s);
                  var repo = document.getElementById(%s);
                  var desde = document.getElementById(%s);
                  var ocupada = espera &&
                    getComputedStyle(espera).display !== 'none' &&
                    getComputedStyle(espera).visibility !== 'hidden';
                  return {
                    ocupada: !!ocupada,
                    texto: area ? area.innerText.trim().length : 0,
                    repo: repo ? repo.value : '',
                    desde: desde ? desde.value : ''
                  };
                })()"""
                % (
                    json.dumps(_AREA_REPORTE),
                    json.dumps(_ESPERA_REPORTE),
                    json.dumps(f"{_CONTROL}ctl03_ddValue"),
                    json.dumps(f"{_CONTROL}ctl07_txtValue"),
                )
            )
            if (
                isinstance(estado, dict)
                and not estado.get("ocupada")
                and int(estado.get("texto", 0)) > 20
            ):
                return
            if (
                time.monotonic() - inicio > 15.0
                and isinstance(estado, dict)
                and not estado.get("ocupada")
                and (
                    str(estado.get("repo", "")) != "1"
                    or not str(estado.get("desde", ""))
                )
            ):
                raise RuntimeError(
                    "SSRS reinició los parámetros sin generar el reporte"
                )
            pagina._dormir(1.0)
        raise RuntimeError("Log Page Audit no terminó dentro de 30 minutos")

    @staticmethod
    def _fecha_ssrs(valor: date) -> str:
        return f"{valor.month}/{valor.day}/{valor.year}"

    @staticmethod
    def _sin_repetidos(
        excepciones: Iterable[ExcepcionLogPageAudit],
    ) -> list[ExcepcionLogPageAudit]:
        salida: list[ExcepcionLogPageAudit] = []
        vistos: set[tuple[str, str, str, str, int | None]] = set()
        for excepcion in excepciones:
            clave = (
                excepcion.tipo,
                excepcion.log_number,
                excepcion.matricula_libro,
                excepcion.destino,
                excepcion.copias,
            )
            if clave not in vistos:
                vistos.add(clave)
                salida.append(excepcion)
        return salida
