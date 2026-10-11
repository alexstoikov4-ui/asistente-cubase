# Control de Cubase 5: mecanismos reales y limitaciones

Este documento separa lo **verificado en código y pruebas**, lo **documentado por Steinberg / conocido de
Cubase 5 pero todavía no probado en un equipo real** y lo **no posible**. El entorno de desarrollo de esta
versión es Linux sin Cubase; por eso todo lo que depende de Windows está implementado con `ctypes`, aislado
y marcado como experimental hasta probarlo en tu equipo.

## Lo que Cubase 5 NO ofrece

| Supuesto | Realidad |
|---|---|
| API de scripting / SDK de control externo | **No existe** en Cubase 5. La "MIDI Remote API" en JavaScript llegó con Cubase 12. |
| Leer o escribir el proyecto `.cpr` | Formato binario propietario y no documentado. **La aplicación nunca lo modifica.** Editarlo mientras Cubase lo usa puede corromperlo. |
| Saber qué plugins hay en cada pista desde fuera | No hay forma fiable sin API. Se usa el escaneo de carpetas para saber qué está instalado. |
| Escuchar el audio | La aplicación mide señales; no "escucha". Lo indica siempre. |

## Mecanismos disponibles, de más a menos fiable

1. **Intercambio por archivos (implementado, fiable).** MIDI tipo 1 multipista con tempo, compás, armadura y
   marcadores; WAV 16/24/32 bits. Se escriben en la carpeta de intercambio sin sobrescribir nada y se dan los
   pasos exactos de importación. La exportación de la mezcla desde Cubase (*Archivo > Exportar > Mezcla de
   audio*) produce un WAV que la aplicación analiza, masteriza y verifica.
2. **Detección de proceso y ventana (implementado, experimental).** `EnumWindows` + nombre del ejecutable;
   el título de la ventana principal contiene el nombre del proyecto (se admiten varios formatos e idiomas).
   La arquitectura de Cubase (32/64 bits) se lee de la cabecera PE del `.exe`.
3. **Comandos de teclado (implementado, experimental, desactivado por defecto).** Se leen los atajos reales
   del usuario de `%APPDATA%\Steinberg\Cubase 5\Key Commands.xml` y se envían con `SendInput`.
   Solo se marcan como **verificadas** las acciones cuyo efecto se comprueba desde fuera (Guardar: cambia la
   fecha del `.cpr`). El resto se informa como **no verificado**. Si un comando no tiene atajo, se pide
   asignarlo (Archivo > Comandos de teclado) en lugar de inventarlo. Las *macros* de comandos de teclado de
   Cubase permiten encadenar acciones con un solo atajo.
4. **Generic Remote por MIDI (pendiente, siguiente fase).** Cubase 5 permite asignar mensajes MIDI a
   parámetros del mezclador y a comandos (Dispositivos > Configuración de dispositivos > Generic Remote) y
   exportar/importar esa configuración. Windows no trae un puerto MIDI virtual: hace falta instalar un
   cable virtual (p. ej. loopMIDI). Permitiría mover faders, paneos, envíos y algunos parámetros con valores
   exactos.
5. **Protocolo Mackie Control (pendiente, a evaluar).** Cubase 5 soporta superficies Mackie Control.
   Emular una superficie daría control **bidireccional**: Cubase devuelve la posición de los faders y los
   nombres de pista, lo que permitiría **verificar** los cambios de nivel y reconocer pistas.
6. **Agente de pantalla (implementado, experimental).** El mismo ciclo que Claude Desktop: captura → un
   modelo de visión decide la acción → ratón/teclado → comprobación de que la pantalla cambió. Usa un modelo
   local gratuito (Ollama), así que es más lento y comete más errores que Claude. Para mover parámetros finos
   (bandas de EQ) es preferible escribir valores o usar presets cuando el plugin lo permita. Además hay una
   búsqueda de elementos por imagen sin modelo (`screen/vision.py`) como base para rutinas grabadas.

## Equipo del usuario: Cubase 5 de 32 bits en Windows 11 ARM

- Cubase 5 (x86) se ejecuta con la emulación de Windows on ARM; los **controladores** no se emulan, así que la
  llave eLicenser, iLok y el ASIO de la interfaz necesitan versión ARM64 (o usar el controlador genérico).
- Con Cubase de 32 bits solo sirven plugins de 32 bits: hay que instalar las versiones de 32 bits de
  FabFilter y Limiter 6. El escáner marca lo que no se puede cargar.
- La aplicación puede usar Python ARM64 nativo o Python x64 emulado; la captura y los clics funcionan sobre
  Cubase x86 en ambos casos.

## Plugins y arquitecturas

- Cubase 5 de **32 bits** solo carga plugins de 32 bits.
- Cubase 5 de **64 bits** carga plugins de 64 bits y puentea los de 32 bits con **VST Bridge**, que puede ser
  inestable. Los VST3 deben coincidir con la arquitectura del host.
- **Waves v7**: históricamente solo de 32 bits (el soporte de 64 bits de Waves llegó en versiones posteriores);
  en Cubase 5 de 64 bits dependería de VST Bridge. El escáner lo **comprueba en tu equipo** leyendo la
  cabecera del `WaveShell` en lugar de suponerlo.
- **FabFilter Pro-Q 3 / Saturn 2** y **Limiter 6** existen en 32 y 64 bits; el escáner indica qué versión
  tienes instalada y si es compatible con tu Cubase.

## Qué hace la aplicación cuando no puede actuar en Cubase

- Mezcla: entrega un plan con valores concretos (ganancia, frecuencia, Q, ratio, ataque, liberación,
  sidechain) asignados a tus plugins reales, una previsualización offline de los stems y la lista de grupos.
- Masterización: entrega un máster de referencia hecho con el procesado interno, verificado, y la cadena
  equivalente con tus plugins y sus ajustes.
- Composición: entrega el MIDI listo para importar.
- Nunca afirma haber hecho en Cubase algo que no ha podido verificar.
