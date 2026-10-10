"""Registro honesto del estado de cada función (mostrado en la GUI y en docs/FEATURES.md)."""
from __future__ import annotations

DONE = "terminada"
PARTIAL = "parcial"
EXPERIMENTAL = "experimental"
PENDING = "pendiente"
IMPOSSIBLE = "no posible en este entorno"

FEATURES: list[tuple[str, str, str]] = [
    # (área, función, estado) — se mantiene sincronizado con docs/FEATURES.md
    ("Infraestructura", "Proyectos, memoria persistente, versiones", DONE),
    ("Infraestructura", "Copias de seguridad y restauración", DONE),
    ("Infraestructura", "Historial de operaciones y registro de errores", DONE),
    ("Infraestructura", "Niveles de autonomía y permisos por categoría", DONE),
    ("Cubase", "Detectar Cubase abierto y proyecto activo (título de ventana)", EXPERIMENTAL),
    ("Cubase", "Leer Key Commands.xml para conocer atajos reales", EXPERIMENTAL),
    ("Cubase", "Enviar atajos de teclado a Cubase (SendInput)", EXPERIMENTAL),
    ("Cubase", "Intercambio por archivos (MIDI/WAV para importar en Cubase)", DONE),
    ("Cubase", "Leer pistas/plugins del .cpr", IMPOSSIBLE),
    ("Cubase", "API de control completa de Cubase 5", IMPOSSIBLE),
    ("Cubase", "Control de faders por MIDI (Generic Remote)", PENDING),
    ("Plugins", "Escaneo de carpetas VST y arquitectura 32/64 bits", DONE),
    ("Audio", "Pico, RMS, true peak, LUFS, LRA, cresta, clipping, DC", DONE),
    ("Audio", "Espectro por bandas, resonancias, equilibrio tonal", DONE),
    ("Audio", "Estéreo: correlación, anchura, compatibilidad mono", DONE),
    ("Audio", "Enmascaramiento entre pistas", DONE),
    ("Audio", "Tempo y tonalidad estimados", PARTIAL),
    ("Audio", "Estructura de la canción desde audio", PARTIAL),
    ("MIDI", "Lectura/escritura SMF multipista con tempo y marcadores", DONE),
    ("Composición", "Teoría: escalas, modos, acordes, inversiones, conducción de voces", DONE),
    ("Composición", "Progresiones por emoción, dominantes secundarias, intercambio modal", DONE),
    ("Composición", "Melodías por motivos con pregunta/respuesta y clímax", DONE),
    ("Composición", "Arreglo completo multipista por estilo", DONE),
    ("Composición", "Transformar MIDI existente (conservar ritmo, rearmonizar)", DONE),
    ("Mezcla", "Plan de mezcla desde stems (balance, EQ, compresión, conflictos)", PARTIAL),
    ("Mezcla", "Aplicar la mezcla dentro de Cubase automáticamente", PENDING),
    ("Master", "Análisis y objetivos por destino; plan de cadena con plugins instalados", DONE),
    ("Master", "Procesado offline de previsualización (EQ, ganancia, limitador TP)", DONE),
    ("Referencias", "Comparación con referencia a loudness igualado", DONE),
    ("Planificación", "Órdenes en español → plan de tareas (reglas locales)", DONE),
    ("Planificación", "Modelo de lenguaje local/remoto opcional", PENDING),
    ("Evaluación", "Antes/después a loudness igualado con detección de regresiones", DONE),
    ("Evaluación", "Escucha auditiva real", IMPOSSIBLE),
]
