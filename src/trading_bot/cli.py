
from __future__ import annotations
import threading, uvicorn, os
from .engine import Trader
from .webapp import app
def main():
    t = Trader(); thr = threading.Thread(target=t.run, daemon=True); thr.start()
    host = os.environ.get("WEB_HOST","0.0.0.0"); port = int(os.environ.get("WEB_PORT","8000"))
    uvicorn.run(app, host=host, port=port, log_level="info")
if __name__ == "__main__": main()
