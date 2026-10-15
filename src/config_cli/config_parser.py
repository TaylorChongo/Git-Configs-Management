"""Read and write JSON and YAML configuration files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

ConfigData = dict[str, Any] | list[Any]


class ConfigParser:
    """Parse and serialize configuration files based on their file extension."""

    _JSON_SUFFIX = ".json"
    _YAML_SUFFIXES = {".yaml", ".yml"}

    def read(self, path: Path) -> ConfigData:
        """Read configuration data from a JSON or YAML file."""
        self._ensure_supported_format(path)

        try:
            with path.open(encoding="utf-8") as config_file:
                if path.suffix.lower() == self._JSON_SUFFIX:
                    data = json.load(config_file)
                else:
                    data = yaml.safe_load(config_file)
        except (OSError, json.JSONDecodeError, yaml.YAMLError) as error:
            raise ValueError(f"Could not read configuration file: {path}") from error

        if data is None:
            return {}
        if not isinstance(data, (dict, list)):
            raise ValueError("Configuration data must be a JSON/YAML object or list")
        return data

    def write(self, path: Path, data: ConfigData) -> None:
        """Write configuration data to a JSON or YAML file."""
        self._ensure_supported_format(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with path.open("w", encoding="utf-8", newline="\n") as config_file:
                if path.suffix.lower() == self._JSON_SUFFIX:
                    json.dump(data, config_file, indent=2)
                    config_file.write("\n")
                else:
                    yaml.safe_dump(data, config_file, sort_keys=False)
        except (OSError, TypeError, yaml.YAMLError) as error:
            raise ValueError(f"Could not write configuration file: {path}") from error

    def _ensure_supported_format(self, path: Path) -> None:
        """Raise an error when a path does not use a supported format."""
        suffix = path.suffix.lower()
        if suffix != self._JSON_SUFFIX and suffix not in self._YAML_SUFFIXES:
            raise ValueError(
                f"Unsupported configuration format: {path.suffix or '<none>'}. "
                "Use .json, .yaml, or .yml."
            )
