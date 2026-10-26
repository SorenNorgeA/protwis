class Categorical {
    /**
     * Stores the cell and row context and reads the category rules from the column settings.
     * @param {HTMLTableCellElement} cell - The cell element to style.
     * @param {*} cellData - The displayed value of the cell.
     * @param {Object} rowData - The full JSON payload of the row.
     * @param {number} rowIndex - The index of the row in the table.
     * @param {number} colIndex - The index of the column in the table.
     * @param {Object} tableManagerReference - The owning TableManager instance.
     * @param {Object} settings - Style settings. Expects `rules` (value to class name map) and optionally `source` (row property to check).
     * @param {string} columnJsonId - The column's JSON property name, used as the condition source when `settings.source` is not set.
     */
    constructor(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference, settings, columnJsonId) {
        this.columnJsonId = columnJsonId;
        this.cell = cell;
        this.cellData = cellData;
        this.rowData = rowData;
        this.rowIndex = rowIndex;
        this.colIndex = colIndex;
        this.tableManagerReference = tableManagerReference;
        this.rules = settings.rules || undefined; // Expecting a dictionary of value: className pairs
        this.conditionSource = settings.source || columnJsonId; // Default to columnJsonId if source is not provided

        if (!this.rules) {
            console.error(`Categorical cell style for column ${this.columnJsonId} is missing 'rules' in settings.`);
        }
        
    }

    /**
     * Adds the class name mapped to the row's condition source value to the cell.
     * Does nothing if the value has no matching rule.
     * @returns {void}
     */
    applyStyle() {
        // Settings is expected to be a dictionary where keys are the possible values of the condition source and values are the corresponding class names to apply.
        // conditionSource is the name of the property in rowData (i.e JSON payload) that contains the value to check against the settings.

        let sourceValue = this.rowData[this.conditionSource];
        
        if (sourceValue !== undefined && this.rules !== undefined && this.rules[sourceValue] !== undefined) {
            let className = this.rules[sourceValue];
            this.cell.classList.add(className);
        }    
    }
}

export { Categorical }