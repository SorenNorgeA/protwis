// This class represents the "numeric" data type used in the table manager.
// It provides numeric range filtering capabilities, allowing users to filter table data based on minimum and maximum numeric values.

import { DataTypeBase } from "./datatypebase.js";

class Numeric extends DataTypeBase {

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
    createFilterInterface(tableManagerReference) {
        const column = this.column;
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