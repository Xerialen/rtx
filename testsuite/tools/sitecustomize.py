"""site.py hook: lab-vakt when testsuite/tools is a sitedir.

Activate with: python3 -c "import site; site.addsitedir('testsuite/tools')"
or PYTHONPATH pointing at a sitedir that contains this file.
Importing this module (or test_lab_guard) is enough.
"""

try:
    from test_lab_guard import install_lab_guard
    install_lab_guard(suite_global=True)
except Exception:
    pass
