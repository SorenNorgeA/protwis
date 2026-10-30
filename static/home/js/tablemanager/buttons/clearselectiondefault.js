import { Checkbox } from "../datatypes/checkbox.js";

class ClearSelectionDefault {

    button_config
    tableManagerReference

    /**
     * Creates the Clear Selection button configuration.
     * @param {Object} tableManagerReference - The TableManager instance that owns the table.
     */
    constructor(tableManagerReference) {
        this.tableManagerReference = tableManagerReference;
        this.button_config = {
            text: 'Clear Selection',
        }

        this.button_config.action = (e, dt, node, config, cb) => { this._actionHandler(e, dt, node, config, cb, this.tableManagerReference) };
    }

    /**
     * Adds the Clear Selection button to the DataTable button bar.
     * @param {number} index - Position at which the button is inserted into the button bar.
     */
    attach(index) {
        this.tableManagerReference.dataTableReference.button().add(index, this.button_config);
    }

    /**
     * Clears the selection. Empties the activeCheckBoxes state and unchecks the checkboxes in the rendered rows, then
     * publishes a single checkboxStateChanged event
     * ({cleared: true, checked: false, clearedCheckBoxes: [[rowId, cellData], ...]}) so subscribers and the master
     * checkbox stay in step. Rows on other pages are unchecked by synchroniseVisibleCheckState when they are drawn.
     * Does nothing if the table has no checkbox column.
     * @param {Event} e - The click event from the button.
     * @param {DataTables.Api} dt - The DataTables API instance.
     * @param {HTMLElement} node - The button element.
     * @param {Object} config - The button configuration.
     * @param {Function} cb - The Buttons callback (unused).
     * @param {Object} tableManagerReference - The TableManager instance that owns the table.
     */
    _actionHandler(e, dt, node, config, cb, tableManagerReference) {
        const activeCheckboxes = tableManagerReference.state.activeCheckBoxes;
        if (!activeCheckboxes) {
            return;
        }

        const previouslyActive = [...activeCheckboxes];
        activeCheckboxes.clear();

        // Uncheck the rendered checkboxes before notifying, so subscribers see a consistent table
        Checkbox.synchroniseVisibleCheckState(tableManagerReference);

        // A single event for the whole clear. Subscribers that only read the activeCheckBoxes state need nothing more;
        // the rows that were deselected are provided for those that do.
        tableManagerReference.eventPubSub.publish('checkboxStateChanged', { cleared: true, checked: false, clearedCheckBoxes: previouslyActive });
    }
}

export { ClearSelectionDefault };
