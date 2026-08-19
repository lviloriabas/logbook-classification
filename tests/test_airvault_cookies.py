"""Lectura de la cabecera Cookie, sin tocar el navegador ni la red."""

from __future__ import annotations

from app.airvault import cookies as galletas

# Una cookie de federacion real es base64 y termina en signos de relleno.
FEDAUTH = "77u/PD94bWwgdmVyc2lvbj0iMS4wIiA/Pg=="


def test_parsea_nombre_y_valor():
    assert galletas.parsear("a=1; b=2") == {"a": "1", "b": "2"}


def test_el_relleno_base64_no_se_pierde():
    """Partir por cada ``=`` destruiria el token de federacion."""
    cookies = galletas.parsear(f"FedAuth={FEDAUTH}; ASP.NET_SessionId=abc")
    assert cookies["FedAuth"] == FEDAUTH
    assert cookies["ASP.NET_SessionId"] == "abc"


def test_admite_la_linea_entera_copiada_del_navegador():
    """Quien copia de las herramientas de desarrollo se trae el nombre."""
    assert galletas.parsear("Cookie: a=1; b=2") == {"a": "1", "b": "2"}
    assert galletas.parsear("cookie:  a=1") == {"a": "1"}


def test_quita_las_comillas_que_pone_el_navegador():
    assert galletas.parsear('a="uno dos"')["a"] == "uno dos"


def test_lo_que_no_es_una_cookie_no_deja_nada():
    assert galletas.parsear("") == {}
    assert galletas.parsear("https://airvault.criticaltech.com/index/") == {}
    assert galletas.parsear("   ;;;   ") == {}


def test_ida_y_vuelta_de_la_cabecera():
    original = {"FedAuth": FEDAUTH, "ASP.NET_SessionId": "abc"}
    assert galletas.parsear(galletas.formatear(original)) == original


def test_reconoce_las_cookies_que_abren_sesion():
    assert galletas.sostienen_sesion({"FedAuth": "x"})
    assert galletas.sostienen_sesion({"FedAuth1": "x"})
    assert galletas.sostienen_sesion({"ASP.NET_SessionId": "x"})
    assert galletas.sostienen_sesion({".ASPXAUTH": "x"})


def test_una_cookie_cualquiera_no_abre_sesion():
    assert not galletas.sostienen_sesion({"_ga": "x", "consent": "1"})
    assert not galletas.sostienen_sesion({})


def test_el_resumen_no_revela_el_valor():
    """Es lo unico de una cookie que puede llegar al log."""
    resumen = galletas.resumir({"FedAuth": FEDAUTH})
    assert "FedAuth" in resumen and str(len(FEDAUTH)) in resumen
    assert FEDAUTH not in resumen
    assert FEDAUTH[:12] not in resumen


def test_el_resumen_de_nada_lo_dice():
    assert galletas.resumir({}) == "ninguna"


def test_dominio_de_la_url_de_airvault():
    assert galletas.dominio(
        "https://airvault.criticaltech.com"
    ) == "airvault.criticaltech.com"
    assert galletas.dominio(
        "https://AirVault.CriticalTech.com/index/"
    ) == "airvault.criticaltech.com"


def test_la_cookie_del_dominio_padre_tambien_viaja():
    """Asi las manda el navegador: ``.criticaltech.com`` llega al subdominio."""
    mapa = {
        "airvault.criticaltech.com": {"FedAuth": "1"},
        ".criticaltech.com": {"consent": "2"},
        "otra.com": {"nada": "3"},
    }
    elegidas = galletas.del_dominio(mapa, "airvault.criticaltech.com")
    assert elegidas == {"FedAuth": "1", "consent": "2"}


def test_no_se_cuela_un_dominio_que_solo_termina_parecido():
    mapa = {"malacriticaltech.com": {"x": "1"}}
    assert galletas.del_dominio(mapa, "airvault.criticaltech.com") == {}


def test_combinar_deja_ganar_a_la_ultima_fuente():
    assert galletas.combinar({"a": "1"}, {"a": "2", "b": "3"}) == {
        "a": "2", "b": "3"
    }
