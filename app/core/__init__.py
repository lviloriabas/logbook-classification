"""Núcleo de la aplicación: configuración y pipeline.

Nota: este paquete no re-exporta módulos para evitar importaciones
circulares (app.core.config ← app.vision.alignment ← app.core.pipeline).
Importe los módulos directamente:
    from app.core.config import AppConfig
    from app.core.pipeline import Pipeline
"""
