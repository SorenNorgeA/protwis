// This class represents the "textselect" data type used in the table manager.
// It provides a select input filtering interface, allowing users to filter table data based on options selected from a dropdown list.
// The select2 library is used to enhance the select input with features such as multi-select, search, and tagging.
// The options for the select input come from the table manager's data source (the loaded rows client-side, the options endpoint server-side).

// It extends Text, inheriting its display rendering (null placeholder, truncation).

import { Text } from "./text.js";

class TextSelect extends Text {

    select2options = {
                            multiple: true,
                            closeOnSelect: true, 
                            dropdownAutoWidth: true,
                            placeholder: "Select...",
                            tags: true,
                            allowClear: true 
                        }    
    
    createFilterInterface(tableManagerReference) {
      if (!this.column.allow_filter) {
        return null;
      }

      //-----------------//
      //  HTML Elements  //
      //-----------------//

      let filterInput = this._createInputForSelectFilter(tableManagerReference);

      //-----------------------------------------------------------------//
      /// Callbacks for post processing and event handler registration ////
      //-----------------------------------------------------------------//

      // Add function to populate select options as a callback to be run once data is loaded into the table,
      // as we need access to the dataset to determine the unique values for the options.
      tableManagerReference.dataLoadedCallBacks.push(async () => {
        const options = await tableManagerReference.dataSource.getOptions(this.column);
        this._populateTextSelectFilterOptionsFromData(options, this.column.json_id, filterInput);
      });

      // Add select2 initialization as a callback to be run when document is ready to ensure the relevant DOM element is available.
      // Use the element reference directly instead of looking up by id, avoiding collisions across tables.
      tableManagerReference.documentReadyCallBacks.push(
        () => $(filterInput).select2(this.select2options)
      );

      // Add event handler registration to callback for when document is ready.
      // Use the element reference directly instead of looking up by id.
      tableManagerReference.documentReadyCallBacks.push(
        () => $(filterInput).on('change', (e) => this.onSelectChangeHandler(e, tableManagerReference))
      );

      return filterInput;
  }

    //------------------//
    //  Event Handlers  //
    //------------------//
    onSelectChangeHandler(e, tableManagerReference) {

      // If we are in the process of clearing filters, we don't want to trigger 
      // a redraw as it will be done after all filters have been cleared.
        if (tableManagerReference.state.clearingFilters) {
            return
        }

        const dataSource = tableManagerReference.dataSource;

        // Work from the selected items only. Each carries its <option> element, which select2 marks with
        // data-select2-tag="true" when the user typed the value in rather than choosing an existing option.
        // Use the event target element directly instead of looking it up by id.
        // Typed-in tags are "contains" matches, chosen options are exact matches.
        const terms = $(e.target).select2('data').map((o) => ({
          // Server-side uses the id/value (the raw column data, needed to match the database). Client-side uses the text,
          // which has been processed for display and matches the internal filter data DataTables keeps (settings.aoData[rowIndex]._aFilterData[columnIndex]).
          value: dataSource.isServerSide ? o.id : o.text,
          contains: o.element?.dataset?.select2Tag === 'true'
        }));

        // Redraws only if the filter has changed
        dataSource.applyColumnFilter(this.column, { type: "text", terms: terms });
    }

    _createInputForSelectFilter(tableManagerReference) {
        const column = this.column;
        let filterInput = document.createElement("select");
        filterInput.setAttribute("data-field", column.json_id);
        // Prefix id with table id to avoid collisions when multiple tables have the same column on one page
        filterInput.setAttribute("id", tableManagerReference.tableId + "_" + column.json_id + "_search");
        filterInput.setAttribute("name", tableManagerReference.tableId + "_" + column.json_id + "_select");

        filterInput.classList.add("col-filter");
        filterInput.classList.add("select-filter");

        if (column.cssClassFilterInput) {
          filterInput.classList.add(column.cssClassFilterInput);
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
}

export { TextSelect };