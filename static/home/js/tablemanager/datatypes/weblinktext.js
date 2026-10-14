// This class represents the "weblink" data type used in the table manager.
// It extends the "text" data type, providing additional functionality for rendering and filtering web links in the table after deserialization. 
// The data for this data type is expected to be an object containing two properties: "anchor_href" and "anchor_content".


import { Text } from "./text.js";

class WebLinkText extends Text {  

    dataTableRenderer(data, type, row, meta) {
        if (type === "display" && data) {
            if (!data.anchor_href) {
                return data.anchor_content || ""; // fallback to displaying anchor_content as plain text if href is missing
            }
            return `<a href="${data.anchor_href}" target="_blank">${data.anchor_content}</a>`;
        }
        // Search, order and type return
        return data ? data.anchor_content : ""
    }
 
    //override the _stripHtmlTags method to handle the specific structure of the weblink data type used in Text.blurHandler
    _stripHtmlTags(str) {
        return str.anchor_content.replace(/<[^>]*>/g, '');
    }    
}

export { WebLinkText };