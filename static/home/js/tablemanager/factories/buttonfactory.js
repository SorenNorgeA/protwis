import { ExcelDefault } from "../buttons/exceldefault.js";
import { ClearFiltersDefault } from "../buttons/clearfiltersdefault.js";
import { ClearSelectionDefault } from "../buttons/clearselectiondefault.js";
import { ShowBestResolution } from "../buttons/showbestresolution.js";
import { ShowClosestToHuman } from "../buttons/showclosesttohuman.js";
import { ColumnVisibility } from "../buttons/columnvisibility.js";


/**
 * Creates button instances for the table manager by button type name.
 * Button classes are kept in a registry keyed by the name used in the table specification.
 */
class ButtonFactory {
    _buttonTypes = {};

    /**
     * Registers the built-in button types.
     */
    constructor() {
        this.registerButtonType("excel_default", ExcelDefault);
        this.registerButtonType("clear_filters_default", ClearFiltersDefault);
        this.registerButtonType("clear_selection_default", ClearSelectionDefault);
        this.registerButtonType("best_resolution_toggle", ShowBestResolution);
        this.registerButtonType("closest_to_human_toggle", ShowClosestToHuman);
        this.registerButtonType("column_visibility", ColumnVisibility);
    }

    /**
     * Registers a button class so it can be created by {@link ButtonFactory#getButtonType}.
     * @param {string} buttonName - Name used to reference the button in the table specification.
     * @param {Function} buttonClass - Button class to instantiate. It is constructed with the table manager reference.
     */
    registerButtonType(buttonName, buttonClass) {
        this._buttonTypes[buttonName] = buttonClass;
    }

    /**
     * Creates a button instance for a registered button type.
     * @param {string} buttonTypeName - Registered name of the button type.
     * @param {Object} tableManagerReference - The TableManager instance the button operates on.
     * @returns {Object|undefined} The new button instance, or undefined if the type is not registered.
     */
    getButtonType(buttonTypeName, tableManagerReference) {
        if (!this._buttonTypes[buttonTypeName]) {
            console.error(`Button type "${buttonTypeName}" is not registered with TableManager.`);
            return undefined;
        }
        return new this._buttonTypes[buttonTypeName](tableManagerReference);
    }
}

export { ButtonFactory }