"""Read and write JSON and YAML configuration files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from config_cli.exceptions import ConfigCliError, ConfigNotFound, InvalidConfigError

ConfigData = dict[str, Any] | list[Any]


class ConfigParser:
    """Parse and serialize configuration files based on their file extension."""

    _JSON_SUFFIX = ".json"
    _YAML_SUFFIXES = {".yaml", ".yml"}

    def read(self, path: Path) -> ConfigData:
        """Read configuration data from a JSON or YAML file.

        Args:
            path: The configuration file to read.

        Returns:
            The parsed configuration. An empty file returns an empty dict.

        Raises:
            ConfigNotFound: If the file does not exist.
            InvalidConfigError: If the format is unsupported or the content is
                not valid JSON/YAML, or is not an object or list.
            ConfigCliError: If the file exists but cannot be read.
        """
        self._ensure_supported_format(path)

        try:
            with path.open(encoding="utf-8") as config_file:
                if path.suffix.lower() == self._JSON_SUFFIX:
                    data = json.load(config_file)
                else:
                    data = yaml.safe_load(config_file)
        except FileNotFoundError as error:
            raise ConfigNotFound(path) from error
        except (json.JSONDecodeError, yaml.YAMLError) as error:
            raise InvalidConfigError(path, "content could not be parsed") from error
        except OSError as error:
            raise ConfigCliError(
                f"Could not read configuration file: {path}"
            ) from error

        if data is None:
            return {}
        if not isinstance(data, (dict, list)):
            raise InvalidConfigError(path, "top level must be an object or a list")
        return data

    def write(self, path: Path, data: ConfigData) -> None:
        """Write configuration data to a JSON or YAML file.

        Missing parent directories are created automatically.

        Args:
            path: The configuration file to write.
            data: The configuration to save.

        Raises:
            InvalidConfigError: If the format is unsupported or the data cannot
                be converted to JSON/YAML.
            ConfigCliError: If the file cannot be written.
        """
        # Convert first, so a serialization error never leaves a half-written
        # or empty file behind.
        text = self.dumps(path, data)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="\n") as config_file:
                config_file.write(text)
        except OSError as error:
            raise ConfigCliError(
                f"Could not write configuration file: {path}"
            ) from error

    def dumps(self, path: Path, data: ConfigData) -> str:
        """Convert configuration data to text in the format ``path`` uses.

        Nothing is written to disk; ``path`` only selects JSON or YAML.

        Args:
            path: A file name whose extension picks the format.
            data: The configuration to convert.

        Returns:
            The formatted text, ending with a newline.

        Raises:
            InvalidConfigError: If the format is unsupported or the data cannot
                be converted to JSON/YAML.
        """
        self._ensure_supported_format(path)
        try:
            if path.suffix.lower() == self._JSON_SUFFIX:
                return json.dumps(data, indent=2) + "\n"
            return yaml.safe_dump(data, sort_keys=False)
        except (TypeError, yaml.YAMLError) as error:
            raise InvalidConfigError(path, "data could not be serialized") from error

    def supports(self, path: Path) -> bool:
        """Return whether ``path`` has a file extension this parser can handle."""
        suffix = path.suffix.lower()
        return suffix == self._JSON_SUFFIX or suffix in self._YAML_SUFFIXES

    def _ensure_supported_format(self, path: Path) -> None:
        """Raise InvalidConfigError when a path does not use a supported format."""
        if not self.supports(path):
            raise InvalidConfigError(
                path,
                f"unsupported format '{path.suffix or '<none>'}', "
                "use .json, .yaml, or .yml",
            )
