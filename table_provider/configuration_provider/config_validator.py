"""
Configuration validation for TableManager column and datatable configurations.

Validates JSON configuration files against JSON schemas to ensure:
1. All required fields are present
2. Data types are correct
3. References to other columns are valid
4. Enum values are correct
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class ConfigValidator:
    """Validates column and datatable configurations against JSON schemas."""

    def __init__(self, schema_dir: Optional[Path] = None):
        """Initialize validator with schema files.

        Args:
            schema_dir: Directory containing schema files. Defaults to current directory.
        """
        if schema_dir is None:
            schema_dir = Path(__file__).parent

        self.schema_dir = schema_dir
        self.column_schema = self._load_schema('column_config_schema.json')
        self.datatable_schema = self._load_schema('datatable_config_schema.json')

    def _load_schema(self, schema_filename: str) -> Dict[str, Any]:
        """Load a JSON schema file."""
        schema_path = self.schema_dir / schema_filename
        try:
            with open(schema_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.warning(f"Schema file not found: {schema_path}")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in schema file {schema_path}: {e}")
            return {}

    def validate_column_config(self, config_file: Path) -> Tuple[bool, List[str]]:
        """Validate a column configuration file.

        Args:
            config_file: Path to column configuration JSON file.

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            return False, [f"Failed to load config file: {e}"]

        errors = []

        # Validate it's a list
        if not isinstance(config, list):
            return False, ["Column config must be an array of column objects"]

        # Track json_ids to detect duplicates
        json_ids = set()
        data_type_map = {}  # json_id -> data_type

        for i, column in enumerate(config):
            column_errors = self._validate_column(column, i)
            if column_errors:
                errors.extend(column_errors)

            # Check for duplicate json_ids
            json_id = column.get('json_id')
            if json_id:
                if json_id in json_ids:
                    errors.append(f"Duplicate json_id '{json_id}' at index {i}")
                else:
                    json_ids.add(json_id)
                    data_type_map[json_id] = column.get('data_type')

        # Validate cellStyle references
        for i, column in enumerate(config):
            cell_style = column.get('cellStyle')
            if cell_style and isinstance(cell_style, dict):
                style_errors = self._validate_cell_style(cell_style, data_type_map, i)
                errors.extend(style_errors)

        return len(errors) == 0, errors

    def _validate_column(self, column: Dict[str, Any], index: int) -> List[str]:
        """Validate a single column configuration."""
        errors = []

        # Check required fields
        if 'json_id' not in column:
            errors.append(f"Column at index {index}: missing required field 'json_id'")

        if 'data_type' not in column:
            errors.append(f"Column at index {index}: missing required field 'data_type'")

        json_id = column.get('json_id', f'<unknown at {index}>')

        # Validate data_type enum
        valid_types = {'text', 'textselect', 'numeric', 'checkbox', 'weblinktext'}
        data_type = column.get('data_type')
        if data_type and data_type not in valid_types:
            errors.append(f"Column '{json_id}': invalid data_type '{data_type}'")

        # Validate cellStyle structure if present
        if 'cellStyle' in column and not isinstance(column['cellStyle'], dict):
            errors.append(f"Column '{json_id}': cellStyle must be an object")

        # Validate precision (numeric only)
        precision = column.get('precision')
        if precision is not None:
            if not isinstance(precision, int) or precision < 0:
                errors.append(f"Column '{json_id}': precision must be a non-negative integer")

        # Validate cellInjectors format
        injectors = column.get('cellInjectors')
        if injectors:
            if not isinstance(injectors, (list, str)):
                errors.append(f"Column '{json_id}': cellInjectors must be an array or string")
            elif isinstance(injectors, list):
                for inj in injectors:
                    if not isinstance(inj, str):
                        errors.append(f"Column '{json_id}': cellInjectors array must contain only strings")

        # Validate filterHeaderInjector format
        fh_injector = column.get('filterHeaderInjector')
        if fh_injector:
            if not isinstance(fh_injector, (list, str)):
                errors.append(f"Column '{json_id}': filterHeaderInjector must be an array or string")
            elif isinstance(fh_injector, list):
                for inj in fh_injector:
                    if not isinstance(inj, str):
                        errors.append(f"Column '{json_id}': filterHeaderInjector array must contain only strings")

        return errors

    def _validate_cell_style(self, cell_style: Dict[str, Any], data_type_map: Dict[str, str],
                            index: int) -> List[str]:
        """Validate cellStyle configuration."""
        errors = []
        style_type = cell_style.get('type')

        if not style_type:
            errors.append(f"Column at index {index}: cellStyle missing required field 'type'")
            return errors

        if style_type == 'categorical':
            if 'source' not in cell_style:
                errors.append(f"Column at index {index}: categorical cellStyle missing required field 'source'")
            else:
                source = cell_style['source']
                if source not in data_type_map:
                    errors.append(f"Column at index {index}: cellStyle source '{source}' not found in column definitions")

            rules = cell_style.get('rules')
            if rules and not isinstance(rules, dict):
                errors.append(f"Column at index {index}: cellStyle rules must be an object")

        elif style_type == 'gradient_greyscale':
            # Optional source field, validated if present
            source = cell_style.get('source')
            if source and source not in data_type_map:
                errors.append(f"Column at index {index}: cellStyle source '{source}' not found in column definitions")

            # Validate numeric fields
            for field in ['shadingMin', 'shadingMax', 'valueClampMin', 'valueClampMax']:
                value = cell_style.get(field)
                if value is not None and not isinstance(value, (int, float)):
                    errors.append(f"Column at index {index}: cellStyle {field} must be numeric")

        else:
            errors.append(f"Column at index {index}: unknown cellStyle type '{style_type}'")

        return errors

    def validate_datatable_config(self, config_file: Path) -> Tuple[bool, List[str]]:
        """Validate a datatable configuration file.

        Args:
            config_file: Path to datatable configuration JSON file.

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            return False, [f"Failed to load config file: {e}"]

        errors = []

        # Validate it's an object
        if not isinstance(config, dict):
            return False, ["Datatable config must be an object"]

        # Check required fields
        if 'tableId' not in config:
            errors.append("Datatable config: missing required field 'tableId'")

        return len(errors) == 0, errors
