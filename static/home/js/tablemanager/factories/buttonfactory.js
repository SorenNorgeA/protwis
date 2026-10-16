import { ExcelDefault } from "../buttons/exceldefault.js";
import { ClearFiltersDefault } from "../buttons/clearfiltersdefault.js";


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
    }

    registerButtonType(buttonName, buttonClass) {
        this._buttonTypes[buttonName] = buttonClass;
    }

    getButtonType(buttonTypeName, tableManagerReference) {
        if (!this._buttonTypes[buttonTypeName]) {
            throw new Error(`Button "${buttonTypeName}" is not registered.`);
        }
        return new this._buttonTypes[buttonTypeName](tableManagerReference);
    }
}

export { ButtonFactory }