"""Punto de entrada: `python -m aiengineer` abre la GUI; con subcomandos funciona en consola."""
from __future__ import annotations

import argparse
import json
import sys

from .app import Engine
from .core.status import FEATURES


def _approve_cli(task) -> bool:
    if not sys.stdin.isatty():
        return False
    ans = input(f"¿Aprobar '{task.description}' [{task.category}]? (s/N) ").strip().lower()
    return ans in ("s", "si", "sí", "y", "yes")


def _utf8_console() -> None:
    """La consola de Windows usa cp1252 por defecto: sin esto, caracteres como '→' o '✔' rompen la salida.
    En el ejecutable sin consola sys.stdout es None y no hay nada que configurar."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv=None) -> int:
    _utf8_console()
    ap = argparse.ArgumentParser(prog="aiengineer", description="AI Audio Engineer para Cubase 5")
    ap.add_argument("--home", help="carpeta de datos (por defecto %%APPDATA%%\\AIEngineer)")
    ap.add_argument("--project", help="proyecto a abrir/crear")
    ap.add_argument("--sources", help="carpeta de stems, WAV o MIDI de trabajo")
    ap.add_argument("--mode", choices=["assisted", "semi", "autonomous"])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("gui", help="abrir la interfaz gráfica")
    p = sub.add_parser("do", help="ejecutar una orden en lenguaje natural")
    p.add_argument("orden", nargs="+")
    p.add_argument("--yes", action="store_true", help="aprobar automáticamente lo que pida confirmación (no destructivo)")
    p.add_argument("--plan-only", action="store_true")
    sub.add_parser("status", help="estado de Cubase y de las funciones")
    sub.add_parser("history", help="historial de operaciones")
    r = sub.add_parser("restore", help="restaurar un punto de recuperación del proyecto")
    r.add_argument("backup_id")
    args = ap.parse_args(argv)
    if args.cmd in (None, "gui"):
        from .ui.app import run_gui
        return run_gui(args.home)
    eng = Engine(args.home)
    if args.mode:
        eng.set_mode(args.mode)
    if args.project:
        eng.open_project(args.project, create=True, source_folder=args.sources or "")
    if args.sources:
        eng.set_sources(args.sources)
    if args.cmd == "status":
        print(eng.cubase.status().summary())
        for area, feat, st in FEATURES:
            print(f"[{st:>26}] {area}: {feat}")
        return 0
    if args.cmd == "history":
        for op in reversed(eng.oplog.recent(50)):
            print(op["id"], op["project"], op["category"], op["action"], op["status"])
        return 0
    if args.cmd == "restore":
        if not eng.project:
            print("Indica --project")
            return 2
        for f in eng.project.backups.restore(args.backup_id):
            print("restaurado:", f)
        return 0
    if args.cmd == "do":
        plan = eng.interpret(" ".join(args.orden))
        print(plan.summary())
        if args.plan_only or not plan.tasks:
            return 0 if plan.tasks or plan.questions else 1
        approve = (lambda t: t.category not in ("structure", "overwrite", "delete")) if args.yes else _approve_cli
        tasks = eng.run(plan, approve=approve, progress=print)
        print()
        for t in tasks:
            print(f"== {t.description} [{t.status}]")
            print(t.result["summary"] if t.result else "")
            for a in (t.result or {}).get("artifacts", []):
                print("   archivo:", a)
        return 0 if all(t.status in ("done", "verified", "skipped") for t in tasks) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
