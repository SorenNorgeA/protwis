// This class represents the "text" data type used in the table manager.
// It provides simple text filtering capabilities, allowing users to filter table data based on text input.

import { DataTypeBase } from "./datatypebase.js";
import { DataTablesParameterBuilder } from "../tablemanager.js";

class Checkbox extends DataTypeBase {
    
    dataTableRenderer(data, type, row, meta, tableManagerReference) {
        if (type === "display" && data) {
            let id_property = '';
            let data_properties = '';

            const newCheckBox = document.createElement("input");
            newCheckBox.type = "checkbox";
            
            if (data.row_id !== undefined) {
                newCheckBox.id = 'chkbx_' + tableManagerReference.tableId + '_' + data.row_id;
                newCheckBox.setAttribute('data-row_id', data.row_id);
            }

            if (data.data !== undefined) {
                let keys = Object.getOwnPropertyNames(data.data)
                for (const key of keys) {
                    newCheckBox.setAttribute('data-' + key, data.data[key]);
                }
            }

            return newCheckBox.outerHTML;
        }
        // Search, order and type return
        return data ? data.row_id : ""
    }

    eventHookup(cell, cellData, rowData, rowIndex, colIndex, tableManagerReference) {
        if (!cellData) {
            return;
        }
        
        // Register the default click handler if an alternative handler has not been specified in the eventRegister.
        if (!this.eventRegister)
        {
            tableManagerReference.eventRegistry.registerEventHandler('defaultCheckboxClick', this.defaultClickHandler);
            this.eventRegister = {
                'click': {
                    handler: 'defaultCheckboxClick',
                    enableRowClick: true
                }
            };
        }
        
        for (const event of Object.getOwnPropertyNames(this.eventRegister)) {
            const handlerName = this.eventRegister[event].handler;
            const handlerFunction = tableManagerReference.eventRegistry.getEventHandler(handlerName);
            
            if (handlerFunction == undefined) {
                console.error(`Handler function ${handlerName} not found in event registry.`);
                continue;
            }

            const listener = (e) => handlerFunction(e, cellData, tableManagerReference);
            const rowClick = this.eventRegister[event].enableRowClick;

            let checkbox = cell.querySelector('input[type="checkbox"]');
            checkbox?.addEventListener(event, listener);
            
            // Add an event listener to the parent row if enableRowClick is true, so that clicking anywhere on the row will trigger the checkbox click event.
            if (rowClick) {
                // attach event to parent row
                cell.parentElement?.addEventListener(event, (e) => { 
                    const sender = e.target;
                    // Prevent click handling for anchor tags to allow normal link navigation
                    if (sender.nodeName.toLowerCase() === "a") {
                        return;
                    }
                    
                    // Prevent click handling for the checkbox itself to avoid double toggling
                    if (sender.nodeName.toLowerCase() === "input" && sender.type === "checkbox") {
                        return;
                    }                    

                    checkbox.click()
                });
            }
        }
    }

    defaultClickHandler = (e, cellData, tableManagerReference) => {
        const checkbox = e.target;
        const rowId = Number.parseInt(cellData.row_id);
        
        if (checkbox.checked) {
            tableManagerReference.state.activeCheckBoxes.set(rowId, cellData);
        } else {
            tableManagerReference.state.activeCheckBoxes.delete(rowId);
        }

        tableManagerReference.eventPubSub.publish('checkboxStateChanged', { rowId: rowId, checked: checkbox.checked, cellData: cellData });
    }
    
    createFilterInterface(tableManagerReference) {
        if (!this.allow_filter) {
            return null;
        }

        
    }

    static synchroniseVisibleCheckState(tableManagerReference) {
    const checkboxCol = tableManagerReference.columns.find((col) => col.data_type === "checkbox");
    if (!checkboxCol) {
      return;
    }

    // Sync the check state of the checkboxes in the rendered rows with the activeCheckBoxes state    
    tableManagerReference.dataTableReference.cells(null, checkboxCol.col_idx, { search: "applied" }).nodes().each((cell) => {
      const rowCheckbox = cell.querySelector('input[type="checkbox"]');
      if (rowCheckbox) {
        tableManagerReference.state.activeCheckBoxes.has(Number.parseInt(rowCheckbox.dataset.row_id)) ? rowCheckbox.checked = true : rowCheckbox.checked = false;
      }
    });
  }
    
}

class MasterCheckbox{

    column;
    tableManagerReference;
    checkboxElement;
    updatingState = false; // Semaphore to track whether a serverside request for state information is ongoing
    
    constructor(checkboxColumnColumnReference, tableManagerReference) {        
        this.column = checkboxColumnColumnReference;
        this.tableManagerReference = tableManagerReference;
    }

    createHtmlElement() {
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.addEventListener("change", (e) => this.clickHandler(e, this.tableManagerReference));
        this.checkboxElement = checkbox;
        return checkbox;
    }

    async clickHandler(event, tableManagerReference) {
        const masterCheckbox = event.target;
        const activeCheckboxes = tableManagerReference.state.activeCheckBoxes;

        while (this.updatingState) {
            // Wait for the ongoing serverside request to complete before proceeding with the click handling
            await new Promise(resolve => setTimeout(resolve, 100));
        }

        // Cell data for every row matching the current search/filters, regardless of pagination
        let searchSetCheckboxes = tableManagerReference.state.currentSearchCheckBoxes;

        this.applyMasterCheckClick(masterCheckbox, activeCheckboxes, searchSetCheckboxes, tableManagerReference);
    }

    applyMasterCheckClick(masterCheckboxElement, activeCheckboxes, searchSetCheckboxes, tableManagerReference) {        
        const wasIndeterminate = masterCheckboxElement.indeterminate;       

        let isChecked;
        // If transitioning from indeterminate, force checked state and add all rows
        if (wasIndeterminate) {
            isChecked = true;
            masterCheckboxElement.checked = true;
            masterCheckboxElement.indeterminate = false;

            searchSetCheckboxes.forEach((checkBox, row_id) =>  {
                activeCheckboxes.set(row_id, checkBox);
            });
        } else {
            // Normal toggle: checked = add all, unchecked = remove all
            isChecked = masterCheckboxElement.checked;
            searchSetCheckboxes.forEach( (checkBox, row_id) =>  {
                isChecked ? activeCheckboxes.set(row_id, checkBox) : activeCheckboxes.delete(row_id);                
            });
        }

        // Sync only the rows currently rendered in the DOM
        tableManagerReference.dataTableReference.cells(null, this.column.col_idx, { page: "current", search: "applied" }).nodes().each((cell) => {
        const rowCheckbox = cell.querySelector('input[type="checkbox"]');
        if (rowCheckbox) {
            rowCheckbox.checked = isChecked;
        }
        });
    }

    async updateCheckBoxSearchSet() {
        // Update the currentSearchCheckBoxes map with the checkbox data for all rows matching the current search/filters, regardless of pagination
        if (this.tableManagerReference.dataTableReference.settings()[0].oFeatures.bServerSide === true) {
            await this.populateCurrentSearchCheckBoxes_ServerSide(this.tableManagerReference);
        }
        else {
            this.populateCurrentSearchCheckBoxes_ClientSide();
        }

        this.synchroniseCheckedState();
    }

      // Populate a map of the checkbox data for all rows matching the current search/filters using client-side data
    populateCurrentSearchCheckBoxes_ClientSide() {
        // Clear the current search checkboxes map
        this.tableManagerReference.state.currentSearchCheckBoxes.clear();

        // Iterate through rows matching the current search and their underlying data
        this.tableManagerReference.dataTableReference.rows({ search: "applied" }).every((rowIndex) => {
            const rowData = this.tableManagerReference.dataTableReference.row(rowIndex).data();
            
            // Get the checkbox cell data (i.e AJAX data) for this row using the column's data property
            const checkboxData = rowData[this.column.json_id];
            
            if (checkboxData && checkboxData.row_id) {
                const rowId = Number.parseInt(checkboxData.row_id);
                
                // If this checkbox is in the active set, add it to current search
                this.tableManagerReference.state.currentSearchCheckBoxes.set(rowId, checkboxData);            
            }
        });
    }

    // Populate a map of the checkbox data for all rows matching the current search/filters using client-side data
    async populateCurrentSearchCheckBoxes_ServerSide(tableManagerReference) {
        while (this.updatingState) {
            // Wait for any ongoing serverside request to complete before proceeding with the click handling
            await new Promise(resolve => setTimeout(resolve, 100));
        }

        this.updatingState = true; // Set the semaphore to indicate that a serverside request is ongoing
        let checkBoxData = await this.fetchSearchSetCheckBoxesFromServerSide(tableManagerReference);
        this.updatingState = false; // Reset the semaphore after the request is complete
        if (!checkBoxData) {
            console.error("Failed to fetch checkbox data for current search set from server.");
            return;
        }

        // Clear the current search checkboxes map
        this.tableManagerReference.state.currentSearchCheckBoxes.clear();

        // Populate the current search checkboxes map with the fetched data
        for (const checkboxEntry of checkBoxData) {
            let checkbox = checkboxEntry.checkbox;
            if (checkbox && checkbox.row_id) {
                const rowId = Number.parseInt(checkbox.row_id);
                this.tableManagerReference.state.currentSearchCheckBoxes.set(rowId, checkbox);
            }
        }
    }

    // Fetch the checkbox data for the current search set from the server
    async fetchSearchSetCheckBoxesFromServerSide(tableManagerReference) {            
        const dataEndpoint = tableManagerReference.dataTableReference.ajax.url()
        
        let queryString = DataTablesParameterBuilder.buildServerSideParams(this.tableManagerReference.dataTableReference.settings()[0], 0, 0, 0).queryString
    
        const checkboxEndpoint = dataEndpoint + "/options/checkbox/?" + queryString
        let result = await fetch(checkboxEndpoint)
        
        if (!result.ok) {
        console.error(`HTTP error when fetching checkbox data for current search set! Status: ${result.status}`);
        return;
        }

        return await result.json();
    }  

    synchroniseCheckedState() {

        // Create a set from the currently checked checkboxes
        const activeRowIds = new Set(this.tableManagerReference.state.activeCheckBoxes.keys());
        // Create a set from the checkboxes that are captured in the current search results
        const currentSearchRowIds = new Set(this.tableManagerReference.state.currentSearchCheckBoxes.keys());

        const allChecked = activeRowIds.size > 0 && 
                            (this.setsAreEqual(activeRowIds, currentSearchRowIds) || //Search set and active set are equal
                            this.setContains(activeRowIds, currentSearchRowIds)); //Search set is a subset of active set
        const allUnchecked = activeRowIds.size == 0 || this.setDoesNotContain(activeRowIds, currentSearchRowIds); //Search set has no overlap with the active set

        // Mixed state shows as indeterminate; otherwise checked only if every rendered row is checked
        const isIndeterminate = !allChecked && !allUnchecked;
        this.checkboxElement.indeterminate = isIndeterminate;
        this.checkboxElement.checked = allChecked && !isIndeterminate;
    }

    setsAreEqual(a, b) {
        try {
            return a.size === b.size && a.symmetricDifference(b).size === 0;
        } catch (error) { // Older browsers may not support Set methods like symmetricDifference, so we fall back to a manual comparison
            if (a === b) return true;
            if (a.size !== b.size) return false;
            for (const value of a) if (!b.has(value)) return false;
            return true;
        }
    }

    setContains(setA, setB) {
        try {
            return setB.isSubsetOf(setA);
        } catch (error) { // Older browsers may not support Set methods like isSubsetOf, so we fall back to a manual comparison
        for (const value of setB) {
                if (!setA.has(value)) {
                    return false;
                }
            }
            return true;
        }
    }

    setDoesNotContain(setA, setB) {
        try {
            return setB.isDisjointFrom(setA);
        } catch (error) { // Older browsers may not support Set methods like isSubsetOf, so we fall back to a manual comparison
            for (const value of setB) {
                if (setA.has(value)) {
                    return false;
                }
            }
            return true;
        }
    }
}

export { Checkbox, MasterCheckbox };