// This class represents the "textselect" data type used in the table manager.
// It provides a select input filtering interface, allowing users to filter table data based on options selected from a dropdown list.
// The select2 library is used to enhance the select input with features such as multi-select, search, and tagging.
// The options for the select input can be populated either from the client-side data or fetched from the server-side endpoint, depending on the configuration of the DataTable.

import { DataTypeBase } from "./datatypebase.js";

class TextSelect extends DataTypeBase {

    select2options = {
                            multiple: true,
                            closeOnSelect: true, 
                            dropdownAutoWidth: true,
                            placeholder: "Select...",
                            tags: true,
                            allowClear: true 
                        }    
    
    createFilterInterface(tableManagerReference) {  
      if (!this.allow_filter) {
        return null;
      }

      //-----------------//
      //  HTML Elements  //
      //-----------------//

      let filterInput = this._createInputForSelectFilter();
      
      //-----------------------------------------------------------------//
      /// Callbacks for post processing and event handler registration ////
      //-----------------------------------------------------------------//

      // Add function to populate select options as a callback to be run once data is loaded into the table,
      // as we need access to the dataset to determine the unique values for the options.
      if(tableManagerReference.dataTableReference.settings()[0].oFeatures.bServerSide === true) {
        tableManagerReference.dataLoadedCallBacks.push( () => this._populateTextSelectFilterOptionsServerSide(this.json_id, this.col_idx, filterInput, tableManagerReference.dataTableReference));
      } else {
        tableManagerReference.dataLoadedCallBacks.push( () => this._populateTextSelectFilterOptionsClientSide(this.json_id, this.col_idx, filterInput, tableManagerReference.dataTableReference));
      }

      // Add select2 initialization as a callback to be run when document is ready to ensure the relevant DOM element is available  
      tableManagerReference.documentReadyCallBacks.push( 
        () => $(`#${filterInput.attributes.id.value}`).select2(this.select2options)
      );

      //Add event handler registration to callback for when document is ready
      tableManagerReference.documentReadyCallBacks.push( () => $(`#${filterInput.attributes.id.value}`).on('change', (e) => this.onSelectChangeHandler(e, this.col_idx, tableManagerReference)));

      return filterInput;
  }

    //------------------//
    //  Event Handlers  //
    //------------------//
    onSelectChangeHandler = (e, col_idx, tableManagerReference) => {
        let currentSearchValue = tableManagerReference.dataTableReference.columns(col_idx).search()[0] || "%";

        let serverSideProcessing = tableManagerReference.dataTableReference.settings()[0].oFeatures.bServerSide;

        let newSearchValues;
        if (serverSideProcessing) {
          // We use the id property of the select2 data object (which actually holds the 'value' property of the select element) rather than the display text, 
          // as the id/value contains the raw data data for the column, which we will need for matching against the database.
          newSearchValues = $('#' + e.target.id).select2('data').map( (o) => o.id)
        } else {
          // For client-side processing, we use the text property of the select2 data object as the text has been processed for display (e.g., HTML tags have been stripped) and 
          // will match the internal data (see settings.aoData[rowIndex]._aFilterData[columnIndex]) that datatables automatically keeps for filtering.
          newSearchValues = $('#' + e.target.id).select2('data').map( (o) => o.text)
        }

        const allOptions = $('#' + e.target.id).find('option').toArray()

        let useRegex = newSearchValues.length > 1; //DataTables regex search is required for multiple values, as we will join them with the OR operator "|"
        let tagState;
        if (this.select2options.tags) {
          tagState = allOptions.map((o) => o.dataset['select2Tag'] === 'true' || false)
          if (tagState.includes(true)) {
            useRegex = true;
          }
        }

        if (useRegex) {
          newSearchValues = newSearchValues.map((v) => v.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')); // escape regex special characters
          for (let i = 0; i < newSearchValues.length; i++) {
            let optIdx;
            if (serverSideProcessing) { // See above for explanation of why we use the id/value for client-side processing and the text for server-side processing
              optIdx = allOptions.map( (o) => o.value).indexOf(newSearchValues[i]);
            } else {
              optIdx = allOptions.map( (o) => o.text).indexOf(newSearchValues[i]);
            }
            
            let isTag = tagState[optIdx];
            
            if (isTag) {
              newSearchValues[i] = "(.*" + newSearchValues[i] + ".*)"; // wrap tag values with wildcards for "contains" search
            } else {
              newSearchValues[i] = "(^" + newSearchValues[i] + "$)"; // wrap non-tag values with anchors for "exact match" search
            }
          }
        }

        let newSearchValue = newSearchValues.join("|"); // join multiple values with OR operator for regex search

        if (newSearchValue !== currentSearchValue) {
          tableManagerReference.dataTableReference.columns(col_idx).search(newSearchValue, useRegex).draw();
        }
    }

    _createInputForSelectFilter() {
        let filterInput = document.createElement("select");
        filterInput.setAttribute("data-field", this.json_id);
        filterInput.setAttribute("id", this.tableId + "_" + this.json_id + "_search");
        filterInput.setAttribute("name", this.tableId + "_" + this.json_id + "_select");

        filterInput.classList.add("col-filter");
        filterInput.classList.add("select-filter");

        if (this.cssClassFilterInput) {
          filterInput.classList.add(this.cssClassFilterInput);
        }

        return filterInput;
    }

    _processHTMLForDisplay(value) {
          if (value === null || value === undefined) return '';
          let text = String(value);
          text = text.replace(/<sub>.*?<\/sub>/g, (match) => {
            return match.replace(/<\/?sub>/g, '');
          });
          text = this._decodeHTMLEntities(text);
          return text;
        }

    _decodeHTMLEntities(text) {
      if (text === null || text === undefined) return '';

      const greekLetters = {
        '&Alpha;': 'Α', '&alpha;': 'α',
        '&Beta;': 'Β', '&beta;': 'β',
        '&Gamma;': 'Γ', '&gamma;': 'γ',
        '&Delta;': 'Δ', '&delta;': 'δ',
        '&Epsilon;': 'Ε', '&epsilon;': 'ε',
        '&Zeta;': 'Ζ', '&zeta;': 'ζ',
        '&Eta;': 'Η', '&eta;': 'η',
        '&Theta;': 'Θ', '&theta;': 'θ',
        '&Iota;': 'Ι', '&iota;': 'ι',
        '&Kappa;': 'Κ', '&kappa;': 'κ',
        '&Lambda;': 'Λ', '&lambda;': 'λ',
        '&Mu;': 'Μ', '&mu;': 'μ',
        '&Nu;': 'Ν', '&nu;': 'ν',
        '&Xi;': 'Ξ', '&xi;': 'ξ',
        '&Omicron;': 'Ο', '&omicron;': 'ο',
        '&Pi;': 'Π', '&pi;': 'π',
        '&Rho;': 'Ρ', '&rho;': 'ρ',
        '&Sigma;': 'Σ', '&sigma;': 'σ',
        '&Tau;': 'Τ', '&tau;': 'τ',
        '&Upsilon;': 'Υ', '&upsilon;': 'υ',
        '&Phi;': 'Φ', '&phi;': 'φ',
        '&Chi;': 'Χ', '&chi;': 'χ',
        '&Psi;': 'Ψ', '&psi;': 'ψ',
        '&Omega;': 'Ω', '&omega;': 'ω'
      };

      let decoded = String(text);
      for (const [entity, unicode] of Object.entries(greekLetters)) {
        decoded = decoded.replace(new RegExp(entity.replace(/&/, '\\&'), 'g'), unicode);
      }

      return decoded;
    }

    _populateTextSelectFilterOptionsFromData(data, ColumnJsonId, filterInput) {
      
      if (!data || data.length === 0) {
        console.warn("No data available to populate select filter options for column " + ColumnJsonId);
        return;
      }

      data.forEach(value => {
        let option = document.createElement("option");
        option.textContent = this._processHTMLForDisplay(value);
        // set option value to empty string for placeholder option to allow clearing the filter when selected
        option.value = value; 
        filterInput.appendChild(option);
      });
    }

    _populateTextSelectFilterOptionsClientSide(ColumnJsonId, columnIdx, filterInput, dataTableReference) {
      const data = dataTableReference.column(columnIdx).data().toArray()
      if (data) {
        const uniqueValues = Array.from(new Set([...data])).sort();      
        this._populateTextSelectFilterOptionsFromData(uniqueValues, ColumnJsonId, filterInput);
      }
    }


    _populateTextSelectFilterOptionsServerSide(ColumnJsonId, columnIdx, filterInput, dataTableReference) {
      const dataEndpoint = dataTableReference.ajax.url()
      const optionsEndpoint = dataEndpoint + "/options/" + ColumnJsonId + "/"

      fetch(optionsEndpoint)
        .then(response => {
          if (!response.ok) {
            throw new Error(`HTTP error when fetching options for column ${ColumnJsonId}! Status: ${response.status}`);
          }
          return response.json();
        })
        .then(data => {
          this._populateTextSelectFilterOptionsFromData(data, ColumnJsonId, filterInput);
        });

    }
}

export { TextSelect };