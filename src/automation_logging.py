"""Bounded diagnostic logs with a correlation ID for each invocation."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import uuid


def configure(project_dir):
    folder = Path(project_dir) / 'logs'
    folder.mkdir(exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    logger = logging.getLogger('aces')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    class RunFilter(logging.Filter):
        def filter(self, record):
            record.run_id = run_id
            return True

    formatter = logging.Formatter('%(asctime)s %(levelname)s run=%(run_id)s %(message)s',
                                  datefmt='%Y-%m-%d %H:%M:%S %z')
    for handler in [RotatingFileHandler(folder / 'automation.log', maxBytes=5_000_000,
                                       backupCount=5, encoding='utf-8'),
                    logging.StreamHandler(sys.stdout)]:
        handler.setFormatter(formatter)
        handler.addFilter(RunFilter())
        logger.addHandler(handler)
    return logger
