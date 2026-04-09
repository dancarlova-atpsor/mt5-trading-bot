"""Functii utilitare."""

import os
from datetime import datetime, time
from pathlib import Path

import yaml


def load_config(config_name: str) -> dict:
    """Incarca un fisier YAML din directorul config/."""
    config_dir = Path(__file__).parent.parent / "config"
    config_path = config_dir / config_name
    if not config_path.exists():
        raise FileNotFoundError(f"Config inexistent: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def is_trading_hours(trading_hours: str, timezone_str: str = "Europe/Bucharest") -> bool:
    """Verifica daca suntem in orele de trading (format 'HH:MM-HH:MM')."""
    try:
        start_str, end_str = trading_hours.split("-")
        start = time.fromisoformat(start_str.strip())
        end = time.fromisoformat(end_str.strip())
        now = datetime.now().time()
        if start <= end:
            return start <= now <= end
        # Interval peste miezul noptii
        return now >= start or now <= end
    except (ValueError, AttributeError):
        return True  # Daca nu se poate parsa, permite trading


def pips_to_price(symbol: str, pips: float) -> float:
    """Converteste pips in diferenta de pret.
    JPY pairs: 1 pip = 0.01, altele: 1 pip = 0.0001.
    Aur (XAU): 1 pip = 0.01.
    """
    symbol_upper = symbol.upper()
    if "JPY" in symbol_upper:
        return pips * 0.01
    if "XAU" in symbol_upper:
        return pips * 0.01
    return pips * 0.0001


def get_mt5_password() -> str:
    """Citeste parola MT5 din environment variable."""
    password = os.environ.get("MT5_PASSWORD", "")
    if not password:
        raise EnvironmentError(
            "MT5_PASSWORD nu este setat. Seteaza env var: export MT5_PASSWORD='parola_ta'"
        )
    return password
