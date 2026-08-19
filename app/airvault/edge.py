"""Lectura de las cookies de AirVault del perfil de Microsoft Edge.

Es un atajo, no un requisito: evita que alguien tenga que copiar la cookie
a mano cada vez que caduca la sesion. Si algo de aqui falla, el modulo
sigue funcionando con la cookie pegada, que es el camino principal. Por eso
todos los errores de este archivo son informativos y ninguno detiene un
trabajo por si mismo.

Es lo unico del proyecto que mira fuera de ``portable/``, y solo lee: la
base de cookies del propio usuario, en su propia maquina, para reutilizar
la sesion que el mismo abrio. No escribe nada en el perfil ni guarda lo que
lee.

Dos limites que conviene conocer antes de contar con esto:

- **Edge tiene que estar cerrado.** Mientras corre mantiene la base de
  cookies abierta en exclusiva y Windows no deja ni copiarla.
- **Las cookies nuevas van cifradas con la identidad del navegador**
  (prefijo ``v20``, la clave ``app_bound_encrypted_key`` del ``Local
  State``). Ese cifrado esta atado al proceso de Edge y no se puede
  deshacer desde fuera; en un Edge moderno eso deja este atajo sin efecto y
  hay que pegar la cookie. Se detecta y se dice; no se intenta rodear.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from app.airvault import cookies as galletas

# Cabecera de los valores cifrados con la clave del ``Local State``.
PREFIJOS_AES = (b"v10", b"v11")
# Cabecera de los valores atados a la identidad del navegador.
PREFIJO_APP_BOUND = b"v20"

_TAM_NONCE = 12
_TAM_TAG = 16
# El ``encrypted_key`` del ``Local State`` viene con esta marca delante.
_MARCA_DPAPI = b"DPAPI"
# Chromium cuenta el tiempo en microsegundos desde 1601.
_EPOCA_CHROMIUM = datetime(1601, 1, 1, tzinfo=timezone.utc)


class ErrorDeNavegador(RuntimeError):
    """No se pudieron leer las cookies del navegador."""


class NavegadorAbierto(ErrorDeNavegador):
    """Edge esta corriendo y no suelta la base de cookies."""


class CifradoNoSoportado(ErrorDeNavegador):
    """La cookie va cifrada con la identidad del navegador (``v20``)."""


# ── perfiles ───────────────────────────────────────────────────────

def carpeta_edge(raiz: Optional[Path] = None) -> Path:
    """Carpeta ``User Data`` de Edge para el usuario actual."""
    if raiz is not None:
        return Path(raiz)
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        raise ErrorDeNavegador("No hay LOCALAPPDATA; no se ubica Edge")
    return Path(local) / "Microsoft" / "Edge" / "User Data"


def perfiles(raiz: Optional[Path] = None) -> List[Path]:
    """Perfiles con base de cookies, empezando por ``Default``.

    Se devuelve ``Default`` primero porque es donde esta la sesion de casi
    todo el mundo; los demas se recorren solo si ahi no aparece nada.
    """
    base = carpeta_edge(raiz)
    if not base.is_dir():
        return []
    encontrados = [
        carpeta for carpeta in sorted(base.iterdir())
        if carpeta.is_dir() and (carpeta / "Network" / "Cookies").is_file()
    ]
    encontrados.sort(key=lambda c: (c.name != "Default", c.name))
    return encontrados


# ── claves y descifrado ────────────────────────────────────────────

def clave_maestra(local_state: Path | str) -> bytes:
    """Saca del ``Local State`` la clave con la que se cifran las cookies.

    La clave viaja protegida con DPAPI, que solo la devuelve al mismo
    usuario de Windows en la misma maquina. Eso es justo lo que queremos: si
    el archivo se copia a otra parte, deja de servir.
    """
    ruta = Path(local_state)
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ErrorDeNavegador(
            f"No se pudo leer {ruta.name} de Edge: {exc}"
        ) from exc
    import base64

    codificada = str(datos.get("os_crypt", {}).get("encrypted_key", ""))
    if not codificada:
        raise ErrorDeNavegador(
            "El perfil de Edge no tiene clave de cifrado de cookies"
        )
    cruda = base64.b64decode(codificada)
    if cruda.startswith(_MARCA_DPAPI):
        cruda = cruda[len(_MARCA_DPAPI):]
    return _desproteger(cruda)


def _desproteger(datos: bytes) -> bytes:
    """Deshace la proteccion DPAPI del usuario actual."""
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    buffer_entrada = ctypes.create_string_buffer(datos, len(datos))
    entrada = _Blob(
        len(datos),
        ctypes.cast(buffer_entrada, ctypes.POINTER(ctypes.c_char)),
    )
    salida = _Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(
        ctypes.byref(entrada), None, None, None, None, 0,
        ctypes.byref(salida),
    ):
        codigo = ctypes.get_last_error()
        raise ErrorDeNavegador(
            f"Windows no devolvio la clave de Edge (error {codigo}). "
            f"Suele pasar cuando el perfil es de otro usuario o de otra "
            f"maquina."
        )
    try:
        return ctypes.string_at(salida.pbData, salida.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(salida.pbData, ctypes.c_void_p))


def descifrar_valor(blob: bytes, clave: bytes) -> str:
    """Descifra el valor de una cookie tal como lo guarda Edge.

    Formato de los valores modernos: tres bytes de version, doce de nonce,
    el texto cifrado y dieciseis de etiqueta de autenticacion, en AES-GCM
    con la clave del ``Local State``. Los antiguos son un blob de DPAPI sin
    cabecera.
    """
    datos = bytes(blob or b"")
    if not datos:
        return ""
    if datos.startswith(PREFIJO_APP_BOUND):
        raise CifradoNoSoportado(
            "La cookie esta cifrada con la identidad de Edge (v20). Ese "
            "cifrado solo lo deshace el propio navegador, asi que hay que "
            "pegar la cookie a mano."
        )
    if not datos.startswith(PREFIJOS_AES):
        # Cookie antigua: DPAPI directo, sin cabecera de version.
        return _desproteger(datos).decode("utf-8", errors="replace")

    from Crypto.Cipher import AES

    nonce = datos[3:3 + _TAM_NONCE]
    cuerpo = datos[3 + _TAM_NONCE:-_TAM_TAG]
    etiqueta = datos[-_TAM_TAG:]
    try:
        cifrador = AES.new(clave, AES.MODE_GCM, nonce=nonce)
        claro = cifrador.decrypt_and_verify(cuerpo, etiqueta)
    except (ValueError, KeyError) as exc:
        raise ErrorDeNavegador(
            f"La cookie no se dejo descifrar: {exc}"
        ) from exc
    return claro.decode("utf-8", errors="replace")


# ── lectura de la base ─────────────────────────────────────────────

def _copiar_base(origen: Path) -> Path:
    """Copia la base de cookies a un temporal para poder abrirla.

    SQLite no puede abrir el archivo mientras Edge lo tiene tomado, ni
    siquiera en solo lectura, asi que se trabaja sobre una copia. Si ni la
    copia se deja hacer, es que Edge esta abierto.
    """
    if not Path(origen).is_file():
        raise ErrorDeNavegador(
            f"El perfil de Edge no tiene base de cookies en {origen}"
        )
    destino = Path(tempfile.mkdtemp(prefix="airvault-ck-")) / "Cookies"
    try:
        shutil.copyfile(origen, destino)
    except OSError as exc:
        shutil.rmtree(destino.parent, ignore_errors=True)
        raise NavegadorAbierto(
            "Edge tiene tomada su base de cookies. Cerrar Edge por completo "
            "y volver a intentar, o pegar la cookie a mano."
        ) from exc
    return destino


def _vigente(expires_utc: int, ahora: Optional[datetime] = None) -> bool:
    """Dice si la cookie no ha caducado todavia.

    Un cero significa cookie de sesion, que dura lo que dure el navegador y
    aqui se da por buena: si esta escrita, Edge la sigue considerando suya.
    """
    if not expires_utc:
        return True
    ahora = ahora or datetime.now(timezone.utc)
    try:
        vence = _EPOCA_CHROMIUM + timedelta(microseconds=int(expires_utc))
    except (OverflowError, ValueError):
        return True
    return vence > ahora


def leer_cookies(
    host: str, perfil: Optional[Path] = None,
    raiz: Optional[Path] = None,
) -> Dict[str, str]:
    """Devuelve ``{nombre: valor}`` de las cookies de ``host`` en Edge.

    Recorre los perfiles hasta dar con uno que traiga cookies del dominio.
    Las que no se dejan descifrar se saltan con su motivo, porque una sola
    cookie ilegible no tiene por que tumbar a las demas; si no queda
    ninguna, se levanta el ultimo motivo para que se pueda explicar.
    """
    candidatos = [Path(perfil)] if perfil is not None else perfiles(raiz)
    if not candidatos:
        raise ErrorDeNavegador(
            "No se encontro ningun perfil de Edge con cookies guardadas"
        )
    ultimo: Optional[Exception] = None
    for carpeta in candidatos:
        base = carpeta / "Network" / "Cookies"
        if not base.is_file():
            continue
        local_state = carpeta.parent / "Local State"
        try:
            clave = clave_maestra(local_state)
        except ErrorDeNavegador as exc:
            ultimo = exc
            continue
        try:
            encontradas = _leer_una_base(base, host, clave)
        except ErrorDeNavegador as exc:
            # Un perfil ilegible no tiene por que tapar al siguiente; el
            # motivo se guarda por si ninguno funciona.
            ultimo = exc
            continue
        if encontradas:
            return encontradas
    if ultimo is not None:
        raise ultimo
    raise ErrorDeNavegador(
        f"Edge no tiene ninguna cookie de {host}. Entrar a AirVault en el "
        f"navegador y volver a intentar."
    )


def _leer_una_base(base: Path, host: str, clave: bytes) -> Dict[str, str]:
    """Lee y descifra las cookies de un dominio en una base concreta."""
    copia = _copiar_base(base)
    try:
        conexion = sqlite3.connect(str(copia))
        try:
            filas = conexion.execute(
                "SELECT host_key, name, encrypted_value, expires_utc "
                "FROM cookies"
            ).fetchall()
        finally:
            conexion.close()
    except sqlite3.Error as exc:
        raise ErrorDeNavegador(
            f"La base de cookies de Edge no se dejo leer: {exc}"
        ) from exc
    finally:
        shutil.rmtree(copia.parent, ignore_errors=True)

    por_dominio: Dict[str, Dict[str, str]] = {}
    sin_soporte: Optional[CifradoNoSoportado] = None
    for host_key, nombre, cifrada, expira in filas:
        if not _vigente(expira):
            continue
        dominio_cookie = str(host_key or "")
        if not _pertenece(dominio_cookie, host):
            continue
        try:
            valor = descifrar_valor(cifrada, clave)
        except CifradoNoSoportado as exc:
            sin_soporte = exc
            continue
        except ErrorDeNavegador:
            continue
        if valor:
            por_dominio.setdefault(dominio_cookie, {})[str(nombre)] = valor

    elegidas = galletas.del_dominio(por_dominio, host)
    if not elegidas and sin_soporte is not None:
        raise sin_soporte
    return elegidas


def _pertenece(host_key: str, host: str) -> bool:
    """Dice si una cookie de ``host_key`` viaja a ``host``."""
    propio = str(host_key or "").lower().lstrip(".")
    objetivo = str(host or "").lower().lstrip(".")
    if not propio or not objetivo:
        return False
    return propio == objetivo or objetivo.endswith("." + propio)


def disponible(raiz: Optional[Path] = None) -> bool:
    """Dice si hay al menos un perfil de Edge del que intentar leer."""
    try:
        return bool(perfiles(raiz))
    except ErrorDeNavegador:
        return False
