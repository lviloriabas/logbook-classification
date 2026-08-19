# Reglas del repositorio

- **CSV:** no modifiques la salida del reporte sin solicitud o autorización previa del usuario.

## Git — obligatorio

- Al terminar cada trabajo, haz `git commit` y `git push` en la rama actual; esta autorización es permanente. Nunca commitees en `main`: si está activa, crea antes otra rama.
- Usa la identidad del usuario. No añadas firmas, coautorías ni marcas de herramienta (`Co-Authored-By`, `Generated with`, etc.).
- Imita el historial: mensaje en español sin acentos; asunto en presente y tercera persona que indique qué hace el cambio (ej.: `Cuenta paginas terminadas del lote, no la ultima que entrega el pool`); cuerpo dedicado al motivo y las consecuencias, no a enumerar archivos.

## Interfaz — obligatorio

- Antes de editarla, copia el estilo existente: grises `TABLE_BASE_BG` y `PANE_*` de `app/gui/widgets.py`; radio de 6 px de `QGroupBox`, `#timeSummary`, `#embeddedPdfPane` y tablas; tipografías y tamaños de `_QSS` en `main_window.py`. Ningún cuadro puede tener esquinas en pico ni colores ajenos a esas constantes.
- No añadas por iniciativa propia botones, paneles, iconos, diálogos, columnas ni indicadores. Si son necesarios para cumplir la solicitud, propónlos y espera respuesta.
- Todo control nuevo debe igualar a sus vecinos en alto, espaciado, colores e idioma. Usa español y no muestres jerga interna, perfiles de rendimiento, clases ni rutas.

## Plataforma — obligatorio

- **Solo CPU:** instancia todo motor de inferencia con `device="cpu"`; no autodetectes GPU ni aceleradores. Se permiten oneDNN/MKL-DNN, hilos, lotes y cuantización int8 para CPU.
- **Portable:** la carpeta completa debe funcionar en cualquier PC Windows, sin administrador ni instalación. No dependas de rutas del sistema, registro o descargas en ejecución. Intérprete, dependencias, Tesseract y modelos viven en `portable/`; dirige su caché allí con `PADDLE_PDX_CACHE_HOME`.
- Precachea cualquier modelo nuevo en `portable/` mediante `tools/precache_paddle.py` y verifica su operación sin internet.

## Dominio de bitácoras

- Un libro físico tiene 50 páginas y pertenece a un solo avión.
- `log_number` tiene exactamente siete dígitos. Sus dos últimos forman la página: `00`–`49` pertenecen a un libro y `50`–`99` al siguiente.
- Dentro de cada libro/avión, al aumentar `log_number` la fecha no puede retroceder; varias páginas pueden compartir día. Esta regla no cruza libros: uno procesado después puede tener fechas anteriores.
- Las fechas manuscritas suelen ocupar casillas `DD|MMM|AA` con separadores verticales; rara vez el mes es numérico.
- La posición de las casillas varía entre escaneos; no dependas solo de coordenadas fijas de plantilla.
