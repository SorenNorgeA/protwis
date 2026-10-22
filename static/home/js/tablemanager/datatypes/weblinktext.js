// This class represents the "weblink" data type used in the table manager.
// It extends the "text" data type, providing additional functionality for rendering and filtering web links in the table after deserialization. 
// The data for this data type is expected to be an object containing two properties: "anchor_href" and "anchor_content".


import { Text } from "./text.js";

/**
 * Data type for web link columns. Cell data is an object with anchor_href and anchor_content. Displays the content as
 * a link that opens in a new tab, and filters on the link text. Extends Text.
 * @extends Text
 */
class WebLinkText extends Text {  

    /**
     * Renders a web link. For the display type, null values show the nullValuePlaceholder. A link to anchor_href is shown
     * with anchor_content as its text. Without an href, anchor_content is shown as plain text. Other types return
     * anchor_content, so ordering and filtering use the link text.
     * @param {{anchor_href?: string, anchor_content?: string}|null|undefined} data - The cell data.
     * @param {string} type - The DataTables render type: "display", "filter", "sort" or "type".
     * @param {*} row - The full row data (unused).
     * @param {Object} meta - The DataTables cell metadata (unused).
     * @param {Object} tableManagerReference - The table manager that owns the table.
     * @returns {string|null|undefined} The rendered value for the render type.
     */
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
 
    /**
     * Removes HTML tags from the link text, so it can be compared with the filter text. Overrides the Text version, which
     * expects a plain value, by reading anchor_content from the link object. Used by Text.blurHandler.
     * @param {{anchor_content?: string}|null|undefined} v - The link cell data.
     * @returns {string} The link text without tags, or an empty string if there is none.
     */
    _stripHtmlTags(v) {
        return v?.anchor_content == null ? "" : String(v.anchor_content).replace(/<[^>]*>/g, '');
    }    
}

export { WebLinkText };