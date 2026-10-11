"""'Cerebros' que deciden la siguiente acción mirando la captura de pantalla.

- OllamaBrain: modelo de visión LOCAL y gratuito servido por Ollama (http://127.0.0.1:11434).
  Modelos probables: qwen2.5vl, qwen3-vl, llava, UI-TARS (según lo que el usuario instale).
- ScriptedBrain: acciones predefinidas (pruebas y rutinas grabadas).
El formato de acción común es un dict: {"action": ..., "x":, "y":, "x2":, "y2":, "text":, "keys":, ...}.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request

ACTIONS = {"click", "double_click", "right_click", "drag", "type", "key", "scroll", "wait", "done", "fail"}

SYSTEM_PROMPT = """You operate a Windows PC running Steinberg Cubase 5 by looking at screenshots, like a human user.
Each turn you receive the task, the history of your previous actions and the current screenshot ({w}x{h} pixels).
Reply with ONE action as a JSON object and nothing else:
{{"thought": "short reasoning", "action": "click|double_click|right_click|drag|type|key|scroll|wait|done|fail",
  "x": int, "y": int, "x2": int, "y2": int, "text": "...", "keys": "ctrl+s", "amount": int}}
- Coordinates are pixels of the screenshot you see (0,0 = top-left).
- "drag" goes from (x,y) to (x2,y2). "scroll": amount >0 up, <0 down, at (x,y).
- "key" presses a shortcut such as "enter", "esc", "f3", "ctrl+z".
- Use "done" only when the screenshot shows the task is finished; "fail" if it is impossible.
- Never close Cubase, never create a new project, never delete tracks unless the task explicitly says so.
"""


def parse_action(text: str, w: int, h: int, coord_mode: str = "pixels") -> dict:
    """Interpreta la respuesta del modelo (JSON o estilo UI-TARS) y la normaliza a píxeles de la captura."""
    act: dict | None = None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            act = json.loads(m.group(0))
        except json.JSONDecodeError:
            act = None
    if act is None:
        act = _parse_uitars(text)
    if not act or act.get("action") not in ACTIONS:
        return {"action": "fail", "thought": f"respuesta no interpretable: {text[:200]}"}

    def conv(v, size):
        v = float(v)
        if coord_mode == "rel1000":
            v = v * size / 1000.0
        return int(max(0, min(size - 1, round(v))))

    for kx, ky in (("x", "y"), ("x2", "y2")):
        if kx in act and ky in act and act[kx] is not None and act[ky] is not None:
            act[kx], act[ky] = conv(act[kx], w), conv(act[ky], h)
    if act["action"] in ("click", "double_click", "right_click", "scroll") and ("x" not in act or "y" not in act):
        return {"action": "fail", "thought": "acción sin coordenadas"}
    if act["action"] == "drag" and not all(k in act for k in ("x", "y", "x2", "y2")):
        return {"action": "fail", "thought": "arrastre sin coordenadas completas"}
    return act


_PT = r"\(?\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)?"


def _parse_uitars(text: str) -> dict | None:
    t = text.replace("<|box_start|>", "").replace("<|box_end|>", "")
    thought = ""
    mt = re.search(r"Thought:(.*?)(?:Action:|$)", t, re.DOTALL)
    if mt:
        thought = mt.group(1).strip()
    m = re.search(r"(left_single|click|left_double|right_single|drag|hotkey|type|scroll|wait|finished)\((.*)\)", t, re.DOTALL)
    if not m:
        return None
    name, args = m.group(1), m.group(2)
    pts = re.findall(_PT, args)
    base = {"thought": thought}
    if name in ("left_single", "click") and pts:
        return {**base, "action": "click", "x": pts[0][0], "y": pts[0][1]}
    if name == "left_double" and pts:
        return {**base, "action": "double_click", "x": pts[0][0], "y": pts[0][1]}
    if name == "right_single" and pts:
        return {**base, "action": "right_click", "x": pts[0][0], "y": pts[0][1]}
    if name == "drag" and len(pts) >= 2:
        return {**base, "action": "drag", "x": pts[0][0], "y": pts[0][1], "x2": pts[1][0], "y2": pts[1][1]}
    if name == "hotkey":
        k = re.search(r"key='([^']*)'", args)
        return {**base, "action": "key", "keys": (k.group(1) if k else "").replace(" ", "+")}
    if name == "type":
        c = re.search(r"content='((?:[^'\\]|\\.)*)'", args)
        return {**base, "action": "type", "text": c.group(1).encode().decode("unicode_escape") if c else ""}
    if name == "scroll" and pts:
        d = re.search(r"direction='(\w+)'", args)
        return {**base, "action": "scroll", "x": pts[0][0], "y": pts[0][1],
                "amount": -3 if d and d.group(1) == "down" else 3}
    if name == "wait":
        return {**base, "action": "wait"}
    if name == "finished":
        return {**base, "action": "done"}
    return None


def coord_mode_for(model: str) -> str:
    """UI-TARS devuelve coordenadas relativas 0-1000; los demás, píxeles de la imagen."""
    return "rel1000" if "tars" in model.lower() else "pixels"


class OllamaBrain:
    def __init__(self, model: str, url: str = "http://127.0.0.1:11434", coord_mode: str | None = None,
                 timeout: float = 300.0):
        self.model = model
        self.url = url.rstrip("/")
        self.coord_mode = coord_mode or coord_mode_for(model)
        self.timeout = timeout

    @staticmethod
    def available_models(url: str = "http://127.0.0.1:11434", timeout: float = 3.0) -> list[str]:
        """Modelos instalados en Ollama; lista vacía si Ollama no está en marcha."""
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=timeout) as r:
                return [m["name"] for m in json.loads(r.read().decode()).get("models", [])]
        except (urllib.error.URLError, OSError, ValueError):
            return []

    def next_action(self, task: str, png: bytes, w: int, h: int, history: list[dict]) -> tuple[dict, str]:
        hist = "\n".join(f"{i + 1}. {json.dumps(hh['action'], ensure_ascii=False)} -> "
                         f"{'la pantalla cambió' if hh.get('changed') else 'la pantalla NO cambió'}"
                         for i, hh in enumerate(history[-8:])) or "(ninguna)"
        body = {
            "model": self.model, "stream": False, "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT.format(w=w, h=h)},
                {"role": "user", "content": f"Task: {task}\nPrevious actions:\n{hist}\nWhat is the next action?",
                 "images": [base64.b64encode(png).decode()]},
            ],
        }
        req = urllib.request.Request(self.url + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            text = json.loads(r.read().decode())["message"]["content"]
        return parse_action(text, w, h, self.coord_mode), text


class ScriptedBrain:
    """Devuelve una lista fija de acciones (coordenadas en píxeles de la captura reducida)."""

    def __init__(self, actions: list[dict]):
        self.actions = list(actions)

    def next_action(self, task, png, w, h, history):
        a = self.actions.pop(0) if self.actions else {"action": "done"}
        return dict(a), json.dumps(a)
