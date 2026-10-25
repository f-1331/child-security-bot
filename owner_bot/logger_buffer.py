import collections
import logging
from typing import List

# Ring buffer storing up to 100 recent log lines in memory
recent_logs: collections.deque = collections.deque(maxlen=100)


class BufferLogHandler(logging.Handler):
    """Memory buffer handler that captures recent log messages."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            recent_logs.append(msg)
        except Exception:
            pass


_buffer_handler = BufferLogHandler()
_buffer_handler.setLevel(logging.DEBUG)
_buffer_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s : %(message)s", datefmt="%H:%M:%S")
)

_root_logger = logging.getLogger()
if _root_logger.level == logging.NOTSET or _root_logger.level > logging.INFO:
    _root_logger.setLevel(logging.INFO)
_root_logger.addHandler(_buffer_handler)


def record_log(msg: str) -> None:
    """Manually append a log message to the buffer."""
    recent_logs.append(msg)


def get_recent_logs(limit: int = 30) -> List[str]:
    """Retrieve up to limit recent log records."""
    logs_list = list(recent_logs)
    if not logs_list:
        return ["Hozircha tizim loglari mavjud emas."]
    return logs_list[-limit:] if len(logs_list) > limit else logs_list
