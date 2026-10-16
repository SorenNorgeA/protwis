// This class represents the "text" data type used in the table manager.
// It provides simple text filtering capabilities, allowing users to filter table data based on text input.

import { DataTypeBase } from "./datatypebase.js";

class Text extends DataTypeBase {

    dataTableRenderer(data, type, row, meta, tableManagerReference) {
        if (type === "display" && (data === null || data === undefined)) {
            return tableManagerReference.nullValuePlaceholder;
        }
        if (type === "display" && data) {
            if(this.max_display_length && data.length > this.max_display_length){
                let truncatedData = data.substring(0, this.max_display_length) + "...";
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
        if (!this.allow_filter) {
            return null;
        }

        let filterInput = document.createElement("input");
        filterInput.setAttribute("data-field", this.json_id);
        // Prefix id with table id to avoid collisions when multiple tables have the same column on one page
        filterInput.setAttribute("id", tableManagerReference.tableId + "_" + this.json_id + "_search");

        filterInput.classList.add("col-filter");
        filterInput.classList.add("text-filter");

        if (this.cssClassFilterInput) {
            filterInput.classList.add(this.cssClassFilterInput);
        }

        filterInput.addEventListener("blur", (e) => this.blurHandler(e, this.col_idx, tableManagerReference));
        filterInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                this.blurHandler(e, this.col_idx, tableManagerReference);
            }
        });

        const wrapper = document.createElement("div");
        wrapper.style.cssText = "position:relative;display:inline-flex;align-items:center;gap:2px;";
        wrapper.appendChild(filterInput);

        if (this.show_symbol_popup) {
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
        
    buildTextSearch(terms, serverSideProcessing) {
        // Builds the DataTables column search for a text filter (shared by Text and TextSelect) from a list of
        // terms {value, contains}: an exact match, or a "contains" (substring) match when contains is true.
        // Returns {value, regex} for column().search(value, regex); no terms gives an empty search (no filter).
        if (terms.length === 0) {
            return { value: "", regex: false };
        }

        if (serverSideProcessing) {
            // Server-side: a JSON filter object the server reads directly, so values are matched exactly as given
            // (no regex escaping or parsing). Both keys are always present, in a fixed order, so the same filter
            // always gives the same string and can be compared with the current search value.
            const filter = { in: [], contains: [] };
            terms.forEach(({ value, contains }) => (contains ? filter.contains : filter.in).push(String(value)));
            return { value: JSON.stringify(filter), regex: false };
        }

        // Client-side: DataTables' own search. A single exact value is a plain search; several values (ORed with "|")
        // or any contains match need a regex.
        if (terms.length === 1 && !terms[0].contains) {
            return { value: terms[0].value, regex: false };
        }
        const escape = (value) => String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); // escape regex special characters
        const value = terms
            .map(({ value, contains }) => contains
                ? "(.*" + escape(value) + ".*)" // wildcards for "contains" search
                : "(^" + escape(value) + "$)") // anchors for "exact match" search
            .join("|");
        return { value: value, regex: true };
    }

    _stripHtmlTags(v) {
        return v == null ? "" : String(v).replace(/<[^>]*>/g, '');
    }

    blurHandler = async (e, col_idx, tableManagerReference) => {

        // If we are in the process of clearing filters, we don't want to trigger a redraw as it will be done after all filters have been cleared.
        if (tableManagerReference.state.clearingFilters) {
            return
        }

        const currentSearchValue = tableManagerReference.dataTableReference.columns(col_idx).search()[0] || "";
        const newSearchValue = e.target.value.trim();

        // Handle empty input: clear filter if one is currently set, otherwise do nothing
        if (newSearchValue === "") {
            if (currentSearchValue) {
                tableManagerReference.dataTableReference.columns(col_idx).search("").draw();
            }
            // If already empty, no change needed - return early to avoid unnecessary work
            return;
        }

        //Get a list of unique values for the column, either from the server or from the client-side data
        let dataOptions;
        let jsonId = tableManagerReference.columns[col_idx].json_id;
        const serverSideProcessing = tableManagerReference.dataTableReference.settings()[0].oFeatures.bServerSide === true;
        if(serverSideProcessing) {
           dataOptions = await this._fetchColumnValueOptions(jsonId, tableManagerReference);
        }
        else {
            dataOptions = tableManagerReference.dataTableReference.column(col_idx).data().toArray().map((item) => this._stripHtmlTags(item));
        }

        //if the search value is not in the list of unique values, use a "contains" search to allow for partial matches
        const search = this.buildTextSearch([{ value: newSearchValue, contains: !dataOptions.includes(newSearchValue) }], serverSideProcessing);

        // Skip the redraw if the search hasn't actually changed
        if (search.value === currentSearchValue) {
            return;
        }

        tableManagerReference.dataTableReference.columns(col_idx).search(search.value, search.regex).draw();
    }

    async _fetchColumnValueOptions(ColumnJsonId, tableManagerReference) {
      const cacheKey = { type: 'columnOptions', columnId: ColumnJsonId };

      // Check cache first
      const cachedData = tableManagerReference.cache.get(cacheKey);
      if (cachedData !== undefined) {
        return cachedData;
      }

      // Fetch from endpoint if not in cache
      const dataEndpoint = tableManagerReference.dataTableReference.ajax.url();
      const optionsEndpoint = dataEndpoint + "/options/" + ColumnJsonId + "/";

      try {
        const response = await fetch(optionsEndpoint);
        if (!response.ok) {
          throw new Error(`HTTP error when fetching options for column ${ColumnJsonId}! Status: ${response.status}`);
        }
        const data = await response.json();

        // Store in cache before returning
        tableManagerReference.cache.push(cacheKey, data);

        return data;
      } catch (error) {
        console.error(`Error fetching column options for ${ColumnJsonId}:`, error);
        return [];
      }
    }
}

export { Text };