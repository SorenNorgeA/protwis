// Data source for DataTables client-side processing.
// Column data is computed from the rows loaded into the DataTable, and column filters use DataTables' own search.

import { DataSourceBase } from "./datasourcebase.js";

class ClientDataSource extends DataSourceBase {

  get _dataTable() {
    return this.tableManagerReference.dataTableReference;
  }

  async getOptions(column) {
    const data = this._dataTable.column(column.col_idx).data().toArray();
    return Array.from(new Set(data)).sort();
  }

  async getRange(sources) {
    let min = Number.POSITIVE_INFINITY;
    let max = Number.NEGATIVE_INFINITY;

    this._dataTable.rows().data().toArray().forEach((rowData) => {
      for (const source of sources) {
        const raw = rowData[source];
        if (raw === null || raw === undefined || raw === "") {
          continue;
        }
        const value = parseFloat(raw);
        if (Number.isFinite(value)) {
          min = Math.min(min, value);
          max = Math.max(max, value);
        }
      }
    });

    return Number.isFinite(min) && Number.isFinite(max) ? { min: min, max: max } : null;
  }

  // Computed from the loaded rows each time: cheap, and always reflects global search and fixed search filters
  async getDistinctCounts(settings) {
    const table = this._dataTable

    // Get all rows (base dataset) and filtered rows
    const baseRows = table.rows().data().toArray()
    const filteredRows = table.rows({search: 'applied'}).data().toArray()

    // Get column information
    const columnSettings = table.settings()[0].aoColumns

    // Helper function to count unique values in a column across rows
    const countUniqueInColumn = (rows, columnInfo) => {
        const uniqueValues = new Set()

        rows.forEach((rowData) => {
            let value

            // Handle different data structures: object vs array
            if (typeof rowData === 'object' && !Array.isArray(rowData)) {
                value = rowData[columnInfo.data] || rowData[columnInfo.name]
            } else if (Array.isArray(rowData)) {
                value = rowData[columnInfo.index] || rowData[columnInfo.columnIndex]
            }

            // Only count non-empty values
            if (value !== null && value !== undefined && value !== '') {
              // Convert value to string, or JSON string if it's an object
              // (there is a risk of the same objects having different string representation due to property order,
              // but this is a reasonable compromise for counting unique values quicky -- sorting object keys for every cell
              // would be too slow for large datasets)
                value = typeof value == 'object' && value !== null ?  JSON.stringify(value) : String(value)
                uniqueValues.add(value)
            }
        })

        return uniqueValues.size
    }

    // Calculate unique counts for each column in both base and filtered datasets
    const uniqueValuesByColumn = {
        'base': {},
        'filtered': {}
    }

    columnSettings.forEach((colInfo) => {
        const columnName = colInfo.data || colInfo.name || colInfo.title

        if (columnName && columnName !== 'checkbox') { // Exclude checkbox columns from unique value counts
            uniqueValuesByColumn.base[columnName] = countUniqueInColumn(baseRows, colInfo)
            uniqueValuesByColumn.filtered[columnName] = countUniqueInColumn(filteredRows, colInfo)
        }
    })

    return uniqueValuesByColumn
  }

  async getSearchSetRows(column, signal) {
    return this._dataTable.rows({ search: "applied" }).data().toArray().map((rowData) => rowData[column.json_id]);
  }

  _buildColumnSearch(column, filter) {
    if (filter.type === "text") {
      const search = this._buildTextSearch(filter.terms);
      return { ...search, key: search.value };
    }
    if (filter.type === "range") {
      return this._buildRangeSearch(column, filter.min, filter.max);
    }
    throw new Error(`Unknown filter type: ${filter.type}`);
  }

  // DataTables' own search. A single exact value is a plain search; several values (ORed with "|") or any contains
  // match need a regex. No terms gives an empty search (no filter).
  _buildTextSearch(terms) {
    if (terms.length === 0) {
      return { value: "", regex: false };
    }
    if (terms.length === 1 && !terms[0].contains) {
      return { value: terms[0].value, regex: false };
    }
    const escape = (value) => String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); // escape regex special characters
    const value = terms
      .map(({ value, contains }) => contains
        ? "(.*" + escape(value) + ".*)" // wildcards for "contains" search
        : "(^" + escape(value) + "$)") // anchors for "exact match" search
      .join("|");
    return { value: value, regex: true };
  }

  // A range is a search function on the column, so clearing the column's search (e.g. the clear filters button)
  // also clears the range. Non-numeric cells (e.g. blank cells) are excluded while a bound is set.
  _buildRangeSearch(column, min, max) {
    if (min === null && max === null) {
      return { value: "", regex: false, key: "" };
    }

    const rangeSearch = (searchData, rowData) => {
      const value = parseFloat(rowData[column.json_id]);
      if (isNaN(value)) {
        return false;
      }
      return (min === null || min <= value) && (max === null || value <= max);
    };
    rangeSearch.filterKey = JSON.stringify({ min: min, max: max });

    return { value: rangeSearch, regex: false, key: rangeSearch.filterKey };
  }
}

export { ClientDataSource };
