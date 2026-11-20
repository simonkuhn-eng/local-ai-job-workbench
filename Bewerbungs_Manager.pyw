"""Windowless launcher for double-clicking Bewerbungs-Manager with pythonw.exe."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
VENV_PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
if VENV_PYTHONW.exists() and Path(sys.executable).resolve() != VENV_PYTHONW.resolve():
    os.execv(str(VENV_PYTHONW), [str(VENV_PYTHONW), str(Path(__file__).resolve()), *sys.argv[1:]])

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.desktop import launch_desktop

if __name__ == "__main__":
    raise SystemExit(launch_desktop())
