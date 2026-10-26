import json
import pathlib

from django.test import SimpleTestCase, TestCase
from django.db.models import Q
from table_provider.serializers import (
    GpcrStatisticsSummarySerializer, PdbStructureSummarySerializer, TABLE_SERIALIZERS,
)
from table_provider.serverside.filters import TextFilter, NumericRangeFilter, BooleanFilter, FilterSet, FilterError
from table_provider.serverside.querystringprocessing import DatatablesQueryStringProcessor, QueryStringProcessingError
from rest_framework import serializers


class TextFilterTestCase(TestCase):
    """Tests for TextFilter.format_query() to ensure correct Q object generation."""

    def test_single_exact_value_only(self):
        """Single exact match value should return exact equality Q."""
        f = TextFilter("state", ["Active"])
        self.assertEqual(f.format_query(), Q(state__in=["Active"]))

    def test_multiple_exact_values_only(self):
        """Multiple exact matches should return __in Q."""
        f = TextFilter("state", ["Active", "Inactive"])
        self.assertEqual(f.format_query(), Q(state__in=["Active", "Inactive"]))

    def test_single_contains_only(self):
        """Single contains value should use icontains."""
        f = TextFilter("state", contains=["inter"])
        self.assertEqual(f.format_query(), Q(state__icontains="inter"))

    def test_multiple_contains_only(self):
        """Multiple contains values should be ORed."""
        f = TextFilter("state", contains=["inter", "act"])
        self.assertEqual(f.format_query(), Q(state__icontains="inter") | Q(state__icontains="act"))

    def test_single_exact_plus_single_contains(self):
        """Single exact value AND single contains value should both be included (ORed).

        This is the bug B2 regression test. When there's exactly one exact match
        and at least one tag, both should be in the result.
        """
        f = TextFilter("state", ["Active"], ["inter"])
        self.assertEqual(f.format_query(), Q(state__in=["Active"]) | Q(state__icontains="inter"))

    def test_multiple_exact_plus_multiple_contains(self):
        """Multiple exact values and multiple contains values should all be ORed."""
        f = TextFilter("state", ["Active", "Inactive"], ["inter", "act"])
        expected = (
            Q(state__in=["Active", "Inactive"]) |
            Q(state__icontains="inter") |
            Q(state__icontains="act")
        )
        self.assertEqual(f.format_query(), expected)

    def test_empty_filters(self):
        """No values should return empty Q, and an empty contains term must not match every row."""
        self.assertEqual(TextFilter("state").format_query(), Q())
        self.assertEqual(TextFilter("state", contains=[""]).format_query(), Q())

    def test_special_characters_used_verbatim(self):
        """Values are not parsed as regex, so brackets, pipes and backslashes are matched as given (B2, B5)."""
        f = TextFilter("state", ["(A|B)\\x"], ["^.*$"])
        self.assertEqual(f.format_query(), Q(state__in=["(A|B)\\x"]) | Q(state__icontains="^.*$"))


class NumericRangeFilterTestCase(TestCase):
    """Tests for NumericRangeFilter.format_query() to ensure correct Q object generation.

    Tests that bounds are inclusive (>=, <=) matching client-side behavior.
    """

    def test_both_bounds_open(self):
        """No bounds should return empty Q (no filtering)."""
        f = NumericRangeFilter("resolution", None, None)
        self.assertEqual(f.format_query(), Q())

    def test_min_only_inclusive(self):
        """Min-only filter should use >= (inclusive).

        This is the B3 regression test. Ensures server-side and client-side
        both use inclusive bounds (>= and <=, not > and <).
        """
        f = NumericRangeFilter("resolution", 2.0, None)
        self.assertEqual(f.format_query(), Q(resolution__gte=2.0))

    def test_max_only_inclusive(self):
        """Max-only filter should use <= (inclusive)."""
        f = NumericRangeFilter("resolution", None, 3.5)
        self.assertEqual(f.format_query(), Q(resolution__lte=3.5))

    def test_both_bounds_inclusive(self):
        """Both bounds should use >= and <= (inclusive)."""
        f = NumericRangeFilter("resolution", 2, 3.5)
        self.assertEqual(f.format_query(), Q(resolution__gte=2) & Q(resolution__lte=3.5))


class BooleanFilterTestCase(TestCase):
    """Tests for BooleanFilter.format_query()."""

    def test_single_value(self):
        self.assertEqual(BooleanFilter("is_complex", True).format_query(), Q(is_complex=True))

    def test_collection_of_values(self):
        self.assertEqual(BooleanFilter("is_complex", {True, False}).format_query(), Q(is_complex__in=[False, True]))


class FilterSetParseFiltersTestCase(TestCase):
    """Tests for FilterSet.parse_filters() reading the JSON filter in each column's search value (A2)."""

    def _make_mock_serializer(self):
        class MockSerializer(serializers.Serializer):
            @staticmethod
            def column_datatypes():
                return {'resolution': 'numeric', 'state': 'text', 'best': 'boolean', 'checkbox': 'checkbox'}

            @staticmethod
            def source_mapping():
                return {'resolution': 'resolution', 'state': 'state_name', 'best': 'resolution_best', 'checkbox': 'checkbox'}

        return MockSerializer

    def _queries(self, data, spec):
        fs = FilterSet(None, self._make_mock_serializer())
        value = spec if isinstance(spec, str) else json.dumps(spec)
        fs.query_parameters = {'columns': [{'data': data, 'search': {'value': value, 'regex': 'false'}}]}
        fs.parse_filters()
        return [f.format_query() for f in fs.get_filters()]

    def test_empty_search_value_is_no_filter(self):
        self.assertEqual(self._queries('state', ''), [])

    def test_text_filter_uses_source_mapping(self):
        self.assertEqual(self._queries('state', {'in': ['A|B'], 'contains': ['(x']}),
                         [Q(state_name__in=['A|B']) | Q(state_name__icontains='(x')])

    def test_text_filter_keys_optional(self):
        self.assertEqual(self._queries('state', {'contains': ['x']}), [Q(state_name__icontains='x')])

    def test_numeric_filter(self):
        self.assertEqual(self._queries('resolution', {'min': 2, 'max': None}), [Q(resolution__gte=2)])

    def test_numeric_filter_without_bounds_is_no_filter(self):
        self.assertEqual(self._queries('resolution', {'min': None, 'max': None}), [])

    def test_boolean_filter_from_text_shape(self):
        self.assertEqual(self._queries('best', {'in': ['True', False], 'contains': []}),
                         [Q(resolution_best__in=[False, True])])

    def test_boolean_filter_contains_matches_names(self):
        self.assertEqual(self._queries('best', {'in': [], 'contains': ['fa']}), [Q(resolution_best__in=[False])])
        self.assertEqual(self._queries('best', {'in': [], 'contains': ['E']}), [Q(resolution_best__in=[False, True])])
        self.assertEqual(self._queries('best', {'in': [], 'contains': ['zz']}), [Q(resolution_best__in=[])])

    def test_boolean_filter_empty_is_no_filter(self):
        self.assertEqual(self._queries('best', {'in': [], 'contains': []}), [])

    def test_boolean_filter_invalid_value_raises(self):
        with self.assertRaises(FilterError):
            self._queries('best', {'in': ['yes']})

    def test_checkbox_column_ignored(self):
        self.assertEqual(self._queries('checkbox', 'anything'), [])


class FilterSetOrderingTestCase(TestCase):
    """Tests for FilterSet.parse_ordering() to ensure deterministic pagination."""

    def _make_mock_serializer(self):
        """Create a minimal mock serializer for testing."""
        class MockSerializer(serializers.Serializer):
            uniprot = serializers.CharField()
            state = serializers.CharField()

            @staticmethod
            def column_datatypes():
                return {
                    'uniprot': 'text',
                    'state': 'text',
                }

            @staticmethod
            def source_mapping():
                return {
                    'uniprot': 'uniprot_entry_name',
                    'state': 'state',
                }

        return MockSerializer

    def test_no_ordering_defaults_to_pk(self):
        """When no ordering is specified, default to pk ordering."""
        fs = FilterSet(None, self._make_mock_serializer())
        fs.query_parameters = {'columns': []}
        # 'order' not in query_parameters
        fs.parse_ordering()
        self.assertEqual(fs.get_ordering(), ['pk'])

    def test_single_column_ordering_adds_pk_tiebreaker(self):
        """Single column ordering should have pk appended as tie-breaker.

        This is the B4 regression test. Ensures that when ordering by a column
        with duplicate values (like uniprot_entry_name), rows are sorted
        deterministically by pk to prevent pagination duplicates/skips.
        """
        fs = FilterSet(None, self._make_mock_serializer())
        fs.query_parameters = {
            'columns': [
                {'data': 'uniprot'},
                {'data': 'state'},
            ],
            'order': [
                {'column': 0, 'dir': 'asc'},
            ]
        }
        fs.parse_ordering()
        # Should have uniprot_entry_name ascending, then pk ascending
        self.assertEqual(fs.get_ordering(), ['uniprot_entry_name', 'pk'])

    def test_multiple_column_ordering_adds_pk_tiebreaker(self):
        """Multiple column ordering should have pk appended as final tie-breaker."""
        fs = FilterSet(None, self._make_mock_serializer())
        fs.query_parameters = {
            'columns': [
                {'data': 'uniprot'},
                {'data': 'state'},
            ],
            'order': [
                {'column': 0, 'dir': 'asc'},
                {'column': 1, 'dir': 'desc'},
            ]
        }
        fs.parse_ordering()
        # Should have both columns, then pk
        self.assertEqual(fs.get_ordering(), ['uniprot_entry_name', '-state', 'pk'])

    def test_pk_not_duplicated(self):
        """If pk is already in the ordering, don't add it again."""
        fs = FilterSet(None, self._make_mock_serializer())
        # Simulate if somehow pk was already in the mapping
        fs.query_parameters = {'columns': [], 'order': []}
        fs._ordering = ['pk']  # Manually set
        fs.parse_ordering()
        # Should not duplicate pk
        self.assertEqual(fs.get_ordering().count('pk'), 1)


class FilterErrorTestCase(TestCase):
    """Malformed request parameters must raise FilterError (a client error) rather than crash (B17)."""

    def _make_mock_serializer(self):
        class MockSerializer(serializers.Serializer):
            @staticmethod
            def column_datatypes():
                return {'resolution': 'numeric', 'state': 'text'}

            @staticmethod
            def source_mapping():
                return {'resolution': 'resolution', 'state': 'state'}

        return MockSerializer

    def _filterset(self, query_parameters):
        fs = FilterSet(None, self._make_mock_serializer())
        fs.query_parameters = query_parameters
        return fs

    def _column(self, data, value):
        return {'columns': [{'data': data, 'search': {'value': value}}]}

    def test_non_numeric_bound_raises(self):
        with self.assertRaises(FilterError):
            NumericRangeFilter("resolution", "2.0", None)

    def test_non_finite_bound_raises(self):
        with self.assertRaises(FilterError):
            NumericRangeFilter("resolution", float("nan"), 3.0)

    def test_boolean_bound_raises(self):
        with self.assertRaises(FilterError):
            NumericRangeFilter("resolution", True, None)

    def test_valid_bounds_accepted(self):
        NumericRangeFilter("resolution", -1.5, 3)

    def test_search_value_not_json_raises(self):
        for value in ('2.0~3.0', '(^Active$)', '{"in": '):
            fs = self._filterset(self._column('state', value))
            with self.assertRaises(FilterError):
                fs.parse_filters()

    def test_search_value_not_object_raises(self):
        fs = self._filterset(self._column('state', '["A"]'))
        with self.assertRaises(FilterError):
            fs.parse_filters()

    def test_unknown_filter_key_raises(self):
        fs = self._filterset(self._column('state', '{"regex": "A"}'))
        with self.assertRaises(FilterError):
            fs.parse_filters()

    def test_text_values_not_strings_raises(self):
        for value in ('{"in": "A"}', '{"in": [1]}', '{"contains": [null]}'):
            fs = self._filterset(self._column('state', value))
            with self.assertRaises(FilterError):
                fs.parse_filters()

    def test_non_numeric_range_via_filterset_raises(self):
        for value in ('{"min": "abc"}', '{"min": NaN}', '{"max": Infinity}'):
            fs = self._filterset(self._column('resolution', value))
            with self.assertRaises(FilterError):
                fs.parse_filters()

    def test_unknown_column_raises(self):
        fs = self._filterset(self._column('nonexistent', 'x'))
        with self.assertRaises(FilterError):
            fs.parse_filters()

    def test_column_missing_search_raises(self):
        fs = self._filterset({'columns': [{'data': 'state'}]})
        with self.assertRaises(FilterError):
            fs.parse_filters()

    def test_order_column_out_of_range_raises(self):
        fs = self._filterset({'columns': [{'data': 'state'}], 'order': [{'column': 5, 'dir': 'asc'}]})
        with self.assertRaises(FilterError):
            fs.parse_ordering()

    def test_order_column_not_integer_raises(self):
        fs = self._filterset({'columns': [{'data': 'state'}], 'order': [{'column': 'x', 'dir': 'asc'}]})
        with self.assertRaises(FilterError):
            fs.parse_ordering()

    def test_order_bad_direction_raises(self):
        fs = self._filterset({'columns': [{'data': 'state'}], 'order': [{'column': 0, 'dir': 'sideways'}]})
        with self.assertRaises(FilterError):
            fs.parse_ordering()


class QueryStringProcessingTestCase(TestCase):
    """Tests for DatatablesQueryStringProcessor to prevent DoS and malformed input (B18)."""

    def test_normal_array_index_accepted(self):
        """Normal array indices should be accepted."""
        class FakeRequest:
            def __init__(self):
                self.GET = {'columns[0][data]': 'state', 'columns[100][data]': 'resolution'}
                self.query_params = self.GET

        processor = DatatablesQueryStringProcessor(FakeRequest())
        self.assertIsNotNone(processor.query_set)

    def test_large_array_index_rejected(self):
        """Array indices above MAX_ARRAY_INDEX should raise QueryStringProcessingError."""
        class FakeRequest:
            def __init__(self):
                self.GET = {'columns[100000000][data]': 'x'}
                self.query_params = self.GET

        with self.assertRaises(QueryStringProcessingError) as ctx:
            DatatablesQueryStringProcessor(FakeRequest())
        self.assertIn('exceeds the maximum', str(ctx.exception))

    def test_max_index_boundary_accepted(self):
        """An index exactly at MAX_ARRAY_INDEX should be accepted."""
        class FakeRequest:
            def __init__(self):
                self.GET = {f'columns[{DatatablesQueryStringProcessor.MAX_ARRAY_INDEX}][data]': 'x'}
                self.query_params = self.GET

        processor = DatatablesQueryStringProcessor(FakeRequest())
        self.assertIsNotNone(processor.query_set)

    def test_just_above_max_index_rejected(self):
        """An index just above MAX_ARRAY_INDEX should be rejected."""
        class FakeRequest:
            def __init__(self):
                self.GET = {f'columns[{DatatablesQueryStringProcessor.MAX_ARRAY_INDEX + 1}][data]': 'x'}
                self.query_params = self.GET

        with self.assertRaises(QueryStringProcessingError):
            DatatablesQueryStringProcessor(FakeRequest())


class SerializerColumnMetadataTestCase(SimpleTestCase):
    """Datatypes derived from the model fields must match the previously hand-written Meta.datatypes (A1)."""

    def test_pdb_datatypes(self):
        self.assertEqual(PdbStructureSummarySerializer.column_datatypes(), {
            'checkbox': 'checkbox', 'uniprot': 'text', 'protein_name_short': 'text', 'receptor_family_short': 'text',
            'receptor_class_code': 'text', 'gene': 'text', 'fraction_of_wt_seq': 'numeric', 'closest_to_human': 'boolean',
            'species_common_name': 'text', 'identity_to_human': 'numeric', 'structure_type': 'text', 'pdb_id': 'text',
            'resolution': 'numeric', 'resolution_best': 'boolean', 'state': 'text', 'query_effector': 'text',
            'gprot_bound_likeness': 'numeric', 'tm6_angle': 'numeric', 'signal_protein': 'text',
            'signal_protein_subtype': 'text', 'signal_protein_note': 'text', 'signal_protein_pcntseq': 'numeric',
            'fusion': 'text', 'antibody': 'text', 'ligand': 'text', 'ligand_role': 'text',
        })

    def test_gpcr_datatypes(self):
        numeric = [
            'st_inact_c', 'st_interm_c', 'st_act_c', 'best_res_inact', 'best_res_interm', 'best_res_act',
            'transd_gio_c', 'transd_gq11_c', 'transd_g1213_c', 'transd_arrest_c', 'transd_grks_c',
            'ligand_types_smmol_c', 'ligand_types_pep_c', 'limodgrp_stim_c', 'limodgrp_inhib_c', 'limodgrp_unk_c',
            'limod_apo_c', 'limod_ag_c', 'limod_ag_partial_c', 'limod_allo_antag_c', 'limod_antag_c',
            'limod_inv_ag_c', 'limod_nam_c', 'limod_pam_c', 'limod_unk_c',
        ]
        expected = {name: 'text' for name in
                    ['uniprot', 'protein_name', 'receptor_family', 'receptor_class', 'gene', 'species_common_name']}
        expected.update({name: 'numeric' for name in numeric})
        self.assertEqual(GpcrStatisticsSummarySerializer.column_datatypes(), expected)

    def test_method_fields_map_to_database_columns(self):
        mapping = PdbStructureSummarySerializer.source_mapping()
        self.assertEqual(mapping['uniprot'], 'uniprot_entry_name')
        self.assertEqual(mapping['gene'], 'gene_name')
        self.assertEqual(mapping['resolution'], 'resolution')


class ColumnConfigurationConsistencyTestCase(SimpleTestCase):
    """Every column JSON file must agree with the serializer behind its table (A1)."""

    CONFIG_DIR = pathlib.Path(__file__).resolve().parent / "configuration_provider" / "column_configurations"

    # Client data_type -> server datatypes it can filter correctly.
    # text/textselect accept boolean because the client sends "true"/"false" search strings.
    COMPATIBLE = {
        'text': {'text', 'boolean'},
        'textselect': {'text', 'boolean'},
        'weblinktext': {'text'},
        'numeric': {'numeric'},
        'checkbox': {'checkbox'},
    }

    def _configs(self):
        files = sorted(self.CONFIG_DIR.glob("*.json"))
        self.assertTrue(files, f"No column configurations found in {self.CONFIG_DIR}")
        for path in files:
            table_name = path.stem.split("__")[0]
            with open(path) as f:
                yield path.name, table_name, json.load(f)

    def test_every_table_has_a_serializer(self):
        for file_name, table_name, _ in self._configs():
            with self.subTest(file=file_name):
                self.assertIn(table_name, TABLE_SERIALIZERS)

    def test_columns_exist_with_compatible_datatypes(self):
        for file_name, table_name, columns in self._configs():
            datatypes = TABLE_SERIALIZERS[table_name].column_datatypes()
            for column in columns:
                json_id = column['json_id']
                with self.subTest(file=file_name, column=json_id):
                    self.assertIn(json_id, datatypes, "column is not a serializer field")
                    self.assertIn(column['data_type'], self.COMPATIBLE, "unknown client data_type")
                    self.assertIn(datatypes[json_id], self.COMPATIBLE[column['data_type']],
                                  f"client type '{column['data_type']}' cannot filter server type '{datatypes[json_id]}'")

    def test_condition_sources_are_columns(self):
        for file_name, _, columns in self._configs():
            json_ids = {column['json_id'] for column in columns}
            for column in columns:
                for source in filter(None, (s.strip() for s in column.get('cssClassCellConditionSource', '').split(';'))):
                    with self.subTest(file=file_name, column=column['json_id'], source=source):
                        self.assertIn(source, json_ids)
