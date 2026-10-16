// Data source for DataTables server-side processing.
// Column data comes from endpoints at fixed extensions of the table's AJAX URL (PATHS), and column filters are sent
// to the server as JSON in the column search value.

import { DataSourceBase } from "./datasourcebase.js";
import { DataTablesParameterBuilder } from "../tablemanager.js";

class ServerDataSource extends DataSourceBase {

  // Endpoint extensions of the table's AJAX URL
  static PATHS = {
    options: (column) => `/options/${encodeURIComponent(column)}/`,
    range: (column) => `/range/${encodeURIComponent(column)}/`,
    counts: () => `/countdistinct`,
  };

  get isServerSide() {
    return true;
  }

  async getOptions(column) {
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

  async getDistinctCounts(settings) {
    // Key on the complete request (endpoint plus every search/order parameter) so any change that alters the counts misses the cache
    const requestUrl = this._url(ServerDataSource.PATHS.counts(), this._currentSearchQuery(settings));
    const cacheKey = { type: 'columnCounts', url: requestUrl };

    let counts = this.tableManagerReference.cache.get(cacheKey);
    if (!counts) {
      counts = await this._fetchJson(requestUrl);
      this.tableManagerReference.cache.push(cacheKey, counts);
    }
    return counts;
  }

  async getSearchSetRows(column, signal) {
    // The search set uses the column's options endpoint with the current search parameters: for the checkbox
    // column the server returns each matching row's checkbox data ({json_id: {row_id, data}}) instead of distinct values.
    const settings = this.tableManagerReference.dataTableReference.settings()[0];
    const rows = await this._fetchJson(this._url(ServerDataSource.PATHS.options(column.json_id), this._currentSearchQuery(settings)), signal);
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

  // The current search, order and column parameters as a query string, without paging (start and length 0)
  _currentSearchQuery(settings) {
    return DataTablesParameterBuilder.buildServerSideParams(settings, 0, 0, 0).queryString;
  }

  _url(path, queryString) {
    const url = this.tableManagerReference.dataTableReference.ajax.url() + path;
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
