class Categorical {
    constructor(cell, cellData, rowData, rowIndex, colIndex, conditionSource, tableManagerReference, settings, columnJsonId) {
        this.columnJsonId = columnJsonId;
        this.cell = cell;
        this.cellData = cellData;
        this.rowData = rowData;
        this.rowIndex = rowIndex;
        this.colIndex = colIndex;
        this.conditionSource = conditionSource;
        this.tableManagerReference = tableManagerReference;
        this.settings = settings;
    }

    applyStyle() {
        // Settings is expected to be a dictionary where keys are the possible values of the condition source and values are the corresponding class names to apply.
        // conditionSource is the name of the property in rowData (i.e JSON payload) that contains the value to check against the settings.
        let sourceValue = this.rowData[this.conditionSource]
        if (sourceValue !== undefined && this.settings[sourceValue] !== undefined) {
            let className = this.settings[sourceValue];
            this.cell.classList.add(className);
        }    
    }
}

export { Categorical }