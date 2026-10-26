// Data source for DataTables server-side processing.
// Column data comes from endpoints at fixed extensions of the table's AJAX URL (PATHS), and column filters are sent
// to the server as JSON in the column search value. Requests that depend on the current search send the parameters
// of the table's last data request (dt.ajax.params()), so they match the rows the table shows.

import { DataSourceBase } from "./datasourcebase.js";

class ServerDataSource extends DataSourceBase {

  // Endpoint extensions of the table's AJAX URL
  static PATHS = {
    /**
     * Endpoint extension for a column's distinct values.
     * @param {string} column - The column's json id.
     * @returns {string} The path, relative to the AJAX URL.
     */
    options: (column) => `/options/${encodeURIComponent(column)}/`,
    /**
     * Endpoint extension for a column's min and max values.
     * @param {string} column - The column's json id.
     * @returns {string} The path, relative to the AJAX URL.
     */
    range: (column) => `/range/${encodeURIComponent(column)}/`,
    /**
     * Endpoint extension for the distinct value counts of all columns.
     * @returns {string} The path, relative to the AJAX URL.
     */
    counts: () => `/countdistinct`,
  };

  // Data request parameters that don't affect which rows match the search
  static NON_SEARCH_PARAMS = /^(draw|start|length|order\[.*)$/;

  /**
   * True for ServerDataSource.
   * @returns {boolean} Always true.
   */
  get isServerSide() {
    return true;
  }

  /**
   * Fetch a reference to the DataTable instance for the current table.
   * @returns {Object} The DataTables instance.
   */
  get _dataTable() {
    return this.tableManagerReference.dataTableReference;
  }

  /**
   * Calls handler when the search changes. DataTables fires search before it builds the request for the new search, so
   * ajax.params() would still hold the previous request's parameters. preXhr fires once they are set; requests that
   * only page or reorder the rows (same search query) are skipped.
   * @param {Function} handler - Called with no arguments on each search change.
   */
  onSearchChange(handler) {
    let lastSearchQuery = this._currentSearchQuery();
    this._dataTable.on("preXhr.dt", () => {
      const searchQuery = this._currentSearchQuery();
      if (searchQuery !== lastSearchQuery) {
        lastSearchQuery = searchQuery;
        handler();
      }
    });
  }

  /**
   * Gets the distinct values of a column from the server, cached per column. Resolves to an empty array if the request
   * fails.
   * @param {Object} column - The column (data type instance) to get options for.
   * @returns {Promise<Array>} The distinct raw values.
   */
  async getOptions(column) {
    await this.tableManagerReference.ready;
    const cacheKey = { type: 'columnOptions', columnId: column.json_id };

    // Check cache first
    const cachedData = this.tableManagerReference.cache.get(cacheKey);
    if (cachedData !== undefined) {
      return cachedData;
    }

    try {
      const data = await this._fetchJson(this._url(ServerDataSource.PATHS.options(column.json_id)));

      // Store in cache before returning
      this.tableManagerReference.cache.push(cacheKey, data);

      return data;
    } catch (error) {
      console.error(`Error fetching column options for ${column.json_id}:`, error);
      return [];
    }
  }

  /**
   * Gets the minimum and maximum over the given source columns from the server. Never rejects.
   * @param {string[]} sources - The json ids of the source columns.
   * @returns {Promise<{min: number, max: number}|null>} The range, or null if there are no numeric values or any range
   *   request fails.
   */
  async getRange(sources) {
    try {
      await this.tableManagerReference.ready;
      const ranges = await Promise.all(sources.map((source) => this._fetchJson(this._url(ServerDataSource.PATHS.range(source)))));

      let min = Number.POSITIVE_INFINITY;
      let max = Number.NEGATIVE_INFINITY;
      for (const range of ranges) {
        const rangeMin = parseFloat(range.min);
        const rangeMax = parseFloat(range.max);
        if (Number.isFinite(rangeMin)) min = Math.min(min, rangeMin);
        if (Number.isFinite(rangeMax)) max = Math.max(max, rangeMax);
      }

      return Number.isFinite(min) && Number.isFinite(max) ? { min: min, max: max } : null;
    } catch (error) {
      console.error(`Failed to retrieve min/max values for ${sources}:`, error);
      return null;
    }
  }

  /**
   * Gets the distinct value counts per column from the server, cached per complete request URL. The URL includes the
   * current search, so any change that alters the counts misses the cache.
   * @returns {Promise<{base: Object<string, number>, filtered: Object<string, number>}>} Counts keyed by json id, for
   *   the whole dataset (base) and for the rows matching the current search (filtered).
   */
  async getDistinctCounts() {
    await this.tableManagerReference.ready;
    // Key on the complete request (endpoint plus every search parameter) so any change that alters the counts misses the cache
    const requestUrl = this._url(ServerDataSource.PATHS.counts(), this._currentSearchQuery());
    const cacheKey = { type: 'columnCounts', url: requestUrl };

    let counts = this.tableManagerReference.cache.get(cacheKey);
    if (!counts) {
      counts = await this._fetchJson(requestUrl);
      this.tableManagerReference.cache.push(cacheKey, counts);
    }
    return counts;
  }

  /**
   * Gets the checkbox cell data of every row matching the current search and filters, from the server.
   * @param {Object} column - The checkbox column.
   * @param {AbortSignal} signal - Aborts the request when the caller no longer needs the result.
   * @returns {Promise<Array<{row_id: string, data: *}>>} The checkbox cell data of each matching row.
   */
  async getSearchSetRows(column, signal) {
    await this.tableManagerReference.ready;
    // The search set uses the column's options endpoint with the current search parameters: for the checkbox
    // column the server returns each matching row's checkbox data ({json_id: {row_id, data}}) instead of distinct values.
    const rows = await this._fetchJson(this._url(ServerDataSource.PATHS.options(column.json_id), this._currentSearchQuery()), signal);
    return rows.map((row) => row[column.json_id]);
  }

  // Server-side filters are JSON objects the server reads directly, so values are matched exactly as given (no regex
  // escaping or parsing). Keys are always present, in a fixed order, so the same filter always gives the same string
  // and can be compared with the current search value. An empty filter gives an empty search (no filter).
  /**
   * Builds the column search as a JSON string the server reads directly.
   * @param {Object} column - The column (data type instance) to filter.
   * @param {Object} filter - {type: "text", terms: [{value, contains}]} or {type: "range", min, max}.
   * @returns {{value: string, regex: boolean, key: string}} The column search. An empty filter gives an empty value.
   * @throws {Error} If the filter type is unknown.
   */
  _buildColumnSearch(column, filter) {
    let value = "";

    if (filter.type === "text") {
      if (filter.terms.length > 0) {
        const textFilter = { in: [], contains: [] };
        filter.terms.forEach(({ value, contains }) => (contains ? textFilter.contains : textFilter.in).push(String(value)));
        value = JSON.stringify(textFilter);
      }
    } else if (filter.type === "range") {
      if (filter.min !== null || filter.max !== null) {
        value = JSON.stringify({ min: filter.min, max: filter.max });
      }
    } else {
      throw new Error(`Unknown filter type: ${filter.type}`);
    }

    return { value: value, regex: false, key: value };
  }

  /**
   * Gets the last data request's parameters as a query string, encoded as DataTables sent them (jQuery's $.param, after
   * any ajax.data additions such as the PDB table's effector), without the draw, paging and ordering parameters.
   * @returns {string} The query string, or an empty string before the first request.
   */
  _currentSearchQuery() {
    const params = this._dataTable.ajax.params();
    if (params == null) {
      return "";
    }

    const query = new URLSearchParams(typeof params === "string" ? params : $.param(params));
    for (const key of [...query.keys()]) {
      if (ServerDataSource.NON_SEARCH_PARAMS.test(key)) {
        query.delete(key);
      }
    }
    return query.toString();
  }

  /**
   * Builds a request URL from the table's AJAX URL.
   * @param {string} path - The endpoint path, appended to the AJAX URL.
   * @param {string} [queryString] - The query string to append, if any.
   * @returns {string} The full request URL.
   */
  _url(path, queryString) {
    const url = this._dataTable.ajax.url() + path;
    return queryString ? url + "?" + queryString : url;
  }

  /**
   * Fetches a URL and parses the response as JSON.
   * @param {string} url - The URL to fetch.
   * @param {AbortSignal} [signal] - Aborts the request when signalled.
   * @returns {Promise<*>} The parsed JSON response.
   * @throws {Error} If the response status is not OK.
   */
  async _fetchJson(url, signal) {
    const response = await fetch(url, { signal });
    if (!response.ok) {
      throw new Error(`HTTP error when fetching ${url}! Status: ${response.status}`);
    }
    return response.json();
  }
}

export { ServerDataSource };
