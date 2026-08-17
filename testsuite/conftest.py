"""pytest from repo root: suite-global lab-vakt."""

import sys
from pathlib import Path

_tools = Path(__file__).resolve().parent / "tools"
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))

from test_lab_guard import install_lab_guard  # noqa: E402

install_lab_guard(suite_global=True)


def pytest_configure(config):
    install_lab_guard(suite_global=True)
