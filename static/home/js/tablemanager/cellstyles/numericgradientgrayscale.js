class NumericGradientGrayscale {
    constructor(cell, cellData, rowData, rowIndex, colIndex, conditionSource, tableManagerReference, settings) {
        this.cell = cell;
        this.cellData = cellData;
        this.rowData = rowData;
        this.rowIndex = rowIndex;
        this.colIndex = colIndex;
        this.conditionSource = conditionSource;
        this.tableManagerReference = tableManagerReference;
        this.settings = settings;
    }

    sleep(delay) { return new Promise((resolve) => setTimeout(resolve, delay)) }

    applyStyle() {
        if (this.tableManagerReference.dataTableReference.settings()[0].oFeatures.bServerSide === true) {
            this._createGradientGreyscale_serverside(this.cell, this.cellData, this.conditionSource, this.tableManagerReference, this.settings);
        }
        else {
            this._createGradientGreyscale_clientside(this.cell, this.cellData, this.conditionSource, this.tableManagerReference, this.settings)
        }
    }

    async _createGradientGreyscale_serverside(cell, cellData, conditionSource, tableManagerReference, settings) {
        //retrieve the stored min and max values for this column from globalThis, if they exist
        let storedMin = globalThis["min_" + this.json_id];
        let storedMax = globalThis["max_" + this.json_id];
        
        // a flag to indicate whether the min and max values have been requested from the server
        // this is necessary because multiple cells in the same column may be rendered before the min and max values are returned from the server, 
        // and we don't want to make multiple requests for the same data
        let requested = globalThis["requested_max_min_" + this.json_id];
        
        //If the min and max values are not stored and nobody has requested them, we need to fetch them from the server. 
        if ((storedMin == undefined || storedMax == undefined) & requested == undefined) {                     
            globalThis["requested_max_min_" + this.json_id] = true;
            let range = await this._get_min_max_serverside(conditionSource, tableManagerReference.dataTableReference)
            globalThis["min_" + this.json_id] = range.min;
            globalThis["max_" + this.json_id] = range.max;            
        }

        // By now, this or some other cell has already requested the min and max values, so we wait here 
        // until the values are available in globalThis before proceeding to set the grayscale style
        let tried = 0;
        while (globalThis["min_" + this.json_id] == undefined || globalThis["max_" + this.json_id] == undefined) {
            await this.sleep(100); // Wait for 100 milliseconds before checking again
            tried++;
            if (tried > 50) { // If we've tried 50 times (5 seconds), break the loop to avoid infinite waiting
                console.error("Failed to retrieve min/max values for gradient greyscale after multiple attempts.");
                break;
            }
        }
        
        this._set_grayscale_style(cell, cellData, globalThis["min_" + this.json_id], globalThis["max_" + this.json_id], settings);            
    }

    _createGradientGreyscale_clientside(cell, cellData, conditionSource, tableManagerReference, settings) {
        //retrieve the stored min and max values for this column from globalThis, if they exist
        let storedMin = globalThis["min_" + this.json_id];
        let storedMax = globalThis["max_" + this.json_id];

        //If the min and max values are not stored, we need to compute them from the AJAX data payload
        if (storedMin == undefined || storedMax == undefined) {                     
            this._get_min_max_clientside(conditionSource, tableManagerReference.dataTableReference)            
        }

        storedMin = globalThis["min_" + this.json_id];
        storedMax = globalThis["max_" + this.json_id];
        
        this._set_grayscale_style(cell, cellData, storedMin, storedMax, settings);            
    }

    _get_min_max_clientside(sources, dataTable) {
        let dataPayload = dataTable.ajax.json().data;
        let min = Number.POSITIVE_INFINITY;
        let max = Number.NEGATIVE_INFINITY;
        
        dataPayload.forEach(function (rowData) {
            for (const source of sources) {
                min = Math.min(min, rowData[source]);
                max = Math.max(max, rowData[source]);
            }
        })
        
        //Store result so every cell doesn't have to recalculate/requery the min/max values
        globalThis["min_" + this.json_id] = min;
        globalThis["max_" + this.json_id] = max;
    }

    async _get_min_max_serverside(sources, dataTable) {
        let promises = [];
        let min = Number.POSITIVE_INFINITY;
        let max = Number.NEGATIVE_INFINITY;

        for (const source of sources) {
            let dataEndpoint = dataTable.settings()[0].ajax.url;
            let optionsEndpoint = dataEndpoint + "/range/" + source + "/"

            promises.push(
                fetch(optionsEndpoint).then(response => response.json())
            )
        }

        let ranges = await Promise.all(promises)

        for (const range of ranges) {
            min = Math.min(min, range.min);
            max = Math.max(max, range.max);
        }

        return {min: min, max: max};
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