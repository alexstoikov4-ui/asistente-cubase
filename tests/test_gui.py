"""Prueba de humo de la GUI: se omite si no hay tkinter o pantalla (en Windows siempre hay)."""
import os
import sys
import time

import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture
def gui(tmp_path, monkeypatch):
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        pytest.skip("sin pantalla")
    from tkinter import messagebox
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)
    from aiengineer.app import Engine
    from aiengineer.ui.app import App
    eng = Engine(tmp_path / "home")
    eng.open_project("GUI", create=True)
    root = tk.Tk()
    app = App(root, eng)
    yield app, root, eng
    root.destroy()


def _wait(app, root, timeout=120):
    end = time.time() + timeout
    while app.worker and app.worker.is_alive() and time.time() < end:
        root.update()
        time.sleep(0.02)
    for _ in range(30):
        root.update()
        time.sleep(0.01)


def test_gui_order_and_panels(gui):
    app, root, eng = gui
    app.order.insert("1.0", "Compón un drop progressive house con una melodía emotiva")
    app._execute_order()
    _wait(app, root)
    assert "verified" in app.chat.get("1.0", "end")
    assert len(app.tasks_tree.get_children()) == 2
    app.mode_var.set("Autónomo")
    app._mode_changed()
    assert eng.policy.mode == "autonomous"
    app.t_file.set(eng.context["midi_file"])
    app._transform()
    _wait(app, root)
    assert "Nueva versión verificada" in app.c_out.get("1.0", "end")
    app._fill_history()
    assert len(app.hist_tree.get_children()) >= 3
