
import logging, json, os, sys, time, pathlib
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3],
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload)
def configure_logging():
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear(); root.addHandler(h); root.setLevel(level)
    log_dir = pathlib.Path("data"); log_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
    fh.setFormatter(JsonFormatter()); root.addHandler(fh)
