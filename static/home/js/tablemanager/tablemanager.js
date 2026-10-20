import { Column } from "./column.js";
import { DataTypeFactory } from "./factories/datatypefactory.js";
import { CellStyleFactory } from "./factories/cellstylefactory.js";
import { ButtonFactory } from "./factories/buttonfactory.js";
import { MasterCheckbox, Checkbox } from "./datatypes/checkbox.js";
import { ClientDataSource } from "./datasources/clientdatasource.js";
import { ServerDataSource } from "./datasources/serverdatasource.js";

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

    // Category count labels ({ col, label }), all updated together by a single handler
    this.categoryCountCells = [];
    // Incremented per count request so a superseded (slower) response can't overwrite a newer one
    this.categoryCountRequestId = 0;

    // Header cells ({ col, th }) in the primary, category count and filter rows that carry column group boundary borders.
    // The borders depend on which columns are visible, so they are recomputed whenever column visibility changes.
    this.groupBoundaryCells = [];

    this.columns = this.initialiseColumns(columnSpecification);
    this.tableId = tableId;

    // Resolves with the DataTables API once the table is initialised and its initial data loaded; rejects if
    // initialisation fails. Setup that needs the data (filter options, counts, the checkbox search set) waits for it,
    // so it never depends on when DataTables fires its init event relative to the TableManager's own setup.
    this.ready = new Promise((resolve, reject) => {
      this._resolveReady = resolve;
      this._rejectReady = reject;
    });
    // initialiseDataTable reports failures itself; this stops a rejection nobody awaits from also being reported as unhandled
    this.ready.catch(() => {});

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
   * Initialisation runs in a fixed order:
   *  1. The whole table header (super-header, title, category count and filter rows) is built, so DataTables knows every row.
   *  2. The DataTable is created.
   *  3. Setup that needs the DataTables API but not the data (data type afterTableCreated hooks, event publishing, buttons).
   *  4. Once the initial data is loaded (this.ready), setup that needs the data (data type afterDataLoaded hooks,
   *     category counts, the checkbox search set), followed by the subscriptions that keep it up to date.
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
      this._rejectReady(error);
      return;
    }

    // Placeholder shown in display mode for null cell values (e.g. "-"); must be set before the DataTable renders any cells
    this.nullValuePlaceholder = dataTableOptions.nullValuePlaceholder ?? "-";

    // The data source hides the processing mode from the columns, cell styles and counts; it must exist before the
    // DataTable is created, as cells can be rendered (and styled) during initialisation
    this.dataSource = dataTableOptions.serverSide === true ? new ServerDataSource(this) : new ClientDataSource(this);

    // -- 1. Table structure -- //
    // Every header row is in place before DataTables reads the header, so it lays them all out: it removes the cells of
    // hidden columns, adjusts the super-header colspans and keeps the rows aligned when column visibility changes.
    const tableComponents = this.createTableStructure(dataTableOptions);
    // Index of the primary header row within the table header; the rows above it are super-headers, the rows below
    // it (category counts, filters) are controls rather than headings
    this.titleRowIndex = tableComponents.titleRowIndex;

    if (!dataTableOptions.columns) {
      dataTableOptions.columns = this.columns.map((col) =>
        this.columnToDataTableColDef(col),
      );
    } else {
      console.log(
        "DataTable options already contains column definitions, skipping automatic column definition generation from specifications.",
      );
    }

    // The column titles, ordering and ordering icons belong to the primary header row. Without this DataTables
    // attaches ordering to every header row and takes the column title from the last non-empty row.
    if (dataTableOptions.titleRow === undefined) {
      dataTableOptions.titleRow = tableComponents.titleRowIndex;
    }

    // -- 2. DataTable -- //
    // Any initComplete function supplied in the config is passed through unchanged; the TableManager's own
    // data-dependent setup waits for this.ready instead.
    this.dataTableReference = $(`#${this.tableId}`).DataTable(dataTableOptions);

    // -- 3. Setup that needs the DataTables API but not the data -- //
    this.columns.forEach((col) => col.type.afterTableCreated(this));

    // Group boundary borders follow the visible columns
    this._refreshGroupBoundaries();
    this.dataTableReference.on("column-visibility.dt", () => this._refreshGroupBoundaries());

    // Set up event publishing for DataTables events that may be of interest to subscribers, such as search, order, and page events.
    this.setupDatTablesEventPublishing();

    // If any of the columns are of type "checkbox", subscribe to relevant events to help synchronise checkboxes.
    if (this.columns.some((col) => col.data_type === "checkbox")) {
      this.setupCheckboxEventSubscriptions();
    }

    this.assignOddEvenRowClasses(dataTableOptions);

    this.runButtonFactory(dataTableOptions);

    // -- 4. Setup that needs the data -- //
    // ready() runs the function straight away if DataTables already finished initialising inside its constructor
    // (e.g. data supplied inline), otherwise once the initial data has loaded.
    this.ready.then(() => this._afterDataLoaded(dataTableOptions));
    this.dataTableReference.ready(() => this._resolveReady(this.dataTableReference));
  }

  // Runs once the initial data is loaded. Each piece of data-dependent state is computed once for the loaded data and
  // then kept up to date by a search change subscription, so no change is missed or handled twice.
  _afterDataLoaded(dataTableOptions) {
    this.columns.forEach((col) => {
      Promise.resolve()
        .then(() => col.type.afterDataLoaded(this))
        .catch((error) => console.error(`Data loaded setup failed for column ${col.json_id}:`, error));
    });

    if (dataTableOptions.showCategoryCounts) {
      this._handleColumnUniqueValueCounts();
      this.dataSource.onSearchChange(() => this._handleColumnUniqueValueCounts());
    }

    if (this.state.masterCheckbox) {
      // Update the currentSearchCheckBoxes state whenever the table's search changes. This goes through the data source
      // rather than the DataTableSearch event, which fires server-side before the new search is sent.
      this.state.masterCheckbox.updateCheckBoxSearchSet();
      this.dataSource.onSearchChange(() => this.state.masterCheckbox.updateCheckBoxSearchSet());
    }
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
      // The currentSearchCheckBoxes state needs the data, so it is set up in _afterDataLoaded.

      // Subscribe to the DataTableDraw event to synchronise the checkboxes in the rendered rows with the activeCheckBoxes 
      // state whenever the table is redrawn (e.g. after a search, order, or page change).
      this.eventPubSub.subscribe("DataTableDraw", () => Checkbox.synchroniseVisibleCheckState(this));
      this.eventPubSub.subscribe("DataTableDraw", () => this.state.masterCheckbox.synchroniseCheckedState(this));
      
      // Subscribe to the checkboxStateChanged event to synchronise the master checkbox state whenever a checkbox is checked or unchecked.
      // The checkboxStateChanged is published by the default checkbox click handler, 
      // and should be published by any custom click handler that is registered for the checkbox column.
      this.eventPubSub.subscribe("checkboxStateChanged", () => this.state.masterCheckbox.synchroniseCheckedState(this));
  }

  async _handleColumnUniqueValueCounts(){
    const requestId = ++this.categoryCountRequestId;
    let counts;

    try {
      counts = await this.dataSource.getDistinctCounts();
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

/**
 * Builds the complete table structure before the DataTable is created: the table header with all of its rows
 * (super-header, primary header, category count and filter rows, in that order) and the table body.
 * Every header row has one cell per column, hidden columns included, because DataTables lays the header out over all
 * columns and removes the cells of hidden columns itself. Rows other than the primary header row are marked
 * data-dt-order="disable" so DataTables adds no ordering icons or listeners to them.
 * @param {Object} dataTableOptions - The options for the DataTable.
 * @returns - A dictionary-like object containing references to the table, table header, header rows and table body
 *   HTML elements, and titleRowIndex, the index of the primary header row within the table header.
 */
  createTableStructure(dataTableOptions) {
    let table = document.getElementById(this.tableId);

    if(!table) {
      throw new Error(`No table element found with id "${this.tableId}". Please ensure an HTML <table> element with this id exists in the DOM before initializing the TableManager.`);
    }

    let tableHead = this._createTableHeaderSection();

    let superHeaderRow = null;
    if (this._hasSuperHeaders()) {
      this.superHeaderRowId = this.tableId + "_superHeaders";
      superHeaderRow = this._createSuperHeaderRow(this.superHeaderRowId);
      superHeaderRow.dataset.dtOrder = "disable";
      tableHead.append(superHeaderRow);
      this.populateSuperHeaders(superHeaderRow);
    }

    let primaryHeaderRow = document.createElement("tr");
    this.primaryHeaderRowId = this.tableId + "_primaryHeaders";
    primaryHeaderRow.id = this.primaryHeaderRowId;
    const titleRowIndex = tableHead.rows.length;
    tableHead.append(primaryHeaderRow);
    this.populateColumnTitleHeaders(primaryHeaderRow);

    let categoryCountsHeaderRow = null;
    if (dataTableOptions.showCategoryCounts) {
      this.categoryCountHeaderRowId = this.tableId + "_categoryCounts";
      categoryCountsHeaderRow = this._createCategoryCountHeaderRow(this.categoryCountHeaderRowId);
      categoryCountsHeaderRow.dataset.dtOrder = "disable";
      tableHead.append(categoryCountsHeaderRow);
      this.createCategoryCountHeaderCells(categoryCountsHeaderRow);
    }

    let filterHeaderRow = null;
    if (this._hasFilters()) {
      this.filterHeaderRowId = this.tableId + "_filterHeaders";
      filterHeaderRow = this._createFilterHeaderRow(this.filterHeaderRowId);
      filterHeaderRow.dataset.dtOrder = "disable";
      tableHead.append(filterHeaderRow);
      this.populateFilterHeaders(filterHeaderRow);
    }

    let tableBody = document.createElement("tbody");
    tableBody.id = this.tableId + "_body";

    table.prepend(tableHead);
    table.append(tableBody);

    return {
      table: table,
      tableHead: tableHead,
      superHeaderRow: superHeaderRow,
      primaryHeaderRow: primaryHeaderRow,
      categoryCountsHeaderRow: categoryCountsHeaderRow,
      filterHeaderRow: filterHeaderRow,
      tableBody: tableBody,
      titleRowIndex: titleRowIndex,
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

  // Registers a header cell whose column group boundary borders are maintained by _refreshGroupBoundaries.
  _registerGroupBoundaryCell(headerCell, column) {
    if (column.column_group) {
      this.groupBoundaryCells.push({ col: column, th: headerCell });
    }
  }

  // Recomputes the group boundary borders of every registered header cell from the DataTable's current column visibility.
  _refreshGroupBoundaries() {
    const isVisible = (col) => this.dataTableReference.column(col.col_idx).visible();
    this.groupBoundaryCells.forEach(({ col, th }) => this._annotateColumnGroupBoundaries(th, col, this.columns, isVisible));
  }

  /**
   * Annotates the boundaries of column groups in the table header.
   * @param {HTMLElement} headerCell - The header cell to annotate.
   * @param {Object} currentColumn - The current column being processed.
   * @param {Array} tableColumns - The array of all columns in the table.
   * @param {Function} isVisible - Returns whether a column is currently visible.
   */
  _annotateColumnGroupBoundaries(headerCell, currentColumn, tableColumns, isVisible){
    if (currentColumn.column_group) {
      // Find the nearest visible column before this one (skipping hidden columns in the same group)
      let prev_col = tableColumns
        .filter((c) => c.col_idx < currentColumn.col_idx && isVisible(c))
        .sort((a, b) => b.col_idx - a.col_idx)[0];

      // Find the nearest visible column after this one (skipping hidden columns in the same group)
      let next_col = tableColumns
        .filter((c) => c.col_idx > currentColumn.col_idx && isVisible(c))
        .sort((a, b) => a.col_idx - b.col_idx)[0];

      // Left boundary: if no visible previous column or it's in a different group
      headerCell.classList.toggle("col_group_boundary_left", !prev_col || prev_col.column_group !== currentColumn.column_group);

      // Right boundary: if no visible next column or it's in a different group
      headerCell.classList.toggle("col_group_boundary_right", !next_col || next_col.column_group !== currentColumn.column_group);
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
   * Computes the required colspan values for super-headers based on the column_group properties of the column specifications.
   * Colspans count every column, hidden ones included: DataTables lays the header out over all columns and shrinks the
   * colspans (or removes the cell of an entirely hidden group) to match the visible columns itself.
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
        last.colspan++;
      } else {
        groups.push({ name: col.column_group, colspan: 1 });
      }
    }

    return groups;
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

      this._registerGroupBoundaryCell(th, col);

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
   * Hidden columns get a cell (and filter interface) too; DataTables shows it when the column is shown.
   * @param {HTMLElement} filterHeaderRow - The <tr> element representing the filter header row.
   */
  populateFilterHeaders(filterHeaderRow) {
    this.columns.forEach((col) => {
      const filterTh = document.createElement("th");

      if (col.cssClassFilterCell) {
        filterTh.classList.add(col.cssClassFilterCell);
      } else {
        filterTh.classList.add("filter_header_cell");
      }

      this._registerGroupBoundaryCell(filterTh, col);

      if (col.allow_filter) {
        let filterInterface = col.type.createFilterInterface(this);

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
   * Populates the category count header row with category count cells, one per column (hidden columns included).
   * @param {HTMLElement} categoryCountHeaderRow - The <tr> element representing the category count header row.
   */
  createCategoryCountHeaderCells(categoryCountHeaderRow) {
    this.columns.forEach((col) => {
      const categoryCountTh = document.createElement("th");
      categoryCountTh.id = "th_categoryCount_" + col.json_id;

      if (col.cssClassHeaderCell) {
        categoryCountTh.classList.add(col.cssClassHeaderCell);
      } else {
        categoryCountTh.classList.add("primary_header_cell");
      }

      this._registerGroupBoundaryCell(categoryCountTh, col);

      // The count is written to its own element: DataTables wraps the cell's contents in its own markup, which
      // setting the cell's text would remove
      const countLabel = document.createElement("span");
      categoryCountTh.append(countLabel);

      categoryCountHeaderRow.append(categoryCountTh);

      // Register the label so that _updateCategoryCountCells can update every count in one pass
      this.categoryCountCells.push({ col: col, label: countLabel });
    });
  }

  _updateCategoryCountCells(counts) {
    for (const { col, label } of this.categoryCountCells) {
      try
      {
        if(counts['base'][col.json_id])
        {
          let baseCount = counts['base'][col.json_id]
          let filterCount = counts['filtered'][col.json_id] || 0; // Default to 0 if undefined (i.e., no rows match the filter)

          label.textContent = filterCount + "/" + baseCount;
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
    // DataTables writes the title into the title row cell as HTML, so it is escaped. Without a title DataTables takes
    // it from the first single-column header cell from the top, which can be a one-column super-header group.
    // The checkbox column has none, as writing one would replace the master checkbox.
    if (column.data_type !== "checkbox") {
      colDef.title = $.fn.dataTable.util.escapeHtml(String(column.title ?? ""));
    }
    colDef.render = (data, type, row, meta) => column.type.dataTableRenderer(data, type, row, meta, this);
    colDef.createdCell = (cell, cellData, rowData, rowIndex, colIndex) => column.type.postCreationTasks(cell, cellData, rowData, rowIndex, colIndex, this);
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

export { TableManager };