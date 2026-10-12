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

## Sesión 2 — 2026-10-11: control de Cubase por pantalla, gratuito

### Contexto del usuario
Cubase 5 de 32 bits en un equipo Windows 11 con procesador ARM. Quiere que la aplicación maneje Cubase como
una persona (como Claude Desktop) sin gastar créditos.

### Implementado
- `screen/desktop.py`: capturas (GDI), ratón, teclado Unicode, proceso de la ventana activa (ctypes, apto para
  DPI alto); `FakeDesktop` para pruebas.
- `screen/brains.py`: modelo de visión local vía Ollama (petición `/api/chat` con la captura), intérprete de
  respuestas JSON y estilo UI-TARS (coordenadas en píxeles o relativas 0-1000).
- `screen/agent.py`: ciclo capturar → decidir → actuar → comprobar, con protecciones (solo ventanas de
  Cubase, parada por botón o esquina, atajos bloqueados, límite de pasos, detección de pantalla sin respuesta,
  captura de cada paso). El resultado se informa como "no verificado": la app no puede confirmar por sí sola
  el objetivo.
- `screen/png.py` y `screen/vision.py`: PNG sin dependencias y búsqueda de plantillas (base para rutinas).
- Integración: orden en español («Inserta Pro-Q 3 en la pista del bajo») → tarea `screen_task` con permiso
  `cubase_control`; pestaña *Control de pantalla* en la GUI.

### Pruebas
85 superadas (incluye agente con pantalla simulada, servidor Ollama simulado, reglas de seguridad).

### Siguiente paso concreto
En el equipo del usuario: instalar Ollama y `qwen2.5vl:7b` (o el modelo de visión que funcione mejor en ARM),
probar tareas simples (abrir el mezclador, insertar un plugin) y ajustar el prompt y el factor de reducción de
la captura según los errores observados.

## Sesión 3 — 2026-10-11: ejecutable, pruebas en el equipo del usuario y mezclador por Mackie Control

### Confirmado en el equipo del usuario (Windows 11 ARM, Cubase 5 de 32 bits)
- `AIEngineer.exe` (compilado por GitHub Actions) arranca; la composición MIDI funciona y suena bien.
- «Abre el mezclador» abre el mezclador de Cubase al instante (atajo + verificación de pantalla).
- Ollama instalado y modelo de visión descargado (pendiente de probar el agente de pantalla).

### Implementado
- Compilación automática del `.exe` en Windows con pruebas, comprobación de captura y publicación en Releases.
- Órdenes rápidas de Cubase sin Ollama; botón de carpeta de archivos; cuadro de texto libre en Composición.
- `cubase/midi_ports.py` (winmm), `cubase/mackie.py` (protocolo Mackie Control, pantalla y LEDs),
  `cubase/mixer_control.py` (volumen en dB con corrección por secante y curva aprendida, mute/solo con LED,
  paneo con V-pot, bancos de 8 pistas, búsqueda de pistas por nombre), `cubase/fake_cubase.py` (Cubase simulado),
  `nlp/mixer_commands.py` (órdenes de mezclador en español), acción para aplicar los niveles del plan de mezcla,
  pestaña *Mezclador (MIDI)*.

### Por verificar en el equipo real
- Que Cubase 5 muestre el valor en dB en la pantalla Mackie al tocar el fader (si no, los niveles se informan
  como "no verificados" y habrá que usar FLIP o NAME/VALUE).
- Formato exacto del texto de paneo en la pantalla.

### Siguiente paso
EQ de canal de Cubase (modo EQ de Mackie) y parámetros de plugins (modo PLUG-IN) para aplicar EQ y compresión.

## Sesión 4 — 2026-10-11: plugin clipper propio (AIE Clip)

### Implementado
- `plugins_vst/aieclip/`: clipper VST 2.4 estilo StandardCLIP escrito en C++ sin SDK (Input, Ceiling, Soft,
  OverSmp 1x–16x con filtro polifásico Kaiser, Output, Mix con señal seca alineada, Safety). Latencia
  declarada a Cubase: 0 / 15 muestras.
- `test_host.c`: anfitrión de pruebas. Verificado en Linux: ningún pico supera el techo en ningún factor de
  sobremuestreo; por debajo del techo la salida es idéntica a la entrada retrasada 15 muestras
  (error < 0,0001), también con Mix al 50 % (sin filtro peine).
- GitHub Actions compila las versiones de 32 y 64 bits con MSVC, ejecuta `test_host` con cada una y las publica.

### Por verificar en el equipo real
- Que Cubase 5 de 32 bits (emulado en ARM) cargue `AIEClip.dll` y lo muestre en los insertos.
- No tiene interfaz gráfica propia todavía: Cubase muestra sus deslizadores genéricos.

### Fallo encontrado por el usuario y corregido
- Cubase 5 se cerraba al tocar OverSmp. Causa: al cambiar la latencia, el plugin avisaba a Cubase
  (`audioMasterIOChanged`) desde el hilo de audio; Cubase lo reiniciaba en el acto (`effMainsChanged`), el
  reinicio volvía a avisar antes de guardar la nueva latencia y se formaba un bucle sin fin (desbordamiento de
  pila). Reproducido con `test_host` imitando ese comportamiento de Cubase (la versión anterior da
  "Segmentation fault").
- Solución: latencia fija de 15 muestras en todos los factores, filtros precalculados al cargar, sin
  reservar memoria en el hilo de audio y sin avisos al anfitrión. Nueva prueba: 752 cambios de OverSmp en
  plena reproducción con reinicios intercalados, sin errores (también con AddressSanitizer/UBSan).

### AIE Clip 1.1: calidad de sobremuestreo
- Medido con `test_host`: la 1.0 perdía 5 dB a 18 kHz y 12,7 dB a 20 kHz con sobremuestreo (filtro de 16
  coeficientes por fase, banda de transición demasiado ancha).
- Nuevo sobremuestreo en cascada de etapas x2 de media banda (123/25/17/17/33 coeficientes): respuesta plana
  (±0,003 dB hasta 20 kHz), nuevo factor 32x, latencia fija 71 muestras, parámetro Delta.
- tanh calculada con exp (mismo resultado, más rápida) y filtros guardados por fases para productos contiguos:
  a 4x gasta ~2,5 % de un núcleo frente a ~4,6 % antes de optimizar (medido en Linux x86).
- `test_host` comprueba ahora respuesta en frecuencia, aliasing por factor y Delta.
- No comparado directamente con StandardCLIP (no disponible aquí): las cifras son medidas propias.

### AIE Clip 1.2: interfaz propia y modos
- El usuario enseñó StandardCLIP (modos, medidores, curva, historial) y pidió algo de esa calidad.
- Interfaz propia dibujada por software (800x470): medidores IN/GR/OUT, curva de recorte, historial, menús de
  modo y sobremuestreo, Ceiling, Delta, Mix. Fondo estático en caché: 1,5 ms por fotograma (Linux x86).
- Modos: Soft Clip Classic, Soft Clip Pro, Hard Clip, Ratio 2:1. Medidor de reducción alineado con la latencia.
- Pruebas: `tests_gui.cpp` (ratón, rueda, menús, dibujo extremo, velocidad) y en Windows `test_host` abre la
  ventana real, hace clic, la captura (debe tener >40 colores) y la cierra dos veces.
- Pendiente: probar en Cubase 5 real (ventana dentro de Cubase, rendimiento en ARM). Sin presets ni A/B todavía.

### AIE Scope 1.0: onda por bandas
- Petición del usuario (foto de Traktor): ver la onda con graves en rojo, medios en verde y agudos en azul.
- Plugin de análisis sin latencia que no toca el sonido; bandas con Linkwitz-Riley 4.º orden (200 Hz, 2,5 kHz);
  vistas Color y 3 bandas, zoom 2–16 s, Gain, Freeze, resumen de 64 s y medidores por banda.
- Código común en `plugins_vst/common/` (lienzo, letras, VST2 mínimo, ventana de Windows reutilizable).
- Pendiente: probarlo en Cubase 5 real.
- 1.1: el usuario vio que la onda "temblaba" y se veía turbia al avanzar. Causas: las columnas se repartían en
  píxeles contando desde "ahora" (cada fotograma agrupaba distinto) y el pico crudo de 3 ms dibujaba un peine.
  Ahora las barras están ancladas al tiempo absoluto (prueba: al avanzar, las barras ya dibujadas no cambian),
  envolventes con caída de 12 ms y dibujo sólido de dos capas (pico oscuro + RMS en color pleno).
- 1.2 (vídeo del usuario en Cubase): con su master la onda llenaba toda la altura (picos siempre cerca de 0 dB),
  avanzaba a tirones (Cubase entrega el audio a trozos según el búfer) y los colores salían mezclados
  (amarillo/naranja). Ahora: alturas por nivel medio (RMS) con escala automática (máximo reciente al 85 %),
  desplazamiento con el reloj a velocidad constante y corrección lenta (prueba: 7-9 px por imagen frente a
  0-19 px sin suavizar), 50 imágenes por segundo, y color con más peso de la banda dominante.
- 1.3 (fotos y vídeo de Traktor del usuario): vista Color con mezcla aditiva natural de rojo/verde/azul según
  cada banda, sin blanco cuando suena todo (el usuario: "cuando suena todo es una mezcla, no blanco"; se quita
  el componente común para conservar la saturación), altura por pico con escala automática (ocupa casi todo el
  alto), líneas con brillo según nivel medio/pico, bordes difuminados y línea de "ahora" roja.

### AIE Imager 1.0: imagen estéreo multibanda
- Petición: "estéreo multibanda como el de iZotope 11, que se pueda poner en mono y abrir estéreo en cada banda".
- 4 bandas (cortes arrastrables), Width 0–200 %, Stereoize por banda, Solo, Mono, correlación por banda y total,
  vectorscopio. Mid/Side: la suma mono no cambia nunca (probado con ajustes extremos, error 1e-7).
- Primer intento con bandas por resta: separación pobre (Width 200 % daba x1,41); se cambió a árbol LR4 con
  compensación de fase (x1,94, Width 0 % deja la banda en mono con lateral residual -38 dB).
- Pendiente: probarlo en Cubase 5 real.
