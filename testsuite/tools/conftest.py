"""pytest: suite-global lab-vakt for every test under testsuite/tools."""

from test_lab_guard import install_lab_guard

install_lab_guard(suite_global=True)


def pytest_configure(config):
    install_lab_guard(suite_global=True)


def pytest_sessionstart(session):
    install_lab_guard(suite_global=True)
