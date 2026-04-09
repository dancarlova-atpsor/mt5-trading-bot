"""
Logging centralizat pentru Trading Bot MT5.
Logging in fisier + consola cu rotatie automata.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml


def setup_logger(name: str = "trading_bot", config_path: str = None) -> logging.Logger:
    """Configureaza si returneaza logger-ul principal."""
    # Defaults
    level = "INFO"
    log_file = "logs/trading_bot.log"
    max_size_mb = 10
    backup_count = 5

    # Incearca sa citeasca din config
    if config_path and os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        log_cfg = config.get("logging", {})
        level = log_cfg.get("level", level)
        log_file = log_cfg.get("file", log_file)
        max_size_mb = log_cfg.get("max_size_mb", max_size_mb)
        backup_count = log_cfg.get("backup_count", backup_count)

    # Creaza directorul de logs
    log_dir = os.path.dirname(log_file)
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Evita duplicate handlers
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handler consola
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Handler fisier cu rotatie
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
