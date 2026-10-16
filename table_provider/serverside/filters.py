from table_provider.serverside.querystringprocessing import DatatablesQueryStringProcessor
import math

from django.db.models import Q


class FilterError(ValueError):
    """Raised when a request contains filter or ordering parameters that cannot be interpreted (a client error)."""


"""
 This module provides classes and functions for filtering and ordering data in a server-side processing context, specifically for use with DataTables.
 It defines a FilterSet class that processes query parameters from the request to generate filter requirements and ordering instructions based on the
 specified serializer's field mappings and data types. The module also includes specific filter classes for text and numeric range filtering,
 allowing for flexible and dynamic filtering of data based on user input.
"""
class FilterSet:
    ignored_datatypes = ['checkbox']

    def __init__(self, request, SerializerClass):
        """Initializes a FilterSet instance."""
        self._filters = []
        self._ordering = []
        self.request = request
        self.SerializerClass = SerializerClass
        self.field_mappings = SerializerClass.source_mapping()
        self.datatypes = SerializerClass.column_datatypes()
        if request is not None:
            self.query_parameters = DatatablesQueryStringProcessor(self.request).query_set
            self.parse_filters()
            self.parse_ordering()

    def __iter__(self):
        return iter(self._filters)

    def has_filters(self):
        return len(self._filters) > 0

    def add_filter(self, filter_requirement):
        self._filters.append(filter_requirement)

    def parse_filters(self):
        """Convert query parameters to filter objects

        Parses query parameters from a DataTables serverside request
        to generate filter requirements based on the specified
        serializer's field mappings and data types.
        """

        if 'columns' not in self.query_parameters:
            return

        for column in self.query_parameters['columns']:
            try:
                data_field = column['data']
                search_value = column['search']['value']
            except (KeyError, TypeError):
                raise FilterError("Each column must provide 'data' and 'search[value]'.")
            field_type = self.datatypes.get(data_field)

            if not field_type:
                raise FilterError(f"Unknown column '{data_field}'.")

            if field_type in self.ignored_datatypes:
                continue

            if search_value:
                if field_type == 'text':
                    filter_req = TextFilter(self.field_mappings.get(data_field), search_value)
                elif field_type == 'numeric':
                    bounds = search_value.split('~')
                    if len(bounds) != 2:
                        raise FilterError(f"Invalid numeric range '{search_value}' for field '{data_field}'; expected 'min~max'.")
                    [filter_min, filter_max] = bounds
                    if filter_min != "-Inf" or filter_max != "Inf":
                        filter_req = NumericRangeFilter(self.field_mappings.get(data_field), filter_min, filter_max)
                    else:
                        continue
                elif field_type == 'boolean':
                    if search_value.lower() in ['true', '1', 'yes']:
                        filter_req = BooleanFilter(self.field_mappings.get(data_field), True)
                    elif search_value.lower() in ['false', '0', 'no']:
                        filter_req = BooleanFilter(self.field_mappings.get(data_field), False)
                    else:
                        continue
                else:
                    continue

                self.add_filter(filter_req)

    def parse_ordering(self):
        """Extract columns used for row ordering from query parameters.

        Always appends 'pk' as a final tie-breaker to ensure deterministic pagination.
        When no ordering is specified, defaults to 'pk' ordering.
        """
        order_array = []

        # Extract ordering from query parameters if provided
        if 'order' in self.query_parameters:
            for order_column in self.query_parameters['order']:
                try:
                    column_idx = int(order_column['column'])
                    json_name = self.query_parameters['columns'][column_idx]['data']
                    direction = order_column['dir']
                except (KeyError, IndexError, TypeError, ValueError):
                    raise FilterError("Invalid 'order' parameter: it must reference an existing column and a direction.")
                if direction not in ('asc', 'desc'):
                    raise FilterError(f"Invalid order direction '{direction}'.")
                field_type = self.datatypes.get(json_name)
                if field_type in self.ignored_datatypes:
                    continue
                db_name = self.field_mappings.get(json_name)
                if not db_name:
                    raise FilterError(f"Cannot order by unknown field '{json_name}'.")
                order_direction = '' if direction == 'asc' else '-'
                order_array.append(f"{order_direction}{db_name}")

        # Always append 'pk' as a final tie-breaker for deterministic pagination.
        # This ensures that rows with identical values in the primary sort column
        # are always returned in the same order, preventing duplicates or skipped rows
        # when using LIMIT/OFFSET pagination.
        if 'pk' not in order_array and '-pk' not in order_array:
            order_array.append('pk')

        # If no ordering was specified at all, default to ordering by pk
        if not order_array:
            order_array = ['pk']

        self._ordering = order_array


    def get_filters(self, data_field=None):
        return self._filters

    def get_ordering(self):
        return self._ordering

class FilterRequirement:
    def __init__(self, db_field_name, filter_type):
        self.filter_type = filter_type
        self.db_field_name = db_field_name
        self.filter_values = None

class TextFilter(FilterRequirement):
    """A text filter requirement for a specific database field.

    Supports filtering based on exact matches or regular expressions, allowing for flexible text-based filtering.
    Interacts with Select2 "tag" system to allow partial matches and multiple values to be filtered on a single field.
    Queries are built using Django's Q objects, allowing for complex filtering logic to be constructed and applied to the database query.
    """

    def __init__(self, db_field_name, filter_value):
        super().__init__(db_field_name, 'text')
        self.tag_filters = []
        self.standard_filters = []

        filter_value = filter_value.split('|')

        #remove regex anchors added for datatables from filter values
        for fv in filter_value:
            if not fv:
                continue # an empty value (e.g. an empty search string) is not a filter
            if fv.startswith('('):
                fv = fv.strip("()")
            if fv.startswith('^'):
                fv = fv.strip("^$")
            if ".*" in fv:
                self.tag_filters.append(fv)
            else:
                fv = fv.replace("\\", "") #Remove escape characters from standard filters (added for regex compatibility with datatables)
                self.standard_filters.append(fv)

    def format_query(self):
        """Converts filter values into a Django Q object for querying the database.

        Combines exact-match standard filters and regex tag filters with OR logic,
        so a row matches if the field equals any standard value OR matches any tag pattern.
        """
        query = Q()

        # Add exact-match values (works for 1 or many; __in ORs them internally)
        if self.standard_filters:
            query |= Q(**{f"{self.db_field_name}__in": self.standard_filters})

        # Add tag filters (contains-match via regex), ORed with exact matches and each other
        for tag_filter in self.tag_filters:
            query |= Q(**{f"{self.db_field_name}__iregex": rf"{tag_filter}"})

        return query



class NumericRangeFilter(FilterRequirement):
    """Represents a numeric range filter requirement for a specific database field.

    It supports filtering based on minimum and maximum values. Values can be specified as '-Inf' or 'Inf'
    to represent unbounded ranges, allowing for filtering based on only a minimum or maximum value.
    """

    def __init__(self, db_field_name, filter_min, filter_max):
        """Initializes a NumericRangeFilter instance with the specified database field name and filter values.

        Bounds are inclusive (match ==, >=, <=, not just >/<).
        """
        super().__init__(db_field_name, 'numeric')

        # Bounds must be numeric (or the infinite sentinels) before they reach the ORM
        for bound, sentinel in ((filter_min, '-Inf'), (filter_max, 'Inf')):
            if bound == sentinel:
                continue
            try:
                valid = math.isfinite(float(bound))
            except (TypeError, ValueError):
                valid = False
            if not valid:
                raise FilterError(f"Invalid numeric bound '{bound}' for field '{db_field_name}'.")

        # Both bounds unbound: no filter needed
        if filter_min == '-Inf' and filter_max == 'Inf':
            self.filter_values = {}
            self.filter_action = 'none'
        # Both bounds specified: between (inclusive on both ends)
        elif filter_min != '-Inf' and filter_max != 'Inf':
            self.filter_values = {'min': filter_min, 'max': filter_max}
            self.filter_action = 'between'
        # Only min bound: greater-than-or-equal
        elif filter_min != '-Inf':
            self.filter_values = {'min': filter_min}
            self.filter_action = 'gte'
        # Only max bound: less-than-or-equal
        elif filter_max != 'Inf':
            self.filter_values = {'max': filter_max}
            self.filter_action = 'lte'

    def format_query(self):
        """Converts filter values into a Django Q object for querying the database.

        Returns an empty Q() if no bounds are set (both infinite).
        """
        if self.filter_action == 'none':
            # No filter needed; both bounds are infinite
            return Q()
        elif self.filter_action == 'between':
            # Both min and max: use >= and <= (inclusive)
            return (Q(**{f"{self.db_field_name}__gte": self.filter_values.get('min')}) &
                    Q(**{f"{self.db_field_name}__lte": self.filter_values.get('max')}))
        else:
            # Min-only (gte) or max-only (lte)
            comp = 'min' if self.filter_action == 'gte' else 'max'
            return Q(**{f"{self.db_field_name}__{self.filter_action}": self.filter_values[comp]})

class BooleanFilter(FilterRequirement):
    """Represents a boolean filter requirement for a specific database field.

    It supports filtering based on a boolean value.
    """

    def __init__(self, db_field_name, filter_value):
        """Initializes a BooleanFilter instance with the specified database field name and filter values."""
        super().__init__(db_field_name, 'boolean')

        self.filter_values = filter_value
        self.filter_action = 'equals'

    def format_query(self):
        """Converts filter values into a Django Q object for querying the database"""
        if(self.filter_action == 'equals'):
            return Q(**{f"{self.db_field_name}": self.filter_values})
        else:
            raise ValueError(f"Invalid filter action for BooleanFilter: {self.filter_action}")