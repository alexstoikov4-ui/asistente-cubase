"""Intercambio por archivos con Cubase: el mecanismo fiable disponible en Cubase 5.

La aplicación escribe MIDI/WAV/informes en una carpeta de intercambio y genera instrucciones
precisas para importarlos (Archivo > Importar > Archivo MIDI / Archivo de audio). Nunca toca
el .cpr. Si el control por teclado está activado, puede además intentar abrir el diálogo de
importación, pero el resultado se marca como no verificado.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..project.files import unique_path

IMPORT_STEPS = {
    "midi": ["En Cubase: Archivo > Importar > Archivo MIDI...",
             "Selecciona el archivo indicado. Si pregunta, elige 'Insertar en el proyecto' (no crear proyecto nuevo).",
             "Cada pista del MIDI aparece como una pista MIDI independiente con su nombre; los marcadores "
             "de estructura se importan en la pista de marcadores.",
             "Asigna a cada pista tu instrumento VST (el programa GM incluido es solo orientativo)."],
    "audio": ["En Cubase: Archivo > Importar > Archivo de audio...",
              "Selecciona el archivo; si la frecuencia de muestreo no coincide con la del proyecto, acepta convertirlo."],
}


def deliver(file: Path, exchange_dir: Path, kind: str) -> dict:
    """Copia el archivo a la carpeta de intercambio (sin sobrescribir) y devuelve los pasos de importación."""
    exchange_dir = Path(exchange_dir)
    exchange_dir.mkdir(parents=True, exist_ok=True)
    dest = unique_path(exchange_dir / Path(file).name)
    shutil.copy2(file, dest)
    return {"file": str(dest), "kind": kind, "steps": IMPORT_STEPS.get(kind, []),
            "verified": dest.exists() and dest.stat().st_size == Path(file).stat().st_size}
