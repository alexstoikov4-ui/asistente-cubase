"""Agente de pantalla: el mismo ciclo que usa Claude Desktop (capturar -> decidir -> actuar -> comprobar),
con un modelo local gratuito y protecciones:

- Solo actúa si la ventana activa pertenece a Cubase (incluidas las ventanas de plugins).
- Parada inmediata con el botón Detener o llevando el ratón a la esquina superior izquierda.
- Atajos peligrosos (cerrar, nuevo proyecto...) bloqueados salvo confirmación.
- Límite de pasos; se detiene si la pantalla deja de responder.
- Guarda la captura de cada paso para revisar lo que hizo.
- Nunca declara 'verificado' el objetivo: el modelo dice que terminó y la app lo informa como tal.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .png import encode_png, write_png
from .vision import changed_fraction

BLOCKED_KEYS = {"alt+f4", "ctrl+w", "ctrl+n", "ctrl+q", "ctrl+shift+w", "win+d", "win+l", "win+r", "ctrl+alt+delete",
                "ctrl+shift+esc", "alt+tab"}


def downscale(img: np.ndarray, max_w: int = 1280) -> tuple[np.ndarray, float]:
    """Reduce la captura (media por bloques) para el modelo. Devuelve (imagen, factor original/reducida)."""
    h, w = img.shape[:2]
    f = int(np.ceil(w / max_w)) if w > max_w else 1
    if f == 1:
        return img, 1.0
    hh, ww = h // f * f, w // f * f
    small = img[:hh, :ww].reshape(hh // f, f, ww // f, f, 3).mean(axis=(1, 3)).astype(np.uint8)
    return small, float(f)


@dataclass
class AgentResult:
    status: str  # done_by_model | failed | stopped | max_steps | unsafe
    message: str
    steps: list = field(default_factory=list)
    screenshots: list = field(default_factory=list)


class ScreenAgent:
    def __init__(self, desktop, brain, stop_event=None, *, max_steps: int = 30, settle: float = 0.8,
                 process_filter: str = "cubase", log_dir: Path | None = None, confirm=None, progress=None,
                 max_width: int = 1280, focus_timeout: float = 10.0):
        self.d = desktop
        self.brain = brain
        self.stop = stop_event
        self.max_steps = max_steps
        self.settle = settle
        self.process_filter = process_filter.lower()
        self.log_dir = Path(log_dir) if log_dir else None
        self.confirm = confirm or (lambda text: False)
        self.say = progress or (lambda s: None)
        self.max_width = max_width
        self.focus_timeout = focus_timeout

    def _bring_cubase(self) -> bool:
        """Al pulsar Ejecutar la ventana activa es la de esta aplicación: se intenta poner Cubase delante
        y, si Windows no lo permite, se espera a que el usuario lo haga."""
        if self._in_cubase():
            return True
        if hasattr(self.d, "focus_process") and self.d.focus_process(self.process_filter):
            return True
        self.say(f"Pon la ventana de Cubase delante: tienes {self.focus_timeout:.0f} segundos.")
        end = time.time() + self.focus_timeout
        while time.time() < end:
            if self.stop is not None and self.stop.is_set():
                return False
            if self._in_cubase():
                time.sleep(0.5)
                return True
            time.sleep(0.25)
        return False

    def _in_cubase(self) -> bool:
        proc = (self.d.foreground_process() or "").lower()
        title = (self.d.foreground_title() or "").lower()
        return self.process_filter in Path(proc).name.lower() or (not proc and self.process_filter in title)

    def _save(self, img, i, res: AgentResult):
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            p = self.log_dir / f"paso_{i:02d}.png"
            write_png(p, img)
            res.screenshots.append(str(p))

    def run(self, task: str) -> AgentResult:
        res = AgentResult("failed", "")
        stale = 0
        if not self._bring_cubase():
            res.status = "unsafe"
            res.message = "Cubase no está abierto o no se ha puesto delante: no se hace ningún clic."
            return res
        for i in range(1, self.max_steps + 1):
            if self.stop is not None and self.stop.is_set():
                res.status, res.message = "stopped", "Detenido por el usuario."
                return res
            if tuple(self.d.cursor()) == (0, 0):
                res.status, res.message = "stopped", "Parada de emergencia: ratón en la esquina superior izquierda."
                return res
            if not self._in_cubase():
                res.status = "unsafe"
                res.message = (f"La ventana activa no es de Cubase ({self.d.foreground_title()!r}); no se hace ningún "
                               "clic fuera de Cubase. Pon Cubase en primer plano y repite.")
                return res
            shot = self.d.screenshot()
            small, f = downscale(shot, self.max_width)
            self._save(small, i, res)
            try:
                act, raw = self.brain.next_action(task, encode_png(small), small.shape[1], small.shape[0], res.steps)
            except Exception as e:  # noqa: BLE001 - el modelo puede no estar disponible
                res.status, res.message = "failed", f"El modelo de visión no respondió: {e}"
                return res
            self.say(f"Paso {i}: {act.get('action')} {act.get('thought', '')[:120]}")
            if act["action"] == "done":
                res.steps.append({"action": act, "changed": None})
                res.status = "done_by_model"
                res.message = (f"El modelo indica que la tarea está terminada en {i - 1} acciones. Revisa la última "
                               "captura: la aplicación no puede confirmar por sí sola el resultado musical.")
                return res
            if act["action"] == "fail":
                res.steps.append({"action": act, "changed": None})
                res.status, res.message = "failed", f"El modelo no puede completar la tarea: {act.get('thought', '')}"
                return res
            if act["action"] == "key" and act.get("keys", "").lower().replace(" ", "") in BLOCKED_KEYS:
                if not self.confirm(f"El agente quiere pulsar {act['keys']}. ¿Permitirlo?"):
                    res.steps.append({"action": act, "changed": False, "blocked": True})
                    continue
            self._execute(act, f)
            time.sleep(self.settle)
            after = self.d.screenshot()
            changed = changed_fraction(shot, after) > 0.0005
            res.steps.append({"action": act, "changed": changed})
            stale = 0 if changed or act["action"] == "wait" else stale + 1
            if stale >= 4:
                res.status = "failed"
                res.message = "Cuatro acciones seguidas sin ningún cambio en pantalla: el agente se detiene."
                self._save(downscale(after, self.max_width)[0], i + 1, res)
                return res
        res.status, res.message = "max_steps", f"Se alcanzó el límite de {self.max_steps} pasos sin terminar."
        return res

    def _execute(self, a: dict, f: float):
        X = lambda v: int(round(v * f))  # de la captura reducida a píxeles reales
        kind = a["action"]
        if kind == "click":
            self.d.click(X(a["x"]), X(a["y"]))
        elif kind == "double_click":
            self.d.click(X(a["x"]), X(a["y"]), double=True)
        elif kind == "right_click":
            self.d.click(X(a["x"]), X(a["y"]), button="right")
        elif kind == "drag":
            self.d.drag(X(a["x"]), X(a["y"]), X(a["x2"]), X(a["y2"]))
        elif kind == "scroll":
            self.d.scroll(X(a["x"]), X(a["y"]), int(a.get("amount", -3)))
        elif kind == "type":
            self.d.type_text(str(a.get("text", "")))
        elif kind == "key":
            self.d.key(str(a.get("keys", "")))
        elif kind == "wait":
            time.sleep(1.5)


def result_json(res: AgentResult) -> str:
    return json.dumps({"status": res.status, "message": res.message, "steps": res.steps,
                       "screenshots": res.screenshots}, indent=2, ensure_ascii=False)
