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

/**
 * Creates data type instances for table columns, based on the column's `data_type`.
 * Data type classes are kept in a registry keyed by the `data_type` string.
 */
class DataTypeFactory {
    _dataTypes = {};

    /**
     * Registers the built-in data types.
     */
    constructor() {
        this.registerDataType("text", Text);
        this.registerDataType("numeric", Numeric);
        this.registerDataType("textselect", TextSelect);
        this.registerDataType("weblinktext", WebLinkText);
        this.registerDataType("checkbox", Checkbox);
    }

    /**
     * Registers a data type class so it can be created by {@link DataTypeFactory#createDataType}.
     * @param {string} dataTypeName - Name used as the `data_type` in the column specification.
     * @param {Function} dataTypeClass - Data type class to instantiate. It is constructed with the column.
     */
    registerDataType(dataTypeName, dataTypeClass) {
        this._dataTypes[dataTypeName] = dataTypeClass;
    }

    /**
     * Creates the data type instance owned by the column. The instance keeps a reference to the column to read its specification.
     * @param {Object} column - Column specification. Its `data_type` selects the data type class.
     * @returns {Object} The new data type instance.
     * @throws {Error} If the column's `data_type` is not registered.
     */
    createDataType(column) {
        if (!this._dataTypes[column.data_type]) {
            throw new Error(`Data type "${column.data_type}" is not registered.`);
        }
        return new this._dataTypes[column.data_type](column);
    }
}

export { DataTypeFactory }