// This is the base class for all data types used in the table manager.
// It provides a common interface and default implementations for methods 
// that can be overridden by subclasses to provide specific functionality for each data type.
// Each column owns one data type instance (column.type). The column specification is read through this.column;
// "this" inside a data type is always the data type instance.

/**
 * Base class for the data types used by the table manager. Each column owns one instance, which controls how the
 * column's cells are rendered, formatted and interacted with, and how its filter is built. Subclasses override the
 * methods they need.
 */
class DataTypeBase {
  // class methods
  /**
   * @param {Object} column - The column specification this data type belongs to.
   */
  constructor(column) {
    this.column = column;
  }
  
  /**
   * Creates the filter control for the column header. Runs while the header is built, before the DataTable exists,
   * so it should only create elements.
   * @param {Object} tableManagerReference - The table manager that owns the table.
   * @returns {HTMLElement|HTMLElement[]|null|false} The filter element(s). The default returns false, which builds no filter.
   */
  createFilterInterface(tableManagerReference) {
    // This method should be overridden by subclasses to create the appropriate filter interface for the data type.
    // It runs while the table header is built, before the DataTable exists, so it should only create elements.
    // Work that needs the DataTable or its data belongs in afterTableCreated or afterDataLoaded.
    // Default implementation returns null, indicating no filter interface is provided, resulting in no filter controls being added/built.
    return false;
  }

  /**
   * Hook for setup that needs the DataTable but not its data, such as initialising filter plugins. Runs right after the
   * DataTable is created, with the header in the DOM.
   * @param {Object} tableManagerReference - The table manager that owns the table.
   */
  afterTableCreated(tableManagerReference) {
    // This method can be overridden by subclasses for setup that needs the DataTable but not its data, e.g. initialising
    // filter interface plugins such as select2. It runs right after the DataTable is created, with the header in the DOM.
  }

  /**
   * Hook for setup that needs the initial table data, such as populating filter options. Runs once the table manager's
   * ready promise has resolved.
   * @param {Object} tableManagerReference - The table manager that owns the table.
   * @returns {Promise<void>|void} A promise if the setup is asynchronous.
   */
  afterDataLoaded(tableManagerReference) {
    // This method can be overridden by subclasses for setup that needs the table's initial data, e.g. populating filter
    // options. It runs once tableManager.ready has resolved; it may return a promise.
  }
  
  /**
   * Renders cell data for display, search, ordering and type detection. The default returns the data unchanged, except
   * that null or undefined display values show the table's nullValuePlaceholder. Subclasses that override this should
   * apply the same placeholder handling.
   * @param {*} data - The cell data.
   * @param {string} type - The DataTables render type: "display", "filter", "sort" or "type".
   * @param {*} row - The full row data.
   * @param {Object} meta - The DataTables cell metadata.
   * @param {Object} tableManagerReference - The table manager that owns the table.
   * @returns {*} The rendered value for the render type.
   */
  dataTableRenderer(data, type, row, meta, tableManagerReference) {
    // This method should be overridden by subclasses to provide the appropriate rendering logic for the data type (if required).
    // Default implementation returns the data as-is, except that null values display the table's nullValuePlaceholder.
    // Subclasses that override this method should apply the same placeholder handling.
    if (type === "display" && (data === null || data === undefined)) {
      return tableManagerReference.nullValuePlaceholder;
    }
    return data;
  }
  
  /**
   * Runs once a cell has been rendered: applies formatting, attaches event handlers, then runs any cell injectors.
   * @param {HTMLElement} cell - The rendered table cell.
   * @param {*} cellData - The cell data.
   * @param {*} rowData - The full row data.
   * @param {number} rowIndex - The index of the row.
   * @param {number} colIndex - The index of the column.
   * @param {Object} tableManagerReference - The table manager that owns the table.
   */
  postCreationTasks(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference) {
    //apply any cell formatting or styling after the cell has been created and rendered in the DataTable.
    this.cellFormatting(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference);
    
    // Call the eventHookup method to allow subclasses to attach event listeners or perform other actions on the cell.
    this.eventHookup(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference);

    // Call the injectorProcessing method to allow subclasses to perform any additional processing or modifications on the cell after it has been created and rendered in the DataTable.
    this.injectorProcessing(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference);

  }

  /**
   * Runs the injectors named in the column's cellInjectors property against the cell. Injector names are looked up in
   * the table manager's cellInjectors registry. Unknown names are logged and skipped.
   * @param {HTMLElement} cell - The rendered table cell.
   * @param {*} cellData - The cell data.
   * @param {*} rowData - The full row data.
   * @param {number} rowIndex - The index of the row.
   * @param {number} colIndex - The index of the column.
   * @param {Object} tableManagerReference - The table manager that owns the table.
   */
  injectorProcessing(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference) {
    // This method collects the names of any injector functions specified in the cellInjectors property of the column definition
    // and calls them to perform any additional processing or modifications on the cell after it has been created and rendered in the DataTable.
    if (this.column.cellInjectors) {
      const injectorNames = Array.isArray(this.column.cellInjectors) ? this.column.cellInjectors : [];

      for (let injectorName of injectorNames) {
        injectorName = injectorName.trim();
        if (!injectorName) {
          continue;
        }
        let injectorFunction = tableManagerReference.cellInjectors.getInjector(injectorName);
        if (injectorFunction === undefined) {
          console.error(`Injector function ${injectorName} not found in cellInjectors registry.`);
          continue;
        }
        injectorFunction(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference);
      }
    }
  }

  /**
   * Hook for attaching event listeners to a rendered cell. The default does nothing.
   * @param {HTMLElement} cell - The rendered table cell.
   * @param {*} cellData - The cell data.
   * @param {*} rowData - The full row data.
   * @param {number} rowIndex - The index of the row.
   * @param {number} colIndex - The index of the column.
   * @param {Object} tableManagerReference - The table manager that owns the table.
   */
  eventHookup(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference) {
    // This method can be overridden by subclasses to attach event listeners or perform other actions on the cell after it has been created and rendered in the DataTable.
  }

  /**
   * Applies the column's cell styling: a cellStyler object if one is set, otherwise the cssClassCell class. Also mirrors
   * the column group boundary borders onto the cell.
   * @param {HTMLElement} cell - The rendered table cell.
   * @param {*} cellData - The cell data.
   * @param {*} rowData - The full row data.
   * @param {number} rowIndex - The index of the row.
   * @param {number} colIndex - The index of the column.
   * @param {Object} tableManagerReference - The table manager that owns the table.
   */
  cellFormatting(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference) {
    const column = this.column;

    // Handle new cellStyler JSON format
    if (column.cellStyler && typeof column.cellStyler === 'object') {
      const styler = tableManagerReference.cellStyleFactory.getStyler(
        column.cellStyler,
        cell,
        cellData,
        rowData,
        rowIndex,
        colIndex,
        tableManagerReference,
        column.json_id
      );
      styler.applyStyle();
    } else if (column.cssClassCell && typeof column.cssClassCell === 'string') {
      // Static CSS class
      cell.classList.add(column.cssClassCell);
    }

    // Mirror the column group boundary borders of the header onto the body cell.
    // The DataTable reference is not assigned until the table has been constructed, so fall back to the column's default visibility.
    const dataTable = tableManagerReference.dataTableReference;
    const isVisible = (col) => dataTable ? dataTable.column(col.col_idx).visible() : col.visible !== false;
    tableManagerReference.annotateColumnGroupBoundaries(cell, column, tableManagerReference.columns, isVisible);
  }

  /**
   * Parses a settings string of the form "key:value;key:value" into an object. Each entry is split on its first colon,
   * so values can contain colons. Blank entries and entries without a colon are skipped.
   * @param {string|null|undefined} settingsList - The settings string.
   * @returns {Object<string, string>|null} The parsed settings, or null if the string is empty or blank.
   */
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