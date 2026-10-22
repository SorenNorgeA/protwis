// Column visibility control built on the DataTables Buttons colvis extension. The columns offered are the TableManager's
// column definitions, listed under their column_group (the super-header name). Clicking a column toggles it; clicking a
// group heading shows all of the group's columns, or hides them all when they are already all visible.
// Requires the DataTables column visibility extension (column_visibility-*.js) to be loaded.
class ColumnVisibility {

    static BUTTON_TEXT = 'Columns';
    static TOOLTIP = 'Show or hide table columns';
    static GROUP_TOOLTIP = 'Show or hide all columns in this group';
    // Added to the popup by the collectionLayout option; used to find the popup and to scope the column layout to it
    static LAYOUT_CLASS = 'colvis_grouped_columns';
    static GROUP_COLUMN_CLASS = 'colvis_group_column';
    static POPOVER_EVENT = 'buttons-popover.dt.colvisGroups';

    button_config
    tableManagerReference

    /**
     * Creates the column visibility (colvis) button configuration, with one toggle per column grouped under its
     * column_group heading.
     * @param {Object} tableManagerReference - The TableManager instance that owns the table.
     */
    constructor(tableManagerReference) {
        this.tableManagerReference = tableManagerReference;
        this.button_config = {
            extend: 'colvis',
            text: ColumnVisibility.BUTTON_TEXT,
            titleAttr: ColumnVisibility.TOOLTIP,
            className: 'colvis_button',
            collectionLayout: ColumnVisibility.LAYOUT_CLASS,
            buttons: this._buildCollectionButtons(),
        }
    }

    /**
     * Adds the column visibility button to the DataTable button bar and binds the popover handler that regroups the
     * popup's buttons. The handler is bound on document and shared by all tables, so re-binding replaces the previous one.
     * @param {number} index - Position at which the button is inserted into the button bar.
     */
    attach(index) {
        this.tableManagerReference.dataTableReference.button().add(index, this.button_config);
        // Buttons fires buttons-popover once a collection is drawn; regroup the popup's buttons into one column per group.
        // Bound on document (one handler shared by all tables), so re-binding replaces the previous handler
        $(document).off(ColumnVisibility.POPOVER_EVENT).on(ColumnVisibility.POPOVER_EVENT, ColumnVisibility._arrangeGroupColumns);
    }

    /**
     * Moves the group headings and column toggles into one column per group. Each heading starts a new column;
     * any columns without a group before the first heading go in a column of their own.
     * The popup's buttons are reused on every open, so they are collected from within the menu (which may already hold
     * the column wrappers from the previous open) rather than from its direct children.
     */
    static _arrangeGroupColumns() {
        document.querySelectorAll(`.dt-button-collection.${ColumnVisibility.LAYOUT_CLASS} > [role="menu"]`).forEach((menu) => {
            const columns = [];
            let current = null;
            Array.from(menu.querySelectorAll('.dt-button')).forEach((button) => {
                if (current === null || button.classList.contains('colvis_group_button')) {
                    current = document.createElement('div');
                    current.className = ColumnVisibility.GROUP_COLUMN_CLASS;
                    columns.push(current);
                }
                current.appendChild(button);
            });
            menu.replaceChildren(...columns);
        });
    }

    /**
     * Returns the columns the user can toggle. Checkbox columns are excluded because they have no title, and columns
     * configured as initially hidden are excluded because they are helper columns (e.g. the flags behind the toggle buttons).
     * @returns {Object[]} The TableManager column definitions that can be toggled.
     */
    _toggleableColumns() {
        return this.tableManagerReference.columns.filter((col) => col.data_type !== 'checkbox' && col.visible !== false);
    }

    /**
     * Groups the toggleable columns by column_group, in col_idx order. Columns without a group share a single unnamed group.
     * @returns {Map<?string, Object[]>} Map of group name (null for ungrouped columns) to its column definitions.
     */
    _groupColumns() {
        const groups = new Map();
        this._toggleableColumns().forEach((col) => {
            const name = col.column_group ?? null;
            if (!groups.has(name)) groups.set(name, []);
            groups.get(name).push(col);
        });
        return groups;
    }

    /**
     * Builds the buttons shown in the column visibility popup: a heading button for each named group followed by a
     * toggle button for each of its columns.
     * @returns {Object[]} The DataTables Buttons definitions for the popup.
     */
    _buildCollectionButtons() {
        const buttons = [];

        this._groupColumns().forEach((columns, groupName) => {
            if (groupName !== null) {
                buttons.push(this._groupHeadingButton(groupName, columns));
            }
            columns.forEach((col) => {
                buttons.push({
                    extend: 'columnToggle',
                    columns: col.col_idx,
                    text: String(col.title || col.json_id),
                    className: 'colvis_column_button',
                });
            });
        });

        return buttons;
    }

    /**
     * Creates the heading button for a column group. Clicking it shows all of the group's columns, or hides them all
     * when they are already all visible. The button is shown as active while every column in the group is visible.
     * @param {string} groupName - The column_group name, used as the button text.
     * @param {Object[]} columns - The column definitions in the group.
     * @returns {Object} The DataTables Buttons definition for the heading.
     */
    _groupHeadingButton(groupName, columns) {
        const indexes = columns.map((col) => col.col_idx);
        const allVisible = (dt) => indexes.every((idx) => dt.column(idx).visible());

        return {
            text: groupName,
            titleAttr: ColumnVisibility.GROUP_TOOLTIP,
            className: 'colvis_group_button',
            action: (e, dt) => {
                dt.columns(indexes).visible(!allVisible(dt), false);
                dt.columns.adjust();
            },
            // Shown as active while every column in the group is visible
            init: function (dt) {
                const refresh = () => this.active(allVisible(dt));
                refresh();
                dt.on('column-visibility.dt', refresh);
            },
        };
    }
}

export { ColumnVisibility };
