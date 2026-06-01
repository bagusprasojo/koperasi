import os
import sys
from pathlib import Path

from print_server import run_forever


def _load_env_file():
    env_file_raw = os.getenv('PRINT_AGENT_ENV_FILE', '').strip()
    env_path = None
    if env_file_raw:
        candidate = Path(env_file_raw).expanduser()
        if candidate.is_file():
            env_path = candidate
    if env_path is None:
        if getattr(sys, 'frozen', False):
            exe_env = Path(sys.executable).resolve().parent / '.env'
            if exe_env.is_file():
                env_path = exe_env
        if env_path is None:
            local_env = Path(__file__).resolve().parent / '.env'
            env_path = local_env if local_env.is_file() else None
    if not env_path:
        return
    for line in env_path.read_text(encoding='utf-8').splitlines():
        raw = line.strip()
        if not raw or raw.startswith('#') or '=' not in raw:
            continue
        key, value = raw.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main():
    _load_env_file()
    host = os.getenv('PRINT_BRIDGE_HOST', '127.0.0.1')
    port = int(os.getenv('PRINT_BRIDGE_PORT', '17971'))
    printer_name = os.getenv('PRINTER_NAME', '').strip()
    if not printer_name:
        try:
            import win32print  # type: ignore
            printer_name = win32print.GetDefaultPrinter() or ''
        except Exception:
            printer_name = ''
    run_forever(host=host, port=port, printer_name=printer_name)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
