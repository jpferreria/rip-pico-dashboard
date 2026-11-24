"""
Minimal .env loader for MicroPython (Pico W / Pico 2 W).

MicroPython on bare-metal boards does not have os.environ, so environment
variables are stored in a `.env` file on the board's flash filesystem.
"""

_vars = {}
_loaded = False


def load_env(path=".env"):
    global _loaded
    _vars.clear()
    try:
        with open(path, "r") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                    val = val[1:-1]
                _vars[key] = val
    except OSError:
        pass
    _loaded = True
    return _vars


def getenv(key, default=None):
    if not _loaded:
        load_env()
    return _vars.get(key, default)
