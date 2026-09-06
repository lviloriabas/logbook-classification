"""AirVault como fuente de autoridad de la memoria de libros.

Lo que se comprueba aqui es de donde sale la evidencia y que no se cuele lo
que no la es: la matricula de una pagina que no esta en verde no es un
indice, es la clasificacion que Quick Upload le pone al archivo entero.
"""

from __future__ import annotations

from datetime import date
import json

from app.airvault.client import PaginaIndexada
from app.airvault.config import (
    CAMPO_END_DATE,
    CAMPO_LOG_NUMBER,
    CAMPO_MATRICULA,
    ESTADO_NECESITA_CORRECCION,
    ESTADO_VALIDO,
)
from app.airvault.memoria import (
    bitacoras_del_libro,
    libros_de_la_memoria,
    libros_por_verificar,
    observaciones_de_paginas,
    observaciones_de_websearch,
    verificar_con_el_batch,
    verificar_con_websearch,
    verificar_por_tandas,
)
from app.airvault.websearch import Indice


def _pagina(numero, matricula="HP-1376CMP", fecha="05/14/2025",
            estado=ESTADO_VALIDO, pagina=1):
    valores = {
        CAMPO_LOG_NUMBER: numero,
        CAMPO_MATRICULA: matricula,
        CAMPO_END_DATE: fecha,
    }
    return PaginaIndexada(
        pagina=pagina, estado=estado, valores=valores, columnas={}
    )


def _instalacion(tmp_path, matriculas=None, flota=("HP-1376CMP",
                                                   "HP-1835CMP")):
    """La raiz portable con los tres archivos que intervienen."""
    (tmp_path / "book_matriculas.json").write_text(
        json.dumps(matriculas or {}), encoding="utf-8"
    )
    (tmp_path / "book_fechas.json").write_text("{}", encoding="utf-8")
    (tmp_path / "fleet.json").write_text(
        json.dumps({"version": 1, "matriculas": list(flota)}),
        encoding="utf-8",
    )
    return tmp_path


# ── de las paginas del batch ───────────────────────────────────────

def test_una_pagina_en_verde_aporta_su_matricula_y_su_fecha():
    observaciones = observaciones_de_paginas([_pagina("2315952")])

    assert len(observaciones) == 1
    assert observaciones[0].log_number == "2315952"
    assert observaciones[0].matricula == "HP-1376CMP"
    assert observaciones[0].fecha == date(2025, 5, 14)


def test_una_pagina_que_no_esta_en_verde_no_aporta_nada():
    """En amarillo lo que se ve es la clasificacion de Quick Upload.

    Quick Upload le pone a todas las paginas del archivo el avion de la
    primera bitacora, asi que contrastar contra eso acusaria a media
    entrega de tener otra matricula.
    """
    paginas = [_pagina("2315952", estado=ESTADO_NECESITA_CORRECCION)]

    assert observaciones_de_paginas(paginas) == []


def test_una_pagina_sin_numero_de_bitacora_no_aporta_nada():
    assert observaciones_de_paginas([_pagina("")]) == []
    assert observaciones_de_paginas([_pagina("23159")]) == []


def test_una_pagina_sin_matricula_ni_fecha_no_aporta_nada():
    assert observaciones_de_paginas([_pagina("2315952", "", "")]) == []


def test_una_pagina_con_fecha_y_sin_matricula_aporta_la_fecha():
    observaciones = observaciones_de_paginas([_pagina("2315952", "")])

    assert observaciones[0].matricula == ""
    assert observaciones[0].fecha == date(2025, 5, 14)


def test_una_fecha_que_no_se_entiende_no_se_inventa():
    observaciones = observaciones_de_paginas(
        [_pagina("2315952", fecha="/Date(1747180800000)/")]
    )

    assert observaciones[0].fecha is None


def test_las_paginas_del_batch_corrigen_la_memoria(tmp_path):
    raiz = _instalacion(tmp_path, {"23159B": "HP-1835CMP"})

    informe = verificar_con_el_batch(
        [_pagina("2315952", pagina=1), _pagina("2315971", pagina=2)], raiz
    )

    assert informe.hay_cambios
    guardado = json.loads(
        (raiz / "book_matriculas.json").read_text(encoding="utf-8")
    )
    assert guardado == {"23159B": "HP-1376CMP"}


def test_un_batch_sin_paginas_en_verde_no_cambia_la_memoria(tmp_path):
    raiz = _instalacion(tmp_path, {"23159B": "HP-1835CMP"})

    informe = verificar_con_el_batch(
        [_pagina("2315952", estado=ESTADO_NECESITA_CORRECCION)], raiz
    )

    assert not informe.hay_cambios
    guardado = json.loads(
        (raiz / "book_matriculas.json").read_text(encoding="utf-8")
    )
    assert guardado == {"23159B": "HP-1835CMP"}


# ── de Web Search ──────────────────────────────────────────────────

def test_las_bitacoras_que_se_consultan_se_reparten_por_el_libro():
    """De un libro publicado a medias, un extremo dice lo contrario al otro."""
    assert bitacoras_del_libro("23159A", 3) == [
        "2315900", "2315924", "2315949"
    ]
    assert bitacoras_del_libro("23159B", 3) == [
        "2315950", "2315974", "2315999"
    ]


def test_una_clave_de_libro_que_no_lo_es_no_se_consulta():
    assert bitacoras_del_libro("23159") == []
    assert bitacoras_del_libro("23159C") == []
    assert bitacoras_del_libro("") == []


class BuscadorFalso:
    """Contesta con los indices de las bitacoras que tiene publicadas."""

    def __init__(self, publicadas):
        self.publicadas = publicadas
        self.consultadas = []

    def indice(self, numero):
        self.consultadas.append(numero)
        datos = self.publicadas.get(numero)
        if datos is None:
            return None
        return Indice(numero=numero, **datos)


def test_solo_se_anota_lo_que_web_search_devuelve():
    """Lo que no aparece no dice nada: ni que este ni que no este."""
    buscador = BuscadorFalso({
        "2315950": {"matricula": "HP-1376CMP", "fecha": "2025-05-14"},
    })

    observaciones = observaciones_de_websearch(buscador, ["23159B"], 3)

    assert len(buscador.consultadas) == 3
    assert [o.log_number for o in observaciones] == ["2315950"]
    assert observaciones[0].fecha == date(2025, 5, 14)


def test_una_fila_sin_matricula_ni_fecha_no_se_anota():
    buscador = BuscadorFalso({"2315950": {}})

    assert observaciones_de_websearch(buscador, ["23159B"], 1) == []


def test_web_search_corrige_un_libro_que_no_viene_en_ningun_batch(tmp_path):
    raiz = _instalacion(tmp_path, {"23159B": "HP-1835CMP"})
    buscador = BuscadorFalso({
        "2315950": {"matricula": "HP-1376CMP"},
        "2315999": {"matricula": "HP-1376CMP"},
    })

    informe = verificar_con_websearch(buscador, raiz, cuantas=2, escribir=True)

    assert informe.hay_cambios
    guardado = json.loads(
        (raiz / "book_matriculas.json").read_text(encoding="utf-8")
    )
    assert guardado == {"23159B": "HP-1376CMP"}


def test_sin_escribir_la_comprobacion_solo_informa(tmp_path):
    raiz = _instalacion(tmp_path, {"23159B": "HP-1835CMP"})
    buscador = BuscadorFalso({
        "2315950": {"matricula": "HP-1376CMP"},
        "2315999": {"matricula": "HP-1376CMP"},
    })

    informe = verificar_con_websearch(buscador, raiz, cuantas=2)

    assert informe.hay_cambios
    guardado = json.loads(
        (raiz / "book_matriculas.json").read_text(encoding="utf-8")
    )
    assert guardado == {"23159B": "HP-1835CMP"}


def test_sin_memoria_guardada_no_se_consulta_nada(tmp_path):
    raiz = _instalacion(tmp_path)
    buscador = BuscadorFalso({})

    informe = verificar_con_websearch(buscador, raiz)

    assert buscador.consultadas == []
    assert informe.observaciones == 0


def test_los_libros_a_comprobar_salen_de_la_memoria(tmp_path):
    raiz = _instalacion(tmp_path, {"23159B": "HP-1376CMP"})

    assert libros_de_la_memoria(raiz) == ["23159B"]


# ── por tandas ─────────────────────────────────────────────────────

def _ronda(raiz):
    ruta = raiz / "book_ronda.json"
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.is_file() else {}


def test_la_tanda_toma_a_los_que_llevan_mas_sin_mirarse(tmp_path):
    raiz = _instalacion(tmp_path, {
        "23159B": "HP-1376CMP", "23160A": "HP-1376CMP",
        "23161A": "HP-1376CMP",
    })
    (raiz / "book_ronda.json").write_text(
        json.dumps({"23159B": "2026-09-01", "23160A": "2026-08-01"}),
        encoding="utf-8",
    )

    # 23161A no se consulto nunca, asi que va antes que los dos fechados.
    assert libros_por_verificar(raiz, 2) == ["23161A", "23160A"]


def test_la_tanda_siguiente_mira_a_los_otros(tmp_path):
    """La rotacion avanza: dos ejecuciones seguidas no repiten libro."""
    raiz = _instalacion(tmp_path, {
        "23159B": "HP-1376CMP", "23160A": "HP-1376CMP",
    })
    buscador = BuscadorFalso({})

    verificar_por_tandas(
        buscador, raiz, cuantos=1, cuantas=1, hoy=date(2026, 9, 6)
    )
    primera = list(buscador.consultadas)
    buscador.consultadas.clear()
    verificar_por_tandas(
        buscador, raiz, cuantos=1, cuantas=1, hoy=date(2026, 9, 7)
    )

    assert primera and buscador.consultadas
    assert primera != buscador.consultadas
    assert _ronda(raiz) == {"23159B": "2026-09-06", "23160A": "2026-09-07"}


def test_el_libro_que_no_contesta_gasta_su_turno_igual(tmp_path):
    """Si no, la cola se atasca en el que nunca va a contestar.

    Un libro sin publicar no aparece en Web Search por mucho que se
    pregunte. Anotar el turno solo de los que contestan lo dejaria siempre
    el primero y los demas no se mirarian jamas.
    """
    raiz = _instalacion(tmp_path, {"23159B": "HP-1376CMP"})
    buscador = BuscadorFalso({})

    informe = verificar_por_tandas(
        buscador, raiz, cuantos=1, cuantas=1, hoy=date(2026, 9, 6)
    )

    assert informe.observaciones == 0
    assert _ronda(raiz) == {"23159B": "2026-09-06"}


def test_la_tanda_corrige_un_libro_viejo(tmp_path):
    raiz = _instalacion(tmp_path, {"23159B": "HP-1835CMP"})
    buscador = BuscadorFalso({
        "2315950": {"matricula": "HP-1376CMP"},
        "2315999": {"matricula": "HP-1376CMP"},
    })

    verificar_por_tandas(buscador, raiz, cuantos=1, cuantas=2)

    guardado = json.loads(
        (raiz / "book_matriculas.json").read_text(encoding="utf-8")
    )
    assert guardado == {"23159B": "HP-1376CMP"}


def test_el_libro_que_sale_de_la_memoria_sale_de_la_ronda(tmp_path):
    """Su matricula no es de ningun avion, asi que la entrada se borra."""
    raiz = _instalacion(tmp_path, {"23159B": "HP-9999XXX"})
    buscador = BuscadorFalso({})

    verificar_por_tandas(
        buscador, raiz, cuantos=1, cuantas=1, hoy=date(2026, 9, 6)
    )

    assert libros_de_la_memoria(raiz) == []
    assert _ronda(raiz) == {}


def test_sin_memoria_guardada_la_tanda_no_consulta_nada(tmp_path):
    raiz = _instalacion(tmp_path)
    buscador = BuscadorFalso({})

    informe = verificar_por_tandas(buscador, raiz)

    assert buscador.consultadas == []
    assert informe.observaciones == 0
    assert not (raiz / "book_ronda.json").is_file()
