// This is the base class for the table manager's data sources.
// A data source hides whether the DataTable uses client-side or server-side processing: the TableManager picks
// ClientDataSource or ServerDataSource once at initialisation, and data types, cell styles and the TableManager
// itself ask it for column data and apply column filters through it, without checking the processing mode.
// All data methods return promises in both modes, so callers can always await them.

class DataSourceBase {

  constructor(tableManagerReference) {
    this.tableManagerReference = tableManagerReference;
  }

  // True for ServerDataSource. Only for the few places where the mode genuinely changes the caller's own logic
  // (e.g. TextSelect filters on the raw value server-side but on the display text client-side).
  get isServerSide() {
    return false;
  }

  // The column's distinct raw values.
  async getOptions(column) {
    throw new Error("getOptions not implemented");
  }

  // {min, max} over the given source columns (json ids), or null if there are no numeric values or the request fails.
  async getRange(sources) {
    throw new Error("getRange not implemented");
  }

  // Distinct value counts per column, {base: {json_id: count}, filtered: {json_id: count}}.
  async getDistinctCounts(settings) {
    throw new Error("getDistinctCounts not implemented");
  }

  // The checkbox cell data ({row_id, data}) of every row matching the current search/filters, regardless of pagination.
  async getSearchSetRows(column, signal) {
    throw new Error("getSearchSetRows not implemented");
  }

  // Builds the DataTables column search {value, regex, key} for a filter. key identifies the filter, so it can be
  // compared with the column's current search to skip redraws that would change nothing.
  _buildColumnSearch(column, filter) {
    throw new Error("_buildColumnSearch not implemented");
  }

  /**
   * Applies a filter to a column and redraws the table, unless the filter is unchanged or filters are being cleared
   * (the clear filters button redraws once after all filters have been cleared).
   * @param {Object} column - The column (data type instance) to filter.
   * @param {Object} filter - {type: "text", terms: [{value, contains}]} for exact or "contains" matches, or
   *   {type: "range", min, max} with null for an open bound. No terms, or no bounds, clears the column's filter.
   * @returns {boolean} True if the filter changed and the table was redrawn.
   */
  applyColumnFilter(column, filter) {
    if (this.tableManagerReference.state.clearingFilters) {
      return false;
    }

    const search = this._buildColumnSearch(column, filter);
    const dataTableColumn = this.tableManagerReference.dataTableReference.column(column.col_idx);

    if (search.key === this._searchKey(dataTableColumn.search())) {
      return false;
    }

    dataTableColumn.search(search.value, search.regex).draw();
    return true;
  }

  // The key of a column's current search value: the string itself, or the key attached to a search function.
  _searchKey(searchValue) {
    if (typeof searchValue === "function") {
      return searchValue.filterKey;
    }
    return searchValue || "";
  }
}

export { DataSourceBase };
