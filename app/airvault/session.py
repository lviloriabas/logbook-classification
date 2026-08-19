"""Sesion autenticada contra AirVault.

El acceso a AirVault esta federado con Microsoft Entra ID: quien entra pasa
por la pagina de Microsoft y por un segundo factor. Eso no se puede
completar desde un script, y tampoco se puede automatizar con un navegador
porque el programa es portable y no instala nada en tiempo de ejecucion.

Lo que si se puede es **reutilizar la sesion que el usuario ya abrio**. Por
orden de preferencia:

1. La cookie que se pasa a mano (``--cookie`` o ``AIRVAULT_COOKIE``).
2. **El navegador que abre el propio programa.** Edge con un perfil propio
   dentro de ``portable/``: la persona entra una vez con su segundo factor y
   el programa le pide la sesion al navegador. Despues el perfil la
   conserva, asi que las veces siguientes no hay que teclear nada. Ver
   :mod:`app.airvault.navegador`.
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
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple

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
ORIGEN_EDGE = "navegador"
ORIGEN_FORMULARIO = "formulario de AirVault"

_AYUDA_COOKIE = (
    "Entrar a AirVault en el navegador, abrir las herramientas de "
    "desarrollo (F12), copiar la cabecera Cookie de cualquier peticion y "
    "pasarla en --cookie o en la variable AIRVAULT_COOKIE."
)


class ErrorDeSesion(RuntimeError):
    """No se pudo autenticar o la sesion caduco."""


class ErrorDeConexion(RuntimeError):
    """No se pudo hablar con AirVault, ni siquiera reintentando."""


class ErrorDeAirVault(RuntimeError):
    """AirVault contesto, y lo que contesto es un rechazo.

    Es distinto de :class:`ErrorDeConexion` a proposito: un 404 o un 403
    hablan de *esa* peticion —una pagina que ya no esta, un lote sin
    permiso— y no del camino, asi que frenan la pagina y no el lote entero.
    """


# Respuestas que no significan que algo este mal, sino que el servidor
# estaba ocupado: reintentar tiene sentido. Un 404 o un 403, no.
ESTADOS_TRANSITORIOS = frozenset({408, 429, 500, 502, 503, 504})

_AYUDA_LOTE_ABIERTO = (
    " AirVault admite un solo dueno por lote y no contesta «ocupado»: deja "
    "la peticion esperando. Si el lote esta abierto —en el navegador o "
    "porque un intento anterior no llego a soltarlo— hay que cerrarlo en "
    "AirVault antes de indexarlo desde aqui."
)

# Rutas que se cuelgan por un motivo concreto y no por la red. Decirlo solo
# donde corresponde evita mandar a cerrar un lote a quien lo que tiene es
# el wifi caido.
_PISTAS_POR_RUTA = {
    "/index/Batch/LockAndGetBatchInfo": _AYUDA_LOTE_ABIERTO,
}


def _pista_de(ruta: str) -> str:
    """Lo que conviene mirar cuando falla justo esta ruta."""
    for prefijo, pista in _PISTAS_POR_RUTA.items():
        if str(ruta or "").startswith(prefijo):
            return pista
    return (
        " Suele ser la red o AirVault ocupado; se puede volver a intentar y "
        "lo que ya se escribio no se repite."
    )


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


def _describir_cuerpo(respuesta: requests.Response, limite: int = 160) -> str:
    """Resume lo que contesto el servidor, para poder pegarlo en un error.

    Va recortado y en una sola linea a proposito: lo que hace falta para
    reconocer si vino una pagina de error, un HTML de acceso o un JSON, sin
    volcar media respuesta en la ventana ni en el log.
    """
    try:
        texto = (respuesta.text or "").strip()
    except Exception:  # noqa: BLE001 - describir no puede fallar
        texto = ""
    if not texto:
        return f"respuesta vacia, codigo {respuesta.status_code}"
    plano = " ".join(texto.split())
    recorte = plano[:limite] + ("…" if len(plano) > limite else "")
    return f"codigo {respuesta.status_code}, empieza por «{recorte}»"


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
        # Perfil de Edge del que salio la sesion, por si hay que volver a
        # entrar cuando el servidor la rechace.
        self._perfil: Optional[Path] = None
        # Inyectable para que las pruebas no esperen de verdad.
        self.dormir = time.sleep

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
                "Entre las cookies no viene ninguna que autentique ({}). Si "
                "AirVault pide acceso, revisar lo que se copio.",
                ", ".join(galletas.PREFIJOS_DE_AUTENTICACION),
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

    def usar_navegador(
        self, perfil: Optional[Path] = None,
        avisar: Optional[Callable[[str], None]] = None,
        forzar_login: bool = False,
    ) -> "SesionAirVault":
        """Toma la sesion del navegador que abre el propio programa."""
        from app.airvault import navegador

        if forzar_login:
            # Las cookies viejas siguen en el tarro y taparian a las nuevas:
            # ``requests`` manda las dos y AirVault se queda con la primera.
            self.http.cookies.clear()
        self._perfil = perfil
        return self.usar_cookies(
            navegador.obtener_cookies(
                self.config.base_url, perfil, self.config.url_sso,
                espera_login_s=self.config.espera_login_s, avisar=avisar,
                forzar_login=forzar_login,
            ),
            ORIGEN_EDGE,
        )

    def renovar_en_navegador(
        self, avisar: Optional[Callable[[str], None]] = None
    ) -> "SesionAirVault":
        """Vuelve a entrar por el navegador, sin mirar lo que guarda el perfil.

        Es la salida del callejon en el que se metia una sesion caducada: el
        perfil conservaba una cookie que AirVault ya no acepta, releerla
        devolvia siempre la misma y el programa solo sabia proponer que
        alguien copiara una cookie a mano.
        """
        return self.usar_navegador(
            getattr(self, "_perfil", None), avisar, forzar_login=True
        )

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

    def _pedir(self, metodo: str, ruta: str, **extra) -> requests.Response:
        """Hace la peticion, reintentando lo que se puede reintentar.

        Un lote son cientos de peticiones y una subida completa casi dos
        mil: a esa escala un corte de red momentaneo o un servidor ocupado
        dejan de ser raros, y sin reintentos cualquiera de los dos tira el
        trabajo entero. Se reintenta lo que puede arreglarse solo —un
        tiempo agotado, una conexion cortada, un servidor que responde que
        esta ocupado— y nada mas: un 404 no mejora por insistir.

        La espera crece con cada intento; reintentar al instante contra un
        servidor que se esta ahogando solo lo empeora.
        """
        if not self._autenticada:
            raise ErrorDeSesion("La sesion no esta autenticada")
        url = self.config.url(ruta)
        intentos = max(1, self.config.reintentos)
        ultimo = ""
        for intento in range(1, intentos + 1):
            try:
                respuesta = self.http.request(
                    metodo, url, timeout=self.config.timeout_s, **extra
                )
            except requests.Timeout as exc:
                ultimo = (
                    f"no contesto en {self.config.timeout_s:.0f}s ({exc})"
                )
            except requests.RequestException as exc:
                ultimo = f"no se pudo conectar ({exc})"
            else:
                if respuesta.status_code not in ESTADOS_TRANSITORIOS:
                    return respuesta
                ultimo = f"el servidor respondio {respuesta.status_code}"
            logger.warning(
                "AirVault {} en {} (intento {}/{})",
                ultimo, ruta, intento, intentos,
            )
            if intento < intentos:
                self.dormir(self.config.espera_reintento_s * intento)
        raise ErrorDeConexion(
            f"No se pudo completar {ruta} tras {intentos} intentos: "
            f"{ultimo}.{_pista_de(ruta)}"
        )

    def get(self, ruta: str, params: Dict[str, object] | None = None,
            json_esperado: bool = True):
        """GET con reintentos. Devuelve el JSON o el texto de la respuesta."""
        respuesta = self._pedir("GET", ruta, params=params)
        self._comprobar_respuesta(respuesta, ruta)
        if not json_esperado:
            return respuesta.text
        try:
            return respuesta.json()
        except ValueError as exc:
            raise ErrorDeSesion(
                f"AirVault contesto {ruta} con algo que no es JSON "
                f"({_describir_cuerpo(respuesta)}). Suele significar que "
                f"contesto una pagina de error o de acceso en vez de datos."
            ) from exc

    def post(self, ruta: str, **extra) -> requests.Response:
        """POST con los mismos reintentos que el GET.

        Lo usa la subida, que manda el archivo en trozos de un mega: sin
        reintentar, un solo trozo perdido obliga a repetir la subida entera.
        """
        respuesta = self._pedir("POST", ruta, **extra)
        self._comprobar_respuesta(respuesta, ruta)
        return respuesta

    def _comprobar_respuesta(self, respuesta: requests.Response,
                             ruta: str) -> None:
        """Traduce lo que contesto el servidor a un motivo que se entienda.

        ``raise_for_status`` levanta un texto en ingles con la URL entera y
        sin decir que hacer; a mitad de un lote eso llega al reporte como
        «500 Server Error for url ...», que no dice ni que pagina fallo ni
        si conviene reintentar.
        """
        if respuesta.status_code == 401 or self._pide_login(
            respuesta.text[:2000], respuesta.url
        ):
            raise ErrorDeSesion(self._motivo_de_caducidad())
        if respuesta.status_code < 400:
            return
        codigo = respuesta.status_code
        if codigo == 403:
            detalle = (
                "la cuenta entro pero no tiene permiso sobre este "
                "repositorio o este lote"
            )
        elif codigo == 404:
            detalle = (
                "AirVault dice que eso no existe; suele ser un lote borrado "
                "o una pagina que ya no esta en el lote"
            )
        else:
            detalle = f"el servidor respondio {codigo}"
        raise ErrorDeAirVault(
            f"AirVault rechazo {ruta}: {detalle} "
            f"({_describir_cuerpo(respuesta)})."
        )

    def _motivo_de_caducidad(self) -> str:
        """Que decir cuando el servidor contesta la pagina de acceso.

        Depende de donde salio la sesion: mandar a copiar una cookie con
        F12 a quien entro por el navegador es mandarlo por el camino largo
        justo cuando el corto —volver a entrar— es el que corresponde.
        """
        if self._origen == ORIGEN_EDGE:
            return (
                "La sesion que guardaba el perfil de Edge ya no vale: "
                "AirVault volvio a pedir acceso. Hay que entrar de nuevo en "
                "la ventana del navegador que abre el programa."
            )
        if self._origen == ORIGEN_FORMULARIO:
            return (
                "AirVault cerro la sesion iniciada por formulario y volvio a "
                "pedir acceso. Hay que volver a entrar."
            )
        return f"La cookie de AirVault ya no vale. {_AYUDA_COOKIE}"


def abrir_sesion(
    config: AirVaultConfig,
    cookie: Optional[str] = None,
    perfil: Optional[Path] = None,
    usar_edge: bool = True,
    credenciales: Optional[Credenciales] = None,
    avisar: Optional[Callable[[str], None]] = None,
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
        from app.airvault import navegador

        try:
            return sesion.usar_navegador(
                Path(perfil) if perfil else _perfil(config), avisar
            )
        except navegador.ErrorDeNavegador as exc:
            motivo_edge = str(exc)
            logger.info("No se pudo tomar la sesion del navegador: {}", exc)

    credenciales = credenciales or Credenciales.desde_entorno()
    if credenciales is not None:
        return sesion.iniciar_sesion(credenciales)

    detalle = f" Navegador: {motivo_edge}" if motivo_edge else ""
    raise ErrorDeSesion(
        f"No hay ninguna sesion de AirVault disponible. {_AYUDA_COOKIE}"
        f"{detalle}"
    )


def comprobar_o_renovar(
    sesion: SesionAirVault,
    avisar: Optional[Callable[[str], None]] = None,
) -> int:
    """Comprueba la sesion y, si el perfil trae una caducada, vuelve a entrar.

    Un perfil de Edge puede conservar una cookie que AirVault ya no acepta:
    tiene la forma correcta, asi que el programa la daba por buena y moria
    en la primera peticion pidiendo que alguien copiara una cookie a mano.
    Aqui se hace lo que haria una persona: abrir el navegador y entrar otra
    vez. Solo tiene sentido cuando la sesion salio del navegador; una cookie
    pegada a mano no se puede renovar sola.
    """
    try:
        return sesion.comprobar()
    except ErrorDeSesion:
        if sesion.origen != ORIGEN_EDGE:
            raise
        logger.info(
            "La sesion guardada en el perfil de Edge ya no vale; se vuelve "
            "a entrar por el navegador"
        )
        if avisar is not None:
            avisar(
                "La sesión guardada ya no vale; hay que entrar otra vez en "
                "AirVault."
            )
        sesion.renovar_en_navegador(avisar)
        return sesion.comprobar()


def _perfil(config: AirVaultConfig) -> Optional[Path]:
    return Path(config.perfil_navegador) if config.perfil_navegador else None


def _buscar_campo(campos: Dict[str, str], claves: Tuple[str, ...]
                  ) -> Optional[str]:
    for nombre in campos:
        minus = nombre.lower()
        if any(clave in minus for clave in claves):
            return nombre
    return None
