import { ExcelDefault } from "../buttons/exceldefault.js";
import { ClearFiltersDefault } from "../buttons/clearfiltersdefault.js";
import { ShowBestResolution } from "../buttons/showbestresolution.js";
import { ShowClosestToHuman } from "../buttons/showclosesttohuman.js";


// This class is responsible for managing the different data types used in the table manager. 
// It facilitate dynamic initialization of data types by calling the constructor for the 
// data type class associated with the data_type string supplied in the column specification.
// New data types can be registered with the factory, allowing for easy extension of the table manager's 
// capabilities without modifying the core codebase.

class ButtonFactory {
    _buttonTypes = {};

    constructor() {
        this.registerButtonType("excel_default", ExcelDefault);
        this.registerButtonType("clear_filters_default", ClearFiltersDefault);
        this.registerButtonType("best_resolution_toggle", ShowBestResolution);
        this.registerButtonType("closest_to_human_toggle", ShowClosestToHuman);
    }

    registerButtonType(buttonName, buttonClass) {
        this._buttonTypes[buttonName] = buttonClass;
    }

    getButtonType(buttonTypeName, tableManagerReference) {
        if (!this._buttonTypes[buttonTypeName]) {
            console.error(`Button type "${buttonTypeName}" is not registered with TableManager.`);
            return undefined;
        }
        return new this._buttonTypes[buttonTypeName](tableManagerReference);
    }
}

export { ButtonFactory }