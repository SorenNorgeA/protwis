// Classification List tab — a name-only, multi-column, foldable receptor list rendered as SVG.
// Used by every /classification/visualizations/* detail page; each page carries its own
// #cv-list-tab pane markup (toolbar + #classification_list_plot mount). Reads
// window.CLASSIFICATION_LIST_DATA (built by ClassificationVisualizationMixin.build_list_context)
// and window.ClassificationCore.
//
// Hierarchy: Class -> [Chemotype | Modality] -> Receptor family -> Receptor. Class is always
// the top level; the optional middle layer is only offered when the payload sets allowGrouping
// (the superfamily page). Label formatting follows the DataMapper List (datamapper.js
// RenderListPlot_Labels -> add_text), but this renderer is self-contained: d3v4 only, no
// globals, no checkbox-DOM reads.
(function (window, $) {
  "use strict";

  const { normKey, CLASS_COLORS, getChemotypeColor } = window.ClassificationCore;

  const DATA = window.CLASSIFICATION_LIST_DATA || {};
  const ROWS = DATA.rows || [];
  const CLASS_ORDER = DATA.classOrder || [];
  const ALLOW_GROUPING = !!DATA.allowGrouping;

  const MOUNT_ID = "classification_list_plot";
  const SVG_ID = "classification_list_plot_svg";
  const TAB_SELECTOR = 'a[href="#cv-list-tab"]';

  const MODALITY_COLORS = {
    "Small molecule receptors": "#1f78b4",
    "Polypeptide receptors": "#2CA02C",
    "Orphan receptors": "#a0b8ba",
  };

  // Per-level text styling (DataMapper List defaults: 20/18/16/14px).
  const LEVEL_STYLE = {
    class:    { size: 20, weight: "bold",   style: "normal", height: 32, gapBefore: 10 },
    group:    { size: 18, weight: "bold",   style: "normal", height: 28, gapBefore: 4 },
    family:   { size: 16, weight: "normal", style: "italic", height: 25, gapBefore: 0 },
    receptor: { size: 14, weight: "normal", style: "normal", height: 21, gapBefore: 0 },
  };
  const INDENT = 18;          // px per depth level
  const GLYPH_X = -16;        // fold glyph sits left of the header text
  const BULLET_X = -9;        // optional receptor bullet, same slot as the fold glyph
  const BULLET_R = 3;
  const COLUMN_GAP = 36;
  const MARGIN = { top: 20, right: 20, bottom: 20, left: 24 };
  const AUTO_ROWS_PER_COLUMN = 40;
  const AUTO_MAX_COLUMNS = 4;
  const FONT_FAMILY = "Arial, Helvetica, sans-serif";

  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

  const state = {
    labelType: "Protein",
    grouping: "None",
    columns: "Auto",
    colorHeaders: true,
    bullets: false,
    collapsed: new Set(),
  };
  let rendered = false;

  // ----------------------------
  // Tree building
  // ----------------------------
  function makeNode(id, kind, label, extra) {
    return Object.assign({ id, kind, label, children: [], childIndex: {}, count: 0 }, extra || {});
  }

  function childOf(parent, id, kind, label, extra) {
    let node = parent.childIndex[id];
    if (!node) {
      node = makeNode(id, kind, label, extra);
      parent.childIndex[id] = node;
      parent.children.push(node);
    }
    return node;
  }

  function buildTree(rows, grouping) {
    const root = makeNode("root", "root", "");
    const useGroup = ALLOW_GROUPING && (grouping === "Chemotype" || grouping === "Modality");
    rows.forEach(row => {
      const classId = "class:" + row.class_key;
      let parent = childOf(root, classId, "class", row.class_title, { classKey: row.class_key });
      if (useGroup) {
        const groupLabel = grouping === "Chemotype" ? row.chemotype : row.modality;
        const groupId = classId + "/" + grouping.toLowerCase() + ":" + groupLabel;
        parent = childOf(parent, groupId, "group", groupLabel, { groupType: grouping });
      }
      const family = childOf(parent, parent.id + "/fam:" + row.family, "family", row.family);
      family.children.push(makeNode(family.id + "/rec:" + row.uniprot, "receptor", "", { row }));
    });

    const classRank = {};
    CLASS_ORDER.forEach((k, i) => { classRank[k] = i; });

    (function sortAndCount(node) {
      if (node.kind === "receptor") return 1;
      if (node.kind === "root") {
        node.children.sort((a, b) => (classRank[a.classKey] ?? 999) - (classRank[b.classKey] ?? 999));
      } else if (node.kind === "family") {
        node.children.sort((a, b) => collator.compare(leafLabel(a.row), leafLabel(b.row)));
      } else {
        node.children.sort((a, b) => collator.compare(plainText(a.label), plainText(b.label)));
      }
      node.count = node.children.reduce((sum, child) => sum + sortAndCount(child), 0);
      return node.count;
    })(root);
    return root;
  }

  // Visible rows in document order; a collapsed header hides all of its descendants.
  function flatten(root) {
    const out = [];
    (function walk(node, depth) {
      node.children.forEach(child => {
        out.push({ node: child, depth });
        if (child.kind !== "receptor" && !state.collapsed.has(child.id)) walk(child, depth + 1);
      });
    })(root, 0);
    return out;
  }

  function allHeaderIds(root, kinds) {
    const ids = [];
    (function walk(node) {
      node.children.forEach(child => {
        if (child.kind === "receptor") return;
        if (!kinds || kinds.indexOf(child.kind) !== -1) ids.push(child.id);
        walk(child);
      });
    })(root);
    return ids;
  }

  // ----------------------------
  // Label formatting
  // ----------------------------
  const decoder = document.createElement("textarea");
  function decodeEntities(s) {
    decoder.innerHTML = s;
    return decoder.value;
  }

  function plainText(html) {
    return decodeEntities(String(html || "").replace(/<[^>]*>/g, "")).trim();
  }

  // Split an HTML-ish label into [{text, sub}] runs: <sub> becomes a subscript run, every other
  // tag is dropped, entities (&beta; ...) are decoded.
  function richRuns(html) {
    const runs = [];
    let inSub = false;
    String(html || "").split(/(<sub>|<\/sub>)/i).forEach(part => {
      if (/^<sub>$/i.test(part)) { inSub = true; return; }
      if (/^<\/sub>$/i.test(part)) { inSub = false; return; }
      const text = decodeEntities(part.replace(/<[^>]*>/g, ""));
      if (text) runs.push({ text, sub: inSub });
    });
    return runs;
  }

  function leafLabel(row) {
    if (state.labelType === "Gene") return row.gene || row.uniprot;
    if (state.labelType === "UniProt") return String(row.uniprot || "").toUpperCase();
    return plainText(shortProteinName(row.protein));
  }

  // Same shortening as the DataMapper List: drop "-adrenoceptor" / " receptor".
  function shortProteinName(html) {
    return String(html || "").replace(/(-adrenoceptor| receptor)/g, "");
  }

  function headerColor(node) {
    if (!state.colorHeaders) return "#000000";
    if (node.kind === "class") return CLASS_COLORS[node.classKey] || "#333333";
    if (node.kind === "group") {
      return node.groupType === "Modality"
        ? (MODALITY_COLORS[normKey(node.label)] || "#555555")
        : getChemotypeColor(node.label);
    }
    if (node.kind === "family") return "#333333";
    return "#000000";
  }

  // Append label text into a d3v4 <text> selection, honouring subscripts.
  function writeRuns(text, runs, fontSize) {
    let shifted = false;
    runs.forEach(run => {
      const tspan = text.append("tspan").text(run.text);
      if (run.sub && !shifted) {
        tspan.attr("dy", "0.3em").attr("font-size", Math.round(fontSize * 0.75) + "px");
        shifted = true;
      } else if (run.sub) {
        tspan.attr("font-size", Math.round(fontSize * 0.75) + "px");
      } else if (shifted) {
        tspan.attr("dy", "-0.3em");
        shifted = false;
      }
    });
  }

  // ----------------------------
  // Layout
  // ----------------------------
  function resolveColumnCount(items) {
    if (state.columns !== "Auto") return Math.max(1, parseInt(state.columns, 10) || 1);
    return Math.max(1, Math.min(AUTO_MAX_COLUMNS, Math.ceil(items.length / AUTO_ROWS_PER_COLUMN)));
  }

  function rowHeight(item, isFirstInColumn) {
    const st = LEVEL_STYLE[item.node.kind];
    return st.height + (isFirstInColumn ? 0 : st.gapBefore);
  }

  function isOpenHeader(item) {
    return item.node.kind !== "receptor" && !state.collapsed.has(item.node.id);
  }

  // Fill columns evenly by height; never leave an expanded header stranded at a column bottom.
  function splitColumns(items, nCols) {
    const total = items.reduce((sum, it, i) => sum + rowHeight(it, i === 0), 0);
    const target = total / nCols;
    const columns = [[]];
    let height = 0;
    items.forEach(item => {
      let col = columns[columns.length - 1];
      const h = rowHeight(item, col.length === 0);
      if (col.length && height + h > target && columns.length < nCols) {
        const carried = [];
        while (col.length && isOpenHeader(col[col.length - 1])) carried.unshift(col.pop());
        if (!col.length) { col.push.apply(col, carried); carried.length = 0; }
        col = carried;
        columns.push(col);
        height = col.reduce((sum, it, i) => sum + rowHeight(it, i === 0), 0);
      }
      height += rowHeight(item, col.length === 0);
      col.push(item);
    });
    return columns.filter(c => c.length);
  }

  // ----------------------------
  // Render
  // ----------------------------
  // Re-render without the page jumping: hold the mount's height while the SVG is swapped (so the
  // document never collapses and the browser never clamps the scroll), then restore scroll.
  function render() {
    const mount = document.getElementById(MOUNT_ID);
    if (!mount) return;
    rendered = true;

    const scrollX = window.pageXOffset;
    const scrollY = window.pageYOffset;
    const scrollLeft = mount.scrollLeft;
    mount.style.minHeight = mount.offsetHeight + "px";
    try {
      draw(mount);
    } finally {
      mount.style.minHeight = "";
      mount.scrollLeft = scrollLeft;
      window.scrollTo(scrollX, scrollY);
    }
  }

  function draw(mount) {
    const root = buildTree(ROWS, state.grouping);
    const items = flatten(root);
    d3v4.select(mount).selectAll("*").remove();

    if (!items.length) {
      d3v4.select(mount).append("p").attr("class", "text-muted").text("No receptors to list.");
      return;
    }

    const svg = d3v4.select(mount).append("svg")
      .attr("id", SVG_ID)
      .style("font-family", FONT_FAMILY);
    const bg = svg.append("rect").attr("fill", "#ffffff");
    const plot = svg.append("g").attr("transform", `translate(${MARGIN.left},${MARGIN.top})`);

    // Pass 1: draw every row at x=0 so it can be measured.
    items.forEach(item => {
      const node = item.node;
      const st = LEVEL_STYLE[node.kind];
      const g = plot.append("g").attr("class", "cl-row cl-row-" + node.kind);
      item.g = g;

      const text = g.append("text")
        .attr("x", item.depth * INDENT)
        .attr("y", 0)
        .attr("dominant-baseline", "middle")
        .attr("font-family", FONT_FAMILY)
        .attr("font-size", st.size + "px")
        .attr("font-weight", st.weight)
        .attr("font-style", st.style)
        .attr("fill", node.kind === "receptor" ? "#000000" : headerColor(node));

      if (node.kind === "receptor") {
        const row = node.row;
        if (state.bullets) {
          g.append("circle")
            .attr("class", "cl-bullet")
            .attr("cx", item.depth * INDENT + BULLET_X)
            .attr("cy", 0)
            .attr("r", BULLET_R)
            .attr("fill", state.colorHeaders ? (CLASS_COLORS[row.class_key] || "#333333") : "#333333");
        }
        if (state.labelType === "Protein") writeRuns(text, richRuns(shortProteinName(row.protein)), st.size);
        else text.text(leafLabel(row));
        g.append("title").text(plainText(row.protein) + " (" + row.uniprot + (row.gene ? ", " + row.gene : "") + ")");
      } else {
        writeRuns(text, richRuns(node.label), st.size);
        text.append("tspan")
          .attr("font-size", Math.round(st.size * 0.75) + "px")
          .attr("font-weight", "normal")
          .attr("font-style", "normal")
          .attr("fill", "#8a8a8a")
          .text(" (" + node.count + ")");

        const isCollapsed = state.collapsed.has(node.id);
        g.append("text")
          .attr("class", "cl-fold-glyph")
          .attr("x", item.depth * INDENT + GLYPH_X)
          .attr("y", 0)
          .attr("dominant-baseline", "middle")
          .attr("font-family", FONT_FAMILY)
          .attr("font-size", Math.round(st.size * 1.05) + "px")
          .attr("fill", "#555555")
          .text(isCollapsed ? "▸" : "▾");

        g.style("cursor", "pointer").on("click", function () {
          if (state.collapsed.has(node.id)) state.collapsed.delete(node.id);
          else state.collapsed.add(node.id);
          render();
        });
      }
      item.width = item.depth * INDENT + text.node().getComputedTextLength();
    });

    // Pass 2: split into columns and position.
    const columns = splitColumns(items, resolveColumnCount(items));
    let x = 0;
    let maxHeight = 0;
    columns.forEach(col => {
      let y = 0;
      let colWidth = 0;
      col.forEach((item, i) => {
        const st = LEVEL_STYLE[item.node.kind];
        if (i > 0) y += st.gapBefore;
        item.g.attr("transform", `translate(${x},${y + st.height / 2})`);
        y += st.height;
        colWidth = Math.max(colWidth, item.width);
      });
      x += colWidth + COLUMN_GAP;
      maxHeight = Math.max(maxHeight, y);
    });

    const width = Math.ceil(x - COLUMN_GAP + MARGIN.left + MARGIN.right);
    const height = Math.ceil(maxHeight + MARGIN.top + MARGIN.bottom);
    svg.attr("width", width).attr("height", height).attr("viewBox", `0 0 ${width} ${height}`);
    bg.attr("width", width).attr("height", height);

    syncControls();
  }

  // ----------------------------
  // Controls
  // ----------------------------
  // Dropdown items follow the tree toolbar convention: active = btn-primary, others = btn-outline-primary.
  function syncDropdown(itemSelector, toggleId, labelPrefix, current) {
    let activeText = String(current);
    $(itemSelector).each(function () {
      const isActive = String($(this).data("value")) === String(current);
      $(this).toggleClass("btn-primary active", isActive).toggleClass("btn-outline-primary", !isActive);
      if (isActive) activeText = $(this).text().trim();
    });
    $("#" + toggleId).html(labelPrefix + ": " + activeText + ' <span class="caret"></span>');
  }

  function syncControls() {
    syncDropdown(".cl-label-btn", "clLabelDropdownBtn", "Receptor names", state.labelType);
    syncDropdown(".cl-group-btn", "clGroupDropdownBtn", "Group by", state.grouping);
    syncDropdown(".cl-columns-btn", "clColumnsDropdownBtn", "Columns", state.columns);
    // On/off toggle, same idea as the tree's "Leaf color" button: green = on, red = off.
    $("#clColorToggle")
      .toggleClass("btn-success", state.colorHeaders)
      .toggleClass("btn-danger", !state.colorHeaders)
      .attr("aria-pressed", state.colorHeaders ? "true" : "false");
    $("#clBulletToggle")
      .toggleClass("btn-success", state.bullets)
      .toggleClass("btn-danger", !state.bullets)
      .attr("aria-pressed", state.bullets ? "true" : "false");
  }

  function setFold(mode) {
    const root = buildTree(ROWS, state.grouping);
    state.collapsed.clear();
    if (mode === "families") allHeaderIds(root, ["family"]).forEach(id => state.collapsed.add(id));
    // "all" folds every level, so unfolding a class reveals its (still folded) sub-layers one at a time.
    if (mode === "all") allHeaderIds(root).forEach(id => state.collapsed.add(id));
    render();
  }

  // Download a copy without the interactive fold glyphs.
  function exportClone() {
    const svgEl = document.getElementById(SVG_ID);
    if (!svgEl) return null;
    const clone = svgEl.cloneNode(true);
    Array.prototype.forEach.call(clone.querySelectorAll(".cl-fold-glyph"), el => el.remove());
    return clone;
  }

  function download(format) {
    const clone = exportClone();
    if (!clone) return;
    const base = $("#cv-list-tab").data("file-base") || "GPCR_classification_list";
    if (format === "png") GPCRomeSvgExport.downloadSvgAsPng(clone, base + ".png");
    else GPCRomeSvgExport.downloadSvg(clone, base + ".svg");
  }

  function bindControls() {
    // Bound on the .dropdown-menu elements themselves (not an ancestor), and the menus stop
    // propagation so they stay open while clicking options -- same as the Mapper pages.
    const $menus = $("#cv-list-tab .dropdown-menu");
    $menus.on("click", function (e) { e.stopPropagation(); });
    $menus.on("click", ".cl-label-btn", function () {
      state.labelType = $(this).data("value");
      render();
    });
    $menus.on("click", ".cl-group-btn", function () {
      state.grouping = $(this).data("value");
      setFold("families");
    });
    $menus.on("click", ".cl-columns-btn", function () {
      state.columns = String($(this).data("value"));
      render();
    });
    $menus.on("click", ".cl-fold-btn", function () { setFold($(this).data("fold")); });
    $menus.on("click", ".cl-download-btn", function () { download($(this).data("format")); });
    $("#clColorToggle").on("click", function () {
      state.colorHeaders = !state.colorHeaders;
      render();
    });
    $("#clBulletToggle").on("click", function () {
      state.bullets = !state.bullets;
      render();
    });
  }

  // First draw starts with families collapsed (classes and groups open).
  function firstRender() {
    if (!rendered) setFold("families");
  }

  $(function () {
    bindControls();
    syncControls();
    // Text can only be measured once the pane is visible, so draw on first show.
    $(document).on("shown.bs.tab", TAB_SELECTOR, firstRender);
    if ($("#cv-list-tab").hasClass("active")) firstRender();
  });

  window.ClassificationList = { render, download };
})(window, jQuery);
