import json

import pytest

from aiengineer.core.config import Config
from aiengineer.core.oplog import OperationLog
from aiengineer.core.permissions import CATEGORIES, Decision, PermissionPolicy
from aiengineer.project.backup import BackupManager
from aiengineer.project.files import unique_path
from aiengineer.project.manager import ProjectManager


def test_config_roundtrip_and_corruption(tmp_path):
    c = Config(tmp_path)
    c.settings.autonomy_mode = "semi"
    c.settings.permissions["eq"] = "deny"
    c.save()
    assert Config(tmp_path).settings.permissions["eq"] == "deny"
    (tmp_path / "settings.json").write_text("{roto", encoding="utf-8")
    c2 = Config(tmp_path)
    assert c2.settings.autonomy_mode == "assisted"
    assert (tmp_path / "settings.corrupt.json").exists()


def test_oplog(tmp_path):
    log = OperationLog(tmp_path / "ops.db")
    i = log.record("componer", "running", project="p", category="compose", detail={"a": 1})
    log.update(i, "verified", {"ok": True})
    rows = log.recent()
    assert rows[0]["status"] == "verified" and rows[0]["detail"] == {"ok": True}
    with pytest.raises(ValueError):
        log.record("x", "inventado")


def test_permissions_modes():
    a = PermissionPolicy("assisted")
    s = PermissionPolicy("semi")
    au = PermissionPolicy("autonomous")
    assert a.decide("analysis") == Decision.ALLOW
    assert a.decide("eq") == Decision.ASK
    assert s.decide("eq") == Decision.ALLOW and s.decide("master") == Decision.ASK
    assert au.decide("master") == Decision.ALLOW
    for cat, info in CATEGORIES.items():
        if info["destructive"]:
            assert au.decide(cat) == Decision.ASK  # nunca implícito
    assert PermissionPolicy("autonomous", {"delete": "allow"}).decide("delete") == Decision.ALLOW


def test_project_memory_versions(tmp_path):
    pm = ProjectManager(tmp_path)
    p = pm.create("Mi Tema")
    p.remember("tempo", 128)
    p.remember("feedback", "el bajo ha perdido cuerpo")
    p.record_decision("hpf 120 en bajo", approved=True, effective=False)
    assert p.was_ineffective("hpf 120 en bajo")
    p2 = pm.open("Mi Tema")
    assert p2.memory["tempo"] == 128 and p2.memory["feedback"] == ["el bajo ha perdido cuerpo"]
    vers = p2.versions()
    assert len(vers) >= 2
    old = json.loads(vers[0].read_text(encoding="utf-8"))
    p2.restore_version(vers[0])
    assert p2.memory["tempo"] == old.get("tempo")
    assert pm.list() == ["Mi Tema"]
    with pytest.raises(FileExistsError):
        pm.create("Mi Tema")


def test_backup_restore(tmp_path):
    f = tmp_path / "song.mid"
    f.write_bytes(b"original")
    bm = BackupManager(tmp_path / "bk")
    bid = bm.create([f], "antes de transformar")
    f.write_bytes(b"cambiado")
    restored = bm.restore(bid)
    assert f.read_bytes() == b"original" and restored
    # restaurar guarda antes el estado actual
    assert any("previo" in b["reason"] for b in bm.list())


def test_unique_path(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(b"x")
    assert unique_path(p).name == "a_v2.wav"
