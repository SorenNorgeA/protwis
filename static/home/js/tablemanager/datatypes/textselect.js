// This class represents the "textselect" data type used in the table manager.
// It provides a select input filtering interface, allowing users to filter table data based on options selected from a dropdown list.
// The select2 library is used to enhance the select input with features such as multi-select, search, and tagging.
// The options for the select input come from the table manager's data source (the loaded rows client-side, the options endpoint server-side).

// It extends Text, inheriting its display rendering (null placeholder, truncation).

import { Text } from "./text.js";

/**
 * Data type for text columns filtered with a select2 multi-select. The options are the column's values. Typed-in
 * values are accepted as tags and matched with a contains search. Extends Text for display rendering.
 * @extends Text
 */
class TextSelect extends Text {

    /** select2 configuration, passed to select2 when the filter is initialised. */
    select2options = {
                            multiple: true,
                            closeOnSelect: true, 
                            dropdownAutoWidth: true,
                            placeholder: "Select...",
                            tags: true,
                            allowClear: true 
                        }    
    
    /**
     * Creates the select element for the filter. It is turned into a select2 control later, in afterTableCreated.
     * @param {Object} tableManagerReference - The table manager that owns the table.
     * @returns {HTMLSelectElement|null} The select element, or null if the column does not allow filtering.
     */
    createFilterInterface(tableManagerReference) {
      const column = this.column;
      if (!column.allow_filter) {
        return false;
      }

      //-----------------//
      //  HTML Elements  //
      //-----------------//

      // Kept for afterTableCreated and afterDataLoaded, which use the element reference directly
      // instead of looking it up by id, avoiding collisions across tables.
      this.filterInput = this._createInputForSelectFilter(tableManagerReference);

      return this.filterInput;
  }

    // select2 is initialised once the header, and so the select, is in the DOM
    /**
     * Initialises select2 on the filter and listens for changes. Runs once the header is in the DOM.
     * @param {Object} tableManagerReference - The table manager that owns the table.
     */
    afterTableCreated(tableManagerReference) {
      if (!this.filterInput) {
        return;
      }

      $(this.filterInput).select2(this.select2options);
      $(this.filterInput).on('change', (e) => this.onSelectChangeHandler(e, tableManagerReference));
    }

    // The options are the column's distinct values, which need the loaded data client-side
    /**
     * Fills the filter with the column's distinct values once the table's data has loaded.
     * @async
     * @param {Object} tableManagerReference - The table manager that owns the table.
     * @returns {Promise<void>}
     */
    async afterDataLoaded(tableManagerReference) {
      if (!this.filterInput) {
        return;
      }

      const options = await tableManagerReference.dataSource.getOptions(this.column);
      this._populateTextSelectFilterOptionsFromData(options, this.column.json_id, this.filterInput);
    }

    //------------------//
    //  Event Handlers  //
    //------------------//
    /**
     * Applies the selected values as a text filter. Typed-in tags use a contains match, and chosen options match exactly.
     * Does nothing while filters are being cleared.
     * @param {Event} e - The select2 change event. Its target is the select element.
     * @param {Object} tableManagerReference - The table manager that owns the table.
     */
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

    /**
     * Creates the select element for the filter, with its id, name and classes.
     * @param {Object} tableManagerReference - The table manager that owns the table.
     * @returns {HTMLSelectElement} The select element.
     */
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

    /**
     * Converts a value to plain text for an option label. Removes the sub tags and decodes Greek letter entities.
     * @param {*} value - The value to convert.
     * @returns {string} The display text, or an empty string if the value is null or undefined.
     */
    _processHTMLForDisplay(value) {
          if (value === null || value === undefined) return '';
          let text = String(value);
          text = text.replace(/<sub>.*?<\/sub>/g, (match) => {
            return match.replace(/<\/?sub>/g, '');
          });
          text = this._decodeHTMLEntities(text);
          return text;
        }

    /**
     * Replaces Greek letter HTML entities (for example the alpha entity) with the matching Unicode characters.
     * @param {*} text - The text to decode.
     * @returns {string} The decoded text, or an empty string if the text is null or undefined.
     */
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

    /**
     * Adds an option to the select for each value. Option labels are processed for display, and option values are the raw values.
     * @param {Array<*>|null|undefined} data - The distinct column values. Logs a warning and adds nothing if empty.
     * @param {string} ColumnJsonId - The column's json_id, used in the warning message.
     * @param {HTMLSelectElement} filterInput - The select element to add the options to.
     */
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