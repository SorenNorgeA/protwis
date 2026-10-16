class NumericGradientGrayscale {
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
        const range = this._getColumnRange();

        if (range instanceof Promise) {
            // Server-side: every cell in the column awaits the same promise, so the range is requested once.
            range.then((resolved) => this._applyRange(resolved));
        } else {
            this._applyRange(range);
        }
    }

    // Applies the shading once the range is known. A null range (e.g. failed fetch, no numeric data) leaves the cell unstyled.
    _applyRange(range) {
        if (range) {
            this._set_grayscale_style(this.cell, this.cellData, range.min, range.max, this.settings);
        }
    }

    // Returns the {min, max} range for this column (client-side) or a promise of it (server-side).
    // Ranges are cached on the table manager's state, keyed by column, so different columns
    // and different tables never share a range.
    _getColumnRange() {
        const state = this.tableManagerReference.state;
        if (!state.gradientRanges) {
            state.gradientRanges = {};
        }

        if (!(this.columnJsonId in state.gradientRanges)) {
            const dataTable = this.tableManagerReference.dataTableReference;
            if (dataTable.settings()[0].oFeatures.bServerSide === true) {
                state.gradientRanges[this.columnJsonId] = this._get_min_max_serverside(this.conditionSource, dataTable);
            } else {
                state.gradientRanges[this.columnJsonId] = this._get_min_max_clientside(this.conditionSource, dataTable);
            }
        }

        return state.gradientRanges[this.columnJsonId];
    }

    _get_min_max_clientside(sources, dataTable) {
        let min = Number.POSITIVE_INFINITY;
        let max = Number.NEGATIVE_INFINITY;

        dataTable.rows().data().toArray().forEach((rowData) => {
            for (const source of sources) {
                const raw = rowData[source];
                if (raw === null || raw === undefined || raw === "") {
                    continue;
                }
                const value = parseFloat(raw);
                if (Number.isFinite(value)) {
                    min = Math.min(min, value);
                    max = Math.max(max, value);
                }
            }
        });

        return Number.isFinite(min) && Number.isFinite(max) ? { min: min, max: max } : null;
    }

    // Never rejects: resolves to null if any range request fails, so cells stay unstyled instead of waiting or erroring.
    async _get_min_max_serverside(sources, dataTable) {
        const dataEndpoint = dataTable.ajax.url();

        try {
            const ranges = await Promise.all(sources.map(async (source) => {
                const response = await fetch(dataEndpoint + "/range/" + source + "/");
                if (!response.ok) {
                    throw new Error(`HTTP error when fetching range for ${source}! Status: ${response.status}`);
                }
                return response.json();
            }));

            let min = Number.POSITIVE_INFINITY;
            let max = Number.NEGATIVE_INFINITY;
            for (const range of ranges) {
                const rangeMin = parseFloat(range.min);
                const rangeMax = parseFloat(range.max);
                if (Number.isFinite(rangeMin)) min = Math.min(min, rangeMin);
                if (Number.isFinite(rangeMax)) max = Math.max(max, rangeMax);
            }

            return Number.isFinite(min) && Number.isFinite(max) ? { min: min, max: max } : null;
        } catch (error) {
            console.error("Failed to retrieve min/max values for gradient greyscale:", error);
            return null;
        }
    }

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