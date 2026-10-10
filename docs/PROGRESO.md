# Registro de progreso

## Sesión 1 — 2026-10-10: inspección del entorno y versión 0.1 funcional

### Entorno inspeccionado
- Contenedor **Linux** (no Windows): no hay Cubase, ni plugins, ni Win32. Python 3.12 con numpy 2.5, tkinter
  y pytest (instalados), Xvfb para probar la GUI. Sin scipy, soundfile ni mido: se implementaron lector/escritor
  WAV y MIDI, filtros y análisis propios con numpy.
- Consecuencia: todo lo que depende de Windows/Cubase está implementado con `ctypes`/`winreg`, aislado en
  `aiengineer/cubase` y `aiengineer/plugins`, cubierto con pruebas de la lógica (archivos PE sintéticos,
  `Key Commands.xml` de ejemplo, modo simulación del envío de teclas) y marcado como **experimental** hasta
  probarlo en un equipo con Cubase 5.

### Implementado (fases 1–8 en versión mínima funcional; ver docs/FEATURES.md)
- Fase 1: configuración, registro de actividad/errores, historial SQLite, proyectos con memoria y versiones,
  copias de seguridad verificadas, permisos y modos de autonomía.
- Fase 2: detección de Cubase (instalación, arquitectura, proceso, ventana, proyecto), lectura de atajos,
  control por teclado con verificación, intercambio por archivos. Investigación documentada en
  docs/CUBASE5_LIMITACIONES.md.
- Fase 3: análisis de audio completo (sonoridad BS.1770-4, true peak, dinámica, espectro, resonancias,
  estéreo, enmascaramiento, tempo, tonalidad, estructura) con observaciones clasificadas.
- Fase 4: teoría musical, 10 estilos, 8 emociones, compositor por motivos, arreglista multipista,
  análisis y transformación de MIDI existente, sintetizador interno de previsualización.
- Fase 5: plan de mezcla desde stems con previsualización.
- Fase 6: masterización con verificación y comparación a sonoridad igualada.
- Fase 7: intérprete de órdenes, planificador, ejecutor con parada, contexto persistente para retomar.
- Fase 8: comparación con referencias, feedback que modifica el siguiente ciclo, evaluación iterativa.
- GUI con todos los paneles pedidos (ningún botón vacío) y consola.

### Archivos
`aiengineer/` (core, project, audio, midi, theory, compose, mix, master, evaluate, plugins, cubase, nlp,
planner, ui, data/styles), `tests/` (9 módulos), `docs/`, `README.md`, `iniciar_windows.bat`,
`pruebas_windows.bat`, `requirements*.txt`.

### Pruebas ejecutadas y resultados
- `python -m pytest`: **78 pruebas superadas** (Python 3.12 con GUI bajo Xvfb). En Python 3.13 sin tkinter:
  77 superadas y la de GUI omitida.
- Verificaciones destacadas: LUFS de la señal de referencia EBU (seno 997 Hz a −23 dBFS) = −23,00 LUFS;
  true peak de un seno a fs/4 desfasado 45° detectado a < 0,6 dB del real; MIDI generado en los 10 estilos
  releído sin diferencias; máster con techo true peak respetado y archivo final verificado; transformaciones
  que conservan exactamente el ritmo pedido.
- Pruebas manuales de extremo a extremo por consola y GUI: componer, transformar, plan de mezcla,
  masterizar para club/festival/streaming, comparar con referencia, feedback, ciclo iterativo.

### Problemas pendientes
1. Probar en Windows 11 con Cubase 5: detección de ventana, formato real de `Key Commands.xml`, envío de
   atajos, escaneo de carpetas reales (Waves v7, FabFilter, Limiter 6).
2. Control con valores exactos dentro de Cubase (Generic Remote vía puerto MIDI virtual o emulación Mackie
   Control) para aplicar el plan de mezcla automáticamente y verificarlo.
3. Carpeta vigilada: detectar automáticamente la mezcla exportada desde Cubase y analizarla.
4. Mejoras musicales: detección de tonalidad desde MIDI (74% exacta), segmentación de audio con nombres de
   sección, más variedad rítmica en bajos y baterías por estilo.
5. Modelo de lenguaje opcional (local o remoto) para órdenes fuera de las reglas.
6. Rendimiento del máster en temas largos (~80 s para 5 min).

### Siguiente paso concreto
Ejecutar `pruebas_windows.bat` y `python -m aiengineer status` en el equipo con Cubase 5, revisar la
pestaña *Plugins y permisos* tras *Escanear plugins*, y con esos datos implementar el puente Generic Remote
(salida MIDI con `winmm` + archivo de configuración importable en Cubase).
