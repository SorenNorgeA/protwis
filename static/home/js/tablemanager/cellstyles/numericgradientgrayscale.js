class NumericGradientGrayscale {
    /**
     * Stores the cell and row context and normalizes the condition source to an array.
     * @param {HTMLTableCellElement} cell - The cell element to style.
     * @param {*} cellData - The displayed value of the cell.
     * @param {Object} rowData - The full JSON payload of the row.
     * @param {number} rowIndex - The index of the row in the table.
     * @param {number} colIndex - The index of the column in the table.
     * @param {Object} tableManagerReference - The owning TableManager instance.
     * @param {Object} settings - Style settings, such as `source`, `shadingMin`, `shadingMax`, `valueClampMin`, `valueClampMax` and `invert`.
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
        this.settings = settings;
        this.conditionSource = settings.source || columnJsonId; // Default to columnJsonId if source is not provided
        this.conditionSource = Array.isArray(this.conditionSource) ? this.conditionSource : [this.conditionSource]; // Ensure source is an array
    }

    /**
     * Shades the cell once the column's numeric range is available.
     * @returns {void}
     */
    applyStyle() {
        // Every cell in the column awaits the same promise, so the range is computed or requested once.
        this._getColumnRange().then((range) => this._applyRange(range));
    }

    /**
     * Applies the grayscale shading to the cell using the given range.
     * @param {{min: number, max: number}|null} range - The column's numeric range, or null if unavailable.
     * @returns {void}
     */
    // Applies the shading once the range is known. A null range (e.g. failed fetch, no numeric data) leaves the cell unstyled.
    _applyRange(range) {
        if (range) {
            this._set_grayscale_style(this.cell, this.cellData, range.min, range.max, this.settings);
        }
    }

    /**
     * Retrieves the numeric range for this column, requesting it from the data source on first use.
     * @returns {Promise<{min: number, max: number}|null>} A promise of the column's range, cached per column on the table manager state.
     */
    // Returns a promise of the {min, max} range for this column, from the table manager's data source.
    // Ranges are cached on the table manager's state, keyed by column, so different columns
    // and different tables never share a range.
    _getColumnRange() {
        const state = this.tableManagerReference.state;
        if (!state.gradientRanges) {
            state.gradientRanges = {};
        }

        if (!(this.columnJsonId in state.gradientRanges)) {
            state.gradientRanges[this.columnJsonId] = this.tableManagerReference.dataSource.getRange(this.conditionSource);
        }

        return state.gradientRanges[this.columnJsonId];
    }

    /**
     * Calculates a grayscale shade for the cell value within the range and sets the cell's background color.
     * Values outside the clamp settings are clamped first. The cell is left unchanged if the value cannot be computed.
     * @param {HTMLTableCellElement} cell - The cell element to style.
     * @param {*} cellData - The value to shade, parsed as a number.
     * @param {number} rangeMin - The lower bound of the column range, used unless `valueClampMin` is set.
     * @param {number} rangeMax - The upper bound of the column range, used unless `valueClampMax` is set.
     * @param {Object} settings - Style settings for shading limits, value clamping and `invert`.
     * @returns {void}
     */
    _set_grayscale_style(cell, cellData, rangeMin, rangeMax, settings)
    {         
        let cellValue = null;
        let min = null;
        let max = null;
        let grayscaleValue = null;
        let shadingMin = 0;
        let shadingMax = 70;
        let valueClampMin = null;
        let valueClampMax = null;
        let invert = false;

        if (settings) {
            if (settings.shadingMin) {
                let parsedShadingMin = parseInt(settings.shadingMin);
                if (!isNaN(parsedShadingMin)) {
                    shadingMin = parsedShadingMin;
                }
            }
            if (settings.shadingMax) {
                let parsedShadingMax = parseInt(settings.shadingMax);
                if (!isNaN(parsedShadingMax)) {
                    shadingMax = parsedShadingMax;
                }
            }
            if (settings.valueClampMin) {
                let parsedClampMin = parseFloat(settings.valueClampMin);
                if (!isNaN(parsedClampMin)) {
                    valueClampMin = parsedClampMin;
                }
            }
            if (settings.valueClampMax) {
                let parsedClampMax = parseFloat(settings.valueClampMax);
                if (!isNaN(parsedClampMax)) {
                    valueClampMax = parsedClampMax;                    
                }
            }
            
            if (settings.invert) {
                if (settings.invert === 'true' || settings.invert === 'True' || settings.invert === '1') {
                    invert = true;
                }                
                else if (settings.invert === 'false' || settings.invert === 'False' || settings.invert === '0') {
                    invert = false;
                }
                else {
                    console.warn("Invalid value for 'invert' setting: " + settings.invert + ". Expected 'true', 'false', '1', or '0'. Defaulting to false.");
                }
            }
        }

        // Parse the cell value to a float, and apply clamping if the clamp settings are defined
        try {
            cellValue = parseFloat(cellData)
        
            // Apply clamping to the cell value if the clamp settings are defined
            if (valueClampMin !== null && cellValue < valueClampMin) {
                cellValue = valueClampMin;
            }

            if (valueClampMax !== null && cellValue > valueClampMax) {
                cellValue = valueClampMax;
            }

        } catch (error) {
            console.error("Failed to parse number from cell value during gradient calculation") 
        }
        
        // Parse the range min and max values to floats, handling any potential errors
        try {            
            if (valueClampMin !== null) {
                min = valueClampMin;
            }
            else {
                min = parseFloat(rangeMin)
            }
        } catch (error) {
            console.error("Failed to parse number from range min during gradient calculation") 
        }
        
        try {
            if (valueClampMax !== null) {
                max = valueClampMax;
            }
            else {
                max = parseFloat(rangeMax)
            }
        } catch (error) {
            console.error("Failed to parse number from range max during gradient calculation") 
        }

        // Calculate the grayscale value based on the cell value and the min/max range after any clamping
        try {
            let black = 0;
            let white = 255;

            let darkestRgb = Math.round(white - (white * (shadingMax / 100)));
            let lightestRgb = Math.round(white - (white * (shadingMin / 100)));
            let cellScaled = (cellValue - min) / (max - min);
            let cellScaledRgb = Math.round((lightestRgb-darkestRgb) * cellScaled);
            
            // Inverting makes the higher numeric values lighter and the lower numeric values darker, which is the opposite of the default behavior.
            if (invert) {
                grayscaleValue = Math.round(darkestRgb + cellScaledRgb);
            } else {
                grayscaleValue = Math.round(lightestRgb - cellScaledRgb);
            }
            
        } catch (error) {
            console.error("Failed to calculate grayscale value during gradient calculation") 
        }

        if (grayscaleValue !== null && !isNaN(grayscaleValue)) {            
            cell.style.backgroundColor = "rgb(" + grayscaleValue + "," + grayscaleValue + "," + grayscaleValue + ")";
        }
    }
}

export { NumericGradientGrayscale };