class ClearFiltersDefault {
    
    button_config
    tableManagerReference

    /**
     * Creates the Clear Filters button configuration.
     * @param {Object} tableManagerReference - The TableManager instance that owns the table.
     */
    constructor(tableManagerReference) {
        this.tableManagerReference = tableManagerReference;
        this.button_config = {
            text: 'Clear Filters',
        }
        
        this.button_config.action = (e, dt, node, config, cb) => { this._actionHandler(e, dt, node, config, cb, this.tableManagerReference) };
    }
    
    /**
     * Initialises the clearingFilters state flag, records the table's initial ordering and adds the Clear Filters button
     * to the DataTable button bar.
     * @param {number} index - Position at which the button is inserted into the button bar.
     */
    attach(index) {
        // Initialize the clearingFilters state --- used to prevent unnecessary redraws when clearing filters
        this.tableManagerReference.state.clearingFilters = false;

        // The buttons are attached right after the DataTable is created, so this is the configured initial ordering.
        // The entries are copied, as DataTables may update its ordering array in place when the user orders the table.
        this.initialOrder = this.tableManagerReference.dataTableReference.order()
            .map((entry) => Array.isArray(entry) ? [...entry] : { ...entry });

        this.tableManagerReference.dataTableReference.button().add(index, this.button_config);
    }

    /**
     * Resets the table to its initial state. Resets the col-filter controls in the filter header row, clears the
     * DataTable column and global searches, restores the initial ordering and returns to the first page, then publishes
     * "filtersCleared" so injected controls can reset. The clearingFilters flag is set for the duration, so the
     * resulting redraws are suppressed, and is always released afterwards.
     * @param {Event} e - The click event from the button.
     * @param {DataTables.Api} dt - The DataTables API instance.
     * @param {HTMLElement} node - The button element.
     * @param {Object} config - The button configuration.
     * @param {Function} cb - The Buttons callback (unused).
     * @param {Object} tableManagerReference - The TableManager instance that owns the table.
     */
    _actionHandler(e, dt, node, config, cb, tableManagerReference) {
        tableManagerReference.state.clearingFilters = true; // Set a flag to indicate that filters are being cleared

        try {
            // Clear the filter controls created by the data types. Only elements marked with the "col-filter" class are
            // reset, so other controls injected into the filter row (e.g. toggle buttons) keep their labels and values.
            const filterHeaderRow = document.getElementById(tableManagerReference.filterHeaderRowId);
            if (filterHeaderRow) {
                const inputs = filterHeaderRow.querySelectorAll('.col-filter');
                inputs.forEach(input => {
                    if (input.type === 'checkbox' || input.type === 'radio') {
                        // Do nothing to checkboxes to preserve row selection state
                    } else if (input.tagName === 'SELECT' && $(input).data('select2')) {
                        $(input).val(null).trigger('change');
                    } else {
                        input.value = '';
                    }
                });
            }

            // Clear the DataTable search state and restore the initial ordering, then redraw once. Each call is made on the
            // table API: chained after columns(), order() would instead order by the selected columns. The full redraw
            // also returns to the first page.
            const dataTable = tableManagerReference.dataTableReference;
            dataTable.search('');
            // Column searches are cleared one column at a time: since DataTables 3, columns().search() sets a single
            // search across all the selected columns together, and leaves each column's own search (set by the column
            // filters through column().search()) in place.
            dataTable.columns().every(function () {
                this.search('');
            });
            dataTable.order(this.initialOrder);
            dataTable.draw();

            // Let injected controls (e.g. filter header toggle buttons) reset themselves to match the cleared state
            tableManagerReference.eventPubSub.publish("filtersCleared");
        } finally {
            // Always release the flag, even if clearing fails part way, otherwise filters would stop triggering redraws
            tableManagerReference.state.clearingFilters = false;
        }
    }
}

export { ClearFiltersDefault };