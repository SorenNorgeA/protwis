class ExcelDefault {
    
    button_config
    tableManagerReference

    /**
     * Creates the Excel export button configuration. The export keeps the super-header and column title rows and drops
     * the rows below the title row.
     * @param {Object} tableManagerReference - The TableManager instance that owns the table.
     */
    constructor(tableManagerReference) {
        this.tableManagerReference = tableManagerReference;
        this.button_config = {
            extend: 'excel',
            text: 'Export (Excel)',
            exportOptions: {
                // Buttons exports every header row DataTables knows about. Keep the super-headers and column titles,
                // and drop the category count and filter rows below the title row.
                customizeData: (data) => {
                    data.headerStructure = data.headerStructure.slice(0, this.tableManagerReference.titleRowIndex + 1);
                }
            }
        }
    }
    
    /**
     * Adds the Excel export button to the DataTable button bar.
     * @param {number} index - Position at which the button is inserted into the button bar.
     */
    attach(index) {
        this.tableManagerReference.dataTableReference.button().add(index, this.button_config);
    }
}

export { ExcelDefault };