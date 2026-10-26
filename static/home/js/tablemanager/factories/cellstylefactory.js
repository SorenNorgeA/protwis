import { NumericGradientGrayscale } from "../cellstyles/numericgradientgrayscale.js";
import { Categorical } from "../cellstyles/categorical.js";


// This class is responsible for managing the different cell style types used in the table manager.
// It facilitates dynamic initialization of cell styler instances by calling the constructor for the
// cell style class associated with the style type (e.g. 'categorical', 'gradient_greyscale').
// New cell styles can be registered with the factory, allowing for easy extension of the table manager's
// capabilities without modifying the core codebase.

/**
 * Creates cell style instances for the table manager by style type name.
 * Style classes are kept in a registry keyed by the `type` field of a cell style configuration.
 */
class CellStyleFactory {
    _cellStylers = {};

    /**
     * Registers the built-in cell style types.
     */
    constructor() {
        this.registerCellStyler("categorical", Categorical);
        this.registerCellStyler("gradient_greyscale", NumericGradientGrayscale);
    }

    /**
     * Registers a cell style class so it can be created by {@link CellStyleFactory#getStyler}.
     * @param {string} cellStyleName - Style type name used in the cell style configuration.
     * @param {Function} cellStyleClass - Style class to instantiate. It is constructed with the arguments passed to getStyler, followed by the style configuration.
     */
    registerCellStyler(cellStyleName, cellStyleClass) {
        this._cellStylers[cellStyleName] = cellStyleClass;
    }

    /**
     * Creates a cell style instance for the given style configuration.
     * @param {Object} styleConfig - Cell style configuration. Must include a registered `type`.
     * @param {Object} cell - The table cell being styled.
     * @param {*} cellData - The data from the datasource associated with the cell.
     * @param {Object} rowData - The data for the row containing the cell.
     * @param {number} rowIndex - Index of the row in the table.
     * @param {number} colIndex - Index of the column in the table.
     * @param {Object} tableManagerReference - The TableManager instance the style operates on.
     * @param {string} columnJsonId - Identifier of the column in the table data.
     * @returns {Object} The new cell styler instance.
     * @throws {Error} If `type` is missing from the configuration or is not registered.
     */
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