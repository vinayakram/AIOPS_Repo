"""
Process manager for AI agent services.
Registers agent processes so they can be stopped and restarted after an AutoFix.
"""
import os
import time
import logging
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

# Registry: app_name -> { cmd, cwd, env, process }
_registry: dict[str, dict] = {}


def register(app_name: str, cmd: list[str], cwd: str, env: Optional[dict] = None):
    """Register an agent's startup command. Does not start it automatically."""
    _registry[app_name] = {
        "cmd": cmd,
        "cwd": cwd,
        "env": env,
        "process": None,
    }
    logger.info(f"Registered agent '{app_name}' cwd={cwd}")


def start(app_name: str) -> bool:
    """Start the registered agent. Returns True if started/already running."""
    info = _registry.get(app_name)
    if not info:
        logger.warning(f"No registered process for '{app_name}'")
        return False
    if info.get("process") and info["process"].poll() is None:
        logger.info(f"Agent '{app_name}' already running (pid={info['process'].pid})")
        return True
    merged_env = {**os.environ, **(info.get("env") or {})}
    proc = subprocess.Popen(info["cmd"], cwd=info["cwd"], env=merged_env)
    info["process"] = proc
    logger.info(f"Started agent '{app_name}' pid={proc.pid}")
    return True


def stop(app_name: str, timeout: int = 10) -> bool:
    """Terminate the agent process gracefully."""
    info = _registry.get(app_name)
    if not info or not info.get("process"):
        return False
    proc = info["process"]
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
    info["process"] = None
    logger.info(f"Stopped agent '{app_name}'")
    return True


def restart(app_name: str, delay: float = 2.0) -> bool:
    """Stop then start the agent. Returns True if started successfully."""
    stop(app_name)
    time.sleep(delay)
    return start(app_name)


def status(app_name: str) -> str:
    """Return 'running', 'stopped', or 'unregistered'."""
    info = _registry.get(app_name)
    if not info:
        return "unregistered"
    proc = info.get("process")
    if proc is None:
        return "stopped"
    return "running" if proc.poll() is None else "stopped"


def all_statuses() -> dict[str, str]:
    return {name: status(name) for name in _registry}
