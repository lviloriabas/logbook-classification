"""El libro de envios: que mando el programa, aunque se borre lo demas.

El registro de la entrega vive dentro de la carpeta de esa ejecucion y se
va con ella. Este libro vive una vez, junto a los trabajos, y contesta la
pregunta que ninguna consulta a la cola de AirVault puede contestar cuando
el batch ya se completo: «esta bitacora, ¿ya la mande yo?».

Lo que se comprueba aqui es sobre todo la identidad, que cambia segun de
donde venga el envio anterior: dentro de una entrega una bitacora es su
pagina de origen, y entre entregas distintas es su numero.
"""

from __future__ import annotations

from app.airvault import duplicados
from app.airvault.config import AirVaultConfig
from app.airvault.model import EstadoEtapa, Manifiesto, Registro


class TrabajoFalso:
    """Lo justo que el libro le pide a un trabajo."""

    def __init__(self, carpeta, csv, bitacoras, subido=True, completado=False):
        self.carpeta = carpeta
        self.config = AirVaultConfig()
        self.manifiesto = Manifiesto(
            job_id=str(carpeta),
            nombre_batch=f"DP | BIT {carpeta}",
            csv_origen=str(csv),
            registros=[
                Registro(
                    seq=indice,
                    archivo_origen=archivo,
                    pagina_origen=pagina,
                    log_number=numero,
                )
                for indice, (numero, archivo, pagina) in enumerate(
                    bitacoras, start=1
                )
            ],
        )
        if subido:
            self.manifiesto.etapa("subir").marcar(EstadoEtapa.HECHA, "x.pdf")
        if completado:
            self.manifiesto.etapa("completar").marcar(EstadoEtapa.HECHA, "ok")


def test_lo_que_no_se_subio_no_se_anota(tmp_path):
    """Un reparto descartado no puede ser el duplicado del siguiente."""
    sin_subir = TrabajoFalso(
        "job-1", tmp_path / "BITS.CSV",
        [("2312238", "Image_001.pdf", 1)],
        subido=False,
    )

    libro = duplicados.anotar(tmp_path, [sin_subir])

    assert libro.envios == {}
    assert not duplicados.ruta_libro(tmp_path).is_file()


def test_entre_entregas_distintas_la_identidad_es_el_numero(tmp_path):
    """Reprocesar unos escaneos ya subidos y volver a mandarlos.

    Es el caso que no tenia defensa: los archivos se llaman de otra forma y
    la numeracion empieza otra vez, asi que la pagina de origen no coincide
    con nada. El numero de bitacora si.
    """
    duplicados.anotar(tmp_path, [
        TrabajoFalso(
            "job-1", tmp_path / "lunes" / "BITS.CSV",
            [("2312238", "Image_001.pdf", 1), ("2312239", "Image_001.pdf", 2)],
        )
    ])
    reprocesada = TrabajoFalso(
        "job-2", tmp_path / "martes" / "BITS.CSV",
        [("2312238", "Escaneo_007.pdf", 14)],
    )

    assert duplicados.repetidas(tmp_path, reprocesada) == ["2312238"]


def test_dentro_de_una_entrega_la_identidad_es_la_pagina(tmp_path):
    """Dos paginas distintas pueden traer el mismo numero mal leido.

    Darlas por la misma dejaria sin subir una pagina que nadie subio, que
    es tan malo como subir dos veces la que ya esta.
    """
    csv = tmp_path / "BITS.CSV"
    duplicados.anotar(tmp_path, [
        TrabajoFalso("job-1", csv, [("2312238", "Image_001.pdf", 1)])
    ])
    otra_pagina = TrabajoFalso(
        "job-2", csv, [("2312238", "Image_002.pdf", 7)]
    )
    la_misma_pagina = TrabajoFalso(
        "job-3", csv, [("2312238", "Image_001.pdf", 1)]
    )

    assert duplicados.repetidas(tmp_path, otra_pagina) == []
    # La misma pagina en otra carpeta si: es el reparto rehecho despues de
    # borrar el registro local, y volver a mandarla la publicaria dos veces.
    assert duplicados.repetidas(tmp_path, la_misma_pagina) == ["2312238"]


def test_el_mismo_batch_no_es_duplicado_de_si_mismo(tmp_path):
    csv = tmp_path / "BITS.CSV"
    trabajo = TrabajoFalso("job-1", csv, [("2312238", "Image_001.pdf", 1)])
    duplicados.anotar(tmp_path, [trabajo])

    assert duplicados.repetidas(tmp_path, trabajo) == []


def test_anotar_dos_veces_no_duplica_la_entrada(tmp_path):
    """Reanudar una ejecucion no la convierte en su propio duplicado."""
    csv = tmp_path / "BITS.CSV"
    trabajo = TrabajoFalso("job-1", csv, [("2312238", "Image_001.pdf", 1)])
    duplicados.anotar(tmp_path, [trabajo])

    libro = duplicados.anotar(tmp_path, [trabajo])

    assert len(libro.envios["2312238"]) == 1


def test_completar_no_retrocede(tmp_path):
    """Un batch cerrado sigue cerrado aunque su manifiesto se reinicie.

    Importa porque los numeros de los batches completados son el control
    con el que se comprueba que la consulta a Web Search pregunta donde
    tiene que preguntar.
    """
    csv = tmp_path / "BITS.CSV"
    duplicados.anotar(tmp_path, [
        TrabajoFalso(
            "job-1", csv, [("2312238", "Image_001.pdf", 1)], completado=True
        )
    ])

    libro = duplicados.anotar(tmp_path, [
        TrabajoFalso("job-1", csv, [("2312238", "Image_001.pdf", 1)])
    ])

    assert libro.envios["2312238"][0].completado
    assert libro.controles() == ["2312238"]


def test_solo_los_completados_sirven_de_control(tmp_path):
    """Uno indexado sigue en la cola: en Web Search todavia no esta."""
    csv = tmp_path / "BITS.CSV"
    duplicados.anotar(tmp_path, [
        TrabajoFalso("job-1", csv, [("2312238", "Image_001.pdf", 1)]),
        TrabajoFalso(
            "job-2", csv, [("2312240", "Image_002.pdf", 1)], completado=True
        ),
    ])

    assert duplicados.leer(tmp_path).controles() == ["2312240"]


def test_un_libro_ilegible_no_impide_trabajar(tmp_path):
    """Esta memoria protege; una proteccion rota no puede ademas frenar."""
    duplicados.ruta_libro(tmp_path).write_text("{roto", encoding="utf-8")

    assert duplicados.leer(tmp_path).envios == {}
