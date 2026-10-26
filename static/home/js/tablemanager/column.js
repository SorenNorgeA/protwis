// This column class represents a data column definition for the table manager.
// It is responsible for managing the properties and behaviors of a specific column in the data table,
// including its data type, filtering capabilities, and rendering logic.



class Column{

    // The constructor takes a column specification object (colSpec) and a data type factory (dataTypeFactory) as parameters.
    // The column holds the specification properties itself, and owns a data type instance (this.type) created by the factory
    // from its data_type property. Rendering, filtering and cell behaviour are delegated to the data type, which keeps
    // a reference back to this column (this.column inside the data type) to read the specification.
    constructor(colSpec, dataTypeFactory) {
        //Assign all properties from colSpec to the Column instance
        Object.assign(this, { ... colSpec } )
        // Create the data type instance for this column based on the data_type property
        this.type = dataTypeFactory.createDataType(this);
    }

}

export { Column }