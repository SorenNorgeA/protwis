"""
Tests for configuration validator.

Tests validate:
1. Valid column configurations pass validation
2. Invalid configurations are caught
3. Schema references are validated
4. Enum values are enforced
"""

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from table_provider.configuration_provider.config_validator import ConfigValidator


class TestConfigValidator(TestCase):
    """Test configuration validator."""

    def setUp(self):
        """Set up test fixtures."""
        self.validator = ConfigValidator(schema_dir=Path(__file__).parent.parent)
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def write_config(self, filename: str, config: dict) -> Path:
        """Write a config dict to a temporary file."""
        path = Path(self.temp_dir.name) / filename
        with open(path, 'w') as f:
            json.dump(config, f)
        return path

    # Column configuration tests

    def test_valid_simple_column_config(self):
        """Valid simple column passes validation."""
        config = [
            {
                "json_id": "test_col",
                "data_type": "text",
                "title": "Test Column",
                "allow_filter": True
            }
        ]
        path = self.write_config('test_col.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertTrue(is_valid, f"Errors: {errors}")
        self.assertEqual(len(errors), 0)

    def test_missing_json_id(self):
        """Column missing json_id fails validation."""
        config = [
            {
                "data_type": "text",
                "title": "Test Column"
            }
        ]
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertFalse(is_valid)
        self.assertTrue(any('json_id' in e for e in errors))

    def test_missing_data_type(self):
        """Column missing data_type fails validation."""
        config = [
            {
                "json_id": "test_col",
                "title": "Test Column"
            }
        ]
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertFalse(is_valid)
        self.assertTrue(any('data_type' in e for e in errors))

    def test_invalid_data_type(self):
        """Column with invalid data_type fails validation."""
        config = [
            {
                "json_id": "test_col",
                "data_type": "invalid_type",
                "title": "Test Column"
            }
        ]
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertFalse(is_valid)
        self.assertTrue(any('invalid_type' in e for e in errors))

    def test_valid_data_types(self):
        """All valid data types pass validation."""
        valid_types = ['text', 'textselect', 'numeric', 'checkbox', 'weblinktext']
        for dtype in valid_types:
            config = [
                {
                    "json_id": f"col_{dtype}",
                    "data_type": dtype,
                    "title": "Test"
                }
            ]
            path = self.write_config(f'test_{dtype}.json', config)
            is_valid, errors = self.validator.validate_column_config(path)
            self.assertTrue(is_valid, f"Failed for type {dtype}: {errors}")

    def test_duplicate_json_ids(self):
        """Duplicate json_ids fail validation."""
        config = [
            {"json_id": "col1", "data_type": "text"},
            {"json_id": "col1", "data_type": "text"}
        ]
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertFalse(is_valid)
        self.assertTrue(any('Duplicate' in e for e in errors))

    def test_valid_precision(self):
        """Valid precision passes validation."""
        config = [
            {
                "json_id": "numeric_col",
                "data_type": "numeric",
                "precision": 2
            }
        ]
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertTrue(is_valid, f"Errors: {errors}")

    def test_invalid_precision(self):
        """Invalid precision fails validation."""
        config = [
            {
                "json_id": "numeric_col",
                "data_type": "numeric",
                "precision": "invalid"
            }
        ]
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertFalse(is_valid)
        self.assertTrue(any('precision' in e for e in errors))

    def test_valid_categorical_cellstyle(self):
        """Valid categorical cellStyle passes validation."""
        config = [
            {"json_id": "source_col", "data_type": "text"},
            {
                "json_id": "styled_col",
                "data_type": "text",
                "cellStyle": {
                    "type": "categorical",
                    "source": "source_col",
                    "rules": {
                        "true": "trueClass",
                        "false": "falseClass"
                    }
                }
            }
        ]
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertTrue(is_valid, f"Errors: {errors}")

    def test_categorical_missing_source(self):
        """Categorical cellStyle without source fails validation."""
        config = [
            {
                "json_id": "col",
                "data_type": "text",
                "cellStyle": {
                    "type": "categorical",
                    "rules": {"true": "class"}
                }
            }
        ]
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertFalse(is_valid)
        self.assertTrue(any('source' in e for e in errors))

    def test_categorical_invalid_source(self):
        """Categorical cellStyle with invalid source fails validation."""
        config = [
            {
                "json_id": "col",
                "data_type": "text",
                "cellStyle": {
                    "type": "categorical",
                    "source": "nonexistent_col",
                    "rules": {"true": "class"}
                }
            }
        ]
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertFalse(is_valid)
        self.assertTrue(any('not found' in e for e in errors))

    def test_valid_gradient_cellstyle(self):
        """Valid gradient_greyscale cellStyle passes validation."""
        config = [
            {
                "json_id": "numeric_col",
                "data_type": "numeric",
                "cellStyle": {
                    "type": "gradient_greyscale",
                    "source": "numeric_col",
                    "shadingMin": 0,
                    "shadingMax": 100,
                    "valueClampMin": 0,
                    "valueClampMax": 100
                }
            }
        ]
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertTrue(is_valid, f"Errors: {errors}")

    def test_gradient_with_invalid_numeric_field(self):
        """Gradient cellStyle with non-numeric field fails validation."""
        config = [
            {
                "json_id": "col",
                "data_type": "numeric",
                "cellStyle": {
                    "type": "gradient_greyscale",
                    "shadingMin": "invalid"
                }
            }
        ]
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertFalse(is_valid)
        self.assertTrue(any('shadingMin' in e for e in errors))

    def test_valid_cellinjectors_array(self):
        """cellInjectors as array passes validation."""
        config = [
            {
                "json_id": "col",
                "data_type": "text",
                "cellInjectors": ["injector1", "injector2"]
            }
        ]
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertTrue(is_valid, f"Errors: {errors}")

    def test_valid_cellinjectors_string(self):
        """cellInjectors as string passes validation (legacy format)."""
        config = [
            {
                "json_id": "col",
                "data_type": "text",
                "cellInjectors": "injector1"
            }
        ]
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertTrue(is_valid, f"Errors: {errors}")

    def test_invalid_cellinjectors(self):
        """cellInjectors with invalid type fails validation."""
        config = [
            {
                "json_id": "col",
                "data_type": "text",
                "cellInjectors": 123
            }
        ]
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertFalse(is_valid)
        self.assertTrue(any('cellInjectors' in e for e in errors))

    def test_invalid_cellinjectors_array(self):
        """cellInjectors array with non-string values fails validation."""
        config = [
            {
                "json_id": "col",
                "data_type": "text",
                "cellInjectors": ["injector1", 123]
            }
        ]
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertFalse(is_valid)
        self.assertTrue(any('only strings' in e for e in errors))

    # Datatable configuration tests

    def test_valid_datatable_config(self):
        """Valid datatable config passes validation."""
        config = {
            "tableId": "test_table",
            "serverSide": True,
            "pageLength": 50
        }
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_datatable_config(path)
        self.assertTrue(is_valid, f"Errors: {errors}")

    def test_datatable_missing_tableId(self):
        """Datatable missing tableId fails validation."""
        config = {
            "serverSide": True
        }
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_datatable_config(path)
        self.assertFalse(is_valid)
        self.assertTrue(any('tableId' in e for e in errors))

    def test_datatable_must_be_object(self):
        """Datatable config must be object, not array."""
        config = []
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_datatable_config(path)
        self.assertFalse(is_valid)
        self.assertTrue(any('object' in e for e in errors))

    def test_column_config_must_be_array(self):
        """Column config must be array, not object."""
        config = {"json_id": "col"}
        path = self.write_config('test.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertFalse(is_valid)
        self.assertTrue(any('array' in e for e in errors))

    def test_missing_config_file(self):
        """Missing config file returns error."""
        path = Path(self.temp_dir.name) / 'nonexistent.json'
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertFalse(is_valid)
        self.assertTrue(any('Failed to load' in e for e in errors))

    def test_invalid_json_file(self):
        """Invalid JSON in file returns error."""
        path = Path(self.temp_dir.name) / 'invalid.json'
        with open(path, 'w') as f:
            f.write('{ invalid json }')
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertFalse(is_valid)
        self.assertTrue(any('Failed to load' in e for e in errors))

    def test_complete_pdb_like_config(self):
        """Complete PDB-like configuration validates successfully."""
        config = [
            {
                "column_group": "Structure",
                "json_id": "pdb_id",
                "title": "PDB ID",
                "data_type": "text",
                "allow_filter": True
            },
            {
                "column_group": "Structure",
                "json_id": "resolution",
                "title": "Resolution",
                "data_type": "numeric",
                "precision": 1,
                "allow_filter": True,
                "cellStyle": {
                    "type": "categorical",
                    "source": "resolution_best",
                    "rules": {
                        "true": "bestTrue",
                        "false": "bestFalse"
                    }
                }
            },
            {
                "column_group": "Structure",
                "json_id": "resolution_best",
                "title": "Best Resolution",
                "data_type": "text",
                "visible": False,
                "allow_filter": True
            },
            {
                "column_group": "State",
                "json_id": "tm6_angle",
                "title": "TM6 Tilt",
                "data_type": "numeric",
                "precision": 0,
                "allow_filter": True,
                "cellStyle": {
                    "type": "gradient_greyscale",
                    "source": "tm6_angle",
                    "shadingMin": 0,
                    "shadingMax": 30,
                    "valueClampMin": 30,
                    "valueClampMax": 100
                }
            }
        ]
        path = self.write_config('pdb_like.json', config)
        is_valid, errors = self.validator.validate_column_config(path)
        self.assertTrue(is_valid, f"Errors: {errors}")
