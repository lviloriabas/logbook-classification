"""Lectura de las cookies del perfil de Edge.

Se prueba sobre perfiles y bases de datos fabricados en el temporal: nada
toca el Edge de la maquina. El descifrado se ejerce con una clave conocida,
que es exactamente el formato que usa el navegador.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from Crypto.Cipher import AES

from app.airvault import edge

CLAVE = b"0123456789abcdef0123456789abcdef"  # 32 bytes, como la de Edge
HOST = "airvault.criticaltech.com"


def cifrar(valor: str, clave: bytes = CLAVE, version: bytes = b"v10") -> bytes:
    """Arma el blob tal como lo guarda Edge: version, nonce, texto y tag."""
    nonce = b"n" * 12
    cifrador = AES.new(clave, AES.MODE_GCM, nonce=nonce)
    cuerpo, etiqueta = cifrador.encrypt_and_digest(valor.encode("utf-8"))
    return version + nonce + cuerpo + etiqueta


def base_de_cookies(ruta, filas) -> None:
    """Crea una base con la tabla que usa Chromium."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    conexion = sqlite3.connect(str(ruta))
    conexion.execute(
        "CREATE TABLE cookies (host_key TEXT, name TEXT, "
        "encrypted_value BLOB, expires_utc INTEGER)"
    )
    conexion.executemany(
        "INSERT INTO cookies VALUES (?, ?, ?, ?)", filas
    )
    conexion.commit()
    conexion.close()


def chromium_utc(momento: datetime) -> int:
    """Pasa una fecha al reloj de Chromium: microsegundos desde 1601."""
    delta = momento - datetime(1601, 1, 1, tzinfo=timezone.utc)
    return int(delta.total_seconds() * 1_000_000)


# ── descifrado ─────────────────────────────────────────────────────

def test_descifra_el_formato_del_navegador():
    assert edge.descifrar_valor(cifrar("FedAuthValue"), CLAVE) == "FedAuthValue"


def test_tambien_descifra_la_version_v11():
    assert edge.descifrar_valor(
        cifrar("x", version=b"v11"), CLAVE
    ) == "x"


def test_el_cifrado_atado_al_navegador_se_dice_y_no_se_rodea():
    """``v20`` solo lo deshace Edge; hay que pegar la cookie a mano."""
    with pytest.raises(edge.CifradoNoSoportado) as fallo:
        edge.descifrar_valor(b"v20" + b"x" * 40, CLAVE)
    assert "pegar la cookie" in str(fallo.value)


def test_una_cookie_vacia_no_es_un_error():
    assert edge.descifrar_valor(b"", CLAVE) == ""


def test_con_otra_clave_no_se_descifra():
    with pytest.raises(edge.ErrorDeNavegador):
        edge.descifrar_valor(cifrar("x"), b"f" * 32)


# ── vigencia ───────────────────────────────────────────────────────

def test_la_cookie_de_sesion_se_da_por_buena():
    """Un cero es cookie de sesion, no una caducada en 1601."""
    assert edge._vigente(0) is True


def test_la_cookie_caducada_se_descarta():
    ayer = datetime.now(timezone.utc) - timedelta(days=1)
    assert edge._vigente(chromium_utc(ayer)) is False


def test_la_cookie_vigente_se_conserva():
    manana = datetime.now(timezone.utc) + timedelta(days=1)
    assert edge._vigente(chromium_utc(manana)) is True


# ── dominios ───────────────────────────────────────────────────────

def test_pertenece_al_host_y_a_su_dominio_padre():
    assert edge._pertenece(HOST, HOST)
    assert edge._pertenece(".criticaltech.com", HOST)
    assert not edge._pertenece("otra.com", HOST)
    assert not edge._pertenece("", HOST)


# ── perfiles ───────────────────────────────────────────────────────

def test_los_perfiles_empiezan_por_default(tmp_path):
    for nombre in ("Profile 2", "Default", "Profile 1"):
        base_de_cookies(tmp_path / nombre / "Network" / "Cookies", [])
    (tmp_path / "Crashpad").mkdir()
    nombres = [p.name for p in edge.perfiles(tmp_path)]
    assert nombres == ["Default", "Profile 1", "Profile 2"]


def test_una_carpeta_sin_cookies_no_es_un_perfil(tmp_path):
    (tmp_path / "Default").mkdir()
    assert edge.perfiles(tmp_path) == []
    assert edge.disponible(tmp_path) is False


# ── lectura completa de una base ───────────────────────────────────

def test_lee_solo_las_cookies_del_dominio(tmp_path):
    manana = chromium_utc(datetime.now(timezone.utc) + timedelta(days=1))
    ruta = tmp_path / "Cookies"
    base_de_cookies(ruta, [
        (HOST, "FedAuth", cifrar("token"), manana),
        (".criticaltech.com", "consent", cifrar("si"), 0),
        ("otra.com", "ajena", cifrar("no"), manana),
    ])
    leidas = edge._leer_una_base(ruta, HOST, CLAVE)
    assert leidas == {"FedAuth": "token", "consent": "si"}


def test_no_se_traen_las_cookies_caducadas(tmp_path):
    ayer = chromium_utc(datetime.now(timezone.utc) - timedelta(days=1))
    ruta = tmp_path / "Cookies"
    base_de_cookies(ruta, [
        (HOST, "vieja", cifrar("x"), ayer),
        (HOST, "FedAuth", cifrar("token"), 0),
    ])
    assert edge._leer_una_base(ruta, HOST, CLAVE) == {"FedAuth": "token"}


def test_una_cookie_ilegible_no_tumba_a_las_demas(tmp_path):
    ruta = tmp_path / "Cookies"
    base_de_cookies(ruta, [
        (HOST, "rota", cifrar("x", clave=b"f" * 32), 0),
        (HOST, "FedAuth", cifrar("token"), 0),
    ])
    assert edge._leer_una_base(ruta, HOST, CLAVE) == {"FedAuth": "token"}


def test_si_todo_el_dominio_esta_en_v20_se_explica(tmp_path):
    """El caso de un Edge moderno: no hay nada que rescatar y se dice."""
    ruta = tmp_path / "Cookies"
    base_de_cookies(ruta, [
        (HOST, "FedAuth", b"v20" + b"x" * 40, 0),
    ])
    with pytest.raises(edge.CifradoNoSoportado):
        edge._leer_una_base(ruta, HOST, CLAVE)


def test_un_dominio_sin_cookies_devuelve_vacio(tmp_path):
    ruta = tmp_path / "Cookies"
    base_de_cookies(ruta, [("otra.com", "ajena", cifrar("x"), 0)])
    assert edge._leer_una_base(ruta, HOST, CLAVE) == {}


def test_sin_base_de_cookies_no_se_culpa_a_edge(tmp_path):
    """El mensaje de 'Edge esta abierto' solo vale si el archivo existe."""
    with pytest.raises(edge.ErrorDeNavegador) as fallo:
        edge._copiar_base(tmp_path / "no-existe")
    assert not isinstance(fallo.value, edge.NavegadorAbierto)


def test_sin_ningun_perfil_se_dice_que_no_hay(tmp_path):
    with pytest.raises(edge.ErrorDeNavegador) as fallo:
        edge.leer_cookies(HOST, raiz=tmp_path)
    assert "perfil de Edge" in str(fallo.value)
