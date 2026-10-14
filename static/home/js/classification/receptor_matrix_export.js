// SVG/PNG export for the receptor-level identity/similarity matrices (Matrix tab on receptor
// family pages -- family_matrix.js -- and on modality/chemotype pages -- the inline renderer in
// ClassificationTreeVisualizationDetail.html). Both render an HTML <table>, so instead of
// re-deriving labels and colours from the payload this walks the rendered table: whatever is on
// screen (current receptor-name type, heat colours, "-" for missing pairs) is what gets exported.
// Saving goes through GPCRomeSvgExport (static/home/js/gpcrome_svg_export.js).
var ReceptorMatrixExport = (function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var FONT_FAMILY = "Arial, Helvetica, sans-serif";
  var FONT_SIZE = 12;
  var CELL_HEIGHT = 24;
  var MIN_CELL_WIDTH = 44;
  var PAD = 16;
  var LABEL_GAP = 8;
  var NOTE = "Upper triangle: sequence identity (%); lower triangle: sequence similarity (%)";

  var measureCtx = null;
  function textWidth(text, bold) {
    if (!measureCtx) measureCtx = document.createElement("canvas").getContext("2d");
    measureCtx.font = (bold ? "bold " : "") + FONT_SIZE + "px " + FONT_FAMILY;
    return measureCtx.measureText(String(text || "")).width;
  }

  function cleanText(el) {
    return (el ? el.textContent || "" : "").replace(/\s+/g, " ").trim();
  }

  function isTransparent(color) {
    return !color || color === "transparent" || /rgba\(\s*0,\s*0,\s*0,\s*0\s*\)/.test(color);
  }

  function svgEl(name, attrs, text) {
    var el = document.createElementNS(SVG_NS, name);
    Object.keys(attrs || {}).forEach(function (key) { el.setAttribute(key, attrs[key]); });
    if (text !== undefined) el.textContent = text;
    return el;
  }

  // Reads the rendered table into {cols: [label], rows: [{label, cells: [{text, fill, color}]}]}.
  function readTable(tableEl) {
    var headerCells = tableEl.querySelectorAll("thead tr:first-child th");
    var cols = [];
    for (var c = 1; c < headerCells.length; c++) cols.push(cleanText(headerCells[c]));

    var rows = [];
    tableEl.querySelectorAll("tbody tr").forEach(function (tr) {
      var rowHeader = tr.querySelector("th");
      var cells = [];
      tr.querySelectorAll("td").forEach(function (td) {
        var style = window.getComputedStyle(td);
        cells.push({
          text: cleanText(td),
          fill: isTransparent(style.backgroundColor) ? "#ffffff" : style.backgroundColor,
          color: style.color || "#333333",
        });
      });
      rows.push({ label: cleanText(rowHeader), cells: cells });
    });
    return { cols: cols, rows: rows };
  }

  function buildSvg(tableEl) {
    var data = readTable(tableEl);
    if (!data.cols.length || !data.rows.length) return null;

    var cellWidth = MIN_CELL_WIDTH;
    data.rows.forEach(function (row) {
      row.cells.forEach(function (cell) {
        cellWidth = Math.max(cellWidth, Math.ceil(textWidth(cell.text) + 12));
      });
    });
    var rowLabelWidth = Math.ceil(Math.max.apply(null, data.rows.map(function (r) { return textWidth(r.label, true); })));
    var colLabelHeight = Math.ceil(Math.max.apply(null, data.cols.map(function (l) { return textWidth(l, true); })));

    var gridX = PAD + rowLabelWidth + LABEL_GAP;
    var gridY = PAD + colLabelHeight + LABEL_GAP;
    var gridWidth = cellWidth * data.cols.length;
    var gridHeight = CELL_HEIGHT * data.rows.length;
    var width = Math.ceil(Math.max(gridX + gridWidth, PAD + textWidth(NOTE)) + PAD);
    var height = gridY + gridHeight + LABEL_GAP + FONT_SIZE + PAD;

    var svg = svgEl("svg", {
      width: width,
      height: height,
      viewBox: "0 0 " + width + " " + height,
      "font-family": FONT_FAMILY,
      "font-size": FONT_SIZE,
    });
    svg.appendChild(svgEl("rect", { x: 0, y: 0, width: width, height: height, fill: "#ffffff" }));

    // Column labels, rotated to read bottom-up and ending just above their column.
    data.cols.forEach(function (label, j) {
      var x = gridX + j * cellWidth + cellWidth / 2 + FONT_SIZE / 3;
      var y = gridY - LABEL_GAP / 2;
      svg.appendChild(svgEl("text", {
        x: x, y: y, "font-weight": "bold", fill: "#333333",
        transform: "rotate(-90 " + x + " " + y + ")",
      }, label));
    });

    data.rows.forEach(function (row, i) {
      var y = gridY + i * CELL_HEIGHT;
      svg.appendChild(svgEl("text", {
        x: gridX - LABEL_GAP, y: y + CELL_HEIGHT / 2, "text-anchor": "end",
        "dominant-baseline": "central", "font-weight": "bold", fill: "#333333",
      }, row.label));

      row.cells.forEach(function (cell, j) {
        var x = gridX + j * cellWidth;
        svg.appendChild(svgEl("rect", {
          x: x, y: y, width: cellWidth, height: CELL_HEIGHT,
          fill: cell.fill, stroke: "#d0d7de", "stroke-width": 1,
        }));
        if (cell.text) {
          svg.appendChild(svgEl("text", {
            x: x + cellWidth / 2, y: y + CELL_HEIGHT / 2, "text-anchor": "middle",
            "dominant-baseline": "central", fill: cell.color,
          }, cell.text));
        }
      });
    });

    svg.appendChild(svgEl("text", {
      x: PAD, y: gridY + gridHeight + LABEL_GAP + FONT_SIZE, fill: "#666666",
    }, NOTE));
    return svg;
  }

  function download(tableEl, format, fileBase) {
    var svg = tableEl ? buildSvg(tableEl) : null;
    if (!svg) return;
    var base = fileBase || "receptor-matrix";
    if (format === "png") GPCRomeSvgExport.downloadSvgAsPng(svg, base + ".png");
    else GPCRomeSvgExport.downloadSvg(svg, base + ".svg");
  }

  // Download buttons: <button class="rm-download-btn" data-format="svg|png"
  //   data-table="#tableId" data-file-base="name">.
  document.addEventListener("click", function (event) {
    var btn = event.target.closest ? event.target.closest(".rm-download-btn") : null;
    if (!btn) return;
    download(document.querySelector(btn.getAttribute("data-table")), btn.getAttribute("data-format"),
      btn.getAttribute("data-file-base"));
  });

  return { download: download };
})();
