# Arquitectura

Python 3.10+ con **numpy** como única dependencia externa. Interfaz con **tkinter** (incluido con Python
para Windows). Integración con Windows mediante `ctypes`/`winreg` (biblioteca estándar). Persistencia con
JSON atómico y SQLite. Sin conexión a Internet.

```
Orden (texto) ─► nlp.intents ─► planner.planner ─► planner.executor ─► planner.actions ─► motores
                  (reglas)        (Plan/Task)       (permisos, parada,   (resultado con     │
                                                     registro, errores)   estado verificable) │
         ┌───────────────────────────────────────────────────────────────────────────────────┘
         ├─ audio/      análisis: wavio, dsp, loudness (BS.1770), spectral, stereo, dynamics, musical, render
         ├─ theory/     escalas, modos, acordes, numerales romanos, conducción de voces
         ├─ compose/    estilos (JSON), emociones, melodía por motivos, arreglista, análisis y transformación MIDI
         ├─ midi/       lector/escritor SMF propio
         ├─ mix/        plan de mezcla desde stems
         ├─ master/     decisión de cadena, procesado offline, verificación
         ├─ evaluate/   referencias y antes/después
         ├─ plugins/    escáner VST + arquitectura PE + catálogo de funciones
         ├─ cubase/     detección, Key Commands, control por teclado, intercambio por archivos
         ├─ project/    proyectos, memoria persistente, versiones, copias de seguridad
         └─ core/       configuración, registro, historial (SQLite), permisos, estado de funciones
app.Engine une todo; ui/app.py (GUI) y __main__.py (consola) son capas finas.
```

## Principios

- **Nada simulado.** Cada acción devuelve `verified`, `done`, `unverified`, `failed`, `skipped` o `cancelled`.
- **No destructivo.** Ningún archivo original se sobrescribe; las salidas usan nombres nuevos (`_v2`...).
  Restaurar crea antes un punto de recuperación del estado actual. Copias verificadas por SHA-256.
- **Permisos por categoría** (`core/permissions.py`): análisis siempre permitido; las categorías destructivas
  (estructura, sobrescribir, eliminar) nunca se autorizan implícitamente, ni en modo autónomo.
- **Medición ≠ valoración.** Las observaciones se etiquetan como `medición`, `técnico` o `artístico`.
- **Determinismo.** La composición es reproducible con su semilla; cada nueva orden usa otra semilla.
- **Extensible.** Estilos nuevos: añadir un JSON a `%APPDATA%\AIEngineer\styles`. Nuevas acciones: función en
  `planner/actions.py` + regla en `nlp/intents.py`. El intérprete de reglas se puede sustituir por un modelo
  de lenguaje (local o remoto) devolviendo las mismas intenciones.

## Rendimiento medido (contenedor de desarrollo)

- Análisis completo de 90 s estéreo a 44,1 kHz: ~5 s. LUFS de 3 min estéreo: ~4 s.
- Masterización de 90 s (búsqueda de ganancia + verificación): ~25 s.
- Composición de un arreglo completo: < 0,1 s.
