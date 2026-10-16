class ExcelDefault {
    
    button_config
    tableManagerReference

    constructor(tableManagerReference) {
        this.tableManagerReference = tableManagerReference;
        this.button_config = {
            extend: 'excel',
            text: 'Export (Excel)'
        }
    }
    
    attach(index) {
        this.tableManagerReference.dataTableReference.button().add(index, this.button_config);
    }
}

export { ExcelDefault };