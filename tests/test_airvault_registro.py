"""El registro durable de los batches de una entrega.

El manifiesto de cada batch dice en qué va ese batch. Lo que no dice
ninguno es qué se hizo con la entrega entera, y esa era la memoria que se
perdía al rehacer un reparto: los manifiestos viejos se apartan y con
ellos se iba lo único que sabía qué bitácoras ya estaban en AirVault.

Aquí se fija que esa memoria sobreviva a los cambios de configuración, que
no invente páginas por subir ni las esconda, y que se vaya entera cuando
se elimina el registro local.
"""

from __future__ import annotations

from pathlib import Path

from app.airvault import registro
from app.airvault.config import AirVaultConfig
from app.airvault.flujo import Trabajo, preparar_partes
from app.airvault.model import EstadoEtapa
from tests.test_airvault_entrega import corrida


def _subido(trabajo, batch_id: str = ""):
    trabajo.manifiesto.etapa("subir").marcar(EstadoEtapa.HECHA, "enviado")
    if batch_id:
        trabajo.manifiesto.batch_id = batch_id
    trabajo.guardar()
    return trabajo


def test_la_carpeta_del_registro_es_la_de_la_entrega(tmp_path):
    """Cada parte vive en su subcarpeta, pero el registro es de la entrega."""
    assert registro.raiz_de_registro(tmp_path / "job" / "parte-02").name == "job"
    assert registro.raiz_de_registro(tmp_path / "job" / "revisar").name == "job"
    assert registro.raiz_de_registro(tmp_path / "job" / "revisar-03").name == "job"
    # Una entrega sin repartir vive directamente en la carpeta del trabajo.
    assert registro.raiz_de_registro(tmp_path / "job").name == "job"


def test_preparar_deja_anotados_todos_los_batches(tmp_path):
    csv_path, _partes = corrida(tmp_path)
    trabajos = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=5,
    )

    anotado = registro.leer(tmp_path / "job")

    assert [b.nombre_batch for b in anotado.batches] == [
        t.manifiesto.nombre_batch for t in trabajos
    ]
    assert anotado.csv_origen == str(csv_path)
    # Todavía no viajó nada, así que no hay nada comprometido.
    assert anotado.comprometidas() == set()
    assert anotado.anotadas()


def test_el_registro_recuerda_lo_subido_aunque_se_pierda_el_manifiesto(
    tmp_path,
):
    """Es la razón de ser del registro: sobrevivir al reparto.

    Un manifiesto apartado al cambiar el máximo de páginas, o borrado por
    quien sea, se llevaba consigo la única constancia de que sus bitácoras
    ya estaban en AirVault. Sin esa constancia el reparto siguiente las
    vuelve a mandar y quedan indexadas dos veces.
    """
    csv_path, _partes = corrida(tmp_path)
    carpeta = tmp_path / "job"
    trabajos = preparar_partes(
        AirVaultConfig(), carpeta, csv_path, paginas_por_batch=5,
    )
    primero = _subido(trabajos[0], "003PRI")
    registro.anotar(primero.carpeta, [primero])
    suyas = {
        (r.archivo_origen.casefold(), r.pagina_origen)
        for r in primero.manifiesto.bitacoras()
    }
    assert registro.comprometidas(carpeta) == suyas

    # Desaparece el manifiesto del batch que ya viajó.
    (Path(primero.carpeta) / "manifiesto.json").unlink()

    nuevos = preparar_partes(
        AirVaultConfig(), carpeta, csv_path, paginas_por_batch=4,
    )

    repartidas = [
        (r.archivo_origen.casefold(), r.pagina_origen)
        for t in nuevos for r in t.manifiesto.bitacoras()
    ]
    assert not (set(repartidas) & suyas), (
        "se volvieron a repartir bitácoras que ya estaban en AirVault"
    )


def test_rehacer_el_reparto_guarda_el_anterior_en_el_historial(tmp_path):
    csv_path, _partes = corrida(tmp_path)
    carpeta = tmp_path / "job"
    preparar_partes(
        AirVaultConfig(), carpeta, csv_path, paginas_por_batch=5,
    )
    antes = [b.nombre_batch for b in registro.leer(carpeta).batches]

    preparar_partes(
        AirVaultConfig(), carpeta, csv_path, paginas_por_batch=3,
    )

    anotado = registro.leer(carpeta)
    assert anotado.historial
    assert [b.nombre_batch for b in anotado.historial[0].batches] == antes
    assert "3 paginas" in anotado.historial[0].motivo


def test_el_historial_no_crece_sin_fin(tmp_path):
    """Se conservan unas cuantas versiones, no todas las de la vida."""
    carpeta = tmp_path / "job"
    carpeta.mkdir(parents=True)
    anotado = registro.RegistroDeEntrega(
        batches=[registro.BatchAnotado(carpeta="uno", nombre_batch="DP | UNO")]
    )
    registro.guardar(anotado, carpeta)

    for vuelta in range(registro.MAXIMO_HISTORIAL + 5):
        registro.archivar(carpeta, f"vuelta {vuelta}")

    guardado = registro.leer(carpeta)
    assert len(guardado.historial) == registro.MAXIMO_HISTORIAL
    # El más reciente encabeza la lista.
    assert guardado.historial[0].motivo.endswith(
        str(registro.MAXIMO_HISTORIAL + 4)
    )


def test_anotar_conserva_los_batches_que_no_se_le_pasan(tmp_path):
    """Anotar una parte no puede borrar la memoria de las demás."""
    carpeta = tmp_path / "job"
    csv_path, _partes = corrida(tmp_path)
    trabajos = preparar_partes(
        AirVaultConfig(), carpeta, csv_path, paginas_por_batch=5,
    )
    cuantos = len(registro.leer(carpeta).batches)

    registro.anotar(trabajos[0].carpeta, [trabajos[0]])

    assert len(registro.leer(carpeta).batches) == cuantos


def test_lo_subido_no_se_desanota_al_volver_a_anotar(tmp_path):
    """Se suman hechos, nunca se restan.

    Un manifiesto recién reconstruido todavía no dice que su batch se
    subió; que el registro le hiciera caso sería olvidar que viajó.
    """
    carpeta = tmp_path / "job"
    csv_path, _partes = corrida(tmp_path)
    trabajos = preparar_partes(
        AirVaultConfig(), carpeta, csv_path, paginas_por_batch=5,
    )
    trabajo = _subido(trabajos[0], "003PRI")
    registro.anotar(trabajo.carpeta, [trabajo])

    trabajo.manifiesto.etapas.pop("subir")
    trabajo.manifiesto.batch_id = None
    trabajo.guardar()
    registro.anotar(trabajo.carpeta, [trabajo])

    anotado = registro.leer(carpeta).por_carpeta()[str(trabajo.carpeta)]
    assert anotado.subido
    assert anotado.batch_id == "003PRI"


def test_el_registro_se_va_con_la_memoria_local(tmp_path):
    """Eliminar el registro tiene que dejar la ejecución sin restos."""
    carpeta = tmp_path / "job"
    csv_path, _partes = corrida(tmp_path)
    preparar_partes(
        AirVaultConfig(), carpeta, csv_path, paginas_por_batch=5,
    )
    apartado = carpeta / "parte-01" / "manifiesto-reemplazado-20260824-101010.json"
    apartado.parent.mkdir(parents=True, exist_ok=True)
    apartado.write_text("{}", encoding="utf-8")

    rutas = registro.rutas_del_registro(carpeta)

    assert registro.ruta_registro(carpeta) in rutas
    assert apartado in rutas


def test_un_registro_ilegible_no_corta_el_trabajo(tmp_path):
    """Los manifiestos siguen estando; el registro acelera, no manda."""
    carpeta = tmp_path / "job"
    carpeta.mkdir(parents=True)
    registro.ruta_registro(carpeta).write_text("{ esto no es", encoding="utf-8")

    assert registro.leer(carpeta).batches == []
    assert registro.comprometidas(carpeta) == set()


def test_olvidar_un_batch_libera_sus_bitacoras_y_deja_las_demas(tmp_path):
    """Eliminar un batch: se olvida el suyo y el resto del reparto sigue.

    Sin esto, el batch desaparecería de la cola pero sus páginas seguirían
    contando como enviadas, así que ningún reparto posterior las volvería a
    mandar y se quedarían sin subir sin que nadie lo dijera.
    """
    csv_path, _partes = corrida(tmp_path)
    trabajos = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=5,
    )
    assert len(trabajos) > 1
    for trabajo in trabajos:
        _subido(trabajo, f"ID-{trabajo.manifiesto.parte}")
    registro.anotar(tmp_path / "job", trabajos, str(csv_path))
    fuera = trabajos[0]
    suyas = {
        (r.archivo_origen, int(r.pagina_origen))
        for r in fuera.manifiesto.registros
        if not r.es_separador and r.archivo_origen
    }

    quedan = registro.olvidar(tmp_path / "job", [fuera.carpeta])

    assert [b.carpeta for b in quedan.batches] == [
        str(t.carpeta) for t in trabajos[1:]
    ]
    assert not (suyas & quedan.comprometidas())
    # Y queda escrito, no solo devuelto.
    assert registro.leer(tmp_path / "job").batches == quedan.batches


def test_olvidar_lo_que_no_esta_anotado_no_toca_el_registro(tmp_path):
    """Un batch que ya se olvidó no vuelve a reescribir el archivo."""
    csv_path, _partes = corrida(tmp_path)
    trabajos = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=5,
    )
    antes = registro.ruta_registro(tmp_path / "job").read_text(encoding="utf-8")

    registro.olvidar(tmp_path / "job", [tmp_path / "job" / "parte-99"])

    assert registro.ruta_registro(tmp_path / "job").read_text(
        encoding="utf-8"
    ) == antes
    assert len(registro.leer(tmp_path / "job").batches) == len(trabajos)
