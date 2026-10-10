"""Ejecutor: corre las tareas en orden con permisos, parada inmediata, registro y gestión de errores."""
from __future__ import annotations

import logging
import traceback

from ..core.permissions import Decision
from .actions import REGISTRY, result
from .planner import Plan, Task

log = logging.getLogger("aiengineer.executor")


def execute(engine, plan: Plan, approve=None, progress=None) -> list[Task]:
    """approve(task) -> bool se llama cuando la política exige confirmación. progress(texto) informa."""
    say = progress or (lambda s: None)
    engine.stop_event.clear()
    for task in plan.tasks:
        if engine.stop_event.is_set():
            task.status = "cancelled"
            task.result = result("cancelled", "Detenido por el usuario antes de empezar.")
            engine.oplog.record(task.action, "cancelled", project=engine.project_name, category=task.category)
            continue
        decision = engine.policy.decide(task.category)
        if decision == Decision.DENY:
            task.status = "skipped"
            task.result = result("skipped", f"Permiso denegado para la categoría '{task.category}'.")
        elif decision == Decision.ASK and not (approve and approve(task)):
            task.status = "skipped"
            task.result = result("skipped", "No aprobado por el usuario.")
        if task.status == "skipped":
            engine.oplog.record(task.action, "skipped", project=engine.project_name, category=task.category,
                                detail=task.result["summary"])
            say(f"⏭ {task.description}: {task.result['summary']}")
            continue
        fn = REGISTRY.get(task.action)
        if fn is None:
            task.status = "failed"
            task.result = result("failed", f"Acción no implementada: {task.action}")
            continue
        op = engine.oplog.record(task.action, "running", project=engine.project_name, category=task.category,
                                 detail=task.params)
        task.status = "running"
        say(f"▶ {task.description}")
        try:
            task.result = fn(engine, task.params)
        except Exception as e:  # noqa: BLE001 - un fallo no debe tumbar la aplicación
            log.error("fallo en %s: %s\n%s", task.action, e, traceback.format_exc())
            task.result = result("failed", f"Error: {e}")
        task.status = task.result["status"]
        engine.oplog.update(op, task.status if task.status in ("verified", "unverified", "failed", "skipped",
                                                               "cancelled") else "done",
                            detail={"summary": task.result["summary"][:2000], "artifacts": task.result["artifacts"]})
        say(f"{'✔' if task.status in ('done', 'verified') else '✖' if task.status == 'failed' else '•'} "
            f"{task.description} [{task.status}]")
    return plan.tasks
