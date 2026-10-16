import { Column } from "./column.js";
import { DataTypeFactory } from "./factories/datatypefactory.js";
import { CellStyleFactory } from "./factories/cellstylefactory.js";
import { ButtonFactory } from "./factories/buttonfactory.js";
import { MasterCheckbox, Checkbox } from "./datatypes/checkbox.js";

// This class represents the TableManager, which is responsible for managing the data table and its associated columns, filters, and rendering logic.
// The TableManager interacts with the back end TableProvider to fetch configuration and content data, then initializes a DataTable with the appropriate settings and column definitions.
// TableManager supports both server-side and client-side processing modes, allowing for flexible handling of large datasets and dynamic filtering capabilities.

class TableManager {

  /**
   * Constructs a new TableManager instance.
   * @param {Array} columnSpecification - An array of column specification objects defining the properties and behaviors of each column in the data table.
   * @param {string} tableId - The ID of the HTML table element to be managed by the TableManager.
  **/
  constructor(
    columnSpecification,
    tableId     
  ) {
    this.dataTypeFactory = new DataTypeFactory();
    this.cellStyleFactory = new CellStyleFactory();
    this.buttonFactory = new ButtonFactory();

    //registry of events and corresponding event handlers that can be attached to table cells, rows, or content
    this.eventRegistry = new TableEventRegister();
    
    // Injector registries used for registering custom function to inject content into table cells, primary header cells and super-header cells
    this.superHeaderInjectors = new SuperHeaderInjectorRegister();
    this.primaryHeaderInjectors = new CellInjectorRegister();
    this.filterHeaderInjectors = new CellInjectorRegister();
    this.cellInjectors = new CellInjectorRegister();

    // Event publisher/subscriber instance used for broadcasting events to subscribers
    this.eventPubSub = new EventPubSub();

    // FetchCache instance used for caching results of server-side fetches
    this.cache = new FetchCache();

    // Category count header cells ({ col, th }), all updated together by a single handler
    this.categoryCountCells = [];
    // Incremented per count request so a superseded (slower) response can't overwrite a newer one
    this.categoryCountRequestId = 0;

    this.columns = this.initialiseColumns(columnSpecification);
    this.tableId = tableId;
    
    //Array to store functions that need to be run after data is loaded into the table, 
    // e.g. to populate filter interfaces that require access to the dataset to determine their options (e.g. select dropdowns populated with unique values from the dataset)
    this.dataLoadedCallBacks = []; 
    this.documentReadyCallBacks = []; 

    // A general purpose state object that can be used to store any additional information or state that may be needed by the TableManager or its associated components.
    this.state = {};

    // If any of the columns are of type "checkbox", initialize a Map to track the active checkboxes and their associated data.
    if (this.columns.some((col) => col.data_type === "checkbox")) {
      this.state.activeCheckBoxes = new Map(); // Map of checkboxes that are currently checked, with checkbox id as key and checkbox cell data ({id, data}) as value
      this.state.currentSearchCheckBoxes = new Map(); // Map of checkboxes that are currently in the search results, with checkbox id as key and checkbox cell data ({id, data}) as value
    }
  }

  /**
   * Initialize the columns based on the provided column specifications. 
   * @param {Array} columnSpecification - An array of column specification objects defining the properties and behaviors of each column in the data table.
   * @returns {Array} An array of initialized Column instances.
  **/   
  initialiseColumns(columnSpecification) {
    return columnSpecification.map(
      (colSpec) =>
        new Column(colSpec, this.dataTypeFactory),
    );
  }

  /**
   * This method initializes the DataTable with the provided options, setting up the table structure, headers, and filters based on the column specifications.
   * It also handles the execution of callbacks for data loading and document readiness, ensuring that filter interfaces are properly populated and initialized.
   * @param {Object} dataTableOptions -  Object containing the configuration options for initializing the DataTable, including column definitions, server-side processing settings, and any custom callbacks.
   */
  initialiseDataTable(dataTableOptions) {   
    
    try {
      if (this._columnIndexesAbsent()) {
        // If no columns have col_idx, we assume they are in the correct order and assign col_idx based on their position in the array
        this._createColumnIndexes();
      } else {
        this.validateColumnSpecifications();
      }
    } catch (error) {
      alert(
        "Error in column specifications during DataTable initialisation:" + error.message        
      );
      return;
    }

    // Placeholder shown in display mode for null cell values (e.g. "-"); must be set before the DataTable renders any cells
    this.nullValuePlaceholder = dataTableOptions.nullValuePlaceholder ?? "-";

    //Returns [table, tableHead,  primaryHeaderRow, tableBody] HTML object references after adding them to DOM
    let tableComponents = this.createPrimaryTableStructure();

    this.populateColumnTitleHeaders(tableComponents.primaryHeaderRow);

    if (!dataTableOptions.columns) {
      dataTableOptions.columns = this.columns.map((col) =>
        this.columnToDataTableColDef(col),
      );
    } else {
      console.log(
        "DataTable options already contains column definitions, skipping automatic column definition generation from specifications.",
      );
    }

    // We need to wait until the initial data has been loaded into the table before populating the filter headers, 
    // as the filter interfaces (e.g. select dropdowns) may need to be populated with unique values from the dataset which are not available until the data is loaded. 
    const userInitComplete = dataTableOptions.initComplete; // store any initComplete function that was supplied in config, may be undefined
    dataTableOptions.initComplete = (settings, json) => {
      this.dataLoadedCallBacks.forEach((callBack) => callBack());

      // invoke original callback, if one was provided
      if (typeof userInitComplete === "function") {
        userInitComplete(settings, json);
      }
    }    

    this.dataTableReference = $(`#${this.tableId}`).DataTable(dataTableOptions);

    tableComponents = this.createSecondaryTableStructure(tableComponents, dataTableOptions);
    this.populateSuperHeaders(tableComponents.superHeaderRow);
    
    // If the showCategoryCounts option is enabled, create the category count header cells 
    // and set up an event listener for DataTables search events to update the counts accordingly.
    if (dataTableOptions.showCategoryCounts) {
     this.setupCategoryCountsHeader(tableComponents.categoryCountsHeaderRow)
    }
        
    this.populateFilterHeaders(tableComponents.filterHeaderRow);

    // -- Document ready callbacks -- //
    //We need to wait until the document is fully loaded before initializing any filter interface plugins (e.g. select2) to ensure the relevant DOM elements are available
    // so we store the initialization of such plugins as callbacks to be run on document ready, and push them into the documentReadyCallBacks array
    $(() => { this.documentReadyCallBacks.forEach((callBack) => callBack()); }); //jQuery document ready function syntax

    // Set up event publishing for DataTables events that may be of interest to subscribers, such as search, order, and page events.
    this.setupDatTablesEventPublishing();

    // If any of the columns are of type "checkbox", subscribe to relevant events to help synchronise checkboxes.
    if (this.columns.some((col) => col.data_type === "checkbox")) {
      this.setupCheckboxEventSubscriptions();
    }

    this.assignOddEvenRowClasses(dataTableOptions);

    this.runButtonFactory(dataTableOptions);
  }

  runButtonFactory(dataTableOptions) {
    // Create and attach buttons based on the button configurations in the dataTableOptions
    if (dataTableOptions.buttonFactory) {
      dataTableOptions.buttonFactory.forEach((buttonTypeName, idx) => {
        const button = this.buttonFactory.getButtonType(buttonTypeName, this);
        button.attach(idx);
      })  
    };
    }  

  assignOddEvenRowClasses(dataTableOptions) {

    let evenRowClass = dataTableOptions.rowClassEven ? dataTableOptions.rowClassEven :"even_row";
    let oddRowClass = dataTableOptions.rowClassOdd ? dataTableOptions.rowClassOdd :"odd_row";

    let row_class_assigner = () => {
      // Apply odd/even classes based on display position (rowLoop), not original data index (rowIdx).
      // Use page: "current" to only iterate rows visible on this page for efficiency.
      // After sort/filter, this ensures stripes alternate correctly based on what's actually displayed.
      this.dataTableReference.rows({ search: "applied", page: "current" }).every(function (_rowIdx, _tableLoop, rowLoop) {
        const row = this.node();
        if (!row) return;
        $(row).toggleClass(oddRowClass, rowLoop % 2 !== 0);
        $(row).toggleClass(evenRowClass, rowLoop % 2 === 0);
      });
    };

    // Apply the row class assigner to the current view
    row_class_assigner();

    // Reapply the row class assigner whenever the table is redrawn (sort, filter, page change)
    this.dataTableReference.on("draw.dt", row_class_assigner);
  }

  setupCategoryCountsHeader(categoryCountsHeaderRow){
    this.createCategoryCountHeaderCells(categoryCountsHeaderRow);      
    
    // Set up an event listener for DataTables search events to update the 
    // category counts whenever a search is performed on the table.
    this.dataTableReference.on("search", (e, settings) => { 
        this._handleColumnUniqueValueCounts(settings);
    })

    //Trigger initial count update after table is initialized and data is loaded
    this.dataLoadedCallBacks.push(() => { this._handleColumnUniqueValueCounts(this.dataTableReference.settings()[0]); });    
  }

  setupDatTablesEventPublishing() {
    //Add event listeners to the DataTable for various events (search, draw, order, page) and publish corresponding events via the eventPubSub instance.
    this.dataTableReference.on("search.dt", (e, settings) => {
      this.eventPubSub.publish("DataTableSearch", settings);
    });

    this.dataTableReference.on("draw.dt", (e, settings) => {
      this.eventPubSub.publish("DataTableDraw", settings);
    });
    
    this.dataTableReference.on("order.dt", (e, settings) => {
      this.eventPubSub.publish("DataTableOrder", settings);
    });
    
    this.dataTableReference.on("page.dt", (e, settings) => {
      this.eventPubSub.publish("DataTablePage", settings);
    });
  }

  setupCheckboxEventSubscriptions() {
      // Subscribe to the DataTableSearch event to update the currentSearchCheckBoxes state whenever a search is performed on the table.
      this.eventPubSub.subscribe("DataTableSearch", () => this.state.masterCheckbox.updateCheckBoxSearchSet());
      // Trigger an initial update of the currentSearchCheckBoxes state after the table is initialized and data is loaded, to ensure the state is accurate from the start.
      this.dataLoadedCallBacks.push( () => this.state.masterCheckbox.updateCheckBoxSearchSet());

      // Subscribe to the DataTableDraw event to synchronise the checkboxes in the rendered rows with the activeCheckBoxes 
      // state whenever the table is redrawn (e.g. after a search, order, or page change).
      this.eventPubSub.subscribe("DataTableDraw", () => Checkbox.synchroniseVisibleCheckState(this));
      this.eventPubSub.subscribe("DataTableDraw", () => this.state.masterCheckbox.synchroniseCheckedState(this));
      
      // Subscribe to the checkboxStateChanged event to synchronise the master checkbox state whenever a checkbox is checked or unchecked.
      // The checkboxStateChanged is published by the default checkbox click handler, 
      // and should be published by any custom click handler that is registered for the checkbox column.
      this.eventPubSub.subscribe("checkboxStateChanged", () => this.state.masterCheckbox.synchroniseCheckedState(this));
  }

  async _handleColumnUniqueValueCounts(settings){
    const requestId = ++this.categoryCountRequestId;
    let counts;

    try {
      if (this.dataTableReference.settings()[0].oFeatures.bServerSide === true) {
        // Key on the complete request (endpoint plus every search/order parameter) so any change that alters the counts misses the cache
        const requestUrl = this._buildCountRequestUrl(settings, this.dataTableReference);
        const cacheKey = { type: 'columnCounts', url: requestUrl };
        counts = this.cache.get(cacheKey);
        if (!counts) {
          counts = await this._fetchColumnUniqueValueCountsServerSide(requestUrl);
          this.cache.push(cacheKey, counts);
        }
      } else {
        // Computed from the loaded rows each time: cheap, and always reflects global search and fixed search filters
        counts = this._fetchColumnUniqueValueCountsClientSide(settings, this.dataTableReference);
      }
    } catch (error) {
      console.error("Fetching unique value counts failed:", error);
      return;
    }

    // A newer request has started since this one, so its result is the one to display
    if (requestId !== this.categoryCountRequestId) {
      return;
    }

    this._updateCategoryCountCells(counts);
  }

  _fetchColumnUniqueValueCountsClientSide(settings, dataTableReference) {
    const table = dataTableReference

    // Get all rows (base dataset) and filtered rows
    const baseRows = table.rows().data().toArray()
    const filteredRows = table.rows({search: 'applied'}).data().toArray()

    // Get column information
    const columnSettings = table.settings()[0].aoColumns

    // Helper function to count unique values in a column across rows
    const countUniqueInColumn = (rows, columnInfo) => {
        const uniqueValues = new Set()

        rows.forEach((rowData) => {
            let value

            // Handle different data structures: object vs array
            if (typeof rowData === 'object' && !Array.isArray(rowData)) {
                value = rowData[columnInfo.data] || rowData[columnInfo.name]
            } else if (Array.isArray(rowData)) {
                value = rowData[columnInfo.index] || rowData[columnInfo.columnIndex]
            }
            
            // Only count non-empty values
            if (value !== null && value !== undefined && value !== '') {
              // Convert value to string, or JSON string if it's an object 
              // (there is a risk of the same objects having different string representation due to property order, 
              // but this is a reasonable compromise for counting unique values quicky -- sorting object keys for every cell 
              // would be too slow for large datasets)
                value = typeof value == 'object' && value !== null ?  JSON.stringify(value) : String(value)
                uniqueValues.add(value)
            }
        })

        return uniqueValues.size
    }

    // Calculate unique counts for each column in both base and filtered datasets
    const uniqueValuesByColumn = {
        'base': {},
        'filtered': {}
    }

    columnSettings.forEach((colInfo) => {
        const columnName = colInfo.data || colInfo.name || colInfo.title

        if (columnName && columnName !== 'checkbox') { // Exclude checkbox columns from unique value counts
            uniqueValuesByColumn.base[columnName] = countUniqueInColumn(baseRows, colInfo)
            uniqueValuesByColumn.filtered[columnName] = countUniqueInColumn(filteredRows, colInfo)
        }
    })

    return uniqueValuesByColumn
  }

  _buildCountRequestUrl(dt_settings, dataTableReference) {
    const dataEndpoint = dataTableReference.ajax.url()
    const queryString = DataTablesParameterBuilder.buildServerSideParams(dt_settings, 0, 0, 0).queryString
    return dataEndpoint + "/countdistinct" + "?" + queryString
  }

  async _fetchColumnUniqueValueCountsServerSide(distinctValuesEndpoint) {
    return fetch(distinctValuesEndpoint)
      .then(response => {
        if (!response.ok) {
          throw new Error(`HTTP error when fetching distinct value counts! Status: ${response.status}`);
        }
        return response.json();
      })
  }

/**
 * Populates the primary table structure by creating and appending the table header, primary header row, and table body elements to the specified HTML table element.
 * @returns - A dictionary-like object containing references to the table, table header, primary header row, and table body HTML elements
 */
  createPrimaryTableStructure() {
    let table = document.getElementById(this.tableId);

    if(!table) {
      throw new Error(`No table element found with id "${this.tableId}". Please ensure an HTML <table> element with this id exists in the DOM before initializing the TableManager.`);
    }

    let tableHead = this._createTableHeaderSection();

    let primaryHeaderRow = document.createElement("tr");
    this.primaryHeaderRowId = this.tableId + "_primaryHeaders";
    primaryHeaderRow.id = this.primaryHeaderRowId;
    tableHead.append(primaryHeaderRow);

    let tableBody = document.createElement("tbody");
    tableBody.id = this.tableId + "_body";
    table.prepend(tableBody);

    table.prepend(tableHead);
    table.append(tableBody);

    return {
      table: table,
      tableHead: tableHead,
      primaryHeaderRow: primaryHeaderRow,
      tableBody: tableBody,
    };
  }

  /**
   * Creates the secondary table structure by adding super-headers and filter headers to the table.
   * @param {Object} TableComponents - A dictionary-like object containing references to the table, table header, primary header row, and table body HTML elements.
   * @param {Object} dataTableOptions - The options for the DataTable.
   * @returns {Object} - The updated table components with secondary structure added.
   */
  createSecondaryTableStructure(TableComponents, dataTableOptions) {
    let superHeaderRow = null;
    if (this._hasSuperHeaders()) {
      this.superHeaderRowId = this.tableId + "_superHeaders";
      superHeaderRow = this._createSuperHeaderRow(this.superHeaderRowId);
      TableComponents.tableHead.prepend(superHeaderRow);
    }

    let categoryCountsHeaderRow = null;
    if (dataTableOptions.showCategoryCounts) {
      this.categoryCountHeaderRowId = this.tableId + "_categoryCounts";
      categoryCountsHeaderRow = this._createCategoryCountHeaderRow(this.categoryCountHeaderRowId);
      TableComponents.tableHead.append(categoryCountsHeaderRow);
    }

    let filterHeaderRow = null;
    if (this._hasFilters()) {
      this.filterHeaderRowId = this.tableId + "_filterHeaders";
      filterHeaderRow = this._createFilterHeaderRow(this.filterHeaderRowId);
      TableComponents.tableHead.append(filterHeaderRow);
    }

    return {
      ...TableComponents,
      superHeaderRow: superHeaderRow,
      categoryCountsHeaderRow: categoryCountsHeaderRow,
      filterHeaderRow: filterHeaderRow,
    };
  }

  //-------------------------/
  //                         /
  //  Table header creation  /
  //                         /
  //-------------------------/

  // Creates the <thead> section of the table and assigns it an ID based on the table's ID.
  _createTableHeaderSection() {
    const tableHead = document.createElement("thead");
    this.headerSetId = this.tableId + "_headerSet";
    tableHead.id = this.headerSetId;
    return tableHead;
  }

  /**
   * Annotates the boundaries of column groups in the table header.
   * @param {HTMLElement} headerCell - The header cell to annotate.
   * @param {Object} currentColumn - The current column being processed.
   * @param {Array} tableColumns - The array of all columns in the table.
   */
  _annotateColumnGroupBoundaries(headerCell, currentColumn, tableColumns){
    if (currentColumn.column_group) {
      // Find the nearest visible column before this one (skipping hidden columns in the same group)
      let prev_col = tableColumns
        .filter((c) => c.col_idx < currentColumn.col_idx && c.visible !== false)
        .sort((a, b) => b.col_idx - a.col_idx)[0];

      // Find the nearest visible column after this one (skipping hidden columns in the same group)
      let next_col = tableColumns
        .filter((c) => c.col_idx > currentColumn.col_idx && c.visible !== false)
        .sort((a, b) => a.col_idx - b.col_idx)[0];

      // Left boundary: if no visible previous column or it's in a different group
      if (!prev_col || prev_col.column_group !== currentColumn.column_group) {
        headerCell.classList.add("col_group_boundary_left");
      }

      // Right boundary: if no visible next column or it's in a different group
      if (!next_col || next_col.column_group !== currentColumn.column_group) {
        headerCell.classList.add("col_group_boundary_right");
      }
    }
  }

  //---------------------------/
  //  Super(/grouped) headers  /
  //---------------------------/

  // Checks if all columns have a column_group property defined, indicating that super-headers should be created for the table.
  _hasSuperHeaders() {
    return this.columns.every((col) => col.column_group);
  }

  // Checks if any columns have allow_filter set to true, indicating that a filter row should be created for the table.
  _hasFilters() {
    return this.columns.some((col) => col.allow_filter);
  }
  
  /**
   * Uses column_group property of column specifications to create and append a super-header row to the table,
   * grouping columns under shared headers as specified. 
   * @Example 
   * If columns 0-4 have column_group 'Classification' and columns 5-7 have column_group 'State',
   * a super-header row is created with 'Classification' spanning columns 0-4 and 'State' spanning columns 5-7.
   * @param {HTMLElement} superHeaderRow - The <tr> element representing the super-header row to which the grouped headers will be appended.
  **/
  populateSuperHeaders(superHeaderRow) {
    //Add super-header row if all columns have a column groups defined in specifications, otherwise remove super-header row (which would just be empty)
    if (!this._hasSuperHeaders()) {
      console.log(
        "Column groups incomplete or absent in specifications, skipping super-header creation.",
      );
      return;
    }

    const superHeaderStructure = this._getSuperHeaderStructure(
      this.columns,
    );

    superHeaderStructure.forEach(({ name, colspan }) => {
      let th = document.createElement("th");
      let span = document.createElement("span");
      span.textContent = name;
      th.colSpan = colspan;
      th.classList.add("super_header_cell");
      
      let superHeaderElements = [span]
      // If there are any registered injectors for this super-header name, apply them to the cell's innerHTML
      if(this.superHeaderInjectors.getInjectors(name).length > 0){
        this.superHeaderInjectors.getInjectors(name).forEach((injectorFunction) => {
          superHeaderElements = injectorFunction(superHeaderElements);
        });
      }
      
      th.append(...superHeaderElements);      
      superHeaderRow.append(th);
    });
  }

  // Creates a super-header row element with the specified ID and returns it.
  _createSuperHeaderRow(superHeaderRowId) {
    const superHeaderRow = document.createElement("tr");
    superHeaderRow.id = superHeaderRowId;
    return superHeaderRow;
  }

  /**
   * Computes the required colspan values for super-headers based on the column_group properties of the column specifications
   * @param {Array} columnsSpecifications 
   * @returns - An array of { name, colspan } objects in col_idx order, e.g. [{ name: 'Classification', colspan: 5 }, { name: 'State', colspan: 3 }, ...] 
   */
  _getSuperHeaderStructure(columnsSpecifications) {
    try {
      this.validateColumnSpecifications();
    } catch (error) {
      console.error(
        "Error in column specification when getting super header structure:",
        error.message,
      );
      return [];
    }

    const groups = [];

    for (const col of columnsSpecifications) {
      const last = groups[groups.length - 1];
      if (last && last.name === col.column_group) {
        if (col.visible !== false) {
          last.colspan++;
        }
      } else {
        // Start a new group. Count only if the first column is visible; otherwise start at 0.
        groups.push({ name: col.column_group, colspan: col.visible !== false ? 1 : 0 });
      }
    }

    // Drop groups with colspan 0 (all columns hidden)
    return groups.filter(g => g.colspan > 0);
  }

  //-------------------/
  //  Primary headers  /
  //-------------------/

  /**
   * Populates the primary header row with column title cells.
   * @param {HTMLElement} primaryHeaderRow - The <tr> element representing the primary header row.
   */
  populateColumnTitleHeaders(primaryHeaderRow) {
    this.columns.forEach((col) => {
      // add header
      const th = document.createElement("th");

      if (col.data_type === "checkbox") {
        const masterCheckbox = new MasterCheckbox(col, this);
        th.appendChild(masterCheckbox.createHtmlElement());
        this.state.masterCheckbox = masterCheckbox; // Store reference to the master checkbox for later use
      } else {
        th.textContent = col.title;
      }
      
      if (col.cssClassHeaderCell) {
        th.classList.add(col.cssClassHeaderCell);
      } else {
        th.classList.add("primary_header_cell");
      }
      
      this._annotateColumnGroupBoundaries(th, col, this.columns);

      primaryHeaderRow.append(th);
    });
  }


  //--------------/
  //  Filter row  /
  //--------------/

  // Filter Header row

  // Creates a filter header row element with the specified ID and returns it.
  _createFilterHeaderRow(filterHeaderRowId) {
    const filterHeaderRow = document.createElement("tr");
    filterHeaderRow.id = filterHeaderRowId;
    return filterHeaderRow;
  }

  /**
   * Populates the filter header row with filter interface elements for each column that allows filtering.
   * @param {HTMLElement} filterHeaderRow - The <tr> element representing the filter header row.
   */
  populateFilterHeaders(filterHeaderRow) {
    this.columns.forEach((col) => {
      if (col.visible !== false) {
        const filterTh = document.createElement("th");

        if (col.cssClassFilterCell) {
          filterTh.classList.add(col.cssClassFilterCell);
        } else {
          filterTh.classList.add("filter_header_cell");
        }

        this._annotateColumnGroupBoundaries(filterTh, col, this.columns);

        if (col.allow_filter) {
          let filterInterface = col.createFilterInterface(this);
          
          //Fetch any registered injectors for this column's json_id and apply them to the filter interface
          if(col.filterHeaderInjector) {
            let filterInjectorFunction = this.filterHeaderInjectors.getInjector(col.filterHeaderInjector);
            if (filterInjectorFunction) { 
              filterInterface = filterInjectorFunction(!Array.isArray(filterInterface) ? [filterInterface] : filterInterface);            
            }
          }
          
          if (filterInterface) {
            //Add filter <input> elements  to filter header cell
            if (!Array.isArray(filterInterface)) {
              filterTh.appendChild(filterInterface);
            } else {
              filterInterface.forEach((el) => filterTh.appendChild(el));
            }
          }
        }
        filterHeaderRow.append(filterTh);
      }
    });
  }

  //----------------------/
  //  Category count row  /
  //----------------------/

  /**
   * Creates a category count header row element with the specified ID and returns it.
   * @param {string} categoryCountHeaderRowId - The ID for the category count header row.
   * @returns {HTMLElement} The created category count header row element.
   */
  _createCategoryCountHeaderRow(categoryCountHeaderRowId) {
    const categoryCountHeaderRow = document.createElement("tr");
    categoryCountHeaderRow.id = categoryCountHeaderRowId;
    return categoryCountHeaderRow;
  }
  
  /**
   * Populates the category count header row with category count cells.
   * @param {HTMLElement} categoryCountHeaderRow - The <tr> element representing the category count header row.
   */
  createCategoryCountHeaderCells(categoryCountHeaderRow) {
    this.columns.forEach((col) => {
      if (col.visible !== false) {
        const categoryCountTh = document.createElement("th");
        categoryCountTh.id = "th_categoryCount_" + col.json_id;

        if (col.cssClassHeaderCell) {
          categoryCountTh.classList.add(col.cssClassHeaderCell);
        } else {
          categoryCountTh.classList.add("primary_header_cell");
        }

        this._annotateColumnGroupBoundaries(categoryCountTh, col, this.columns);
        
        categoryCountHeaderRow.append(categoryCountTh);

        // Register the cell so that _updateCategoryCountCells can update every count in one pass
        this.categoryCountCells.push({ col: col, th: categoryCountTh });
      }
    });
  }

  _updateCategoryCountCells(counts) {
    for (const { col, th } of this.categoryCountCells) {
      try
      {
        if(counts['base'][col.json_id])
        {
          let baseCount = counts['base'][col.json_id]
          let filterCount = counts['filtered'][col.json_id] || 0; // Default to 0 if undefined (i.e., no rows match the filter)

          th.textContent = filterCount + "/" + baseCount;
        }
      } catch {
        console.error("Updating unique value counts failed for column:" + col.json_id)
      }
    }
  }

  //------------------------------------/
  //  Column specification validation   /
  //------------------------------------/

  validateColumnSpecifications() {
    if (!this._checkColumnsIdxContinuous(this.columns)) {
      throw new Error(
        "Column specifications must have continuous col_idx values starting from 0 with no gaps",
      );
    }

    if (!this._checkColumnsIdxSorted(this.columns)) {
      throw new Error(
        "Column specifications must be sorted in ascending order of col_idx",
      );
    }

    if (!this._checkAllOrNoneColumnGroups(this.columns)) {
      throw new Error(
        "Column specifications must have either all or none of the columns in a column group",
      );
    }

    if (!this._checkConsecutiveColumnGroups(this.columns)) {
      throw new Error(
        "Column specifications must have column groups that form contiguous blocks of columns",
      );
    }
  }

  _createColumnIndexes() {
    this.columns.forEach((col, index) => col.col_idx = index);
  }

  _columnIndexesAbsent() {
    return this.columns.every((col) => !col.hasOwnProperty("col_idx"));
  }

  _checkAllOrNoneColumnGroups(columnsSpecifications) {
    // Check that all columns have a group or all lack one (don't mix)
    const hasGrouped = columnsSpecifications.some((col) => col.column_group);
    const hasUngrouped = columnsSpecifications.some((col) => !col.column_group);
    return !(hasGrouped && hasUngrouped)
  }

  // Checks if the col_idx values of the column specifications are continuous, meaning that they form a sequence without any gaps.
  _checkColumnsIdxContinuous(columnsSpecifications) {
    let someColumnsMissingColIdx = columnsSpecifications.some((col) => !col.hasOwnProperty("col_idx"));

    if (someColumnsMissingColIdx) {      
        throw new Error("Either all or none of the column specifications should have a col_idx property");
    }    

    for (let i = 1; i < columnsSpecifications.length; i++) {
      if (
        columnsSpecifications[i].col_idx - columnsSpecifications[i - 1].col_idx !== 1
      ) {
        return false;
      }
    }
    return true;
  }

  // Checks if the col_idx values of the column specifications are sorted in ascending order.
  _checkColumnsIdxSorted(columnsSpecifications) {
    for (let i = 1; i < columnsSpecifications.length; i++) {
      if (
        columnsSpecifications[i].col_idx < columnsSpecifications[i - 1].col_idx
      ) {
        return false;
      }
    }
    return true;
  }

  /**
   * Checks if the column groups are consecutive, meaning that they form contiguous blocks of columns.
   * @param {Array} columnSpecification - An array of column specification objects defining the properties and behaviors of each column in the data table.
   * @returns Boolean indicating whether the column groups are consecutive/contiguous
  */
  _checkConsecutiveColumnGroups(columnsSpecifications) {
    if (!columnsSpecifications.some((col) => !col.column_group)) {
      return true; // If no columns have a column_group, we consider them as consecutive by default
    }

    const sorted = [...columnsSpecifications].sort(
      (a, b) => a.col_idx - b.col_idx,
    );
    const closedGroups = new Set();
    let currentGroup = null;

    for (const col of sorted) {
      if (col.column_group !== currentGroup) {
        // true = end of group
        if (closedGroups.has(col.column_group)) {
          //group previously closed
          throw new Error(
            `column_group "${col.column_group}" is non-contiguous: it appears at col_idx ${col.col_idx} after being interrupted by other groups`,
          );
        }
        if (currentGroup !== null) closedGroups.add(currentGroup);
        currentGroup = col.column_group;
      }
    }
    return true;
  }

  //------------------------------/
  //  Data Table Initialisation   /
  //------------------------------/

  /**
    * Converts a column specification into a DataTables column definition object.
    * @param {Column} column - The Column instance representing the column specification.
    * @returns {Object} - The DataTables column definition object with the appropriate data and render properties.
  */
  columnToDataTableColDef(column) {
    const colDef = { data: column.json_id };
    colDef.render = (data, type, row, meta) => column.dataTableRenderer(data, type, row, meta, this);
    colDef.createdCell = (cell, cellData, rowData, rowIndex, colIndex) => column.postCreationTasks(cell, cellData, rowData, rowIndex, colIndex, this);
    colDef.visible = column.visible !== false; // Default to true if not specified
    colDef.orderable = column.orderable !== false; // Default to true if not specified
    colDef.name = column.json_id; // Set the name property to the json_id for easier reference
    return colDef;
  }


  //-------------------------/
  //  Configuration request  /
  //-------------------------/

  /**
   * Fetches the configuration for a data table and its columns.
   * @param {string} dataTableConfigName - The name of the data table configuration.
   * @param {string} dataTableConfigVariant - The variant of the data table configuration.
   * @param {string} columnConfigName - The name of the column configuration.
   * @param {string} columnConfigVariant - The variant of the column configuration.
   * @returns {Promise<Object>} - A promise resolving to the fetched configuration objects.
   */
  static async fetchConfiguration(dataTableConfigName, dataTableConfigVariant, columnConfigName, columnConfigVariant,) {
    
     const [cols, datatable] = await Promise.all([
      TableManager._fetchConfigurationAsync(
        "column",
        columnConfigName,
        columnConfigVariant),      
      TableManager._fetchConfigurationAsync(
        "datatable",
        dataTableConfigName,
        dataTableConfigVariant,
      )]);      

      return {
        columnsSpecifications: cols,
        datatableConfiguration: datatable,
      };

  }

  // Fetch table configuration JSON from API endpoint
  // 
  /**
   * Fetches a configuration asynchronously from the API endpoint. Endpoint format: /table_provider/configuration/{configurationType}/{tableName}/{configurationVariant}
   * @param {string} configurationType - The type of the configuration.
   * @param {string} tableName - The name of the table.
   * @param {string} configurationVariant - The variant of the configuration.
   * @returns {Promise<Object>} - A promise resolving to the fetched configuration object.
   */
  static async _fetchConfigurationAsync(
    configurationType,
    tableName,
    configurationVariant,
  ) {
    if (!configurationType || !tableName || !configurationVariant) {
      throw new Error(
        "configurationType, tableName and configurationVariant are required",
      );
    }

    const url = `/table_provider/configuration/${encodeURIComponent(
      configurationType,
    )}/${encodeURIComponent(tableName)}/${encodeURIComponent(
      configurationVariant,
    )}`;

    try {
      const resp = await fetch(url, {
        method: "GET",
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });

      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(
          `Failed to fetch configuration: ${resp.status} ${resp.statusText} - ${text}`,
        );
      }

      const data = await resp.json();
      return data;
    } catch (err) {
      console.error("Error fetching table configuration:", err);
      throw err;
    }
  }
}

//----------------------//
//                      //
// Table Event Register //
//                      //
//----------------------//

class TableEventRegister {
  constructor() {
    this.eventHandlers = {};
  }

  registerEventHandler(eventHandlerName, eventHandlerFunction) {
    this.eventHandlers[eventHandlerName] = eventHandlerFunction;
  }

  getEventHandler(eventHandlerName) {
    return this.eventHandlers[eventHandlerName];
  }
}

//------------------------//
//                        //
// Cell Injector Register //
//                        //
//------------------------//

class CellInjectorRegister {
  constructor() {
    this.injectors = {};
  }

  registerInjector(injectorName, injectorFunction) {
    this.injectors[injectorName] = injectorFunction;
  }

  getInjector(injectorName) {
    return this.injectors[injectorName];
  }
}

//-------------------------------//
//                               //
// SuperHeader Injector Register //
//                               //
//-------------------------------//

class SuperHeaderInjectorRegister {
  constructor() {
    this.injectors = {};
  }

  registerInjector(superHeaderName, injectorFunction) {
    if (this.injectors[superHeaderName] == undefined) {
      this.injectors[superHeaderName] = []
    }
    this.injectors[superHeaderName].push(injectorFunction);
  }

  getInjectors(superHeaderName) {
    return this.injectors[superHeaderName] || [];
  }
}

//----------------------//
//                      //
// Event Pub/Sub System //
//                      //
//----------------------//

class EventPubSub{
    constructor(){
        this.events = {}
    }

    // Subscribe to an event with a handler function. Returns an unsubscribe function for convenience.
    subscribe(event, handler){
        if(!this.events[event]){
            this.events[event] = []
        }
        this.events[event].push(handler)
        // Return an unsubscribe function so callers don't need to remember the handler reference
        return () => this.unsubscribe(event, handler);
    }

    // Unsubscribe from an event with a handler function
    unsubscribe(event, handler){
        if(this.events[event]){
            const index = this.events[event].findIndex(item => item === handler)
            // Guard against index being -1 (handler not found). splice(-1, 1) would remove the last element.
            if(index !== -1){
                this.events[event].splice(index, 1);
            }
        }
    }

    // Publish an event with optional data. Errors in one handler don't prevent other handlers from running.
    publish(event, data){
        if(this.events[event]){
            this.events[event].forEach(handler => {
                try {
                    handler(data);
                } catch(error) {
                    console.error(`Error in event handler for '${event}':`, error);
                }
            });
        }
    }
}

//---------------//
//               //
//  Fetch Cache  //
//               //
//---------------//

class FetchCache {
  constructor(maxEntries = 200) {
    this.cache = new Map();
    this.maxEntries = maxEntries;
  }

  get(keyObject) {
    return this.cache.get(this.createKey(keyObject));
  }

  push(keyObject, value) {
    const key = this.createKey(keyObject);
    this.cache.delete(key); // re-insert so the entry becomes the most recent
    this.cache.set(key, value);
    if (this.cache.size > this.maxEntries) {
      this.cache.delete(this.cache.keys().next().value); // evict the oldest entry
    }
  }

  // The canonical JSON string is used directly as the key. 
  createKey(object) {
    return JSON.stringify(this.sortKeys(object));
  }

  sortKeys(obj) {
  if (Array.isArray(obj)) return obj.map(item => this.sortKeys(item));
  if (obj !== null && typeof obj === 'object') {
    return Object.keys(obj)
      .sort()
      .reduce((result, key) => {
        result[key] = this.sortKeys(obj[key]);
        return result;
      }, {});
  }
  return obj;
}

  clear() {
    this.cache.clear();
  }
}

//------------------------------//
//                              //
// DataTables Parameter Builder //
//                              //
//------------------------------//

class DataTablesParameterBuilder {
  /**
   * Builds server-side processing parameters in DataTables format
   * @param {Object} settings - DataTables settings object (oSettings or obtained via dataTable.settings())
   * @param {number} draw - The draw request number
   * @param {number} start - Pagination: starting row index
   * @param {number} length - Pagination: number of rows per page
   * @returns {Object} Object with queryString and params properties
   */
  static buildServerSideParams(settings, draw, start, length) {
    const params = {};

    // Add draw counter
    params['draw'] = draw;

    // Add pagination
    params['start'] = start;
    params['length'] = length;

    // Add global search
    if (settings.oPreviousSearch && settings.oPreviousSearch.sSearch) {
      params['search[value]'] = settings.oPreviousSearch.sSearch;
      params['search[regex]'] = settings.oPreviousSearch.bRegex ? 'true' : 'false';
    } else {
      params['search[value]'] = '';
      params['search[regex]'] = 'false';
    }

    // Add column definitions and per-column search
    if (settings.aoColumns) {
      settings.aoColumns.forEach((column, index) => {
        // Column metadata
        params[`columns[${index}][data]`] = column.data || '';
        params[`columns[${index}][name]`] = column.sName || '';
        params[`columns[${index}][searchable]`] = column.bSearchable ? 'true' : 'false';
        params[`columns[${index}][orderable]`] = column.bSortable ? 'true' : 'false';

        // Per-column search (from aoPreSearchCols)
        const searchCol = settings.aoPreSearchCols && settings.aoPreSearchCols[index]
          ? settings.aoPreSearchCols[index]
          : { search: '', regex: false };

        params[`columns[${index}][search][value]`] = searchCol.search || '';
        params[`columns[${index}][search][regex]`] = searchCol.regex ? 'true' : 'false';
      });
    }

    // Add ordering (sort) information
    if (settings.aaSorting && settings.aaSorting.length > 0) {
      settings.aaSorting.forEach((sort, index) => {
        params[`order[${index}][column]`] = sort[0]; // column index
        params[`order[${index}][dir]`] = sort[1];    // 'asc' or 'desc'
      });
    }

    return {
      params: params,
      queryString: this.serializeParams(params)
    };
  }

  /**
   * Converts parameter object to URL query string
   * @param {Object} params - Parameters object
   * @returns {string} URL-encoded query string (without leading ?)
   */
  static serializeParams(params) {
    return Object.entries(params)
      .map(([key, value]) => {
        return `${encodeURIComponent(key)}=${encodeURIComponent(value)}`;
      })
      .join('&');
  }

  /**
   * Converts parameter object to URLSearchParams (modern approach)
   * @param {Object} params - Parameters object
   * @returns {URLSearchParams} URLSearchParams object
   */
  static toURLSearchParams(params) {
    const searchParams = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      searchParams.append(key, value);
    });
    return searchParams;
  }
}

export { TableManager, DataTablesParameterBuilder };