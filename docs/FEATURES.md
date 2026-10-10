# Estado de las funciones

Se genera desde `aiengineer/core/status.py` (también visible en la GUI, pestaña *Estado de funciones*).

| Área | Función | Estado |
|---|---|---|
| Infraestructura | Proyectos, memoria persistente, versiones | terminada |
| Infraestructura | Copias de seguridad y restauración | terminada |
| Infraestructura | Historial de operaciones y registro de errores | terminada |
| Infraestructura | Niveles de autonomía y permisos por categoría | terminada |
| Cubase | Detectar Cubase abierto y proyecto activo (título de ventana) | experimental |
| Cubase | Leer Key Commands.xml para conocer atajos reales | experimental |
| Cubase | Enviar atajos de teclado a Cubase (SendInput) | experimental |
| Cubase | Intercambio por archivos (MIDI/WAV para importar en Cubase) | terminada |
| Cubase | Leer pistas/plugins del .cpr | no posible en este entorno |
| Cubase | API de control completa de Cubase 5 | no posible en este entorno |
| Cubase | Control de faders por MIDI (Generic Remote) | pendiente |
| Plugins | Escaneo de carpetas VST y arquitectura 32/64 bits | terminada |
| Audio | Pico, RMS, true peak, LUFS, LRA, cresta, clipping, DC | terminada |
| Audio | Espectro por bandas, resonancias, equilibrio tonal | terminada |
| Audio | Estéreo: correlación, anchura, compatibilidad mono | terminada |
| Audio | Enmascaramiento entre pistas | terminada |
| Audio | Tempo y tonalidad estimados | parcial |
| Audio | Estructura de la canción desde audio | parcial |
| MIDI | Lectura/escritura SMF multipista con tempo y marcadores | terminada |
| Composición | Teoría: escalas, modos, acordes, inversiones, conducción de voces | terminada |
| Composición | Progresiones por emoción, dominantes secundarias, intercambio modal | terminada |
| Composición | Melodías por motivos con pregunta/respuesta y clímax | terminada |
| Composición | Arreglo completo multipista por estilo | terminada |
| Composición | Transformar MIDI existente (conservar ritmo, rearmonizar) | terminada |
| Mezcla | Plan de mezcla desde stems (balance, EQ, compresión, conflictos) | parcial |
| Mezcla | Aplicar la mezcla dentro de Cubase automáticamente | pendiente |
| Master | Análisis y objetivos por destino; plan de cadena con plugins instalados | terminada |
| Master | Procesado offline de previsualización (EQ, ganancia, limitador TP) | terminada |
| Referencias | Comparación con referencia a loudness igualado | terminada |
| Planificación | Órdenes en español → plan de tareas (reglas locales) | terminada |
| Planificación | Modelo de lenguaje local/remoto opcional | pendiente |
| Evaluación | Antes/después a loudness igualado con detección de regresiones | terminada |
| Evaluación | Escucha auditiva real | no posible en este entorno |

Leyenda: **terminada** = implementada y cubierta por pruebas; **parcial** = funciona con limitaciones conocidas;
**experimental** = implementada pero no probada en un equipo con Cubase 5; **pendiente** = planificada;
**no posible en este entorno** = Cubase 5 o la plataforma no lo permiten (ver CUBASE5_LIMITACIONES.md).

## Limitaciones conocidas de las funciones parciales

- Tempo y tonalidad desde audio: estimaciones; el tempo usa una preferencia suave por ~125 BPM y la tonalidad puede confundirse con su relativa (se informa la alternativa).
- Tonalidad desde MIDI: en las pruebas con 160 composiciones generadas, 74% exacta y 99% exacta o relativa.
- Estructura desde audio: bloques de 4 compases con nivel de energía y fronteras por novedad; no nombra secciones con certeza.
- Plan de mezcla: los ajustes se calculan y se previsualizan offline, pero deben aplicarse en Cubase a mano hasta implementar Generic Remote / Mackie Control.
- Melodías: en 180 frases de prueba, 0 saltos de más de una octava y un 3% con un recorrido escalar de 5 notas.
