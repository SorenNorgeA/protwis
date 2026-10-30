// Stacked class dots for ONE plot only: Levels & terms -> "Modality and Chemotype" -> Tree
// (/classification/levels_and_terms, plot LevelsTerms:Chemotypes, built by
// Classification_tree.build_levels_terms_tree_context). Not a general "outer dots" feature of
// the classification tree -- tree.js only knows it as a named leaf decorator
// (meta.leafDecorator = "chemotypeClassDots"), and only Classification.html loads this file.
//
// Each chemotype leaf gets one dot per class it's found in (leaf.classKeys, in class-table
// order), packed outward right after its own label. The legend (meta.chemotypeClassDots) uses
// tree.js's generic renderLeafDotLegend.
(function (window) {
  "use strict";

  const { CLASS_COLORS } = window.ClassificationCore;

  const DOT_R = 5;
  const DOT_STEP = 2 * DOT_R + 2;   // center-to-center distance between a leaf's dots
  const LABEL_GAP = 4;              // gap between the end of the label and the first dot

  // How far out (radially, in the leaf node's rotated frame) the leaf's label ends. The label's
  // own transform is rotate(180)... on the left half of the circle, so map its bbox through it.
  function labelOuterEdge(textNode) {
    let edge = 0;
    if (!textNode) return edge;
    let bb;
    let m;
    try {
      bb = textNode.getBBox();
      const consolidated = textNode.transform && textNode.transform.baseVal
        ? textNode.transform.baseVal.consolidate()
        : null;
      m = consolidated ? consolidated.matrix : null;
    } catch (err) {
      return edge;
    }
    if (!bb || !isFinite(bb.x) || !isFinite(bb.width)) return edge;
    [bb.x, bb.x + bb.width].forEach(function (x0) {
      [bb.y, bb.y + bb.height].forEach(function (y0) {
        const x = m ? (m.a * x0 + m.c * y0 + m.e) : x0;
        if (isFinite(x)) edge = Math.max(edge, x);
      });
    });
    return edge;
  }

  function drawLeafDots(leafNodes) {
    leafNodes.each(function (d) {
      const keys = d.classKeys || [];
      if (!keys.length) return;
      const g = d3.select(this);
      const firstX = labelOuterEdge(g.select("text").node()) + LABEL_GAP + DOT_R;
      keys.forEach(function (key, i) {
        g.append("circle")
          .attr("class", "chemotype-class-dot")
          .attr("cx", firstX + i * DOT_STEP)
          .attr("r", DOT_R)
          .style("fill", CLASS_COLORS[key] || "#ccc")
          .style("stroke", "black")
          .style("stroke-width", 0.8);
      });
    });
  }

  function drawLegend(svgId, options, meta) {
    const cfg = meta.chemotypeClassDots || {};
    // Same text size as the leaf labels and same dot size as the leaf dots.
    renderLeafDotLegend(svgId, classLegendEntries(cfg.legendClassKeys), options,
      { fontSize: leafLabelFontSize(svgId), dotR: DOT_R });
  }

  window.ClassificationTreeDecorators = window.ClassificationTreeDecorators || {};
  window.ClassificationTreeDecorators.chemotypeClassDots = {
    leaves: drawLeafDots,
    afterRender: drawLegend,
  };
})(window);
