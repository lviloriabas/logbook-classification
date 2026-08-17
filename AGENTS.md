El output del reporte CSV no se debe modificar sin antes preguntar al usuario o sin que él lo pida.

## Reglas de git (no negociables)

- **Cada trabajo terminado se commitea y se sube.** Al cerrar un cambio se
  hace `git commit` y `git push` de la rama actual sin volver a preguntar;
  el usuario ya dio esa autorización de forma permanente. Nunca se
  commitea directo sobre `main`: si esa es la rama activa, primero se crea
  una rama.
- **Los commits van a nombre del usuario, como si los escribiera él.** No
  se agrega ninguna firma, coautoría ni marca de herramienta al mensaje
  (nada de `Co-Authored-By`, `Generated with` ni similares).
- **Se copia el estilo de mensaje que ya tiene el historial**: en español,
  sin acentos, el asunto en presente y en tercera persona diciendo qué hace
  el cambio (`Cuenta paginas terminadas del lote, no la ultima que entrega
  el pool`), y el cuerpo explicando el porqué y las consecuencias, no la
  lista de archivos tocados.

## Reglas de interfaz (no negociables)

- **Se sigue el estilo que la aplicación ya tiene, al pie de la letra.** Antes
  de tocar la interfaz hay que mirar cómo está resuelto lo que ya existe y
  copiarlo: los grises de `app/gui/widgets.py` (`TABLE_BASE_BG`,
  `PANE_*`), el radio de esquina de los cuadros (6 px: `QGroupBox`,
  `#timeSummary`, `#embeddedPdfPane`, tablas), las tipografías y los tamaños
  de la hoja `_QSS` de `main_window.py`. Ningún cuadro puede quedar con las
  esquinas en pico ni con un color que no salga de esas constantes.
- **No se agregan elementos nuevos sin preguntar.** Botones, paneles,
  iconos, diálogos, columnas o indicadores que el usuario no pidió no se
  añaden por iniciativa propia, aunque parezcan una mejora. Si algo hace
  falta para cumplir lo pedido, se propone primero y se espera respuesta.
- **Nada puede verse fuera de lugar.** Un control nuevo se ve como sus
  vecinos: mismo alto, mismo espaciado, mismos colores y el mismo idioma
  (español, sin jerga interna del código). Los nombres internos —perfiles de
  rendimiento, nombres de clases, rutas— no se muestran en pantalla.

## Reglas de la plataforma (no negociables)

- **Solo CPU.** Nunca se puede usar GPU ni ningún acelerador. Todo motor de
  inferencia se instancia explícitamente con `device="cpu"` y no debe
  autodetectar hardware. Las optimizaciones permitidas son las de CPU
  (oneDNN/MKL-DNN, hilos, tamaño de lote, cuantización int8).
- **Portable.** La carpeta completa se copia a cualquier PC Windows sin
  permisos de administrador y funciona sin instalar nada. No se puede
  depender de rutas del sistema, del registro, ni de descargas en tiempo de
  ejecución: intérprete, dependencias, Tesseract y modelos viven dentro de
  `portable/`, y el cache de modelos se redirige ahí con
  `PADDLE_PDX_CACHE_HOME`.
- Cualquier modelo nuevo debe quedar precacheado en `portable/` (ver
  `tools/precache_paddle.py`) y funcionar sin conexión a internet.

## Reglas del dominio de bitácoras

- Cada libro físico contiene 50 páginas y corresponde a un solo avión.
- El `log_number` tiene exactamente siete dígitos. Los últimos dos indican
  la página: `00`-`49` forman un libro y `50`-`99` forman el siguiente.
- Las bitácoras se llenan secuencialmente: al aumentar el `log_number`, la
  fecha no debe retroceder dentro del mismo libro. Varias páginas pueden
  compartir el mismo día.
- La regla temporal anterior se aplica dentro de cada libro/avión. Otros
  libros procesados después pueden contener fechas anteriores.
- Las fechas son manuscritas. Normalmente ocupan casillas `DD|MMM|AA` con
  separadores verticales; en casos raros el mes se escribe numéricamente.
- La posición de las casillas cambia entre escaneos, por lo que no se debe
  confiar únicamente en coordenadas fijas de plantilla.
