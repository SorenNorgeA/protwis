class InjectorTemplates {

    static addInfoIconLink(cellElementArray, href, tooltipText="") {
        let anchor = document.createElement('a');
        anchor.href = href;
        anchor.target = "_blank";

        let span = document.createElement('span');
        span.className = "glyphicon glyphicon-info-sign";
        
        anchor.appendChild(span);
        anchor.title = tooltipText;

        cellElementArray.push(anchor);
        
        return cellElementArray;
    }

}

export { InjectorTemplates }