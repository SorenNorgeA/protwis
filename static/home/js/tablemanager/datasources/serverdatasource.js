// Data source for DataTables server-side processing.
// Column data comes from endpoints at fixed extensions of the table's AJAX URL (PATHS), and column filters are sent
// to the server as JSON in the column search value. Requests that depend on the current search send the parameters
// of the table's last data request (dt.ajax.params()), so they match the rows the table shows.

import { DataSourceBase } from "./datasourcebase.js";

class ServerDataSource extends DataSourceBase {

  // Endpoint extensions of the table's AJAX URL
  static PATHS = {
    options: (column) => `/options/${encodeURIComponent(column)}/`,
    range: (column) => `/range/${encodeURIComponent(column)}/`,
    counts: () => `/countdistinct`,
  };

  // Data request parameters that don't affect which rows match the search
  static NON_SEARCH_PARAMS = /^(draw|start|length|order\[.*)$/;

  get isServerSide() {
    return true;
  }

  get _dataTable() {
    return this.tableManagerReference.dataTableReference;
  }

  // DataTables fires search before it builds the request for the new search, so ajax.params() would still hold the
  // previous request's parameters. preXhr fires once they are set; requests that only page or reorder the rows
  // (same search query) are skipped.
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

  // The data methods build their URLs from the DataTable's AJAX URL and last request, so each waits for the table's
  // initial data first (it may be called earlier, e.g. by a cell style while the first rows are rendered).

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

  // Never rejects: resolves to null if any range request fails.
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

  // The last data request's parameters as a query string, encoded as DataTables sent them (jQuery's $.param, after any
  // ajax.data additions such as the PDB table's effector), without the draw, paging and ordering parameters.
  // Empty before the first request.
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

  _url(path, queryString) {
    const url = this._dataTable.ajax.url() + path;
    return queryString ? url + "?" + queryString : url;
  }

  async _fetchJson(url, signal) {
    const response = await fetch(url, { signal });
    if (!response.ok) {
      throw new Error(`HTTP error when fetching ${url}! Status: ${response.status}`);
    }
    return response.json();
  }
}

export { ServerDataSource };
