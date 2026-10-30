// This is the base class for the table manager's data sources.
// A data source hides whether the DataTable uses client-side or server-side processing: the TableManager picks
// ClientDataSource or ServerDataSource once at initialisation, and data types, cell styles and the TableManager
// itself ask it for column data and apply column filters through it, without checking the processing mode.
// All data methods return promises in both modes, so callers can always await them. They wait for tableManager.ready
// (the table's initial data) before reading the table, so they can be called at any point after the data source exists.

class DataSourceBase {

  /**
   * @param {Object} tableManagerReference - The TableManager that owns the DataTable and the cache.
   */
  constructor(tableManagerReference) {
    this.tableManagerReference = tableManagerReference;
  }

  /**
   * True for ServerDataSource. Only for the few places where the mode genuinely changes the caller's own logic
   * (e.g. TextSelect filters on the raw value server-side but on the display text client-side).
   * @returns {boolean} True if the data source uses server-side processing.
   */
  get isServerSide() {
    return false;
  }

  /**
   * Configure client side caching of the AJAX response, if applicable. Abstract: implemented by the subclasses.
   * Does nothing by default.
   * @param {Object} dataTableOptions - The options for the DataTable; may be modified.
   */
  configureDataCache(dataTableOptions) {
    return;
  }

  /**
   * Gets the column's distinct raw values. Abstract: implemented by the subclasses.
   * @param {Object} column - The column (data type instance) to get options for.
   * @returns {Promise<Array>} The distinct raw values.
   * @throws {Error} Always, in the base class.
   */
  async getOptions(column) {
    throw new Error("getOptions not implemented");
  }

  /**
   * Gets the minimum and maximum over the given source columns. Abstract: implemented by the subclasses.
   * @param {string[]} sources - The json ids of the source columns.
   * @returns {Promise<{min: number, max: number}|null>} The range, or null if there are no numeric values or the
   *   request fails.
   * @throws {Error} Always, in the base class.
   */
  async getRange(sources) {
    throw new Error("getRange not implemented");
  }

  /**
   * Calls handler whenever the table's search (global or column) changes, at a point where the data methods already
   * answer for the new search. Abstract: implemented by the subclasses.
   * @param {Function} handler - Called with no arguments on each search change.
   * @throws {Error} Always, in the base class.
   */
  onSearchChange(handler) {
    throw new Error("onSearchChange not implemented");
  }

  /**
   * Gets the distinct value counts per column. Abstract: implemented by the subclasses.
   * @returns {Promise<{base: Object<string, number>, filtered: Object<string, number>}>} Counts keyed by json id, for
   *   the whole dataset (base) and for the rows matching the current search (filtered).
   * @throws {Error} Always, in the base class.
   */
  async getDistinctCounts() {
    throw new Error("getDistinctCounts not implemented");
  }

  /**
   * Gets the checkbox cell data of every row matching the current search and filters, regardless of pagination.
   * Abstract: implemented by the subclasses.
   * @param {Object} column - The checkbox column.
   * @param {AbortSignal} signal - Aborts the request when the caller no longer needs the result.
   * @returns {Promise<Array<{row_id: string, data: *}>>} The checkbox cell data of each matching row.
   * @throws {Error} Always, in the base class.
   */
  async getSearchSetRows(column, signal) {
    throw new Error("getSearchSetRows not implemented");
  }

  /**
   * Builds the DataTables column search for a filter. Abstract: implemented by the subclasses.
   * @param {Object} column - The column (data type instance) to filter.
   * @param {Object} filter - The filter, in the form described for applyColumnFilter.
   * @returns {{value: *, regex: boolean, key: string}} The column search. key identifies the filter, so it can be
   *   compared with the column's current search to skip redraws that would change nothing.
   * @throws {Error} Always, in the base class.
   */
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

  /**
   * Gets the key of a column's current search value.
   * @param {string|Function|null} searchValue - The column's current search value.
   * @returns {string} The string itself, or the filterKey attached to a search function.
   */
  _searchKey(searchValue) {
    if (typeof searchValue === "function") {
      return searchValue.filterKey;
    }
    return searchValue || "";
  }
}

export { DataSourceBase };
