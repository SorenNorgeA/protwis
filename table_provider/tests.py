from django.test import TestCase
from django.db.models import Q
from table_provider.serverside.filters import TextFilter, NumericRangeFilter, BooleanFilter, FilterSet, FilterError
from table_provider.serverside.querystringprocessing import DatatablesQueryStringProcessor, QueryStringProcessingError
from rest_framework import serializers


class TextFilterTestCase(TestCase):
    """Tests for TextFilter.format_query() to ensure correct Q object generation."""

    def test_single_exact_value_only(self):
        """Single exact match value should return exact equality Q."""
        f = TextFilter("state", "Active")
        q = f.format_query()
        # Should match "Active" exactly
        self.assertEqual(q, Q(state__in=["Active"]))

    def test_multiple_exact_values_only(self):
        """Multiple exact matches should return __in Q."""
        f = TextFilter("state", "Active|Inactive")
        q = f.format_query()
        self.assertEqual(q, Q(state__in=["Active", "Inactive"]))

    def test_single_tag_only(self):
        """Single tag filter should use iregex."""
        f = TextFilter("state", "(.*inter.*)")
        q = f.format_query()
        self.assertEqual(q, Q(state__iregex=".*inter.*"))

    def test_multiple_tags_only(self):
        """Multiple tag filters should be ORed."""
        f = TextFilter("state", "(.*inter.*)|(.*act.*)")
        q = f.format_query()
        expected = Q(state__iregex=".*inter.*") | Q(state__iregex=".*act.*")
        self.assertEqual(q, expected)

    def test_single_exact_plus_single_tag(self):
        """Single exact value AND single tag should both be included (ORed).

        This is the bug B2 regression test. When there's exactly one exact match
        and at least one tag, both should be in the result.
        """
        f = TextFilter("state", "(^Active$)|(.*inter.*)")
        q = f.format_query()
        expected = Q(state__in=["Active"]) | Q(state__iregex=".*inter.*")
        self.assertEqual(q, expected)

    def test_single_exact_plus_multiple_tags(self):
        """Single exact value and multiple tags should all be ORed."""
        f = TextFilter("state", "(^Active$)|(.*inter.*)|(.*act.*)")
        q = f.format_query()
        expected = (
            Q(state__in=["Active"]) |
            Q(state__iregex=".*inter.*") |
            Q(state__iregex=".*act.*")
        )
        self.assertEqual(q, expected)

    def test_multiple_exact_plus_multiple_tags(self):
        """Multiple exact values and multiple tags should all be ORed."""
        f = TextFilter("state", "(^Active$)|(^Inactive$)|(.*inter.*)|(.*act.*)")
        q = f.format_query()
        expected = (
            Q(state__in=["Active", "Inactive"]) |
            Q(state__iregex=".*inter.*") |
            Q(state__iregex=".*act.*")
        )
        self.assertEqual(q, expected)

    def test_empty_filters(self):
        """Empty filter should return empty Q."""
        f = TextFilter("state", "")
        q = f.format_query()
        # Empty Q() is falsy but represents an empty condition
        self.assertEqual(q, Q())

    def test_anchors_removed_correctly(self):
        """Anchors should be stripped from exact values but not from tags."""
        # Exact value with anchors: (^value$) -> value
        f = TextFilter("state", "(^Active$)")
        q = f.format_query()
        self.assertEqual(q, Q(state__in=["Active"]))

    def test_escape_characters_removed_from_exact(self):
        """Escape characters should be removed from exact values."""
        # This would come from a value with special chars that were escaped
        f = TextFilter("state", "Test\\(Escaped\\)")
        q = f.format_query()
        # Backslashes are removed from standard filters
        self.assertEqual(q, Q(state__in=["Test(Escaped)"]))


class NumericRangeFilterTestCase(TestCase):
    """Tests for NumericRangeFilter.format_query() to ensure correct Q object generation.

    Tests that bounds are inclusive (>=, <=) matching client-side behavior.
    """

    def test_both_bounds_infinite(self):
        """Both bounds infinite should return empty Q (no filtering)."""
        f = NumericRangeFilter("resolution", "-Inf", "Inf")
        q = f.format_query()
        self.assertEqual(q, Q())

    def test_min_only_inclusive(self):
        """Min-only filter should use >= (inclusive)."""
        f = NumericRangeFilter("resolution", "2.0", "Inf")
        q = f.format_query()
        self.assertEqual(q, Q(resolution__gte="2.0"))

    def test_max_only_inclusive(self):
        """Max-only filter should use <= (inclusive)."""
        f = NumericRangeFilter("resolution", "-Inf", "3.5")
        q = f.format_query()
        self.assertEqual(q, Q(resolution__lte="3.5"))

    def test_both_bounds_inclusive(self):
        """Both bounds should use >= and <= (inclusive)."""
        f = NumericRangeFilter("resolution", "2.0", "3.5")
        q = f.format_query()
        expected = Q(resolution__gte="2.0") & Q(resolution__lte="3.5")
        self.assertEqual(q, expected)

    def test_boundary_value_2_inclusive(self):
        """Value 2.0 should be included with min=2.0 (inclusive).

        This is the B3 regression test. Ensures server-side and client-side
        both use inclusive bounds (>= and <=, not > and <).
        """
        f = NumericRangeFilter("resolution", "2.0", "Inf")
        q = f.format_query()
        # Must use >= not > so value 2.0 is included
        self.assertEqual(q, Q(resolution__gte="2.0"))

    def test_boundary_value_35_inclusive(self):
        """Value 3.5 should be included with max=3.5 (inclusive)."""
        f = NumericRangeFilter("resolution", "-Inf", "3.5")
        q = f.format_query()
        # Must use <= not < so value 3.5 is included
        self.assertEqual(q, Q(resolution__lte="3.5"))


class FilterSetOrderingTestCase(TestCase):
    """Tests for FilterSet.parse_ordering() to ensure deterministic pagination."""

    def _make_mock_serializer(self):
        """Create a minimal mock serializer for testing."""
        class MockSerializer(serializers.Serializer):
            uniprot = serializers.CharField()
            state = serializers.CharField()

            class Meta:
                datatypes = {
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
            class Meta:
                datatypes = {'resolution': 'numeric', 'state': 'text'}

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
            NumericRangeFilter("resolution", "abc", "Inf")

    def test_non_finite_bound_raises(self):
        with self.assertRaises(FilterError):
            NumericRangeFilter("resolution", "nan", "3.0")

    def test_valid_bounds_accepted(self):
        NumericRangeFilter("resolution", "-1.5", "3")

    def test_range_without_separator_raises(self):
        fs = self._filterset(self._column('resolution', '2.0'))
        with self.assertRaises(FilterError):
            fs.parse_filters()

    def test_range_with_multiple_separators_raises(self):
        fs = self._filterset(self._column('resolution', '1~2~3'))
        with self.assertRaises(FilterError):
            fs.parse_filters()

    def test_non_numeric_range_via_filterset_raises(self):
        fs = self._filterset(self._column('resolution', 'abc~2'))
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
