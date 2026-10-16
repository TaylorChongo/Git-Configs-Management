"""Git-powered configuration management CLI."""

import logging

__version__ = "0.1.0"

# A library should never print log messages unless the application asks for
# them; the CLI attaches real handlers in config_cli.audit.configure_logging.
logging.getLogger(__name__).addHandler(logging.NullHandler())
