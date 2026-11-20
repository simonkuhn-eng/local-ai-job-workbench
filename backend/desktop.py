"""Native lightweight Windows desktop shell via pywebview (Edge WebView2, zero Electron bloat)."""
from __future__ import annotations

import argparse
from contextlib import closing
import ctypes
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zlib

import uvicorn

from backend.config import DATA_DIR, ROOT, ensure_dirs


def _ensure_stdio_handles() -> None:
    """When launched via pythonw.exe on Windows, stdout/stderr are None; bind them to a log file."""
    ensure_dirs()
    log_path = DATA_DIR / "desktop.log"
    if sys.stdout is None:
        sys.stdout = open(log_path, "a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LocalAiJobWorkbench.Desktop.1.0")
    except Exception:
        pass


def _show_error_dialog(title: str, message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
    except Exception:
        pass


def find_free_port(preferred: int = 8785) -> int:
    for candidate in (preferred, 0):
        if candidate > 0:
            with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
                probe.settimeout(0.25)
                if probe.connect_ex(("127.0.0.1", candidate)) == 0:
                    continue
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                sock.bind(("127.0.0.1", candidate))
                return int(sock.getsockname()[1])
            except OSError:
                continue
    return preferred


def wait_for_server(
    url: str,
    server_thread: threading.Thread | None = None,
    timeout_s: float = 15.0,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if server_thread is not None and not server_thread.is_alive():
            return False
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=1.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.12)
    return False


def launch_desktop(smoke_test: bool = False, browser_only: bool = False) -> int:
    _ensure_stdio_handles()
    os.chdir(ROOT)

    mutex_handle = None
    if not smoke_test and not browser_only and os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32
            mutex = kernel32.CreateMutexW(None, False, "Global\\LocalAiJobWorkbench_SingleInstance")
            if mutex:
                if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                    kernel32.CloseHandle(mutex)
                    hwnd = user32.FindWindowW(None, "Local AI Job Workbench")
                    if hwnd:
                        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                        user32.SetForegroundWindow(hwnd)
                    return 0
                mutex_handle = mutex
        except Exception:
            pass

    from backend.server import app

    port = find_free_port(8785)
    base_url = f"http://127.0.0.1:{port}"

    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        log_config=None,
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

    server_thread = threading.Thread(target=server.run, name="bm-uvicorn", daemon=True)
    server_thread.start()

    if not wait_for_server(base_url, server_thread=server_thread, timeout_s=18.0):
        msg = "Lokaler Backend-Server konnte nicht gestartet werden. Details siehe data/desktop.log."
        print(f"Fehler: {msg}", file=sys.stderr)
        _show_error_dialog("Bewerbungs-Manager — Startfehler", msg)
        server.should_exit = True
        server_thread.join(timeout=2.0)
        if mutex_handle and os.name == "nt":
            try:
                ctypes.windll.kernel32.CloseHandle(mutex_handle)
            except Exception:
                pass
        return 1

    if browser_only:
        print(f"Bewerbungs-Manager läuft unter {base_url} (Strg+C zum Beenden)")
        if not smoke_test:
            webbrowser.open(base_url)
            try:
                while server_thread.is_alive():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
        server.should_exit = True
        server_thread.join(timeout=3.0)
        if mutex_handle and os.name == "nt":
            try:
                ctypes.windll.kernel32.CloseHandle(mutex_handle)
            except Exception:
                pass
        return 0

    try:
        import webview

        webview.settings["ALLOW_DOWNLOADS"] = True
        webview_profile = DATA_DIR / "webview_profile"
        webview_profile.mkdir(parents=True, exist_ok=True)

        window = webview.create_window(
            title="Local AI Job Workbench",
            url=base_url,
            width=1480,
            height=920,
            min_size=(1020, 680),
            background_color="#111113",
            text_select=True,
        )

        icon_path = ROOT / "data" / "app_icon.ico"
        _ensure_app_icon(icon_path)
        icon_arg = str(icon_path) if icon_path.exists() else None

        if smoke_test:
            def _auto_close() -> None:
                time.sleep(1.5)
                window.destroy()

            webview.start(
                _auto_close,
                private_mode=False,
                storage_path=str(webview_profile),
                icon=icon_arg,
            )
        else:
            webview.start(
                private_mode=False,
                storage_path=str(webview_profile),
                icon=icon_arg,
            )
    except Exception as exc:
        print(f"Hinweis: WebView2-Fenster konnte nicht geöffnet werden ({exc}). Öffne Standardbrowser …", file=sys.stderr)
        if not smoke_test:
            webbrowser.open(base_url)
            try:
                while server_thread.is_alive():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
    finally:
        server.should_exit = True
        server_thread.join(timeout=3.0)
        if mutex_handle and os.name == "nt":
            try:
                ctypes.windll.kernel32.CloseHandle(mutex_handle)
            except Exception:
                pass

    return 0


def _render_png_rgba(size: int) -> bytes:
    """Render a crisp rounded navy/blue 'BM' badge icon as an RGBA PNG byte stream (pure stdlib)."""
    # 9x5 crisp pixel bitmap for "BM" monogram
    glyph = [
        "111001001",
        "100101111",
        "111001111",
        "100101011",
        "111001001",
    ]
    rows: list[bytes] = []
    radius = size * 0.22
    half = size / 2.0
    box = half - 1.5
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            dx = max(0.0, abs(px - half) - (box - radius))
            dy = max(0.0, abs(py - half) - (box - radius))
            dist = (dx * dx + dy * dy) ** 0.5
            if dist > radius:
                row.extend((0, 0, 0, 0))
                continue
            alpha = 255 if dist <= radius - 1.0 else max(0, min(255, int((radius - dist) * 255)))
            # Gradient navy (#132644 -> #2563EB)
            t = (px + py) / (2.0 * size)
            r = int(19 + (37 - 19) * t)
            g = int(38 + (99 - 38) * t)
            b = int(68 + (235 - 68) * t)
            # Draw white 'BM' badge centered inside icon
            gx = int(((px / size) - 0.19) / 0.62 * 9)
            gy = int(((py / size) - 0.28) / 0.44 * 5)
            if 0 <= gy < 5 and 0 <= gx < 9 and glyph[gy][gx] == "1":
                r, g, b = 248, 250, 252
            row.extend((r, g, b, alpha))
        rows.append(bytes(row))

    def _chunk(ctype: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    idat = zlib.compress(b"".join(rows), level=9)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _ensure_app_icon(icon_path: Path) -> None:
    """Create a multi-resolution Windows .ico file if missing."""
    if icon_path.exists() and icon_path.stat().st_size > 0:
        return
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    sizes = (16, 32, 48, 64)
    pngs = [_render_png_rgba(sz) for sz in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = bytearray()
    for sz, png_bytes in zip(sizes, pngs):
        entries.extend(
            struct.pack(
                "<BBBBHHII",
                sz if sz < 256 else 0,
                sz if sz < 256 else 0,
                0,
                0,
                1,
                32,
                len(png_bytes),
                offset,
            )
        )
        offset += len(png_bytes)
    icon_path.write_bytes(header + bytes(entries) + b"".join(pngs))


def install_windows_shortcuts(include_desktop: bool = True) -> list[str]:
    """Create Windows Start Menu (indexed by Windows Search) and Desktop shortcuts."""
    icon_path = ROOT / "data" / "app_icon.ico"
    _ensure_app_icon(icon_path)

    pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    target = str(pythonw if pythonw.exists() else Path(sys.executable))
    if Path(target).name.lower() == "pythonw.exe":
        args = "-m backend.desktop"
    else:
        args = "-m backend.desktop"

    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    start_menu_lnk = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Bewerbungs-Manager.lnk"
    destinations = [start_menu_lnk]

    if include_desktop:
        desktop_dir = Path(os.environ.get("USERPROFILE") or str(Path.home())) / "Desktop"
        if desktop_dir.exists():
            destinations.append(desktop_dir / "Bewerbungs-Manager.lnk")

    ps_lines = ["$ws = New-Object -ComObject WScript.Shell"]
    for lnk in destinations:
        lnk.parent.mkdir(parents=True, exist_ok=True)
        lnk_esc = str(lnk).replace("'", "''")
        tgt_esc = target.replace("'", "''")
        wd_esc = str(ROOT).replace("'", "''")
        ico_esc = f"{icon_path},0".replace("'", "''")
        desc_esc = "Bewerbungs-Manager — Stellen-Radar, CV-Studio & Anschreiben-Editor"
        ps_lines.extend(
            [
                f"$sc = $ws.CreateShortcut('{lnk_esc}')",
                f"$sc.TargetPath = '{tgt_esc}'",
                f"$sc.Arguments = '{args}'",
                f"$sc.WorkingDirectory = '{wd_esc}'",
                f"$sc.IconLocation = '{ico_esc}'",
                f"$sc.Description = '{desc_esc}'",
                "$sc.Save()",
            ]
        )

    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "\n".join(ps_lines)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"PowerShell-Verknüpfung fehlgeschlagen: {proc.stderr.strip() or proc.stdout.strip()}")
    return [str(p) for p in destinations]


def main() -> None:
    parser = argparse.ArgumentParser(description="Startet den Bewerbungs-Manager als native Windows-App.")
    parser.add_argument("--smoke-test", action="store_true", help="Öffnet das Fenster kurz zur Funktionsprüfung und beendet sich.")
    parser.add_argument("--browser", action="store_true", help="Im Standardbrowser statt als eigenes Desktop-Fenster öffnen.")
    parser.add_argument("--install-shortcut", action="store_true", help="Erstellt Startmenü- & Desktop-Verknüpfungen für die Windows-Suchleiste.")
    args = parser.parse_args()
    if args.install_shortcut:
        created = install_windows_shortcuts(include_desktop=True)
        for path_str in created:
            print(f"Verknüpfung erstellt: {path_str}")
        raise SystemExit(0)
    raise SystemExit(launch_desktop(smoke_test=args.smoke_test, browser_only=args.browser))


if __name__ == "__main__":
    main()
