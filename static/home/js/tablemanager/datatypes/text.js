// This class represents the "text" data type used in the table manager.
// It provides simple text filtering capabilities, allowing users to filter table data based on text input.

import { DataTypeBase } from "./datatypebase.js";

// Symbol popups close when the user clicks anywhere else. One document listener serves every popup, and popups
// that have been removed from the page (e.g. a destroyed table) are dropped from the set as they are found.
const symbolPopups = new Set();
let outsideClickListenerAdded = false;

/**
 * Registers a symbol popup to be closed by the next click anywhere on the document. The document listener is added
 * once, on first use.
 * @param {HTMLElement} popup - The symbol popup element.
 */
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

/**
 * Data type for text columns. Displays text, truncated to max_display_length when set, and filters by typed text.
 * Also the base class for the other text-based data types.
 * @extends DataTypeBase
 */
class Text extends DataTypeBase {

    /**
     * Renders text for display. Null values show the nullValuePlaceholder. Text longer than max_display_length is
     * truncated with an ellipsis, and the full text is kept in the title attribute. Other types return the raw data.
     * @param {string|null|undefined} data - The cell text.
     * @param {string} type - The DataTables render type: "display", "filter", "sort" or "type".
     * @param {*} row - The full row data (unused).
     * @param {Object} meta - The DataTables cell metadata (unused).
     * @param {Object} tableManagerReference - The table manager that owns the table.
     * @returns {string|null|undefined} The rendered value for the render type.
     */
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

    /**
     * Creates a text input that applies the filter when it loses focus or Enter is pressed. If show_symbol_popup is set,
     * a button is added beside the input that opens a popup of Greek symbols to insert into it. If filter_suggestions is
     * set, the input gets a datalist that the browser shows as a dropdown of matching column values while typing; it is
     * filled in afterDataLoaded, client-side only.
     * @param {Object} tableManagerReference - The table manager that owns the table.
     * @returns {HTMLDivElement|false} The filter wrapper, or false if the column does not allow filtering.
     */
    createFilterInterface(tableManagerReference) {
        const column = this.column;
        if (!column.allow_filter) {
            return false;
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

        if (column.filter_suggestions) {
            const suggestionList = document.createElement("datalist");
            suggestionList.setAttribute("id", tableManagerReference.tableId + "_" + column.json_id + "_suggestions");
            filterInput.setAttribute("list", suggestionList.id);
            filterInput.setAttribute("autocomplete", "off"); // show the column values, not the browser's form history
            wrapper.appendChild(suggestionList);
            // Kept for afterDataLoaded, which fills the list once the data is loaded
            this.suggestionList = suggestionList;

            // Picking a suggestion sets the input to exactly that value: apply it straight away (as an exact match)
            // rather than waiting for blur or Enter. Typed partial text still applies on blur or Enter.
            filterInput.addEventListener("input", (e) => {
                if (this.suggestionValues?.has(e.target.value.trim())) {
                    this.blurHandler(e, tableManagerReference);
                }
            });
        }

        if (column.show_symbol_popup) {
            const { symbolBtn, popup } = this.createSymbolPopup(filterInput);
            wrapper.appendChild(symbolBtn);
            wrapper.appendChild(popup);
        }

        return wrapper;
    }

    /**
     * Creates the Greek symbol button and its popup. Clicking a symbol appends it to the filter input as if it had been
     * typed: an input event is dispatched, and the filter_suggestions dropdown (if any) is reopened for the new text.
     * @param {HTMLInputElement} filterInput - The filter input to insert symbols into.
     * @returns {{symbolBtn: HTMLButtonElement, popup: HTMLDivElement}} The toggle button and the popup.
     */
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
                // Setting the value from script fires no input event, so dispatch one to treat the symbol like typing
                filterInput.dispatchEvent(new Event("input", { bubbles: true }));
                // Nor does it update the browser's datalist dropdown; reopen it for the new text. showPicker needs the
                // user activation this mousedown provides, and is not supported for datalists in every browser.
                if (filterInput.list) {
                    try {
                        filterInput.showPicker();
                    } catch {
                        // Suggestions then appear with the next typed character instead
                    }
                }
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
        
    /**
     * Fills the filter_suggestions datalist with the column's distinct values once the data is loaded. Client-side only:
     * server-side, the values would need a request per column and the filter applies on the server anyway.
     * The suggestions are the same plain-text values blurHandler compares against, so a picked suggestion is an exact match.
     * @async
     * @param {Object} tableManagerReference - The table manager that owns the table.
     * @returns {Promise<void>}
     */
    async afterDataLoaded(tableManagerReference) {
        const dataSource = tableManagerReference.dataSource;
        if (!this.suggestionList || dataSource.isServerSide) {
            return;
        }

        const options = await dataSource.getOptions(this.column);
        const values = Array.from(new Set(options.map((item) => this._stripHtmlTags(item).trim())))
            .filter((value) => value !== "")
            .sort((a, b) => a.localeCompare(b));

        this.suggestionValues = new Set(values);
        this.suggestionList.replaceChildren(...values.map((value) => {
            const option = document.createElement("option");
            option.value = value;
            return option;
        }));
    }

    /**
     * Removes HTML tags from a value, so it can be compared with plain text.
     * @param {*} v - The value to clean.
     * @returns {string} The value as text, or an empty string if it is null or undefined.
     */
    _stripHtmlTags(v) {
        return v == null ? "" : String(v).replace(/<[^>]*>/g, '');
    }

    /**
     * Applies the typed text as a column filter. If the text matches one of the column's options exactly, it filters on
     * that value. Otherwise it filters with a contains match. Empty text clears the filter. Does nothing while filters
     * are being cleared.
     * @async
     * @param {Event} e - The blur or Enter event. Its target is the filter input.
     * @param {Object} tableManagerReference - The table manager that owns the table.
     * @returns {Promise<void>}
     */
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