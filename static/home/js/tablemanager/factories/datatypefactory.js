import { Text } from "../datatypes/text.js";
import { Numeric } from "../datatypes/numeric.js";
import { TextSelect } from "../datatypes/textselect.js";
import { WebLinkText } from "../datatypes/weblinktext.js";
import { Checkbox } from "../datatypes/checkbox.js"

// This class is responsible for managing the different data types used in the table manager. 
// It facilitate dynamic initialization of data types by calling the constructor for the 
// data type class associated with the data_type string supplied in the column specification.
// New data types can be registered with the factory, allowing for easy extension of the table manager's 
// capabilities without modifying the core codebase.

class DataTypeFactory {
    _dataTypes = {};

    constructor() {
        this.registerDataType("text", Text);
        this.registerDataType("numeric", Numeric);
        this.registerDataType("textselect", TextSelect);
        this.registerDataType("weblinktext", WebLinkText);
        this.registerDataType("checkbox", Checkbox);
    }

    registerDataType(dataTypeName, dataTypeClass) {
        this._dataTypes[dataTypeName] = dataTypeClass;
    }

    // Create the data type instance owned by the column. The instance keeps a reference to the column to read its specification.
    createDataType(column) {
        if (!this._dataTypes[column.data_type]) {
            throw new Error(`Data type "${column.data_type}" is not registered.`);
        }
        return new this._dataTypes[column.data_type](column);
    }
}

export { DataTypeFactory }