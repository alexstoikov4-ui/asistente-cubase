import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np

from aiengineer.app import Engine
from aiengineer.screen.agent import ScreenAgent, downscale
from aiengineer.screen.brains import OllamaBrain, ScriptedBrain, parse_action
from aiengineer.screen.desktop import FakeDesktop, key_codes


def screen(w=2560, h=1440):
    return np.full((h, w, 3), 40, np.uint8)


def flip_on_action(action, d):
    d.image = d.image.copy()
    d.image[: 50, : 50] = (int(d.image[0, 0, 0]) + 100) % 256  # la interfaz "responde" a cada acción


def test_parse_action_formats():
    a = parse_action('bla {"thought": "x", "action": "click", "x": 100, "y": 50} bla', 1280, 720)
    assert (a["action"], a["x"], a["y"]) == ("click", 100, 50)
    b = parse_action("Thought: abro\\nAction: click(start_box='<|box_start|>(500,500)<|box_end|>')", 1280, 720, "rel1000")
    assert (b["x"], b["y"]) == (640, 360)
    c = parse_action("Action: drag(start_box='(10,10)', end_box='(20,30)')", 1000, 1000, "pixels")
    assert (c["action"], c["x2"], c["y2"]) == ("drag", 20, 30)
    assert parse_action("Action: hotkey(key='ctrl s')", 10, 10)["keys"] == "ctrl+s"
    assert parse_action("Action: finished(content='ok')", 10, 10)["action"] == "done"
    assert parse_action("no sé", 10, 10)["action"] == "fail"
    assert parse_action('{"action": "click"}', 10, 10)["action"] == "fail"  # sin coordenadas
    assert key_codes("ctrl+shift+e") == [0x11, 0x10, ord("E")]


def test_downscale_and_coordinate_mapping():
    small, f = downscale(screen())
    assert small.shape[1] <= 1280 and f == 2.0
    d = FakeDesktop(screen())
    d.on_action = flip_on_action
    brain = ScriptedBrain([{"action": "click", "x": 100, "y": 200}, {"action": "drag", "x": 10, "y": 10, "x2": 30, "y2": 40},
                           {"action": "type", "text": "Pro-Q"}, {"action": "key", "keys": "enter"}, {"action": "done"}])
    res = ScreenAgent(d, brain, settle=0).run("insertar plugin")
    assert res.status == "done_by_model"
    assert d.actions[0] == ("click", 200, 400, "left", False)  # coordenadas multiplicadas por 2
    assert d.actions[1] == ("drag", 20, 20, 60, 80)
    assert ("type", "Pro-Q") in d.actions and ("key", "enter") in d.actions
    assert all(s["changed"] for s in res.steps[:4])


def test_safety_rules(tmp_path):
    d = FakeDesktop(screen(800, 600), title="Correo", process=r"C:\\Windows\\explorer.exe")
    d.focusable = False  # Cubase no se puede poner delante
    res = ScreenAgent(d, ScriptedBrain([{"action": "click", "x": 1, "y": 1}]), settle=0, focus_timeout=0.3).run("x")
    assert res.status == "unsafe" and d.actions == [("focus", "cubase")]  # nunca hace clic fuera de Cubase
    d = FakeDesktop(screen(800, 600), title="AI Audio Engineer", process=r"C:\\Python\\python.exe")
    d.on_action = flip_on_action
    res = ScreenAgent(d, ScriptedBrain([{"action": "click", "x": 1, "y": 1}]), settle=0).run("x")
    assert res.status == "done_by_model" and d.actions[0] == ("focus", "cubase")  # trae Cubase al frente
    d = FakeDesktop(screen(800, 600))
    d._cursor = (0, 0)
    assert ScreenAgent(d, ScriptedBrain([]), settle=0).run("x").status == "stopped"
    d = FakeDesktop(screen(800, 600))
    ev = threading.Event()
    ev.set()
    assert ScreenAgent(d, ScriptedBrain([]), ev, settle=0).run("x").status == "stopped"
    d = FakeDesktop(screen(800, 600))
    d.on_action = flip_on_action
    res = ScreenAgent(d, ScriptedBrain([{"action": "key", "keys": "alt+f4"}, {"action": "done"}]), settle=0,
                      log_dir=tmp_path / "log").run("x")
    assert ("key", "alt+f4") not in d.actions and res.steps[0]["blocked"]
    assert res.screenshots and (tmp_path / "log" / "paso_01.png").exists()
    d = FakeDesktop(screen(800, 600))  # la pantalla nunca cambia
    res = ScreenAgent(d, ScriptedBrain([{"action": "click", "x": 5, "y": 5}] * 10), settle=0).run("x")
    assert res.status == "failed" and "sin ningún cambio" in res.message


class _FakeOllama(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        self._send({"models": [{"name": "qwen2.5vl:7b"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeOllama.requests.append(body)
        n = len(_FakeOllama.requests)
        content = '{"action": "click", "x": 10, "y": 20}' if n == 1 else '{"action": "done"}'
        self._send({"message": {"content": content}})

    def _send(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_ollama_brain_and_engine_integration(tmp_path):
    srv = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    try:
        assert OllamaBrain.available_models(url) == ["qwen2.5vl:7b"]
        eng = Engine(tmp_path / "home")
        eng.open_project("S", create=True)
        eng.set_mode("autonomous")
        eng.config.settings.ollama_url = url
        eng.config.settings.vision_model = "qwen2.5vl:7b"
        d = FakeDesktop(screen(1920, 1080))
        d.on_action = flip_on_action
        eng.context["_desktop"] = d
        plan = eng.interpret("Inserta Pro-Q 3 en la pista del bajo")
        assert [t.action for t in plan.tasks] == ["screen_task"]
        t = eng.run(plan)[0]
        assert t.status == "unverified" and d.actions[0][:3] == ("click", 20, 40)  # captura reducida a 960 px: factor 2
        req = _FakeOllama.requests[0]
        assert req["model"] == "qwen2.5vl:7b" and req["messages"][1]["images"]
    finally:
        srv.shutdown()
    assert OllamaBrain.available_models("http://127.0.0.1:1") == []


def test_quick_commands_without_ollama(tmp_path):
    from aiengineer.cubase.keycommands import KeyMap
    from aiengineer.screen.quick import match_quick, run_quick
    assert match_quick("abre el mezclador de cubase")[1] == "mezclador"
    assert match_quick("inserta pro-q 3 en el bajo") is None
    d = FakeDesktop(screen(800, 600), title="AI Audio Engineer", process=r"C:\\Python\\python.exe")
    d.on_action = flip_on_action
    st, msg = run_quick(d, KeyMap({}), "Abre el mezclador", settle=0)
    assert st == "verified" and ("key", "f3") in d.actions and d.actions[0] == ("focus", "cubase")
    d2 = FakeDesktop(screen(800, 600))
    st, _ = run_quick(d2, KeyMap({("Devices", "Mixer"): ["Ctrl+M"]}), "abre el mezclador", settle=0)
    assert st == "unverified" and ("key", "ctrl+m") in d2.actions  # usa el atajo del usuario
    # integración: la orden en español no necesita Ollama
    eng = Engine(tmp_path / "home")
    eng.open_project("Q", create=True)
    eng.set_mode("autonomous")
    d3 = FakeDesktop(screen(800, 600))
    d3.on_action = flip_on_action
    eng.context["_desktop"] = d3
    t = eng.run(eng.interpret("Abre el mezclador de Cubase"))[0]
    assert t.status == "verified" and "mezclador" in t.result["summary"]
