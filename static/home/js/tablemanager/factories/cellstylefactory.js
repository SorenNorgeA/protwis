import { NumericGradientGrayscale } from "../cellstyles/numericgradientgrayscale.js";
import { Categorical } from "../cellstyles/categorical.js";


// This class is responsible for managing the different cell style types used in the table manager.
// It facilitates dynamic initialization of cell stylists by calling the constructor for the
// cell style class associated with the style type (e.g. 'categorical', 'gradient_greyscale').
// New cell styles can be registered with the factory, allowing for easy extension of the table manager's
// capabilities without modifying the core codebase.

class CellStyleFactory {
    _cellStylers = {};

    constructor() {
        this.registerCellStyler("categorical", Categorical);
        this.registerCellStyler("gradient_greyscale", NumericGradientGrayscale);
    }

    registerCellStyler(cellStyleName, cellStyleClass) {
        this._cellStylers[cellStyleName] = cellStyleClass;
    }

    getStyler(styleConfig, cell, cellData, rowData, rowIndex, colIndex, tableManagerReference, columnJsonId) {

        if (!styleConfig.type) {
            throw new Error('cellStyle missing required "type" field');
        }

        const { type } = styleConfig;
        if (!this._cellStylers[type]) {
            throw new Error(`Style provider "${type}" is not registered.`);
        }

        return new this._cellStylers[type](cell, cellData, rowData, rowIndex, colIndex, tableManagerReference, styleConfig, columnJsonId);
    }
}

export { CellStyleFactory }