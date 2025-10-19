from __future__ import annotations

import logging, os, threading

from .engine import Trader
from .webapp import app

def _configure_logging():
    level = getattr(logging, (os.environ.get("LOG_LEVEL","INFO")).upper(), logging.INFO)
    fmt = '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
    logging.basicConfig(level=level, format=fmt)

def _start_web():
    import uvicorn
    host = os.environ.get("WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("WEB_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level=os.environ.get("UVICORN_LOG_LEVEL", "info"))

def run() -> None:
    _configure_logging()

    # start web in a background thread
    t_web = threading.Thread(target=_start_web, daemon=True)
    t_web.start()

    # start engine
    t = Trader()
    if hasattr(t, "self_test"):
        try:
            t.self_test()
        except Exception as e:
            logging.getLogger("cli").error("self_test failed: %s", e)
    t.run()

if __name__ == "__main__":
    run()
