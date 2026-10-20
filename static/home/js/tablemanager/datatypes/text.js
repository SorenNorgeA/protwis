// This class represents the "text" data type used in the table manager.
// It provides simple text filtering capabilities, allowing users to filter table data based on text input.

import { DataTypeBase } from "./datatypebase.js";

// Symbol popups close when the user clicks anywhere else. One document listener serves every popup, and popups
// that have been removed from the page (e.g. a destroyed table) are dropped from the set as they are found.
const symbolPopups = new Set();
let outsideClickListenerAdded = false;

function registerPopupForOutsideClick(popup) {
    if (!outsideClickListenerAdded) {
        outsideClickListenerAdded = true;
        document.addEventListener("click", () => {
            for (const p of symbolPopups) {
                if (!p.isConnected) {
                    symbolPopups.delete(p);
                } else {
                    p.classList.remove("tm-open");
                }
            }
        }, true);
    }
    symbolPopups.add(popup);
}

class Text extends DataTypeBase {

    dataTableRenderer(data, type, row, meta, tableManagerReference) {
        if (type === "display" && (data === null || data === undefined)) {
            return tableManagerReference.nullValuePlaceholder;
        }
        if (type === "display" && data) {
            const maxDisplayLength = this.column.max_display_length;
            if(maxDisplayLength && data.length > maxDisplayLength){
                let truncatedData = data.substring(0, maxDisplayLength) + "...";
                const span = document.createElement("span");
                span.setAttribute("title", data);
                span.textContent = truncatedData;
                return span.outerHTML;
            }
            return data;
        }
        return data; // Search, order and type return
    } 

    createFilterInterface(tableManagerReference) {
        const column = this.column;
        if (!column.allow_filter) {
            return null;
        }

        let filterInput = document.createElement("input");
        filterInput.setAttribute("data-field", column.json_id);
        // Prefix id with table id to avoid collisions when multiple tables have the same column on one page
        filterInput.setAttribute("id", tableManagerReference.tableId + "_" + column.json_id + "_search");

        filterInput.classList.add("col-filter");
        filterInput.classList.add("text-filter");

        if (column.cssClassFilterInput) {
            filterInput.classList.add(column.cssClassFilterInput);
        }

        filterInput.addEventListener("blur", (e) => this.blurHandler(e, tableManagerReference));
        filterInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                this.blurHandler(e, tableManagerReference);
            }
        });

        const wrapper = document.createElement("div");
        wrapper.classList.add("tm-text-filter-wrapper");
        wrapper.appendChild(filterInput);

        if (column.show_symbol_popup) {
            const { symbolBtn, popup } = this.createSymbolPopup(filterInput);
            wrapper.appendChild(symbolBtn);
            wrapper.appendChild(popup);
        }

        return wrapper;
    }

    createSymbolPopup(filterInput) {
        const greekSymbols = [
            { label: "α", name: "alpha" },
            { label: "β", name: "beta" },
            { label: "δ", name: "delta" },
            { label: "κ", name: "kappa" },
            { label: "μ", name: "mu" },
        ];

        const popup = document.createElement("div");
        popup.classList.add("tm-symbol-popup");

        greekSymbols.forEach(({ label, name }) => {
            const cell = document.createElement("button");
            cell.type = "button";
            cell.title = name;
            cell.textContent = label;
            cell.classList.add("tm-symbol-popup-item");
            cell.addEventListener("mousedown", (e) => {
                e.preventDefault();
                filterInput.value += label;
                filterInput.focus();
            });
            popup.appendChild(cell);
        });

        const symbolBtn = document.createElement("button");
        symbolBtn.type = "button";
        symbolBtn.textContent = "Ω";
        symbolBtn.classList.add("tm-symbol-button");

        symbolBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            popup.classList.toggle("tm-open");
        });

        registerPopupForOutsideClick(popup);

        return { symbolBtn, popup };
    }
        
    _stripHtmlTags(v) {
        return v == null ? "" : String(v).replace(/<[^>]*>/g, '');
    }

    async blurHandler(e, tableManagerReference) {
        const column = this.column;

        // If we are in the process of clearing filters, we don't want to trigger a redraw as it will be done after all filters have been cleared.
        if (tableManagerReference.state.clearingFilters) {
            return
        }

        const dataSource = tableManagerReference.dataSource;
        const newSearchValue = e.target.value.trim();

        // An empty input gives no terms, which clears the filter
        const terms = [];
        if (newSearchValue !== "") {
            // Get the list of unique values for the column. Client-side these are the raw cell data, so compare
            // against their text; server-side they are the values the server matches on, so compare them as given.
            let dataOptions = await dataSource.getOptions(column);
            if (!dataSource.isServerSide) {
                dataOptions = dataOptions.map((item) => this._stripHtmlTags(item));
            }

            //if the search value is not in the list of unique values, use a "contains" search to allow for partial matches
            terms.push({ value: newSearchValue, contains: !dataOptions.includes(newSearchValue) });
        }

        // Redraws only if the filter has changed
        dataSource.applyColumnFilter(column, { type: "text", terms: terms });
    }
}

export { Text };