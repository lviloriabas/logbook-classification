"""Cookies de la sesion de AirVault.

El acceso a AirVault esta federado con Microsoft Entra ID, y un login
federado con segundo factor no se puede completar desde un script. Lo que
si se puede es reutilizar la sesion que el usuario ya abrio en el
navegador, que es exactamente una cookie.

Aqui vive lo que se puede probar sin tocar ni el navegador ni la red:
entender la cabecera que alguien pega, saber si trae lo que sostiene una
sesion y describirla sin revelar su contenido. La lectura del perfil de
Edge vive en :mod:`app.airvault.edge`.

Ninguna funcion de este modulo escribe cookies en disco ni las devuelve
formateadas para el log: el valor de una cookie de sesion vale tanto como
la contrasena.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, Mapping
from urllib.parse import urlsplit

# Cookies que **autentican**. ``Critical`` es la de AirVault: la pone el
# sitio al volver de Entra ID y es la unica que abre la sesion, medido
# pidiendo el listado de batches con cada cookie por separado. Las otras dos
# son las formas habituales de ASP.NET —``FedAuth`` se parte en
# ``FedAuth1``, ``FedAuth2``... cuando el token no cabe en una sola, asi que
# se compara por prefijo— y se dejan por si otra instalacion las usa.
#
# Buscar solo las de ASP.NET dejaba al programa esperando cinco minutos con
# la sesion ya abierta delante: las cookies estaban, ninguna se llamaba como
# se esperaba, y el aviso acusaba a la ventana de haberse quedado en la
# pagina de Microsoft.
PREFIJOS_DE_AUTENTICACION = ("Critical", "FedAuth", ".ASPXAUTH")

# Cookies que acompanan pero no autentican. ``ASP.NET_SessionId`` lo pone el
# servidor al primer contacto, antes de saber quien eres: darla por buena
# hacia pasar por sesion abierta una que todavía estaba en la pagina de
# Microsoft, y el batch moria en la primera pagina.
PREFIJOS_DE_ACOMPANAMIENTO = ("ASP.NET_SessionId",)

PREFIJOS_DE_SESION = PREFIJOS_DE_AUTENTICACION + PREFIJOS_DE_ACOMPANAMIENTO

# Alguien puede pegar la linea entera que copia de las herramientas del
# navegador, con el nombre de la cabecera delante.
_PREFIJO_CABECERA = re.compile(r"^\s*cookie\s*:\s*", re.IGNORECASE)


def parsear(cabecera: str) -> Dict[str, str]:
    """Convierte ``"a=1; b=2"`` en un diccionario.

    Los valores se parten por el primer ``=`` y no por todos: las cookies
    de federacion son base64 y terminan en ``=`` de relleno, asi que
    partirlas por cada signo las destruiria.
    """
    texto = _PREFIJO_CABECERA.sub("", str(cabecera or ""))
    cookies: Dict[str, str] = {}
    for trozo in texto.split(";"):
        nombre, sep, valor = trozo.partition("=")
        nombre = nombre.strip()
        if not nombre or not sep:
            continue
        cookies[nombre] = _sin_comillas(valor.strip())
    return cookies


def _sin_comillas(valor: str) -> str:
    if len(valor) >= 2 and valor[0] == '"' and valor[-1] == '"':
        return valor[1:-1]
    return valor


def formatear(cookies: Mapping[str, str]) -> str:
    """Arma la cabecera ``Cookie`` a partir del diccionario."""
    return "; ".join(f"{n}={v}" for n, v in cookies.items() if n)


def sostienen_sesion(cookies: Mapping[str, str]) -> bool:
    """Dice si entre las cookies esta la que de verdad autentica.

    Sirve para avisar temprano de que lo pegado no es lo que hace falta, en
    vez de descubrirlo a mitad de un batch de 400 paginas. Una
    ``ASP.NET_SessionId`` sola no cuenta: la pone el servidor antes de saber
    quien eres.
    """
    return any(
        nombre.startswith(prefijo)
        for nombre in cookies
        for prefijo in PREFIJOS_DE_AUTENTICACION
    )


def resumir(cookies: Mapping[str, str]) -> str:
    """Describe las cookies sin revelar ni un caracter de sus valores.

    Es lo unico de una cookie que puede llegar al log: el nombre y cuanto
    mide. Con el valor, cualquiera que lea el log entra a AirVault como el
    usuario.
    """
    if not cookies:
        return "ninguna"
    return ", ".join(f"{n} ({len(v)} car.)" for n, v in sorted(cookies.items()))


def dominio(base_url: str) -> str:
    """Host al que pertenecen las cookies de AirVault."""
    partes = urlsplit(str(base_url or ""))
    return (partes.hostname or str(base_url or "").strip()).lower()


def del_dominio(cookies: Mapping[str, Mapping[str, str]], host: str) -> Dict[str, str]:
    """Filtra un mapa ``{host: {nombre: valor}}`` para quedarse con ``host``.

    Se aceptan tambien los dominios padre: una cookie puesta en
    ``.criticaltech.com`` viaja a ``airvault.criticaltech.com``, que es como
    las manda el navegador.
    """
    objetivo = str(host or "").lower().lstrip(".")
    elegidas: Dict[str, str] = {}
    for propio, valores in cookies.items():
        limpio = str(propio or "").lower().lstrip(".")
        if limpio == objetivo or objetivo.endswith("." + limpio):
            elegidas.update(valores)
    return elegidas


def combinar(*fuentes: Iterable[Mapping[str, str]]) -> Dict[str, str]:
    """Une varios diccionarios de cookies; gana el ultimo que las trae."""
    total: Dict[str, str] = {}
    for fuente in fuentes:
        if isinstance(fuente, Mapping):
            total.update(fuente)
    return total
