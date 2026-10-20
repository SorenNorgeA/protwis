class ShowBestResolution {

    // json_id of the boolean-like column flagging the best resolution row for each receptor and state
    static COLUMN_JSON_ID = 'resolution_best';
    static ON_TOOLTIP = 'Limit table to showing only the best resolution (for each receptor and state)';
    static OFF_TOOLTIP = 'Show all resolutions (for each receptor and state)';
    static BUTTON_TEXT_ON = 'Show Best Resolution';
    static BUTTON_TEXT_OFF = 'Show All Resolutions';

    button_config
    tableManagerReference
    buttonNode = null
    bestOnly = false

    constructor(tableManagerReference) {
        this.tableManagerReference = tableManagerReference;
        this.button_config = {
            text: ShowBestResolution.BUTTON_TEXT_ON,
            className: 'toggle_on',
            titleAttr: ShowBestResolution.ON_TOOLTIP,
        }

        this.button_config.action = (e, dt, node, config, cb) => { this._actionHandler(node) };
    }

    attach(index) {
        this.tableManagerReference.dataTableReference.button().add(index, this.button_config);

        // Reset the toggle to its initial "On" state (filter off) when Clear Filters clears all column searches
        this.tableManagerReference.eventPubSub.subscribe('filtersCleared', () => {
            this.bestOnly = false;
            this._renderState();
        });
    }

    _actionHandler(node) {
        this.buttonNode = node;
        this.bestOnly = !this.bestOnly;

        const column = this.tableManagerReference.columns.find((x) => x.json_id == ShowBestResolution.COLUMN_JSON_ID);
        // Filter on: hide rows where the best resolution flag is not "true". Filter off: no terms.
        const terms = this.bestOnly ? [{ value: 'true', contains: false }] : [];
        this.tableManagerReference.dataSource.applyColumnFilter(column, { type: 'text', terms: terms });

        this._renderState();
    }

    // Show the button as the action it offers next: "On" turns the best resolution filter on, "Off" turns it off
    _renderState() {
        if (!this.buttonNode) return; // Never clicked, so the initial "On" state is still showing

        const $node = $(this.buttonNode);
        $node.toggleClass('toggle_on', !this.bestOnly);
        $node.toggleClass('toggle_off', this.bestOnly);
        $node.attr('title', this.bestOnly ? ShowBestResolution.OFF_TOOLTIP : ShowBestResolution.ON_TOOLTIP);
        $node.text(this.bestOnly ? ShowBestResolution.BUTTON_TEXT_OFF : ShowBestResolution.BUTTON_TEXT_ON);
    }
}

export { ShowBestResolution };
