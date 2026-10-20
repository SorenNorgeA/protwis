// Data source for DataTables client-side processing.
// Column data is computed from the rows loaded into the DataTable, and column filters use DataTables' own search.

import { DataSourceBase } from "./datasourcebase.js";

class ClientDataSource extends DataSourceBase {

  get _dataTable() {
    return this.tableManagerReference.dataTableReference;
  }

  // The data methods read the rows loaded into the DataTable, so each waits for the initial data first (it may be
  // called earlier, e.g. by a cell style while the first rows are rendered).

  async getOptions(column) {
    await this.tableManagerReference.ready;
    const data = this._dataTable.column(column.col_idx).data().toArray();
    return Array.from(new Set(data)).sort();
  }

  async getRange(sources) {
    await this.tableManagerReference.ready;
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

  // DataTables fires search whenever it re-filters the loaded rows, after which the rows({search: "applied"}) used by
  // the data methods are up to date
  onSearchChange(handler) {
    this._dataTable.on("search.dt", () => handler());
  }

  // Computed from the loaded rows each time: cheap, and always reflects global search and fixed search filters
  async getDistinctCounts() {
    await this.tableManagerReference.ready;
    const table = this._dataTable

    // Helper function to count unique values in a column's cell data
    const countUnique = (values) => {
        const uniqueValues = new Set()

        values.forEach((value) => {
            // Only count non-empty values
            if (value !== null && value !== undefined && value !== '') {
              // Convert value to string, or JSON string if it's an object
              // (there is a risk of the same objects having different string representation due to property order,
              // but this is a reasonable compromise for counting unique values quicky -- sorting object keys for every cell
              // would be too slow for large datasets)
                value = typeof value == 'object' ?  JSON.stringify(value) : String(value)
                uniqueValues.add(value)
            }
        })

        return uniqueValues.size
    }

    // Calculate unique counts for each column in both base and filtered datasets, keyed by the column's data source
    // (the json_id for columns generated from the column specifications)
    const uniqueValuesByColumn = {
        'base': {},
        'filtered': {}
    }

    table.columns().every(function () {
        const columnName = this.dataSrc()

        if ((typeof columnName === 'string' || typeof columnName === 'number') && columnName !== 'checkbox') { // Exclude checkbox columns from unique value counts
            uniqueValuesByColumn.base[columnName] = countUnique(this.data().toArray())
            uniqueValuesByColumn.filtered[columnName] = countUnique(table.column(this.index(), { search: 'applied' }).data().toArray())
        }
    })

    return uniqueValuesByColumn
  }

  async getSearchSetRows(column, signal) {
    await this.tableManagerReference.ready;
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
