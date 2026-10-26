// This class represents the "checkbox" data type used in the table manager.
// This is a specialised data type that renders a checkbox in each cell of the column. 
// It allows users to select or deselect rows by checking or unchecking the checkboxes.

import { DataTypeBase } from "./datatypebase.js";

/**
 * Data type for checkbox columns. Each cell renders a checkbox. Checked rows are tracked in the table manager's
 * activeCheckBoxes state, so the selection is kept across pages and filters.
 * @extends DataTypeBase
 */
class Checkbox extends DataTypeBase {
    
    /**
     * Renders the checkbox for a cell. The display type builds an input with the row id and the row's data as data-*
     * attributes. Other types return the row id, for search, ordering and type detection.
     * @param {{row_id: (number|string), data?: Object}|null} data - The cell data for the row.
     * @param {string} type - The DataTables render type: "display", "filter", "sort" or "type".
     * @param {*} row - The full row data (unused).
     * @param {Object} meta - The DataTables cell metadata (unused).
     * @param {Object} tableManagerReference - The table manager that owns the table.
     * @returns {string|number} The checkbox HTML for the display type, otherwise the row id, or "" if there is no data.
     */
    dataTableRenderer(data, type, row, meta, tableManagerReference) {
        if (type === "display" && data) {
            let id_property = '';
            let data_properties = '';

            const newCheckBox = document.createElement("input");
            newCheckBox.type = "checkbox";
            
            if (data.row_id !== undefined) {
                newCheckBox.id = 'chkbx_' + tableManagerReference.tableId + '_' + data.row_id;
                newCheckBox.setAttribute('data-row_id', data.row_id);
            }

            if (data.data !== undefined) {
                let keys = Object.getOwnPropertyNames(data.data)
                for (const key of keys) {
                    newCheckBox.setAttribute('data-' + key, data.data[key]);
                }
            }

            return newCheckBox.outerHTML;
        }
        // Search, order and type return
        return data ? data.row_id : ""
    }

    /**
     * Attaches the checkbox event handlers to a cell. Handlers come from the column's eventRegister, or from the default
     * click handler if none is set. If any event enables row click, clicking anywhere on the row toggles the checkbox.
     * @param {HTMLElement} cell - The rendered table cell containing the checkbox.
     * @param {{row_id: (number|string), data?: Object}} cellData - The cell data for the row. Nothing is attached if it is empty.
     * @param {*} rowData - The full row data (unused).
     * @param {number} rowIndex - The index of the row (unused).
     * @param {number} colIndex - The index of the column (unused).
     * @param {Object} tableManagerReference - The table manager that owns the table.
     */
    eventHookup(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference) {
        if (!cellData) {
            return;
        }
        
        // Resolved once per column and kept on the data type, so the column specification is not modified.
        // Use the column's eventRegister if specified, otherwise register and use the default click handler.
        if (!this.eventRegister)
        {
            if (this.column.eventRegister) {
                this.eventRegister = this.column.eventRegister;
            } else {
                tableManagerReference.eventRegistry.registerEventHandler('defaultCheckboxClick', this.defaultClickHandler.bind(this));
                this.eventRegister = {
                    'click': {
                        handler: 'defaultCheckboxClick',
                        enableRowClick: true
                    }
                };
            }
        }

        const checkbox = cell.querySelector('input[type="checkbox"]');
        let anyRowClick = false;

        for (const event of Object.getOwnPropertyNames(this.eventRegister)) {
            const handlerName = this.eventRegister[event].handler;
            const handlerFunction = tableManagerReference.eventRegistry.getEventHandler(handlerName);
            
            if (handlerFunction == undefined) {
                console.error(`Handler function ${handlerName} not found in event registry.`);
                continue;
            }

            const listener = (e) => handlerFunction(e, cellData, tableManagerReference);
            checkbox?.addEventListener(event, listener);

            if (this.eventRegister[event].enableRowClick) {
                anyRowClick = true;
            }
        }

        // Add a single listener to the parent row if any event enables row click, so that clicking anywhere
        // on the row toggles the checkbox. Registered once regardless of how many events enable it.
        if (anyRowClick && checkbox) {
            cell.parentElement?.addEventListener("click", (e) => {
                // Ignore clicks on (or inside) interactive elements: links keep their navigation,
                // and the checkbox itself must not be toggled twice.
                if (e.target.closest("a, input, button, select, label")) {
                    return;
                }
                checkbox.click();
            });
        }
    }

    /**
     * Default click handler. Adds the row to, or removes it from, the active checkboxes state, then publishes a
     * checkboxStateChanged event. In single-select mode (checkboxMultiSelect: false) checking a row first clears any
     * other selection.
     * @param {Event} e - The click event. Its target is the checkbox.
     * @param {{row_id: (number|string), data?: Object}} cellData - The cell data for the row.
     * @param {Object} tableManagerReference - The table manager that owns the table.
     */
    defaultClickHandler(e, cellData, tableManagerReference) {
        const checkbox = e.target;
        const rowId = Number.parseInt(cellData.row_id);

        if (checkbox.checked) {
            if (this.column.checkboxMultiSelect === false) {
                this.clearOtherSelections(checkbox, rowId, tableManagerReference);
            }
            tableManagerReference.state.activeCheckBoxes.set(rowId, cellData);
        } else {
            tableManagerReference.state.activeCheckBoxes.delete(rowId);
        }

        tableManagerReference.eventPubSub.publish('checkboxStateChanged', { rowId: rowId, checked: checkbox.checked, cellData: cellData });
    }

    /**
     * Single-select helper. Removes every other row from the active checkboxes state, publishes an unchecked
     * checkboxStateChanged event for each one, and unchecks their rendered checkboxes.
     * @param {HTMLInputElement} checkbox - The checkbox that was just checked. It is left alone.
     * @param {number} rowId - The row id of the checkbox that was just checked.
     * @param {Object} tableManagerReference - The table manager that owns the table.
     */
    clearOtherSelections(checkbox, rowId, tableManagerReference) {
        const activeCheckboxes = tableManagerReference.state.activeCheckBoxes;

        for (const [activeRowId, activeCellData] of [...activeCheckboxes]) {
            if (activeRowId === rowId) {
                continue;
            }
            activeCheckboxes.delete(activeRowId);
            tableManagerReference.eventPubSub.publish('checkboxStateChanged', { rowId: activeRowId, checked: false, cellData: activeCellData });
        }

        // Rendered rows on the current page; rows on other pages are unchecked by synchroniseVisibleCheckState when drawn
        checkbox.closest("tbody")?.querySelectorAll('input[type="checkbox"]').forEach((rowCheckbox) => {
            if (rowCheckbox !== checkbox) {
                rowCheckbox.checked = false;
            }
        });
    }

    /**
     * Syncs the checked state of the checkboxes in the rendered rows with the active checkboxes state. Does nothing
     * if the table has no checkbox column.
     * @param {Object} tableManagerReference - The table manager that owns the table.
     */
    static synchroniseVisibleCheckState(tableManagerReference) {
    const checkboxCol = tableManagerReference.columns.find((col) => col.data_type === "checkbox");
    if (!checkboxCol) {
      return;
    }

    // Sync the check state of the checkboxes in the rendered rows with the activeCheckBoxes state    
    tableManagerReference.dataTableReference.cells(null, checkboxCol.col_idx, { search: "applied" }).nodes().each((cell) => {
      const rowCheckbox = cell.querySelector('input[type="checkbox"]');
      if (rowCheckbox) {
        tableManagerReference.state.activeCheckBoxes.has(Number.parseInt(rowCheckbox.dataset.row_id)) ? rowCheckbox.checked = true : rowCheckbox.checked = false;
      }
    });
  }
    
}

/**
 * The header checkbox for a checkbox column. Clicking it selects or deselects every row matching the current search
 * and filters, across all pages. Its state shows whether those rows are all, some or none selected.
 */
class MasterCheckbox{

    column;
    tableManagerReference;
    checkboxElement;
    pending = null; // Promise for the in-flight search-set request
    searchSetValid = true; // False while an update is pending or if the last one failed
    abortController = null; // Cancels a superseded request
    
    /**
     * @param {Object} checkboxColumnColumnReference - The checkbox column specification.
     * @param {Object} tableManagerReference - The table manager that owns the table.
     */
    constructor(checkboxColumnColumnReference, tableManagerReference) {        
        this.column = checkboxColumnColumnReference;
        this.tableManagerReference = tableManagerReference;
    }

    /**
     * Creates the master checkbox input and connects its change event to clickHandler. In single-select mode
     * (checkboxMultiSelect: false) the master checkbox is disabled, so it does nothing.
     * @returns {HTMLInputElement} The master checkbox element.
     */
    createHtmlElement() {
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.disabled = this.column.checkboxMultiSelect === false;
        checkbox.addEventListener("change", (e) => this.clickHandler(e, this.tableManagerReference));
        this.checkboxElement = checkbox;
        return checkbox;
    }

    /**
     * Handles a change to the master checkbox. Waits for any in-flight search-set request, then selects or deselects
     * every row in the search set. If the search set could not be loaded, the click is ignored and the checkbox is
     * reverted to match the active state.
     * @async
     * @param {Event} event - The change event. Its target is the master checkbox.
     * @param {Object} tableManagerReference - The table manager that owns the table.
     * @returns {Promise<void>}
     */
    async clickHandler(event, tableManagerReference) {
        const masterCheckbox = event.target;
        const activeCheckboxes = tableManagerReference.state.activeCheckBoxes;

        // Wait for any in-flight search-set request (never rejects). Loop in case a newer search
        // superseded the request we were waiting on.
        let awaited;
        do {
            awaited = this.pending;
            await awaited;
        } while (awaited !== this.pending);

        // The search set is stale if the last update failed, so don't act on the click
        if (!this.searchSetValid) {
            console.error("Master checkbox click ignored: the search set could not be updated from the server.");
            this.synchroniseCheckedState(); // Revert the checkbox to reflect the real state
            return;
        }

        // Cell data for every row matching the current search/filters, regardless of pagination
        let searchSetCheckboxes = tableManagerReference.state.currentSearchCheckBoxes;

        this.applyMasterCheckClick(masterCheckbox, activeCheckboxes, searchSetCheckboxes, tableManagerReference);
    }

    /**
     * Adds or removes every row in the search set from the active checkboxes, depending on the master checkbox state.
     * A click from the indeterminate state always selects all rows. Rendered rows on the current page are updated to match.
     * @param {HTMLInputElement} masterCheckboxElement - The master checkbox element.
     * @param {Map<number, Object>} activeCheckboxes - The active checkboxes, keyed by row id.
     * @param {Map<number, Object>} searchSetCheckboxes - Checkbox data for every row matching the current search, keyed by row id.
     * @param {Object} tableManagerReference - The table manager that owns the table.
     */
    applyMasterCheckClick(masterCheckboxElement, activeCheckboxes, searchSetCheckboxes, tableManagerReference) {        
        const wasIndeterminate = masterCheckboxElement.indeterminate;       

        let isChecked;
        // If transitioning from indeterminate, force checked state and add all rows
        if (wasIndeterminate) {
            isChecked = true;
            masterCheckboxElement.checked = true;
            masterCheckboxElement.indeterminate = false;

            searchSetCheckboxes.forEach((checkBox, row_id) =>  {
                activeCheckboxes.set(row_id, checkBox);
            });
        } else {
            // Normal toggle: checked = add all, unchecked = remove all
            isChecked = masterCheckboxElement.checked;
            searchSetCheckboxes.forEach( (checkBox, row_id) =>  {
                isChecked ? activeCheckboxes.set(row_id, checkBox) : activeCheckboxes.delete(row_id);                
            });
        }

        // Sync only the rows currently rendered in the DOM
        tableManagerReference.dataTableReference.cells(null, this.column.col_idx, { page: "current", search: "applied" }).nodes().each((cell) => {
        const rowCheckbox = cell.querySelector('input[type="checkbox"]');
        if (rowCheckbox) {
            rowCheckbox.checked = isChecked;
        }
        });
    }

    /**
     * Reloads the search set for the current search and filters, then updates the master checkbox to match.
     * @async
     * @returns {Promise<void>}
     */
    async updateCheckBoxSearchSet() {
        // Update the currentSearchCheckBoxes map with the checkbox data for all rows matching the current search/filters, regardless of pagination
        await this.populateCurrentSearchCheckBoxes();

        this.synchroniseCheckedState();
    }

    /**
     * Loads the checkbox data for every row matching the current search and filters from the data source, and stores it
     * in state.currentSearchCheckBoxes keyed by row id. A newer call aborts any request still in flight. On failure the
     * error is logged and the previous search set is kept. Never rejects.
     * @async
     * @returns {Promise<void>} Resolves once the search set has been updated.
     */
    async populateCurrentSearchCheckBoxes() {
        // A newer search supersedes any request still in flight, so a stale response can never overwrite fresh state
        this.abortController?.abort();
        const controller = new AbortController();
        this.abortController = controller;

        // Stored as a promise so anything needing the search set (e.g. the master checkbox click) can await it.
        // It never rejects: failures are logged and the previous search set is kept.
        this.searchSetValid = false;
        const request = (async () => {
            try {
                const checkBoxData = await this.tableManagerReference.dataSource.getSearchSetRows(this.column, controller.signal);
                if (controller.signal.aborted) {
                    return;
                }

                this.tableManagerReference.state.currentSearchCheckBoxes.clear();

                for (const checkbox of checkBoxData) {
                    if (checkbox?.row_id != null) {
                        const rowId = Number.parseInt(checkbox.row_id);
                        this.tableManagerReference.state.currentSearchCheckBoxes.set(rowId, checkbox);
                    }
                }
                this.searchSetValid = true;
            } catch (error) {
                if (error.name !== "AbortError") {
                    console.error("Failed to get checkbox data for the current search set:", error);
                }
            }
        })();

        this.pending = request;
        await request;
    }

    /**
     * Sets the master checkbox to checked, unchecked or indeterminate, depending on how many rows in the current
     * search set are active.
     */
    synchroniseCheckedState() {

        // Create a set from the currently checked checkboxes
        const activeRowIds = new Set(this.tableManagerReference.state.activeCheckBoxes.keys());
        // Create a set from the checkboxes that are captured in the current search results
        const currentSearchRowIds = new Set(this.tableManagerReference.state.currentSearchCheckBoxes.keys());

        const allChecked = activeRowIds.size > 0 && 
                            (this.setsAreEqual(activeRowIds, currentSearchRowIds) || //Search set and active set are equal
                            this.setContains(activeRowIds, currentSearchRowIds)); //Search set is a subset of active set
        const allUnchecked = activeRowIds.size == 0 || this.setDoesNotContain(activeRowIds, currentSearchRowIds); //Search set has no overlap with the active set

        // Mixed state shows as indeterminate; otherwise checked only if every rendered row is checked
        const isIndeterminate = !allChecked && !allUnchecked;
        this.checkboxElement.indeterminate = isIndeterminate;
        this.checkboxElement.checked = allChecked && !isIndeterminate;
    }

    /**
     * @param {Set<*>} a - The first set.
     * @param {Set<*>} b - The second set.
     * @returns {boolean} True if both sets hold the same values.
     */
    setsAreEqual(a, b) {
        try {
            return a.size === b.size && a.symmetricDifference(b).size === 0;
        } catch (error) { // Older browsers may not support Set methods like symmetricDifference, so we fall back to a manual comparison
            if (a === b) return true;
            if (a.size !== b.size) return false;
            for (const value of a) if (!b.has(value)) return false;
            return true;
        }
    }

    /**
     * @param {Set<*>} setA - The containing set.
     * @param {Set<*>} setB - The set that should be contained in setA.
     * @returns {boolean} True if every value in setB is also in setA.
     */
    setContains(setA, setB) {
        try {
            return setB.isSubsetOf(setA);
        } catch (error) { // Older browsers may not support Set methods like isSubsetOf, so we fall back to a manual comparison
        for (const value of setB) {
                if (!setA.has(value)) {
                    return false;
                }
            }
            return true;
        }
    }

    /**
     * @param {Set<*>} setA - The first set.
     * @param {Set<*>} setB - The second set.
     * @returns {boolean} True if setA and setB have no values in common.
     */
    setDoesNotContain(setA, setB) {
        try {
            return setB.isDisjointFrom(setA);
        } catch (error) { // Older browsers may not support Set methods like isSubsetOf, so we fall back to a manual comparison
            for (const value of setB) {
                if (setA.has(value)) {
                    return false;
                }
            }
            return true;
        }
    }
}

export { Checkbox, MasterCheckbox };