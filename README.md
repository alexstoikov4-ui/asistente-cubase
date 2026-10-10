# AI Audio Engineer para Cubase 5

Ingeniero de sonido, compositor, mezclador y masterizador virtual que trabaja junto a **Steinberg Cubase 5**
en **Windows 11**. Recibe órdenes en español, las convierte en un plan de tareas, ejecuta lo que realmente
puede ejecutar, mide el resultado y explica qué ha hecho y qué limitaciones ha encontrado.

> Estado: **versión 0.1 funcional**. Ver [docs/FEATURES.md](docs/FEATURES.md) para saber exactamente qué
> está terminado, qué es parcial, qué es experimental y qué no es posible en Cubase 5.

## Qué hace hoy (verificado con pruebas automáticas)

| Área | Funciona |
|---|---|
| Órdenes | Interpreta órdenes en español (sin conexión), hace preguntas breves si falta información y crea un plan separado en área técnica / musical. |
| Análisis de audio | LUFS integrado/corto/momentáneo y LRA (ITU-R BS.1770-4, verificado con la señal de referencia EBU), true peak (sobremuestreo x4), RMS, cresta, rango dinámico, clipping, DC, espectro por bandas, resonancias persistentes, correlación, anchura M/S, compatibilidad mono, graves mono, enmascaramiento entre pistas, tempo, tonalidad y segmentación. Separa **mediciones**, **problemas técnicos** y **valoraciones artísticas**. |
| Composición MIDI | 10 perfiles de estilo editables (JSON), 8 intenciones emocionales, motor de teoría (modos, numerales romanos, dominantes secundarias, intercambio modal, inversiones, conducción de voces), melodías por motivos con pregunta/respuesta, secuencias, clímax y cadencias; arreglo completo multipista (lead, contramelodía, acordes, pad, arpegio, pluck, bajo, subgrave, batería) con marcadores, tempo y armadura. MIDI tipo 1 verificado releyéndolo. |
| Transformar MIDI | Rearmonizar conservando el ritmo, conservar ritmo y cambiar notas, cambiar de modo, reescribir la respuesta conservando el motivo. El original nunca se modifica. |
| Mezcla | Plan desde stems: función de cada pista, balance inicial, paso alto solo cuando hay energía grave que no aporta, compresión según el material, conflictos de enmascaramiento con la herramienta menos invasiva (sidechain, EQ, EQ dinámica), grupos, margen de 6 dB y previsualización offline. |
| Masterización | Análisis, objetivos por destino (streaming, comercial, club, festival, CD), cadena decidida y justificada, previsualización offline (EQ, mono de graves, compresión, saturación, limitador true peak), búsqueda de ganancia que **se detiene antes de aplastar transitorios**, exportación 16/24 bits con dither y remuestreo, verificación del archivo final y comparación a sonoridad igualada. |
| Referencias | Comparación a sonoridad igualada y propuestas prudentes (la referencia orienta, no se copia). |
| Ciclo iterativo | Mide → evalúa → corrige (p. ej. repite el máster si pierde pegada). Nunca dice que ha "escuchado". |
| Memoria y seguridad | Proyectos con memoria persistente y versiones, historial de operaciones, puntos de recuperación verificados por hash, tres niveles de autonomía y permisos por categoría, botón de parada. |
| Cubase | Detección de instalación (y su arquitectura real), proceso, ventana y proyecto activo; lectura de *Key Commands.xml*; envío de atajos (experimental, desactivado por defecto) con verificación cuando es posible; carpeta de intercambio con instrucciones de importación. |
| Plugins | Escaneo real de carpetas VST2/VST3 y bundles de Waves, arquitectura 32/64 bits leída del DLL, compatibilidad con tu Cubase y asignación de funciones (EQ, compresor, limitador...). |

## Instalación en Windows 11

1. Instala **Python 3.10 o superior** desde python.org (marca *Add Python to PATH*; incluye tkinter).
2. Descarga este repositorio.
3. Doble clic en `iniciar_windows.bat` (la primera vez crea un entorno e instala numpy).

Sin Internet: copia una rueda de numpy (`.whl`) y ejecuta `pip install numpy-*.whl`. El resto usa solo la biblioteca estándar.

## Uso

- **Interfaz gráfica**: `iniciar_windows.bat` (o `python -m aiengineer`).
  1. *Nuevo…* para crear un proyecto. 2. *Fuentes…* para indicar la carpeta de stems, la mezcla WAV o un MIDI.
  3. Escribe la orden y pulsa *Ejecutar* (Ctrl+Enter). *Detener* para parar.
- **Consola**:
  ```
  python -m aiengineer --project "Mi tema" --sources C:\stems do "Analiza este proyecto y dime qué problemas tiene"
  python -m aiengineer --project "Mi tema" do "Compón un drop progressive house con una melodía emotiva" --yes
  python -m aiengineer --project "Mi tema" do "Masteriza el tema para festival" --yes
  python -m aiengineer status
  ```

Los archivos generados van a `%APPDATA%\AIEngineer\projects\<proyecto>\output` y una copia de los MIDI a
`%APPDATA%\AIEngineer\exchange` para importarlos en Cubase (*Archivo > Importar > Archivo MIDI*).

## Pruebas

`pruebas_windows.bat` o `python -m pytest`. Ver [docs/PROGRESO.md](docs/PROGRESO.md) para el último resultado.

## Documentación

- [docs/CUBASE5_LIMITACIONES.md](docs/CUBASE5_LIMITACIONES.md) — qué se puede y qué no se puede controlar en Cubase 5 y por qué.
- [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) — módulos y flujo de datos.
- [docs/FEATURES.md](docs/FEATURES.md) — estado honesto de cada función.
- [docs/PROGRESO.md](docs/PROGRESO.md) — registro de progreso y siguiente paso.
