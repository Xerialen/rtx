"""testsuite.tools: install lab-vakt only under unittest/pytest."""

import sys

_testing = (
    "unittest" in sys.modules
    or "pytest" in sys.modules
    or any("unittest" in a or "pytest" in a for a in sys.argv)
)
if _testing:
    try:
        from .test_lab_guard import install_lab_guard
    except ImportError:
        from test_lab_guard import install_lab_guard
    install_lab_guard(suite_global=True)
