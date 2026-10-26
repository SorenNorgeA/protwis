class ShowClosestToHuman {

    // json_id of the boolean-like column flagging the Human (or closest species) row for each receptor and state
    static COLUMN_JSON_ID = 'closest_to_human';
    static ON_TOOLTIP = 'Limit table to showing only Human or closest species (for each receptor and state)';
    static OFF_TOOLTIP = 'Show all structures';
    static BUTTON_TEXT_ON = 'Show Human (Or Closest) Only';
    static BUTTON_TEXT_OFF = 'Show All Structures';

    button_config
    tableManagerReference
    buttonNode = null
    closestOnly = false

    /**
     * Creates the Show Human (Or Closest) Only toggle button configuration, starting in the initial state (filter off).
     * @param {Object} tableManagerReference - The TableManager instance that owns the table.
     */
    constructor(tableManagerReference) {
        this.tableManagerReference = tableManagerReference;
        this.button_config = {
            text: ShowClosestToHuman.BUTTON_TEXT_ON,
            className: 'toggle_on',
            titleAttr: ShowClosestToHuman.ON_TOOLTIP,
        }

        this.button_config.action = (e, dt, node, config, cb) => { this._actionHandler(node) };
    }

    /**
     * Adds the toggle button to the DataTable button bar and subscribes to "filtersCleared" so the toggle resets
     * when Clear Filters is used.
     * @param {number} index - Position at which the button is inserted into the button bar.
     */
    attach(index) {
        this.tableManagerReference.dataTableReference.button().add(index, this.button_config);

        // Reset the toggle to its initial state (filter off) when Clear Filters clears all column searches
        this.tableManagerReference.eventPubSub.subscribe('filtersCleared', () => {
            this.closestOnly = false;
            this._renderState();
        });
    }

    /**
     * Toggles the closest to human filter on the closest_to_human column and updates the button to match.
     * @param {HTMLElement} node - The button element that was clicked.
     */
    _actionHandler(node) {
        this.buttonNode = node;
        this.closestOnly = !this.closestOnly;

        const column = this.tableManagerReference.columns.find((x) => x.json_id == ShowClosestToHuman.COLUMN_JSON_ID);
        // Filter on: hide rows where the closest to human flag is not "true". Filter off: no terms.
        const terms = this.closestOnly ? [{ value: 'true', contains: false }] : [];
        this.tableManagerReference.dataSource.applyColumnFilter(column, { type: 'text', terms: terms });

        this._renderState();
    }

    /**
     * Shows the button as the action it offers next. Does nothing until the button has been clicked, while the
     * initial state is still showing.
     */
    _renderState() {
        if (!this.buttonNode) return; // Never clicked, so the initial state is still showing

        const $node = $(this.buttonNode);
        $node.toggleClass('toggle_on', !this.closestOnly);
        $node.toggleClass('toggle_off', this.closestOnly);
        $node.attr('title', this.closestOnly ? ShowClosestToHuman.OFF_TOOLTIP : ShowClosestToHuman.ON_TOOLTIP);
        $node.text(this.closestOnly ? ShowClosestToHuman.BUTTON_TEXT_OFF : ShowClosestToHuman.BUTTON_TEXT_ON);
    }
}

export { ShowClosestToHuman };
