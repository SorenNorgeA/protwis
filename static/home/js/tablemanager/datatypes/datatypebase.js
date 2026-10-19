// This is the base class for all data types used in the table manager.
// It provides a common interface and default implementations for methods 
// that can be overridden by subclasses to provide specific functionality for each data type.
// Each column owns one data type instance (column.type). The column specification is read through this.column;
// "this" inside a data type is always the data type instance.

class DataTypeBase {
  // class methods
  constructor(column) {
    this.column = column;
  }
  
  createFilterInterface(tableManagerReference) { 
    // This method should be overridden by subclasses to create the appropriate filter interface for the data type.
    // Default implementation returns null, indicating no filter interface is provided, resulting in no filter controls being added/built.
    return false;
  }
  
  dataTableRenderer(data, type, row, meta, tableManagerReference) {
    // This method should be overridden by subclasses to provide the appropriate rendering logic for the data type (if required).
    // Default implementation returns the data as-is, except that null values display the table's nullValuePlaceholder.
    // Subclasses that override this method should apply the same placeholder handling.
    if (type === "display" && (data === null || data === undefined)) {
      return tableManagerReference.nullValuePlaceholder;
    }
    return data;
  }
  
  postCreationTasks(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference) {
    //apply any cell formatting or styling after the cell has been created and rendered in the DataTable.
    this.cellFormatting(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference);
    
    // Call the eventHookup method to allow subclasses to attach event listeners or perform other actions on the cell.
    this.eventHookup(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference);

    // Call the injectorProcessing method to allow subclasses to perform any additional processing or modifications on the cell after it has been created and rendered in the DataTable.
    this.injectorProcessing(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference);

  }

  injectorProcessing(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference) {
    // This method collects the names of any injector functions specified in the cellInjector property of the column definition
    // and calls them to perform any additional processing or modifications on the cell after it has been created and rendered in the DataTable.
    if (this.column.cellInjector){
      let injectorSet = this.column.cellInjector.split(';');
      for (let injector of injectorSet){
        injector = injector.trim();
        if (!injector) {
          continue;
        }
        let injectorFunction = tableManagerReference.cellInjectors.getInjector(injector);
        if (injectorFunction == undefined) {
          console.error(`Injector function ${injector} not found in cellInjectors registry.`);
          continue;
        }
        injectorFunction(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference);
      }
    }
  }

  eventHookup(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference) {
    // This method can be overridden by subclasses to attach event listeners or perform other actions on the cell after it has been created and rendered in the DataTable.
  }

  cellFormatting(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference) {
    const column = this.column;
    if (column.cssClassCell) {
        if(column.cssClassCell.includes("_conditional_")){
            let conditionSource = null;
            if (column.cssClassCellConditionSource) { conditionSource = column.cssClassCellConditionSource.split(';'); }

            let settings = null;
            if (column.cssClassCellConditionRules) { settings = this.parseSettingsList(column.cssClassCellConditionRules); }

            let styler = tableManagerReference.cellStyleFactory.getStyler(column.cssClassCell, cell, cellData, rowData, rowIndex, colIndex, conditionSource, tableManagerReference, settings, column.json_id);
            styler.applyStyle();
        } else {
            cell.classList.add(column.cssClassCell);
        }
    }
  }

  parseSettingsList(settingsList){
        // Parse the settings list into a dictionary of key-value pairs

        if (settingsList == null || settingsList === undefined || settingsList.trim() === "") {
            return null;
        }

        let settingsDict = {};
        let settingsArray = settingsList.split(';');
        for (let setting of settingsArray) {
            // Skip empty entries (e.g. from trailing semicolon)
            if (!setting.trim()) {
                continue;
            }
            // Split on the first colon only, so values can contain colons (e.g. "url:http://x")
            const colonIndex = setting.indexOf(':');
            if (colonIndex === -1) {
                continue; // Skip entries without a colon
            }
            const key = setting.slice(0, colonIndex).trim();
            const value = setting.slice(colonIndex + 1).trim();
            settingsDict[key] = value;
        }
        return settingsDict;
    }

}

export { DataTypeBase };