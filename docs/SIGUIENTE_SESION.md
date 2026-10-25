# Encargo para la siguiente sesión: control total de Cubase 5 por órdenes

## Qué pide el usuario
Que AI Audio Engineer maneje Cubase 5 por completo bajo sus órdenes en español: mezclador (volumen, paneo,
mute, solo, envíos), insertar y ajustar plugins (EQ, compresión...), transporte, pistas, importar y exportar,
y que diga siempre lo que pudo comprobar y lo que no.

## Estado real comprobado en su equipo (Windows 11 ARM, Cubase 5 de 32 bits)
- `AIEngineer.exe` arranca; componer MIDI funciona; «Abre el mezclador» funciona (atajo de teclado).
- Mezclador por Mackie Control (`aiengineer/cubase/mackie.py`, `mixer_control.py`, `midi_ports.py`):
  **solo funcionan los botones Solo y Mute** (encienden y apagan). Volumen y paneo no hacen efecto visible.
  Falta saber por qué: puerto MIDI de vuelta, toque del fader (nota 0x68+tira), formato de la pantalla
  Mackie de Cubase 5 (si muestra dB), o el banco/tira elegida.
- Agente de pantalla con Ollama (`aiengineer/screen/`): instalado pero sin probar a fondo.

## Primer paso propuesto
1. Pedir al usuario el archivo `%APPDATA%\AIEngineer\logs\aiengineer.log` tras ordenar «sube el bajo 3 dB»
   y una foto de lo que hace Cubase, y cómo tiene configurado el dispositivo Mackie Control (puertos MIDI
   virtuales usados, p. ej. loopMIDI).
2. Añadir un diagnóstico del Mackie en la app (botón): registra cada mensaje enviado y recibido, mueve un
   fader con y sin toque, lee la pantalla LCD y guarda un informe que el usuario pueda pasar.
3. Con eso, arreglar volumen y paneo y seguir con EQ de canal (modo EQ de Mackie) y parámetros de plugins
   (modo PLUG-IN); para lo que Mackie no cubra, agente de pantalla y atajos de teclado.

## Contexto útil
- Registro completo: `docs/PROGRESO.md`; estado por función: `docs/FEATURES.md`; límites de Cubase 5:
  `docs/CUBASE5_LIMITACIONES.md`.
- No hay entorno Windows aquí: lo que dependa de Cubase se prueba con `cubase/fake_cubase.py` y luego en el
  equipo del usuario (el `.exe` se compila con GitHub Actions y se publica en Releases, etiqueta
  `version-de-prueba`).
- En la sesión anterior quedó sin subir (solo en aquel contenedor, no en git) un cambio de unas 30 líneas en
  `midi_ports.py` que registraba con detalle los mensajes MIDI; no existe aquí: hay que rehacerlo en el paso 2.
