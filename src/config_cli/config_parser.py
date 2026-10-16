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
        self._ensure_supported_format(path)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="\n") as config_file:
                if path.suffix.lower() == self._JSON_SUFFIX:
                    json.dump(data, config_file, indent=2)
                    config_file.write("\n")
                else:
                    yaml.safe_dump(data, config_file, sort_keys=False)
        except (TypeError, yaml.YAMLError) as error:
            raise InvalidConfigError(path, "data could not be serialized") from error
        except OSError as error:
            raise ConfigCliError(
                f"Could not write configuration file: {path}"
            ) from error

    def _ensure_supported_format(self, path: Path) -> None:
        """Raise InvalidConfigError when a path does not use a supported format."""
        suffix = path.suffix.lower()
        if suffix != self._JSON_SUFFIX and suffix not in self._YAML_SUFFIXES:
            raise InvalidConfigError(
                path,
                f"unsupported format '{path.suffix or '<none>'}', "
                "use .json, .yaml, or .yml",
            )
