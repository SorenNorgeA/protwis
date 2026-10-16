// This class represents the "weblink" data type used in the table manager.
// It extends the "text" data type, providing additional functionality for rendering and filtering web links in the table after deserialization. 
// The data for this data type is expected to be an object containing two properties: "anchor_href" and "anchor_content".


import { Text } from "./text.js";

class WebLinkText extends Text {  

    dataTableRenderer(data, type, row, meta, tableManagerReference) {
        if (type === "display" && (data === null || data === undefined)) {
            return tableManagerReference.nullValuePlaceholder;
        }
        if (type === "display" && data) {
            if (!data.anchor_href) {
                return data.anchor_content || ""; // fallback to displaying anchor_content as plain text if href is missing
            }
            const link = document.createElement("a");
            link.setAttribute("href", data.anchor_href);
            link.setAttribute("target", "_blank");
            link.textContent = data.anchor_content;
            return link.outerHTML;
        }
        // Search, order and type return
        return data ? data.anchor_content : ""
    }
 
    //override the _stripHtmlTags method to handle the specific structure of the weblink data type used in Text.blurHandler
    _stripHtmlTags(v) {
        return v?.anchor_content == null ? "" : String(v.anchor_content).replace(/<[^>]*>/g, '');
    }    
}

export { WebLinkText };