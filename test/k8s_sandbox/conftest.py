import logging

from inspect_ai._util.constants import SANDBOX


def pytest_configure(config):
    # Set the log level to SANDBOX for the tests in this directory.
    # This lets us see log messages when used with pytest -s.
    logging.basicConfig(level=SANDBOX)
