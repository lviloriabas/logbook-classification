# Reglas del repositorio

- **CSV:** no cambies la salida del reporte sin solicitud o autorización del usuario.

## Git

- Cierra cada trabajo con `git commit` y `git push` en la rama activa; la autorización es permanente. Si estás en `main`, crea antes otra rama.
- Usa la identidad del usuario. No agregues firmas, coautorías ni marcas como `Co-Authored-By` o `Generated with`.
- Imita el historial: español sin acentos; asunto presente, en tercera persona y descriptivo, por ejemplo `Cuenta paginas terminadas del lote, no la ultima que entrega el pool`; cuerpo sobre motivo y consecuencias, no sobre archivos.

## Interfaz

- Al editarla, replica los grises `TABLE_BASE_BG` y `PANE_*` de `app/gui/widgets.py`, las fuentes y tamaños de `_QSS` en `main_window.py`, y el radio de 6 px de `QGroupBox`, `#timeSummary`, `#embeddedPdfPane` y tablas. Prohibidas las esquinas en pico y otros colores.
- No agregues por iniciativa propia botones, paneles, iconos, diálogos, columnas o indicadores. Si la solicitud los requiere, proponlos y espera.
- Todo control nuevo debe igualar a sus vecinos en alto, espacio, color e idioma. Usa español; no muestres jerga interna, perfiles de rendimiento, clases ni rutas.

## Plataforma

- **Solo CPU:** crea cada motor con `device="cpu"`; no detectes GPU ni aceleradores. Se permiten oneDNN/MKL-DNN, hilos, lotes y cuantización int8.
- **Portable:** la carpeta completa debe funcionar en cualquier PC Windows sin administrador ni instalación. No dependas de rutas del sistema, registro o descargas en ejecución. Intérprete, dependencias, Tesseract y modelos viven en `portable/`; dirige allí `PADDLE_PDX_CACHE_HOME`.
- Precarga modelos nuevos en `portable/` con `tools/precache_paddle.py` y comprueba su uso sin internet.

## Bitácoras

- Cada libro físico tiene 50 páginas y una sola aeronave.
- `log_number` tiene exactamente siete dígitos: `00` a `49` forman un libro; `50` a `99`, el siguiente.
- Dentro del libro, la fecha no retrocede al aumentar `log_number`; puede repetirse. La regla no cruza libros.
- La fecha manuscrita suele usar casillas `DD|MMM|AA` con separadores verticales; rara vez el mes es numérico.
- Las casillas cambian de posición entre escaneos; no dependas solo de coordenadas fijas.
