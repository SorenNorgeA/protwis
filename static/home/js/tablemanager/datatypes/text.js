// This class represents the "text" data type used in the table manager.
// It provides simple text filtering capabilities, allowing users to filter table data based on text input.

import { DataTypeBase } from "./datatypebase.js";

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
        wrapper.style.cssText = "position:relative;display:inline-flex;align-items:center;gap:2px;";
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
        let popupCss = "display:none;"
        popupCss += "position:absolute;"
        popupCss += "z-index:9999;"
        popupCss += "background:#fff;"
        popupCss += "border:1px solid #ccc;"
        popupCss += "border-radius:4px;"
        popupCss += "padding:4px;"
        popupCss += "box-shadow:0 2px 6px rgba(0,0,0,0.2);"
        popupCss += "display:none;"
        popupCss += "grid-template-columns:repeat(3,1fr);"
        popupCss += "gap:3px;"
        popup.style.cssText = popupCss;

        greekSymbols.forEach(({ label, name }) => {
            const cell = document.createElement("button");
            cell.type = "button";
            cell.title = name;
            cell.textContent = label;
            let cellCss = "cursor:pointer;"
            cellCss += "border:1px solid #ddd;"
            cellCss += "border-radius:3px;"
            cellCss += "background:#f8f8f8;"
            cellCss += "padding:2px 5px;"
            cellCss += "font-size:1em;"
            cellCss += "line-height:1.4;"
            cell.style.cssText = cellCss;
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
        let symbolBtnCss = "cursor:pointer;"
        symbolBtnCss += "border:1px solid #ccc;"
        symbolBtnCss += "border-radius:3px;"
        symbolBtnCss += "background:#f0f0f0;"
        symbolBtnCss += "padding:1px 4px;"
        symbolBtnCss += "font-size:0.8em;"
        symbolBtnCss += "white-space:nowrap;"
        symbolBtn.style.cssText = symbolBtnCss;

        let popupVisible = false;

        symbolBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            popupVisible = !popupVisible;
            popup.style.display = popupVisible ? "grid" : "none";
        });

        document.addEventListener("click", () => {
            if (popupVisible) {
                popupVisible = false;
                popup.style.display = "none";
            }
        }, true);

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