class ExcelDefault {
    
    button_config
    tableManagerReference

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
    
    attach(index) {
        this.tableManagerReference.dataTableReference.button().add(index, this.button_config);
    }
}

export { ExcelDefault };