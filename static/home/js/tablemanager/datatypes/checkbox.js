// This class represents the "text" data type used in the table manager.
// It provides simple text filtering capabilities, allowing users to filter table data based on text input.

import { DataTypeBase } from "./datatypebase.js";

class CheckBox extends DataTypeBase {
    
    dataTableRenderer(data, type, row, meta, tableManagerReference) {
        if (type === "display" && data) {
            let id_property = '';
            let data_properties = '';

            const newCheckBox = document.createElement("input");
            newCheckBox.type = "checkbox";
            
            if (data.id !== undefined) {
                newCheckBox.id = 'chkbx_' + tableManagerReference.tableId + '_' + data.id;
            }

            if (data.data !== undefined) {
                let keys = Object.getOwnPropertyNames(data.data)
                for (const key of keys) {
                    newCheckBox.setAttribute('data-' + key, data.data[key]);
                }
            }

            if (this.eventRegister !== undefined) {
                let events = Object.getOwnPropertyNames(this.eventRegister)
                for (const event of events) {
                    const handlerName = this.eventRegister[event].handler
                    const handlerFunction = tableManagerReference.eventRegistry.getEventHandler(handlerName);
                    if (handlerFunction == undefined) {
                        console.error(`Handler function ${handlerName} not found in event registry.`);
                        continue;
                    }

                    if(this.eventRegister[event].eventOwner === undefined || this.eventRegister[event].eventOwner == "self") { 
                      // push event to checkbox itself
                      newCheckBox.addEventListener(event, handlerFunction);
                    } else if (this.eventRegister[event].eventOwner == "cell") {
                      // push event to parent cell 
                      tableManagerReference.dataLoadedCallBacks.push(() => {
                        newCheckBox.closest('td').addEventListener(event, (event) => handlerFunction(event, data.data));
                      });
                    } else if (this.eventRegister[event].eventOwner == "row") {
                      // push event to parent row
                      tableManagerReference.dataLoadedCallBacks.push(() => {
                        newCheckBox.closest('tr').addEventListener(event, (event) => handlerFunction(event, data.data));
                      });
                    }
                }
            }

            return newCheckBox;
        }
        // Search, order and type return
        return data ? data.id : ""
    }

    createFilterInterface(tableManagerReference) {
        if (!this.allow_filter) {
            return null;
        }

        
    }

    
}

export { CheckBox };