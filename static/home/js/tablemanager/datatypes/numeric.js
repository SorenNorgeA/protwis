// This class represents the "numeric" data type used in the table manager.
// It provides numeric range filtering capabilities, allowing users to filter table data based on minimum and maximum numeric values.

import { DataTypeBase } from "./datatypebase.js";

/**
 * Data type for numeric columns. Displays values rounded to the column's precision, and filters by a numeric minimum
 * and maximum.
 * @extends DataTypeBase
 */
class Numeric extends DataTypeBase {

    /**
     * Renders numeric values. For the display type, null values show the nullValuePlaceholder, numeric values are
     * formatted to the column's precision when one is set, and non-numeric values are shown unchanged. Other types
     * return the raw data, so ordering and filtering use the original values.
     * @param {*} data - The cell data.
     * @param {string} type - The DataTables render type: "display", "filter", "sort" or "type".
     * @param {*} row - The full row data (unused).
     * @param {Object} meta - The DataTables cell metadata (unused).
     * @param {Object} tableManagerReference - The table manager that owns the table.
     * @returns {*} The rendered value for the render type.
     */
    dataTableRenderer(data, type, row, meta, tableManagerReference) {
        if (type === "display" && (data === null || data === undefined)) {
            return tableManagerReference.nullValuePlaceholder;
        }
        if (type === "display") {
            // Show non-numeric values (e.g. empty strings or placeholder text from older cached responses) unchanged rather than as "NaN"
            const numericValue = parseFloat(data);
            if (!Number.isFinite(numericValue)) {
                return data;
            }
            const precision = this.column.precision;
            if (precision !== undefined && !isNaN(precision)) {
                return numericValue.toFixed(precision);
            }
            return data;
        }
        // Search, order and type return
        return data;
    }
    /**
     * Creates the minimum and maximum inputs for a range filter. The range is applied when an input loses focus. An input
     * that is empty or not a number leaves that bound open, and clearing both removes the filter.
     * @param {Object} tableManagerReference - The table manager that owns the table.
     * @returns {HTMLInputElement[]} The minimum and maximum inputs, in that order.
     */
    createFilterInterface(tableManagerReference) {
        const column = this.column;
        
        if (!column.allow_filter) {
            return false;
        }
        
        let filterInputMin = document.createElement("input");
        filterInputMin.setAttribute("data-field", column.json_id);
        // Prefix id with table id to avoid collisions when multiple tables have the same column on one page
        filterInputMin.setAttribute("id", tableManagerReference.tableId + "_" + column.json_id + "_search_min");
        filterInputMin.setAttribute("placeholder", "Min");

        let filterInputMax = document.createElement("input");
        filterInputMax.setAttribute("data-field", column.json_id);
        // Prefix id with table id to avoid collisions when multiple tables have the same column on one page
        filterInputMax.setAttribute("id", tableManagerReference.tableId + "_" + column.json_id + "_search_max");
        filterInputMax.setAttribute("placeholder", "Max");

        filterInputMin.classList.add("col-filter");
        filterInputMin.classList.add("numericrange-filter");
        filterInputMax.classList.add("col-filter");
        filterInputMax.classList.add("numericrange-filter");

        if (column.cssClassFilterInput) {
        filterInputMin.classList.add(column.cssClassFilterInput);
        filterInputMax.classList.add(column.cssClassFilterInput);
        }

        // The range is applied through the data source: as a JSON {"min", "max"} search server-side, or a search function
        // on the column client-side. An input that is not a number leaves that bound open; no bounds clears the filter.
        const bound = (input) => {
            const value = parseFloat(input.value);
            return Number.isFinite(value) ? value : null;
        };
        const filterChangeListener = () => tableManagerReference.dataSource.applyColumnFilter(
            column,
            { type: "range", min: bound(filterInputMin), max: bound(filterInputMax) }
        );

        filterInputMin.addEventListener("blur", filterChangeListener);
        filterInputMax.addEventListener("blur", filterChangeListener);

        return [filterInputMin, filterInputMax];
    }
}

export { Numeric };