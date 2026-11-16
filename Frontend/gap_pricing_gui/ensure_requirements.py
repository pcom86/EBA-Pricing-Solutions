from __future__ import annotations

import importlib.metadata as metadata
import re
import subprocess
import sys
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
REQ_FILE = HERE / "requirements.txt"

FALLBACK_PACKAGES = [
    "PySide6",
    "pandas",
    "polars",
    "openpyxl",
    "pyodbc",
    "python-calamine",
    "rapidfuzz",
    "xlrd",
    "xlsxwriter",
]

NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+")


def parse_requirement_names(req_file: Path) -> list[str]:
    names = []
    for raw_line in req_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.split("#", 1)[0].split(";", 1)[0].strip()
        match = NAME_RE.match(line)
        if match:
            names.append(match.group(0))
    return names


def find_missing(package_names: list[str]) -> list[str]:
    missing = []
    for name in package_names:
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
    return missing


def pip_install(args: list[str]) -> int:
    cmd = [sys.executable, "-m", "pip", "install",
           "--disable-pip-version-check", "-q", *args]
    logger.info(f"Installing: {' '.join(args)}")
    return subprocess.call(cmd)


def main() -> int:
    if REQ_FILE.exists():
        logger.info(f"Using requirements file: {REQ_FILE}")
        package_names = parse_requirement_names(REQ_FILE)
        missing = find_missing(package_names)
        if not missing:
            logger.info("All required packages are already installed.")
            return 0
        logger.info(f"Missing/outdated packages: {', '.join(missing)}")
        rc = pip_install(["-r", str(REQ_FILE)])
    else:
        logger.info(f"NOTE: {REQ_FILE} not found - using built-in fallback package list.")
        missing = find_missing(FALLBACK_PACKAGES)
        if not missing:
            logger.info("All required packages are already installed.")
            return 0
        logger.info(f"Missing packages: {', '.join(missing)}")
        rc = pip_install(FALLBACK_PACKAGES)

    if rc != 0:
        logger.info("\n*** ERROR: pip install failed. Check your internet/VPN connection and try again. ***")
        return rc

    logger.info("Packages installed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
