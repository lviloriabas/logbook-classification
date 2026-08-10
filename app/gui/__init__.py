"""Interfaz gráfica de la aplicación.

Los módulos se exponen bajo demanda para que iniciar una ventana no cargue
también el editor y sus dependencias.
"""

__all__ = ["MainWindow", "EditorWindow", "PipelineWorker"]


def __getattr__(name: str):
    if name == "MainWindow":
        from app.gui.main_window import MainWindow

        return MainWindow
    if name == "EditorWindow":
        from app.gui.editor_window import EditorWindow

        return EditorWindow
    if name == "PipelineWorker":
        from app.gui.worker import PipelineWorker

        return PipelineWorker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
