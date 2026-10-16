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
            if (this.precision !== undefined && !isNaN(this.precision)) {
                return numericValue.toFixed(this.precision);
            }
            return data;
        }
        // Search, order and type return
        return data;
    }
    createFilterInterface(tableManagerReference) {    
        let filterInputMin = document.createElement("input");
        filterInputMin.setAttribute("data-field", this.json_id);
        // Prefix id with table id to avoid collisions when multiple tables have the same column on one page
        filterInputMin.setAttribute("id", tableManagerReference.tableId + "_" + this.json_id + "_search_min");
        filterInputMin.setAttribute("placeholder", "Min");

        let filterInputMax = document.createElement("input");
        filterInputMax.setAttribute("data-field", this.json_id);
        // Prefix id with table id to avoid collisions when multiple tables have the same column on one page
        filterInputMax.setAttribute("id", tableManagerReference.tableId + "_" + this.json_id + "_search_max");
        filterInputMax.setAttribute("placeholder", "Max");

        filterInputMin.classList.add("col-filter");
        filterInputMin.classList.add("numericrange-filter");
        filterInputMax.classList.add("col-filter");
        filterInputMax.classList.add("numericrange-filter");

        if (this.cssClassFilterInput) {
        filterInputMin.classList.add(this.cssClassFilterInput);
        filterInputMax.classList.add(this.cssClassFilterInput);
        }

        let filterChangeListener = null;
        if (tableManagerReference.dataTableReference.settings()[0].oFeatures.bServerSide === true) {
        // ---- SERVER SIDE PROCESSING MODE ----
        filterChangeListener = this._createNumericFilterChangeListener_ServerSide(
            this.col_idx,
            filterInputMin,
            filterInputMax,
            tableManagerReference
        );
        } else {
        // ---- CLIENT SIDE PROCESSING MODE ----
        // add a custom search function to DataTables which will read the current values of the min and max
        // inputs and apply the appropriate filtering logic to the relevant column.
        // This function will be called automatically by DataTables whenever the table is redrawn
        // so we just need to trigger a redraw whenever the filter inputs change to ensure the filtering is applied.
        this._attachFixedSearchToDataTable(this.json_id, filterInputMin, filterInputMax, tableManagerReference.dataTableReference);
        // trigger redraw on filter input change to apply the filtering
        filterChangeListener = this._createNumericFilterChangeListener_ClientSide(tableManagerReference);
        }

        filterInputMin.addEventListener("blur", filterChangeListener);
        filterInputMax.addEventListener("blur", filterChangeListener);

        return [filterInputMin, filterInputMax];
    }


    _attachFixedSearchToDataTable(fieldJsonId, filterInputMin, filterInputMax, dataTableReference) {
        dataTableReference.search.fixed(
        "range_" + fieldJsonId,
        function (searchStr, data, index) {
            var min = parseFloat (filterInputMin.value);
            var max = parseFloat (filterInputMax.value);
            var value = parseFloat(data[fieldJsonId]);
            
            // exclude non-numeric cells (e.g. blank cells) from range filter when a filter is being applied, 
            // include them when no filter is set 
            if (isNaN(value) && (!isNaN(min) || !isNaN(max))) {
            return false; 
            }

            if (
            (isNaN(min) && isNaN(max)) ||
            (isNaN(min) && value <= max) ||
            (min <= value && isNaN(max)) ||
            (min <= value && value <= max)
            ) {
            return true;
            }
            return false;
        },
        );
    }

    _createNumericFilterChangeListener_ClientSide(tableManagerReference){        
        let handler = (e) => {
            // If we are in the process of clearing filters, we don't want to trigger a redraw as it will be done after all filters have been cleared.
            if (!tableManagerReference.state.clearingFilters) {
                tableManagerReference.dataTableReference.draw();            
            }
        }
        return handler;
    }

    _createNumericFilterChangeListener_ServerSide(
        columnIdx,
        filterInputMin,
        filterInputMax,
        tableManagerReference,
        ) {
        //create event callback function
        return function () {
            // For server-side processing, we will encode the range filter as a single search string in the format "min~max"
            // where min and max are the values from the two inputs, or -Inf/Inf if not set. The server can then parse this string to apply the appropriate filtering.
            let rangeMin = filterInputMin.value || "-Inf";
            let rangeMax = filterInputMax.value || "Inf";

            // DataTables sends the current search value for the column as the first element of the array returned by .search()
            // we can use this to avoid unnecessary reloads if the range hasn't actually changed
            let currentSearchValue =
            tableManagerReference.dataTableReference.columns(columnIdx).search()[0] || "-Inf~Inf"; // default to full range if no search value currently set

            let newSearchValue = `${rangeMin}~${rangeMax}`;

            if (newSearchValue !== currentSearchValue) {
                 // If we are in the process of clearing filters, we don't want to trigger 
                 // a redraw as it will be done after all filters have been cleared.
                if (!tableManagerReference.state.clearingFilters) {
                    tableManagerReference.dataTableReference.columns(columnIdx).search(newSearchValue).draw();
                }
            }
        };
    }
}

export { Numeric };