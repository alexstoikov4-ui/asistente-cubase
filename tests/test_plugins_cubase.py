import struct

from aiengineer.cubase.bridge import deliver
from aiengineer.cubase.control import CubaseController, parse_shortcut
from aiengineer.cubase.detect import detect, parse_project_from_title
from aiengineer.cubase.keycommands import KeyMap, parse_key_commands
from aiengineer.plugins.scanner import classify, pe_architecture, roles_index, scan


def fake_pe(path, machine):
    head = bytearray(64)
    head[:2] = b"MZ"
    head[0x3C:0x40] = struct.pack("<I", 64)
    path.write_bytes(bytes(head) + b"PE\0\0" + struct.pack("<H", machine) + b"\0" * 64)


def test_pe_and_scan(tmp_path):
    vst = tmp_path / "VSTPlugins"
    (vst / "FabFilter").mkdir(parents=True)
    fake_pe(vst / "FabFilter" / "FabFilter Pro-Q 3.dll", 0x8664)
    fake_pe(vst / "FabFilter" / "FabFilter Saturn 2.dll", 0x014C)
    fake_pe(vst / "Limiter6.dll", 0x014C)
    (vst / "readme.dll").write_bytes(b"no es un PE")
    waves = tmp_path / "Waves" / "Plug-Ins"
    (waves / "L2.bundle").mkdir(parents=True)
    (waves / "C1.bundle").mkdir()
    assert pe_architecture(vst / "Limiter6.dll") == "x86"
    assert pe_architecture(vst / "readme.dll") is None
    pls = scan([vst, waves], host_arch="x86")
    names = {p.name: p for p in pls}
    assert set(names) >= {"FabFilter Pro-Q 3", "FabFilter Saturn 2", "Limiter 6", "L2", "C1"}
    assert names["FabFilter Pro-Q 3"].compatible is False  # 64 bits en Cubase de 32 bits
    assert names["Limiter 6"].compatible is True
    idx = roles_index(pls)
    assert "FabFilter Pro-Q 3" not in idx.get("eq", [])
    assert "Limiter 6" in idx["limiter"]
    pls64 = scan([vst], host_arch="x64")
    assert "VST Bridge" in {p.name: p for p in pls64}["Limiter 6"].note


def test_classify_is_not_overeager():
    assert classify("Rapid Synth.dll")[0] == ""  # 'api' no debe coincidir dentro de otra palabra
    assert classify("StudioEQ.vst3")[1] == "StudioEQ"


def test_title_parsing():
    assert parse_project_from_title("Cubase 5 Project - Mi tema") == "Mi tema"
    assert parse_project_from_title("Cubase 5 - [Drop final.cpr]") == "Drop final"
    assert parse_project_from_title("Explorador de archivos") is None


def test_detect_reports_platform():
    st = detect()
    assert isinstance(st.summary(), str)


KC = """<?xml version="1.0" encoding="utf-8"?>
<KeyCommands><list name="Categories" type="list">
 <item><string name="Name" value="File"/><list name="Commands" type="list">
   <item><string name="Name" value="Save"/><string name="Key" value="Ctrl+Alt+S"/></item>
   <item><string name="Name" value="Export Audio Mixdown"/></item>
 </list></item>
 <item><string name="Name" value="Transport"/><list name="Commands" type="list">
   <item><string name="Name" value="Start"/><list name="Key" type="string"><string value="Enter"/><string value="Num Enter"/></list></item>
 </list></item>
</list></KeyCommands>"""


def test_key_commands_and_controller(tmp_path):
    p = tmp_path / "Key Commands.xml"
    p.write_text(KC, encoding="utf-8")
    kc = parse_key_commands(p)
    assert kc[("File", "Save")] == ["Ctrl+Alt+S"] and kc[("Transport", "Start")] == ["Enter", "Num Enter"]
    km = KeyMap(kc)
    assert km.shortcut("File", "Save") == ("Ctrl+Alt+S", "usuario")
    assert km.shortcut("Edit", "Undo")[1] == "por defecto"
    assert parse_shortcut("Ctrl+Shift+E") == [0x11, 0x10, ord("E")]
    c = CubaseController(km, enabled=True, dry_run=True)
    r = c.run_command("File", "Save")
    assert r.status == "unverified" and c.sent == [[0x11, 0x12, ord("S")]]
    assert c.run_command("File", "Export Audio Mixdown").status == "failed"  # sin atajo asignado
    assert CubaseController(km, enabled=False).run_command("File", "Save").status == "skipped"
    calls = iter([(False, "no"), (True, "sí")])
    r2 = c.run_command("File", "Save", verify=lambda: next(calls), timeout=2)
    assert r2.status == "verified"


def test_bridge_never_overwrites(tmp_path):
    f = tmp_path / "song.mid"
    f.write_bytes(b"MThd")
    ex = tmp_path / "exchange"
    a = deliver(f, ex, "midi")
    b = deliver(f, ex, "midi")
    assert a["file"] != b["file"] and a["verified"] and b["steps"]
