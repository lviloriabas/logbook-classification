"""La sesion de AirVault, tomada del navegador que el propio programa abre.

El acceso esta federado con Microsoft Entra ID y pide segundo factor. Eso
no se automatiza —ni se debe: el segundo factor existe justamente para que
lo haga una persona—, pero lo que sigue despues si.

El programa abre Edge con **un perfil propio**, dentro de ``portable/``,
apuntando a AirVault. La persona entra una vez, con su usuario y su segundo
factor, y en cuanto la sesion queda abierta el programa se la pide al propio
navegador por su protocolo de depuracion y cierra la ventana. A partir de
ahi el perfil conserva la sesion, asi que las veces siguientes el navegador
se abre sin ventana, entrega la cookie y se cierra: nadie teclea nada.

Por que por el protocolo y no leyendo el archivo de cookies: un Edge moderno
las cifra con la identidad del navegador (``v20``), que no se deshace desde
fuera, y ademas mantiene su base abierta en exclusiva mientras corre. El
navegador si sabe descifrar las suyas, y por el protocolo las entrega ya en
claro. Ver :mod:`app.airvault.cookies`.

Nada de esto instala ni descarga nada: Edge ya viene con Windows y el perfil
es una carpeta mas dentro de ``portable/``. Si no hay Edge, o si algo falla,
se dice y se sigue con la cookie pegada a mano.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional

from loguru import logger

from app.airvault import cookies as galletas

# Donde vive el perfil que usa el programa. Va en portable/ para que la
# carpeta entera se pueda copiar a otra maquina con la sesion incluida.
PERFIL_POR_DEFECTO = Path("portable") / "edge-airvault"

_UBICACIONES_EDGE = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

# Argumentos que aislan la ventana del Edge de la persona: perfil propio, sin
# la primera ejecucion, sin extensiones y sin restaurar pestanas.
_ARGUMENTOS = (
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-background-networking",
    "--restore-last-session=false",
)


class ErrorDeNavegador(RuntimeError):
    """No se pudo obtener la sesion a traves del navegador."""


# ── protocolo de depuracion ────────────────────────────────────────

class _WebSocket:
    """Cliente WebSocket con lo justo para hablar con Edge.

    Se escribe a mano porque el programa es portable y no puede traer una
    dependencia nueva: son un saludo HTTP y unas tramas, y el interlocutor
    es siempre el mismo navegador en la maquina local.
    """

    def __init__(self, url: str, timeout: float = 15.0):
        resto = url.split("://", 1)[1]
        host_puerto, _, ruta = resto.partition("/")
        host, _, puerto = host_puerto.partition(":")
        self.sock = socket.create_connection((host, int(puerto or 80)), timeout)
        self.sock.settimeout(timeout)
        clave = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            f"GET /{ruta} HTTP/1.1\r\nHost: {host_puerto}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {clave}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            .encode()
        )
        datos = b""
        while b"\r\n\r\n" not in datos:
            trozo = self.sock.recv(4096)
            if not trozo:
                raise ErrorDeNavegador("Edge cerro la conexion de depuracion")
            datos += trozo
        if b" 101 " not in datos.split(b"\r\n")[0] + b" ":
            raise ErrorDeNavegador("Edge no acepto la conexion de depuracion")
        self._resto = datos.split(b"\r\n\r\n", 1)[1]
        self._siguiente_id = 0

    def _leer(self, cuantos: int) -> bytes:
        while len(self._resto) < cuantos:
            trozo = self.sock.recv(65536)
            if not trozo:
                raise ErrorDeNavegador("Edge cerro la conexion de depuracion")
            self._resto += trozo
        salida, self._resto = self._resto[:cuantos], self._resto[cuantos:]
        return salida

    def _enviar(self, texto: str) -> None:
        carga = texto.encode("utf-8")
        largo = len(carga)
        cabecera = b"\x81"
        if largo < 126:
            cabecera += struct.pack("!B", largo | 0x80)
        elif largo < 65536:
            cabecera += struct.pack("!BH", 126 | 0x80, largo)
        else:
            cabecera += struct.pack("!BQ", 127 | 0x80, largo)
        mascara = os.urandom(4)
        self.sock.sendall(
            cabecera + mascara
            + bytes(b ^ mascara[i % 4] for i, b in enumerate(carga))
        )

    def _recibir(self) -> str:
        primero = self._leer(2)
        largo = primero[1] & 0x7F
        if largo == 126:
            largo = struct.unpack("!H", self._leer(2))[0]
        elif largo == 127:
            largo = struct.unpack("!Q", self._leer(8))[0]
        return self._leer(largo).decode("utf-8", "replace")

    def pedir(self, metodo: str, **params) -> dict:
        """Manda un comando y devuelve su respuesta, saltandose los avisos."""
        self._siguiente_id += 1
        propio = self._siguiente_id
        self._enviar(json.dumps(
            {"id": propio, "method": metodo, "params": params or {}}
        ))
        while True:
            mensaje = json.loads(self._recibir())
            if mensaje.get("id") == propio:
                if "error" in mensaje:
                    raise ErrorDeNavegador(
                        f"Edge rechazo {metodo}: "
                        f"{mensaje['error'].get('message')}"
                    )
                return mensaje.get("result", {})

    def cerrar(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# ── el navegador ───────────────────────────────────────────────────

def ruta_de_edge(candidatas=_UBICACIONES_EDGE) -> Path:
    """Ubica el Edge que trae Windows."""
    for ruta in candidatas:
        if Path(ruta).is_file():
            return Path(ruta)
    raise ErrorDeNavegador(
        "No se encontro Microsoft Edge en esta maquina. Hay que pegar la "
        "cookie a mano."
    )


def _puerto_libre() -> int:
    """Un puerto que nadie este usando, para no chocar con otra ventana."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class SesionDeNavegador:
    """Una ventana de Edge con perfil propio, abierta por el programa."""

    def __init__(self, perfil: Path, edge: Optional[Path] = None,
                 visible: bool = True):
        self.perfil = Path(perfil)
        self.edge = Path(edge) if edge else ruta_de_edge()
        self.visible = visible
        self.puerto = _puerto_libre()
        self._proceso: Optional[subprocess.Popen] = None

    def abrir(self, url: str, espera_s: float = 30.0) -> dict:
        """Arranca el navegador y espera a que conteste el protocolo."""
        self.perfil.mkdir(parents=True, exist_ok=True)
        orden = [
            str(self.edge),
            f"--remote-debugging-port={self.puerto}",
            f"--user-data-dir={self.perfil}",
            *_ARGUMENTOS,
        ]
        if not self.visible:
            orden.append("--headless=new")
        orden.append(url)
        self._proceso = subprocess.Popen(
            orden, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        limite = time.monotonic() + espera_s
        while time.monotonic() < limite:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.puerto}/json/version", timeout=2
                ) as respuesta:
                    return json.load(respuesta)
            except (urllib.error.URLError, OSError, ValueError):
                if self._proceso.poll() is not None:
                    raise ErrorDeNavegador(
                        "Edge se cerro antes de abrir la sesion"
                    )
                time.sleep(0.5)
        raise ErrorDeNavegador(
            f"Edge no contesto en {espera_s:.0f}s. Puede que ya este abierto "
            f"con este mismo perfil."
        )

    def cookies(self, version: dict) -> Dict[str, List[dict]]:
        """Pide al navegador sus cookies, ya descifradas."""
        ws = _WebSocket(version["webSocketDebuggerUrl"])
        try:
            crudas = ws.pedir("Storage.getCookies").get("cookies", [])
        finally:
            ws.cerrar()
        por_dominio: Dict[str, List[dict]] = {}
        for cookie in crudas:
            dominio = str(cookie.get("domain", ""))
            por_dominio.setdefault(dominio, []).append(cookie)
        return por_dominio

    def cerrar(self) -> None:
        if self._proceso is None:
            return
        self._proceso.terminate()
        try:
            self._proceso.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proceso.kill()
        self._proceso = None

    def __enter__(self) -> "SesionDeNavegador":
        return self

    def __exit__(self, *_excepcion) -> None:
        self.cerrar()


def _del_dominio(por_dominio: Dict[str, List[dict]], host: str
                 ) -> Dict[str, str]:
    """Cookies que viajan a ``host``, en el formato del resto del modulo."""
    mapa = {
        dominio: {str(c.get("name")): str(c.get("value", ""))
                  for c in lista}
        for dominio, lista in por_dominio.items()
    }
    return galletas.del_dominio(mapa, host)


def obtener_cookies(
    base_url: str,
    perfil: Optional[Path] = None,
    url_sso: str = "",
    edge: Optional[Path] = None,
    espera_login_s: float = 300.0,
    avisar: Optional[Callable[[str], None]] = None,
    dormir: Callable[[float], None] = time.sleep,
    reloj: Callable[[], float] = time.monotonic,
) -> Dict[str, str]:
    """Devuelve las cookies de AirVault, abriendo el navegador si hace falta.

    Primero lo intenta **sin ventana**: si el perfil conserva la sesion de la
    vez anterior, nadie tiene que hacer nada. Solo cuando no hay sesion se
    abre una ventana para que la persona entre; en cuanto AirVault suelta sus
    cookies, la ventana se cierra sola.
    """
    perfil = Path(perfil or PERFIL_POR_DEFECTO)
    host = galletas.dominio(base_url)
    # Se entra por el enlace federado, no por la raiz: es el que dispara la
    # redireccion a Microsoft y, con ella, la cookie que autentica.
    entrada = url_sso or base_url

    with SesionDeNavegador(perfil, edge, visible=False) as navegador:
        encontradas = _del_dominio(navegador.cookies(
            navegador.abrir(entrada)
        ), host)
    if galletas.sostienen_sesion(encontradas):
        logger.info("La sesion del perfil de Edge seguia abierta")
        return encontradas

    if avisar is not None:
        avisar(
            "Se abrio una ventana de Edge: entre a AirVault con su usuario "
            "de Microsoft. La ventana se cierra sola al terminar."
        )
    with SesionDeNavegador(perfil, edge, visible=True) as navegador:
        version = navegador.abrir(entrada)
        limite = reloj() + espera_login_s
        while reloj() < limite:
            encontradas = _del_dominio(navegador.cookies(version), host)
            if galletas.sostienen_sesion(encontradas):
                logger.info(
                    "Sesion de AirVault abierta en el navegador: {}",
                    galletas.resumir(encontradas),
                )
                return encontradas
            dormir(2.0)
    raise ErrorDeNavegador(
        f"Nadie entro a AirVault en {espera_login_s / 60:.0f} minutos. "
        f"Se puede volver a intentar o pegar la cookie a mano."
    )


def disponible() -> bool:
    """Dice si en esta maquina se puede tomar la sesion del navegador."""
    try:
        ruta_de_edge()
        return True
    except ErrorDeNavegador:
        return False
