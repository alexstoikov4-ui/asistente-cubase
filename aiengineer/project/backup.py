"""Puntos de recuperación: copia archivos antes de operaciones potencialmente destructivas."""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class BackupManager:
    def __init__(self, backups_dir: Path):
        self.dir = Path(backups_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def create(self, files: list[Path], reason: str) -> str:
        """Copia los archivos y verifica cada copia por hash. Devuelve el id del punto de recuperación."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bid = stamp
        n = 1
        while (self.dir / bid).exists():
            n += 1
            bid = f"{stamp}-{n}"
        dest = self.dir / bid
        dest.mkdir()
        manifest = {"id": bid, "created": time.time(), "reason": reason, "files": []}
        for i, src in enumerate(files):
            src = Path(src)
            if not src.is_file():
                raise FileNotFoundError(src)
            target = dest / f"{i:03d}_{src.name}"
            shutil.copy2(src, target)
            digest = sha256(src)
            if sha256(target) != digest:
                shutil.rmtree(dest)
                raise IOError(f"la copia de {src} no coincide con el original")
            manifest["files"].append({"original": str(src.resolve()), "copy": target.name, "sha256": digest})
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return bid

    def list(self) -> list[dict]:
        out = []
        for d in sorted(self.dir.iterdir()):
            m = d / "manifest.json"
            if m.exists():
                out.append(json.loads(m.read_text(encoding="utf-8")))
        return out

    def restore(self, backup_id: str) -> list[str]:
        """Restaura los archivos del punto indicado. El estado actual se guarda antes en otro punto."""
        d = self.dir / backup_id
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        current = [Path(f["original"]) for f in manifest["files"] if Path(f["original"]).exists()]
        if current:
            self.create(current, f"estado previo a restaurar {backup_id}")
        restored = []
        for f in manifest["files"]:
            src = d / f["copy"]
            if sha256(src) != f["sha256"]:
                raise IOError(f"copia dañada: {src}")
            Path(f["original"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, f["original"])
            restored.append(f["original"])
        return restored
