# BITS

## Manual de operación y descripción técnica

### 0.1 Aplicabilidad

Este manual corresponde a BITS — Logbook Classification para Windows. Describe la operación de la interfaz gráfica, la línea de comandos, el tratamiento de las bitácoras, las salidas y el mantenimiento del paquete portable.

### 0.2 Finalidad

BITS convierte lotes de bitácoras escaneadas en datos verificables y PDF ordenados para indexación. El sistema conserva la página fuente, registra la procedencia de cada lectura y separa los casos que requieren decisión humana.

### 0.3 Contenido

1. [Descripción del sistema](01-descripcion-del-sistema.md)
2. [Operación de la interfaz](02-operacion-de-la-interfaz.md)
3. [Proceso de datos](03-proceso-de-datos.md)
4. [Salidas y trazabilidad](04-salidas-y-trazabilidad.md)
5. [Plantillas, libros y flota](05-plantillas-libros-y-flota.md)
6. [Operación por línea de comandos](06-operacion-por-linea-de-comandos.md)
7. [Instalación y mantenimiento](07-instalacion-y-mantenimiento.md)

Anexos de ingeniería:

- [Decisión del motor OCR](ocr-engine-decision.md)
- [Auditoría del OCR portable](portable-ocr-audit.md)

### 0.4 Convenciones

- **Corrida:** procesamiento de uno o más PDF bajo una misma carpeta de salida.
- **Lote:** secuencia completa de páginas seleccionadas, aunque procedan de varios PDF.
- **Libro:** bloque físico de 50 páginas perteneciente a una aeronave.
- **Página fuente:** página original copiada al PDF de entrega sin anotaciones ni rasterización adicional.
- **PRECAUCIÓN:** condición que puede afectar la integridad o clasificación de los datos.
- **NOTA:** información necesaria para ejecutar o interpretar una tarea.

Las rutas se indican desde la raíz de la carpeta BITS.
