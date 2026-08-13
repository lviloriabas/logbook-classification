El output del reporte CSV no se debe modificar sin antes preguntar al usuario o sin que él lo pida.

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
