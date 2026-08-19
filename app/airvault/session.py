"""Sesion autenticada contra AirVault.

El acceso a AirVault esta federado con Microsoft Entra ID: quien entra pasa
por la pagina de Microsoft y por un segundo factor. Eso no se puede
completar desde un script, y tampoco se puede automatizar con un navegador
porque el programa es portable y no instala nada en tiempo de ejecucion.

Lo que si se puede es **reutilizar la sesion que el usuario ya abrio**. Por
orden de preferencia:

1. La cookie que se pasa a mano (``--cookie`` o ``AIRVAULT_COOKIE``).
2. La misma cookie leida del perfil de Edge, para no tener que copiarla.
   Es un atajo con condiciones; ver :mod:`app.airvault.edge`.
3. El formulario propio de AirVault en ``/signin2/``, que solo sirve para
   las cuentas locales que no pasan por Entra ID. Es un respaldo, no el
   camino normal.

Ni las cookies ni las contrasenas se guardan en disco ni se escriben en el
log: de una cookie de sesion solo se registra el nombre y cuanto mide.

Si la sesion caduca, el servidor no responde con un error sino con la
pagina de acceso. Se detecta y se dice; nunca se falla en silencio a mitad
de un lote.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

import requests
from loguru import logger

from app.airvault import cookies as galletas
from app.airvault.config import AirVaultConfig

ENV_USUARIO = "AIRVAULT_USER"
ENV_CLAVE = "AIRVAULT_PASSWORD"
ENV_COOKIE = "AIRVAULT_COOKIE"

# De donde salio la sesion, para poder decirlo en el reporte y en la
# interfaz sin que el usuario tenga que adivinarlo.
ORIGEN_COOKIE = "cookie pegada"
ORIGEN_EDGE = "perfil de Edge"
ORIGEN_FORMULARIO = "formulario de AirVault"

_AYUDA_COOKIE = (
    "Entrar a AirVault en el navegador, abrir las herramientas de "
    "desarrollo (F12), copiar la cabecera Cookie de cualquier peticion y "
    "pasarla en --cookie o en la variable AIRVAULT_COOKIE."
)


class ErrorDeSesion(RuntimeError):
    """No se pudo autenticar o la sesion caduco."""


class _FormParser(HTMLParser):
    """Extrae los formularios de una pagina con sus campos ocultos.

    Se usa ``html.parser`` de la libreria estandar a proposito: cualquier
    dependencia nueva tendria que viajar dentro de ``portable/``.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.formularios: List[Tuple[str, str, Dict[str, str]]] = []
        self._actual: Optional[Tuple[str, str, Dict[str, str]]] = None

    def handle_starttag(self, tag: str, attrs) -> None:
        atributos = {k.lower(): (v or "") for k, v in attrs}
        if tag == "form":
            self._actual = (
                atributos.get("action", ""),
                atributos.get("method", "get").lower(),
                {},
            )
        elif tag == "input" and self._actual is not None:
            nombre = atributos.get("name")
            if nombre:
                self._actual[2][nombre] = atributos.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._actual is not None:
            self.formularios.append(self._actual)
            self._actual = None

    def close(self) -> None:  # noqa: D102 - cierra un form sin etiqueta
        if self._actual is not None:
            self.formularios.append(self._actual)
            self._actual = None
        super().close()


def _formularios(html: str) -> List[Tuple[str, str, Dict[str, str]]]:
    parser = _FormParser()
    parser.feed(html)
    parser.close()
    return parser.formularios


@dataclass
class Credenciales:
    """Usuario y contrasena de una cuenta local, solo en memoria."""

    usuario: str
    clave: str

    @classmethod
    def desde_entorno(cls) -> Optional["Credenciales"]:
        usuario = os.environ.get(ENV_USUARIO, "").strip()
        clave = os.environ.get(ENV_CLAVE, "")
        if usuario and clave:
            return cls(usuario, clave)
        return None

    @classmethod
    def preguntar(cls, usuario: str = "") -> "Credenciales":
        import getpass

        usuario = usuario or input("Usuario de AirVault: ").strip()
        clave = getpass.getpass("Contrasena: ")
        if not usuario or not clave:
            raise ErrorDeSesion("Hacen falta usuario y contrasena")
        return cls(usuario, clave)


class SesionAirVault:
    """Sesion HTTP con reintentos y deteccion de caducidad."""

    def __init__(self, config: AirVaultConfig,
                 sesion: requests.Session | None = None):
        self.config = config
        self.http = sesion or requests.Session()
        self.http.headers.update({
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        })
        self._autenticada = False
        self._origen = ""

    @property
    def autenticada(self) -> bool:
        return self._autenticada

    @property
    def origen(self) -> str:
        """De donde salio la sesion, para poder decirlo sin adivinar."""
        return self._origen

    # ── autenticacion ──────────────────────────────────────────────

    def usar_cookies(self, cookies: Mapping[str, str],
                     origen: str = ORIGEN_COOKIE) -> "SesionAirVault":
        """Adopta cookies ya obtenidas en el navegador.

        Van al tarro de ``requests`` y no a una cabecera fija: en cuanto el
        servidor devuelve su primera cookie, ``requests`` reconstruye la
        cabecera ``Cookie`` desde el tarro y se comeria cualquier valor
        puesto a mano.
        """
        if not cookies:
            raise ErrorDeSesion(f"No hay ninguna cookie que usar. {_AYUDA_COOKIE}")
        host = galletas.dominio(self.config.base_url)
        for nombre, valor in cookies.items():
            self.http.cookies.set(nombre, valor, domain=host, path="/")
        if not galletas.sostienen_sesion(cookies):
            logger.warning(
                "Entre las cookies no viene ninguna de sesion ({}). Si "
                "AirVault pide acceso, revisar lo que se copio.",
                ", ".join(galletas.PREFIJOS_DE_SESION),
            )
        self._autenticada = True
        self._origen = origen
        logger.info(
            "Sesion de AirVault tomada del {}: {}",
            origen, galletas.resumir(cookies),
        )
        return self

    def usar_cookie(self, cookie: str) -> "SesionAirVault":
        """Adopta la cabecera ``Cookie`` tal como se copia del navegador."""
        if not str(cookie or "").strip():
            raise ErrorDeSesion(f"La cookie viene vacia. {_AYUDA_COOKIE}")
        analizadas = galletas.parsear(cookie)
        if not analizadas:
            raise ErrorDeSesion(
                f"Lo que se paso no parece una cabecera Cookie. "
                f"{_AYUDA_COOKIE}"
            )
        return self.usar_cookies(analizadas, ORIGEN_COOKIE)

    def usar_edge(self, perfil: Optional[Path] = None) -> "SesionAirVault":
        """Adopta las cookies de AirVault guardadas por Edge."""
        from app.airvault import edge

        host = galletas.dominio(self.config.base_url)
        return self.usar_cookies(edge.leer_cookies(host, perfil), ORIGEN_EDGE)

    def iniciar_sesion(self, credenciales: Credenciales) -> "SesionAirVault":
        """Autentica contra el formulario propio de AirVault.

        Solo sirve para cuentas locales: las que pasan por Entra ID nunca
        llegan a ver este formulario. No se registra ni el usuario ni la
        contrasena en el log; solo si el intento funciono o no.
        """
        entrada = self.config.url("/signin2/")
        respuesta = self.http.get(
            entrada, timeout=self.config.timeout_s, allow_redirects=True
        )
        respuesta.raise_for_status()
        formulario = self._formulario_de_login(respuesta.text)
        if formulario is None:
            raise ErrorDeSesion(
                "No se encontro el formulario de acceso en /signin2/. Es lo "
                "esperable si la cuenta entra por Microsoft Entra ID: en ese "
                f"caso hay que usar la cookie. {_AYUDA_COOKIE}"
            )
        accion, campos, campo_usuario, campo_clave = formulario
        campos[campo_usuario] = credenciales.usuario
        campos[campo_clave] = credenciales.clave
        destino = requests.compat.urljoin(respuesta.url, accion or "")
        envio = self.http.post(
            destino, data=campos, timeout=self.config.timeout_s,
            allow_redirects=True,
        )
        envio.raise_for_status()
        # WS-Federation devuelve un formulario que el navegador reenvia solo.
        envio = self._reenviar_wsfed(envio)
        if self._pide_login(envio.text, envio.url):
            raise ErrorDeSesion(
                "AirVault rechazo el acceso. Revisar usuario y contrasena."
            )
        self._autenticada = True
        self._origen = ORIGEN_FORMULARIO
        logger.info("Sesion de AirVault iniciada por formulario")
        return self

    def _formulario_de_login(
        self, html: str
    ) -> Optional[Tuple[str, Dict[str, str], str, str]]:
        for accion, _metodo, campos in _formularios(html):
            usuario = _buscar_campo(campos, ("user", "usuario", "login",
                                             "email"))
            clave = _buscar_campo(campos, ("pass", "clave", "pwd"))
            if usuario and clave:
                return accion, dict(campos), usuario, clave
        return None

    def _reenviar_wsfed(self, respuesta: requests.Response
                        ) -> requests.Response:
        """Reenvia el formulario con ``wresult`` si el servidor lo devolvio."""
        for _ in range(3):
            formularios = _formularios(respuesta.text)
            objetivo = next(
                (f for f in formularios if "wresult" in f[2]
                 or "wa" in f[2]),
                None,
            )
            if objetivo is None:
                return respuesta
            accion, _metodo, campos = objetivo
            destino = requests.compat.urljoin(respuesta.url, accion or "")
            respuesta = self.http.post(
                destino, data=campos, timeout=self.config.timeout_s,
                allow_redirects=True,
            )
            respuesta.raise_for_status()
        return respuesta

    @staticmethod
    def _pide_login(texto: str, url: str) -> bool:
        minuscula = (url or "").lower()
        if "/signin2/" in minuscula or "login.microsoftonline.com" in minuscula:
            return True
        parciales = ("dosignin", "wsignin1.0")
        return any(p in (texto or "").lower()[:4000] for p in parciales)

    # ── comprobacion ───────────────────────────────────────────────

    def comprobar(self) -> int:
        """Confirma que la sesion sirve y devuelve cuantos lotes ve.

        Se llama antes de empezar, no despues: descubrir que la cookie
        caduco en la pagina 250 de 400 cuesta mucho mas que descubrirlo
        ahora, y la unica senal de que caduco es que el servidor devuelve la
        pagina de acceso en vez de datos.
        """
        datos = self.get(
            "/index/Batch/GetBatches",
            {"repoId": -1, "eventLabel": "", "encodedFilter": "",
             "encodedKeywordFilter": "", "_search": "false", "rows": 1,
             "page": 1, "sidx": "", "sord": "asc"},
        )
        if isinstance(datos, Mapping):
            try:
                return int(datos.get("records", 0) or 0)
            except (TypeError, ValueError):
                return 0
        return 0

    # ── peticiones ─────────────────────────────────────────────────

    def get(self, ruta: str, params: Dict[str, object] | None = None,
            json_esperado: bool = True):
        """GET con reintentos. Devuelve el JSON o el texto de la respuesta."""
        if not self._autenticada:
            raise ErrorDeSesion("La sesion no esta autenticada")
        url = self.config.url(ruta)
        ultimo: Exception | None = None
        for intento in range(1, self.config.reintentos + 1):
            try:
                respuesta = self.http.get(
                    url, params=params, timeout=self.config.timeout_s
                )
            except requests.RequestException as exc:
                ultimo = exc
                logger.warning(
                    "Fallo la peticion a {} (intento {}/{}): {}",
                    ruta, intento, self.config.reintentos, exc,
                )
                continue
            if respuesta.status_code == 401 or self._pide_login(
                respuesta.text[:2000], respuesta.url
            ):
                raise ErrorDeSesion(
                    f"La sesion de AirVault caduco. {_AYUDA_COOKIE}"
                )
            respuesta.raise_for_status()
            if not json_esperado:
                return respuesta.text
            try:
                return respuesta.json()
            except ValueError as exc:
                raise ErrorDeSesion(
                    f"AirVault devolvio algo que no es JSON en {ruta}"
                ) from exc
        raise ErrorDeSesion(
            f"No se pudo contactar {ruta} tras "
            f"{self.config.reintentos} intentos"
        ) from ultimo


def abrir_sesion(
    config: AirVaultConfig,
    cookie: Optional[str] = None,
    perfil: Optional[Path] = None,
    usar_edge: bool = True,
    credenciales: Optional[Credenciales] = None,
) -> SesionAirVault:
    """Arma la sesion con la primera fuente disponible.

    Es el unico punto por el que la linea de comandos y la interfaz abren
    sesion, para que las dos se comporten igual y para que el orden de
    preferencia no se escriba dos veces.

    No comprueba contra el servidor: eso lo hace
    :meth:`SesionAirVault.comprobar`, que si toca la red y a veces no
    conviene (por ejemplo al armar el manifiesto, que es una etapa
    puramente local).
    """
    sesion = SesionAirVault(config)
    pegada = str(cookie or os.environ.get(ENV_COOKIE, "") or "")
    if pegada.strip():
        return sesion.usar_cookie(pegada)

    motivo_edge = ""
    if usar_edge:
        from app.airvault import edge

        try:
            return sesion.usar_edge(perfil)
        except edge.ErrorDeNavegador as exc:
            motivo_edge = str(exc)
            logger.info("No se pudo tomar la cookie de Edge: {}", exc)

    credenciales = credenciales or Credenciales.desde_entorno()
    if credenciales is not None:
        return sesion.iniciar_sesion(credenciales)

    detalle = f" Edge: {motivo_edge}" if motivo_edge else ""
    raise ErrorDeSesion(
        f"No hay ninguna sesion de AirVault disponible. {_AYUDA_COOKIE}"
        f"{detalle}"
    )


def _buscar_campo(campos: Dict[str, str], claves: Tuple[str, ...]
                  ) -> Optional[str]:
    for nombre in campos:
        minus = nombre.lower()
        if any(clave in minus for clave in claves):
            return nombre
    return None
