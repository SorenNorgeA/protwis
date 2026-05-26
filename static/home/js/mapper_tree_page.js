/**
 * Mapper 2.0 classification tree — client-side keep_by_names filter, circles payload,
 * debounced redraw (mapper_classification_tree + DrawCircles).
 */
(function ($) {
  'use strict';

  var DEBOUNCE_MS = 140;
  var redrawTimer;
  var suppressRedraw = false;

  var ORIG_SKEL = null;
  var ORIG_OPTS = null;

  var ServerReceptorDict = {};
  var ServerGeneDict = {};

  window.mapperTreeInputMode = 'numeric';
  window.mapperTreeRedrawNow = function () {};

  window.MAPPER20_LABEL_COLORS = window.MAPPER20_LABEL_COLORS || {};
  window.MAPPER20_LABEL_ENABLED = window.MAPPER20_LABEL_ENABLED || {};
  /** Stem-upper → label arrays for SVG leaf relabelling (`custom_changeLeavesLabels`). */
  window.mapperTreeStemLabelDicts = { IUPHAR: {}, Gene: {}, UniProt: {} };

  var MAPPER_TREE_MAX_ROWS = 500;
  /** Mapper tree page uses circular layout only (no curved/straight dendrogram UI). */
  var MAPPER_TREE_LAYOUT = 'Tree - Circular';
  var MAPPER_TREE_RADIAL_LEAF_LABEL_GAP_BASE = 10;
  var MAPPER_TREE_DEMO_ROWS = [
    { receptor: '5HT1A', numeric: [1, 73, -10.1, -25, 1], text: 'Serotonergic' },
    { receptor: '5HT1B', numeric: [2, 72, -10, -24, 2], text: 'Serotonergic' },
    { receptor: '5HT1D', numeric: [3, 71, -9.9, -23, 3], text: 'Serotonergic' },
    { receptor: '5HT1E', numeric: [4, 70, -9.8, -22, 4], text: 'Serotonergic' },
    { receptor: '5HT1F', numeric: [5, 69, -9.7, -21, 5], text: 'Serotonergic' },
    { receptor: '5HT2A', numeric: [6, 68, -9.6, -20, 6], text: 'Serotonergic' },
    { receptor: '5HT2B', numeric: [7, 67, -9.5, -19, 7], text: 'Serotonergic' },
    { receptor: '5HT2C', numeric: [8, 66, -9.4, -18, 8], text: 'Serotonergic' },
    { receptor: 'ACKR1', numeric: [13, 61, -8.9, -13, 13], text: 'Chemokine' },
    { receptor: 'ACKR2', numeric: [14, 60, -8.8, -12, 14], text: 'Chemokine' },
    { receptor: 'ACKR3', numeric: [15, 59, -8.7, -11, 15], text: 'Chemokine' },
    { receptor: 'ACKR4', numeric: [16, 58, -8.6, -10, 16], text: 'Chemokine' },
    { receptor: 'ACM1', numeric: [17, 57, -8.5, -9, 17], text: 'Cholinergic' },
    { receptor: 'ACM2', numeric: [18, 56, -8.4, -8, 18], text: 'Cholinergic' },
    { receptor: 'ACM3', numeric: [19, 55, -8.3, -7, 19], text: 'Cholinergic' },
    { receptor: 'ADA1A', numeric: [23, 51, -7.9, -3, 23], text: 'Adrenergic' },
    { receptor: 'ADA1B', numeric: [24, 50, -7.8, -2, 24], text: 'Adrenergic' },
    { receptor: 'ADA1D', numeric: [25, 49, -7.7, -1, 25], text: 'Adrenergic' },
    { receptor: 'ADRB1', numeric: [29, 45, -7.3, 3, 29], text: 'Adrenergic' },
    { receptor: 'ADRB2', numeric: [30, 44, -7.2, 4, 30], text: 'Adrenergic' },
    { receptor: 'ADGRA1', numeric: [31, 43, -7.1, 5, 31], text: 'Adhesion' },
    { receptor: 'ADGRA2', numeric: [32, 42, -7.0, 6, 32], text: 'Adhesion' },
    { receptor: 'ADGRA3', numeric: [33, 41, -6.9, 7, 33], text: 'Adhesion' }
  ];

  var Tree_circles;
  var Tree_colors;
  var Tree_circle_styling_dict;
  var Label_dict;
  var Tree_datatypes_dict;
  var Tree_textlegend_styling;
  var ShowLegend;
  var TreeLegendPosition;
  var styling_circles;
  var maxLeafNodeLength_scaler;

  function deepClone(obj) {
    return JSON.parse(JSON.stringify(obj));
  }

  function mapperJsKeepByNames(node, namesToKeep) {
    if (Array.isArray(node)) {
      var kept = [];
      for (var i = 0; i < node.length; i++) {
        var xi = mapperJsKeepByNames(node[i], namesToKeep);
        if (xi != null) {
          kept.push(xi);
        }
      }
      return kept.length ? kept : null;
    }
    if (!node || typeof node !== 'object') {
      return node;
    }
    var nm = node.name;
    var hasKids = !!(node.children && node.children.length);
    if (!Object.prototype.hasOwnProperty.call(namesToKeep, nm)) {
      if (hasKids) {
        var ch = mapperJsKeepByNames(node.children, namesToKeep);
        if (!ch || !ch.length) {
          return null;
        }
        var o = deepClone(node);
        o.children = ch;
        return o;
      }
      return null;
    }
    var pay = namesToKeep[nm];
    var out = deepClone(node);
    if (pay && Object.prototype.hasOwnProperty.call(pay, 'Inner')) {
      out.value = pay.Inner;
    }
    if (out.children && out.children.length) {
      var k2 = mapperJsKeepByNames(out.children, namesToKeep);
      if (k2 && k2.length) {
        out.children = k2;
      } else {
        delete out.children;
      }
    }
    return out;
  }

  function mapperTreeMaybePromoteRoot(md, opts) {
    var o = deepClone(opts);
    if (md && md.children && md.children.length === 1) {
      return {
        tree: deepClone(md.children[0]),
        opts: $.extend(o, {
          depth: 3,
          branch_length: { 1: 'Alicarboxylic acid', 2: 'Gonadotrophin-releasing hormone', 3: '' }
        })
      };
    }
    return { tree: md, opts: o };
  }

  function mapperStemFromEntry(entryId) {
    return String(entryId || '').trim().replace(/_human$/i, '');
  }

  function mapperTreeStemKeyUpper(entryId) {
    return mapperStemFromEntry(entryId).toUpperCase();
  }

  function mapperFallbackUniprotFromEntry(entryId, meta) {
    var sid = entryId != null ? String(entryId).trim().toUpperCase() : '';
    var u = meta && meta.uniprot ? String(meta.uniprot).trim().toUpperCase() : '';
    if (u) {
      return u;
    }
    if (sid.indexOf('_') !== -1) {
      return sid.split('_')[0].trim().toUpperCase();
    }
    return sid;
  }

  function mapperTreeBuildStemLabelDicts() {
    window.mapperTreeStemLabelDicts = { IUPHAR: {}, Gene: {}, UniProt: {} };
    if (!window.receptorSelect2Data || !window.receptorSelect2Data.length) {
      return;
    }
    window.receptorSelect2Data.forEach(function (item) {
      var id = item.id;
      if (id == null) {
        return;
      }
      var meta = (window.MAPPER20_ENTRY_META && window.MAPPER20_ENTRY_META[id]) || {};
      var stemU = mapperTreeStemKeyUpper(id);
      if (!stemU) {
        return;
      }
      var uni = mapperFallbackUniprotFromEntry(id, meta);

      window.mapperTreeStemLabelDicts.UniProt[stemU] = [uni];

      var iuphar = '';
      if (meta.name_html) {
        iuphar = meta.name_html;
      } else if (meta.name_plain) {
        iuphar = meta.name_plain;
      } else if (item.name_html) {
        iuphar = String(item.name_html);
      } else if (item.text) {
        iuphar = String(item.text);
      }
      if (iuphar) {
        window.mapperTreeStemLabelDicts.IUPHAR[stemU] = [iuphar];
      }

      var g = meta.gene || item.gene;
      if (g) {
        window.mapperTreeStemLabelDicts.Gene[stemU] = [String(g).trim()];
      }

      var rd = ServerReceptorDict[uni];
      if (!meta.name_html && rd && rd.length) {
        window.mapperTreeStemLabelDicts.IUPHAR[stemU] = rd;
      }
      var eg = ServerGeneDict[uni];
      if (eg && eg.length) {
        window.mapperTreeStemLabelDicts.Gene[stemU] = eg;
      }
    });
  }

  function mapperTreeLeafLabelLookupBuild() {
    window.tree_leaf_label_lookup = {};
    if (!window.receptorSelect2Data || !window.receptorSelect2Data.length) {
      return;
    }
    window.receptorSelect2Data.forEach(function (item) {
      var id = item.id;
      if (id == null) {
        return;
      }
      var meta = (window.MAPPER20_ENTRY_META && window.MAPPER20_ENTRY_META[id]) || {};
      var uni = mapperFallbackUniprotFromEntry(id, meta);
      var stemKey = mapperTreeStemKeyUpper(id);
      function row() {
        var proteinHtml = meta.name_html || item.name_html || '';
        var proteinPlain = (meta.name_plain || item.name_plain || item.text || id).trim();
        return {
          Protein: proteinPlain,
          ProteinHtml: proteinHtml || $('<span/>').text(proteinPlain).html(),
          Gene: (meta.gene || '').trim(),
          UniProt: uni || stemKey || ''
        };
      }
      var r = row();
      if (stemKey) {
        window.tree_leaf_label_lookup[stemKey] = r;
      }
      if (uni) {
        window.tree_leaf_label_lookup[uni] = r;
      }
    });
  }

  function mapperTreeFindLeafNameExact(skel, entryId, taRaw, unmatchedRaw) {
    var prefer = mapperStemFromEntry(entryId);
    if (prefer && skel) {
      var tgt = prefer.toLowerCase();
      var found = null;
      function walk(node) {
        if (!node) {
          return;
        }
        var ch = node.children;
        if (!ch || !ch.length) {
          var nm = node.name != null ? String(node.name).trim() : '';
          if (nm.replace(/_human$/i, '').toLowerCase() === tgt) {
            found = nm;
          }
          return;
        }
        ch.forEach(walk);
      }
      walk(skel);
      if (found) {
        return found;
      }
    }
    var rawCandidate = unmatchedRaw || taRaw || '';
    if (window.mapper20ResolveEntry && typeof window.mapper20ResolveEntry === 'function') {
      var resolved = window.mapper20ResolveEntry(rawCandidate);
      if (resolved) {
        return mapperTreeFindLeafNameExact(skel, resolved, '', '');
      }
    } else if (window.MAPPER20_RESOLVE) {
      var up = String(rawCandidate || '').trim().toUpperCase();
      var rid = window.MAPPER20_RESOLVE[up];
      if (rid) {
        return mapperTreeFindLeafNameExact(skel, rid, '', '');
      }
    }
    return null;
  }

  function mapperTreeCollectNamesPayload(skel, circlesObj) {
    var map = {};
    $('#mapper-tree-input-tbody tr').each(function () {
      var $tr = $(this);
      var entry = ($tr.find('.mapper20-receptor-entry').val() || '').trim();
      var ta = (($tr.find('.mapper20-in-receptor').val() || '') + '').trim();
      var unmatched = ($tr.data('mapper20UnmatchedRaw') || '') + '';
      if (!entry && !ta && !String(unmatched).trim()) {
        return;
      }
      var ln = mapperTreeFindLeafNameExact(skel, entry, ta, unmatched);
      if (!ln) {
        return;
      }
      var keyUpper = ln.replace(/_human$/i, '').toUpperCase();
      var cir = circlesObj[keyUpper];
      if (!cir) {
        return;
      }
      map[ln] = cir;
    });
    return map;
  }

  function mapperTreeIsTextMode() {
    return window.mapperTreeInputMode === 'text';
  }

  function mapper20FNV1a32(str) {
    var h = 0x811c9dc5;
    var s = String(str || '');
    var i;
    var code;
    for (i = 0; i < s.length; i++) {
      code = s.charCodeAt(i);
      h ^= code;
      h += (h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24);
      h >>>= 0;
    }
    return h >>> 0;
  }

  function mapperTreeDefaultHexForLabelKey(lbl) {
    var hue = mapper20FNV1a32(String(lbl || '').toLowerCase()) % 360;
    return 'hsl(' + hue + ', 68%, 48%)';
  }

  function mapperTreeParseNumLoose(s) {
    if (s == null || String(s).trim() === '') {
      return null;
    }
    var t = String(s).trim().replace(',', '.');
    var x = parseFloat(t);
    return isFinite(x) ? x : null;
  }

  function mapperTreeBuildTreeCircles() {
    var out = {};
    var textMode = mapperTreeIsTextMode();
    $('#mapper-tree-input-tbody tr').each(function () {
      var $tr = $(this);
      var entry = ($tr.find('.mapper20-receptor-entry').val() || '').trim();
      var ta = (($tr.find('.mapper20-in-receptor').val() || '') + '').trim();
      var unmatched = ($tr.data('mapper20UnmatchedRaw') || '') + '';
      if (!entry && !ta && !String(unmatched).trim()) {
        return;
      }
      var id = entry;
      if (!id && window.MAPPER20_RESOLVE) {
        id =
          window.MAPPER20_RESOLVE[String(ta || unmatched).trim().toUpperCase()] || '';
      }
      if (!id) {
        return;
      }
      var uniKey = mapperTreeStemKeyUpper(id);
      if (!uniKey) {
        return;
      }
      var innerRaw = (($tr.find('.mapper-tree-inner').val() || '') + '').trim();
      var o = {};
      if (!textMode) {
        var innerN = mapperTreeParseNumLoose(innerRaw);
        var o1 = mapperTreeParseNumLoose($tr.find('.mapper-tree-o1').val());
        var o2 = mapperTreeParseNumLoose($tr.find('.mapper-tree-o2').val());
        var o3 = mapperTreeParseNumLoose($tr.find('.mapper-tree-o3').val());
        var o4 = mapperTreeParseNumLoose($tr.find('.mapper-tree-o4').val());
        if (innerN == null && o1 == null && o2 == null && o3 == null && o4 == null) {
          return;
        }
        if (innerN != null) {
          o.Inner = innerN;
        }
        if (o1 != null) {
          o.Outer1 = o1;
        }
        if (o2 != null) {
          o.Outer2 = o2;
        }
        if (o3 != null) {
          o.Outer3 = o3;
        }
        if (o4 != null) {
          o.Outer4 = o4;
        }
      } else {
        if (!innerRaw) {
          return;
        }
        if (!window.MAPPER20_LABEL_COLORS[innerRaw]) {
          window.MAPPER20_LABEL_COLORS[innerRaw] = mapperTreeDefaultHexForLabelKey(innerRaw);
        }
        var enabled = window.MAPPER20_LABEL_ENABLED[innerRaw] !== false;
        o.Inner = innerRaw;
        o.ColorValue = enabled ? window.MAPPER20_LABEL_COLORS[innerRaw] || mapperTreeDefaultHexForLabelKey(innerRaw) : '#ffffff';
      }
      out[uniKey] = o;
    });
    return out;
  }

  function mapperTreeFilterSkeleton(skel, namesPayload) {
    if (!namesPayload || !Object.keys(namesPayload).length) {
      return deepClone(skel);
    }
    var k = mapperJsKeepByNames(deepClone(skel), namesPayload);
    return k;
  }

  function mergedFontSizesFromDom() {
    var def = (ORIG_OPTS && ORIG_OPTS.fontSize) || {};
    function px(id, fb) {
      var $e = $('#' + id);
      return $e.length ? $e.val() + 'px' : fb;
    }
    return {
      class: px('classFontSizeSlider', def.class || '15px'),
      ligandtype: px('ligandTypeFontSizeSlider', def.ligandtype || '14px'),
      receptorfamily: px('receptorFamilyFontSizeSlider', def.receptorfamily || '13px'),
      receptor: px('receptorFontSizeSlider', def.receptor || '12px')
    };
  }

  function parsePxFromFontSize(css) {
    var m = /(\d+)/.exec(String(css || ''));
    return m ? parseInt(m[1], 10) : 12;
  }

  function mapperTreeRadialLeafLabelGap() {
    var circleSize = Number(styling_circles && styling_circles.circle_size);
    return MAPPER_TREE_RADIAL_LEAF_LABEL_GAP_BASE + (isFinite(circleSize) ? circleSize : 3);
  }

  function mapperTreeSyncCircleSizingFromUi() {
    var csEl = $('#mapper-tree-circle-size-slider');
    if (csEl.length) {
      styling_circles.circle_size = 3 + 1 * Number(csEl.val());
    }
    var spEl = $('#mapper-tree-circle-spacer-slider');
    if (spEl.length) {
      styling_circles.circle_spacer =
        styling_circles.circle_size * (2 + 0.5 * Number(spEl.val())) + 1;
    }
  }

  function mapperTreeActiveLeafDropdownVal() {
    var $b = $('.mapper-tree-leaf-btn.btn-primary');
    var dv = ($b.attr('data-value') || '').trim();
    return dv === 'UniProt' || dv === 'Gene' ? dv : 'IUPHAR';
  }

  function mapperTreeSyncLeafLabelUi(btnVal) {
    var mapUi = {
      IUPHAR: 'Protein',
      Gene: 'Gene',
      UniProt: 'UniProt'
    };
    window.TREE_UI = window.TREE_UI || {};
    window.TREE_UI.leafLabelType = mapUi[btnVal] || 'Protein';
    $('.mapper-tree-leaf-btn').each(function () {
      var ok = ($(this).attr('data-value') || '') === btnVal;
      $(this).toggleClass('btn-outline-primary', !ok).toggleClass('btn-primary', ok);
    });
  }

  function mapperTreeActiveStemDict(btnVal) {
    if (btnVal === 'UniProt') {
      return window.mapperTreeStemLabelDicts.UniProt;
    }
    if (btnVal === 'Gene') {
      return window.mapperTreeStemLabelDicts.Gene;
    }
    return window.mapperTreeStemLabelDicts.IUPHAR;
  }

  /** No mapped rows yet: empty plot host (no skeleton draw). */
  function mapperTreeShowPlaceholderPlot(kind, msg) {
    var host = $('#tree_plot');
    if (!host.length) {
      return;
    }
    host.empty();
    var wrap = $('<div class="mapper-tree-plot-placeholder" role="region" aria-label="Tree plot placeholder"/>');
    if (kind === 'nomatch') {
      wrap.append(
        $('<p class="mapper-tree-plot-placeholder-title"/>').text(
          'Receptors present but none match this phylogeny'
        )
      );
      wrap.append($('<p class="mapper-tree-plot-placeholder-warn"/>').text(msg || 'Check receptors against the phylogenetic scaffold.'));
    } else {
      wrap.append($('<p class="mapper-tree-plot-placeholder-title"/>').text('No receptors mapped yet'));
      wrap.append(
        $('<p class="mapper-tree-plot-placeholder-hint"/>').text(
          'Pick or paste receptors at left and add Numeric values (Inner + optional O1–O4) or a Text Inner label — the phylogeny renders incrementally row by row.'
        )
      );
    }
    host.append(wrap);
  }

  function mapperTreeFitFinalSvgViewBox() {
    var svgNode = d3.select('#tree_plot svg').node();
    if (!svgNode) {
      return;
    }
    var bbox;
    try {
      bbox = svgNode.getBBox();
    } catch (err) {
      return;
    }
    if (!bbox || !isFinite(bbox.x) || !isFinite(bbox.y) || !isFinite(bbox.width) || !isFinite(bbox.height) || bbox.width <= 0 || bbox.height <= 0) {
      return;
    }
    var pad = 28;
    var minX = bbox.x - pad;
    var minY = bbox.y - pad;
    var vbWidth = bbox.width + pad * 2;
    var vbHeight = bbox.height + pad * 2;
    d3.select(svgNode)
      .attr('viewBox', minX + ' ' + minY + ' ' + vbWidth + ' ' + vbHeight)
      .attr('preserveAspectRatio', 'xMidYMid meet');
  }

  function mapperTreeSetCircleStarterFromLeafLabels() {
    var maxOuterEdge = 0;
    var labelGap = 3;
    var circleSize = Number(styling_circles && styling_circles.circle_size);
    var circleSpacer = Number(styling_circles && styling_circles.circle_spacer);
    if (!(isFinite(circleSize) && circleSize > 0)) {
      circleSize = 3;
    }
    if (!(isFinite(circleSpacer) && circleSpacer > 0)) {
      circleSpacer = 10;
    }
    d3.select('#tree_plot').selectAll('g.node[id]').each(function (d) {
      if (!d || d.depth !== window.tree_options_draw.depth) {
        return;
      }
      var textNode = d3.select(this).select('text').node();
      if (!textNode) {
        return;
      }
      var bbox;
      var matrix;
      try {
        bbox = textNode.getBBox();
        var transform = textNode.transform && textNode.transform.baseVal
          ? textNode.transform.baseVal.consolidate()
          : null;
        matrix = transform ? transform.matrix : null;
      } catch (err) {
        return;
      }
      if (!bbox || !isFinite(bbox.x) || !isFinite(bbox.width)) {
        return;
      }
      var corners = [
        { x: bbox.x, y: bbox.y },
        { x: bbox.x + bbox.width, y: bbox.y },
        { x: bbox.x, y: bbox.y + bbox.height },
        { x: bbox.x + bbox.width, y: bbox.y + bbox.height }
      ];
      corners.forEach(function (corner) {
        var x = corner.x;
        if (matrix) {
          x = matrix.a * corner.x + matrix.c * corner.y + matrix.e;
        }
        if (isFinite(x)) {
          maxOuterEdge = Math.max(maxOuterEdge, x);
        }
      });
    });
    if (maxOuterEdge > 0) {
      styling_circles.starter = Math.max(1, maxOuterEdge + circleSize + labelGap - (2 * circleSpacer));
    }
  }

  function mapperTreeRedrawNow() {
    if (!ORIG_SKEL || !ORIG_OPTS || typeof window.mapperClassificationRedraw !== 'function') {
      mapperTreeShowPlaceholderPlot('empty');
      $('#mapper-tree-messages').empty();
      return;
    }

    $('#mapper-tree-messages').empty();

    Tree_circles = mapperTreeBuildTreeCircles();

    var circlesKeys = Tree_circles && Object.keys(Tree_circles).length;
    if (!circlesKeys) {
      mapperTreeShowPlaceholderPlot('empty');
      return;
    }

    $('#tree_plot').empty();

    var namesPayload = mapperTreeCollectNamesPayload(ORIG_SKEL, Tree_circles);

    /** Values present but no leaves resolved on the scaffold (wrong / unresolved receptors). */
    if (!Object.keys(namesPayload).length) {
      mapperTreeShowPlaceholderPlot(
        'nomatch',
        'Entries have values but no receptors match a leaf on this phylogeny. Use search / picker until each row resolves cleanly.'
      );
      return;
    }

    var filteredSk = mapperTreeFilterSkeleton(ORIG_SKEL, namesPayload);

    var labelDdVal = mapperTreeActiveLeafDropdownVal();
    mapperTreeSyncLeafLabelUi(labelDdVal);

    if (
      filteredSk == null ||
      (filteredSk.children && filteredSk.children.length === 0) ||
      ((filteredSk.name === '' || filteredSk.name === null) &&
        (!filteredSk.children || !filteredSk.children.length))
    ) {
      mapperTreeShowPlaceholderPlot(
        'nomatch',
        'Filtered tree would be empty for this receptor subset. Adjust rows or mappings.'
      );
      return;
    }

    var btnVal = mapperTreeActiveLeafDropdownVal();

    window.TREE_UI = window.TREE_UI || {};
    window.TREE_UI.layout = MAPPER_TREE_LAYOUT;

    var fontMerge = mergedFontSizesFromDom();
    mapperTreeSyncCircleSizingFromUi();
    if (window.console && typeof window.console.log === 'function') {
      window.console.log('[MapperTree] leaf gap inputs', {
        radialLeafLabelGapBase: MAPPER_TREE_RADIAL_LEAF_LABEL_GAP_BASE,
        stylingCircleSize: styling_circles.circle_size,
        computedRadialLeafLabelGap: mapperTreeRadialLeafLabelGap(),
        stylingCircleSpacer: styling_circles.circle_spacer
      });
    }
    var baseOptsIn = $.extend(deepClone(ORIG_OPTS), {
      fontSize: fontMerge,
      fontFamily: ORIG_OPTS.fontFamily || 'Palatino',
      radialLeafLabelGap: mapperTreeRadialLeafLabelGap(),
      leafEndDotRadius:
        ORIG_OPTS.leafEndDotRadius != null && isFinite(Number(ORIG_OPTS.leafEndDotRadius))
          ? Number(ORIG_OPTS.leafEndDotRadius)
          : 2
    });

    var promLocal = mapperTreeMaybePromoteRoot(deepClone(filteredSk), baseOptsIn);
    var td = deepClone(promLocal.tree);
    window.tree_options_draw = window.mapperClassificationRedraw(td, deepClone(promLocal.opts), window.TREE_UI.layout);

    if (mapperTreeIsTextMode()) {
      styling_circles.mode = 'Text';
      Tree_datatypes_dict = $.extend({}, window.mapperTreeDiscreteDatatypes);
    } else {
      styling_circles.mode = 'Numeric';
      Tree_datatypes_dict = $.extend({}, window.mapperTreeNumericDatatypes);
    }

    styling_circles.starter =
      (maxLeafNodeLength_scaler || 10) * parsePxFromFontSize(fontMerge.receptor || '12px');

    /** Circle size / spacer already synced before tree draw so label gap uses the same effective size. */

    var dictStem = mapperTreeActiveStemDict(btnVal);

    custom_changeLeavesLabels(
      'tree_plot',
      btnVal === 'UniProt' ? 'UniProt' : btnVal === 'Gene' ? 'Gene' : 'IUPHAR',
      dictStem,
      styling_circles
    );
    mapperTreeSetCircleStarterFromLeafLabels();

    DrawCircles('tree_plot', Tree_circles, Tree_colors, styling_circles, Tree_circle_styling_dict);

    d3.select('#tree_plot svg').selectAll('.legend-group').remove();
    if (ShowLegend) {
      if (mapperTreeIsTextMode() && typeof CreateTextLegend === 'function') {
        CreateTextLegend('tree_plot', Tree_circles, Tree_textlegend_styling);
      } else if (typeof createLegendBars === 'function') {
        createLegendBars(
          'tree_plot',
          Tree_circles,
          Tree_colors,
          Tree_circle_styling_dict,
          Tree_datatypes_dict,
          Label_dict,
          TreeLegendPosition || 'Top'
        );
      }
    }
    mapperTreeFitFinalSvgViewBox();
  }

  window.mapperTreeRedrawNow = mapperTreeRedrawNow;

  function mapperTreeScheduleRedraw() {
    if (suppressRedraw) {
      return;
    }
    window.clearTimeout(redrawTimer);
    redrawTimer = window.setTimeout(mapperTreeRedrawNow, DEBOUNCE_MS);
  }

  function mapperTreeSetInputMode(mode) {
    window.mapperTreeInputMode = mode === 'text' ? 'text' : 'numeric';
    var text = mapperTreeIsTextMode();
    $('#mapper-tree-mode-numeric-btn, #mapper-tree-mode-labels-btn').each(function () {
      var isNum = $(this).attr('id') === 'mapper-tree-mode-numeric-btn';
      var on = text ? !isNum : isNum;
      $(this).toggleClass('active', on).attr('aria-pressed', on ? 'true' : 'false');
    });
    var $tbl = $('#mapper-tree-input-table');
    $tbl.toggleClass('mapper20-text-mode', text);

    mapperTreeRefreshInnerSwatches();
    mapperTreeScheduleRedraw();
  }

  function mapperTreeDestroyRowColorSpectrum($picker) {
    if (!$picker || !$picker.length || !$.fn.spectrum) {
      return;
    }
    try {
      if ($picker.data('spectrum.id') != null || $picker.hasClass('sp-replaced')) {
        $picker.spectrum('destroy');
      }
    } catch (e2) {}
  }

  function mapperTreeApplyLabelColor(label, color, $activePicker) {
    var key = String(label || '').trim();
    if (!key || !color) {
      return;
    }
    window.MAPPER20_LABEL_COLORS[key] = color;
    $('#mapper-tree-input-tbody tr').each(function () {
      var $tr = $(this);
      if (($tr.find('.mapper-tree-inner').val() || '').trim() !== key) {
        return;
      }
      var $sw = $tr.find('.mapper20-row-color-picker');
      $sw.val(color).css('background-color', color);
      if ($activePicker && $activePicker.length && $sw[0] === $activePicker[0]) {
        return;
      }
      if ($.fn.spectrum && ($sw.data('spectrum.id') != null || $sw.hasClass('sp-replaced'))) {
        try {
          $sw.spectrum('set', color);
        } catch (e3) {}
      }
    });
    mapperTreeScheduleRedraw();
  }

  function mapperTreeEnsureRowColorSpectrum($picker, label, color) {
    if (!$picker.length || !$.fn.spectrum) {
      $picker.css('background-color', color || '#f5f5f5');
      return;
    }
    var currentLabel = $picker.attr('data-mapper-tree-label') || '';
    if ($picker.data('spectrum.id') != null || $picker.hasClass('sp-replaced')) {
      if (currentLabel === label) {
        $picker.spectrum('set', color || '#f5f5f5');
        return;
      }
      mapperTreeDestroyRowColorSpectrum($picker);
    }
    $picker.attr('data-mapper-tree-label', label || '');
    $picker.val(color || '#f5f5f5').css('background-color', color || '#f5f5f5');
    $picker.spectrum({
      color: color || '#f5f5f5',
      preferredFormat: 'hex',
      showInput: true,
      showPalette: true,
      showSelectionPalette: true,
      clickoutFiresChange: true,
      containerClassName: 'mapper20-row-color-spectrum',
      replacerClassName: 'mapper20-row-swatch-replacer',
      palette: [
        ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd'],
        ['#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'],
        ['#000000', '#666666', '#aaaaaa', '#ffffff']
      ],
      move: function (tiny) {
        mapperTreeApplyLabelColor(label, tiny ? tiny.toHexString() : color, $picker);
      },
      change: function (tiny) {
        mapperTreeApplyLabelColor(label, tiny ? tiny.toHexString() : color, $picker);
      }
    });
  }

  function mapperTreeRefreshInnerSwatches() {
    if (!mapperTreeIsTextMode()) {
      $('#mapper-tree-input-tbody .mapper20-row-color-picker').each(function () {
        mapperTreeDestroyRowColorSpectrum($(this));
      });
      $('#mapper-tree-input-tbody .mapper20-label-swatch').css('background-color', 'transparent');
      return;
    }
    $('#mapper-tree-input-tbody tr').each(function () {
      var $sw = $(this).find('.mapper20-label-swatch');
      var inner = (($(this).find('.mapper-tree-inner').val() || '') + '').trim();
      if (!inner) {
        mapperTreeDestroyRowColorSpectrum($sw);
        $sw.css('background-color', '#f5f5f5');
        return;
      }
      var hex = window.MAPPER20_LABEL_COLORS[inner] || mapperTreeDefaultHexForLabelKey(inner);
      $sw.css('background-color', hex);
      mapperTreeEnsureRowColorSpectrum($sw, inner, hex);
    });
  }

  function mapperTreeResolvedDisplay(entryId) {
    var sid = entryId != null ? String(entryId).trim() : '';
    var meta = (window.MAPPER20_ENTRY_META && window.MAPPER20_ENTRY_META[sid]) || {};
    return meta.name_html ? String(meta.name_html) : $('<span/>').text(sid || '').html();
  }

  function mapperTreeDestroyAc($inp) {
    try {
      if ($inp.hasClass('ui-autocomplete-input')) {
        $inp.autocomplete('destroy');
      }
    } catch (e1) {}
  }

  function mapperTreeSyncClearBtn($tr) {
    var entry = ($tr.find('.mapper20-receptor-entry').val() || '').trim();
    var typed = (($tr.find('.mapper20-in-receptor').val() || '') + '').trim();
    var um = $tr.data('mapper20UnmatchedRaw');
    var has = !!(entry || typed || (um != null && String(um).trim()));
    $tr.find('.mapper20-receptor-input-wrap').toggleClass('is-empty', !has);
  }

  function mapperTreeFocusInnerCell($tr) {
    window.setTimeout(function () {
      var $inner = $tr.find('.mapper-tree-inner:visible').first();
      if ($inner.length) {
        $inner.focus().select();
      }
    }, 0);
  }

  function mapperTreeSyncRemoveButtons() {
    $('#mapper-tree-input-tbody tr').each(function () {
      var $tr = $(this);
      $tr.find('.mapper20-remove-cell').toggleClass('is-remove-hidden', mapperTreeRowBlank($tr));
    });
  }

  function mapperTreeCompactReceptorRowsAfterInput() {
    var hasContent = false;
    $('#mapper-tree-input-tbody tr').each(function () {
      if (!mapperTreeRowBlank($(this))) {
        hasContent = true;
        return false;
      }
    });
    $('#mapper-tree-input-table').toggleClass('mapper20-receptors-compact', hasContent);
  }

  function mapperTreeSetResolved($tr, id) {
    var sid = id != null ? String(id).trim() : '';
    var $inp = $tr.find('.mapper20-in-receptor');
    var $hid = $tr.find('.mapper20-receptor-entry');
    var $view = $tr.find('.mapper20-receptor-html-view');
    mapperTreeDestroyAc($inp);
    if (!sid) {
      $hid.val('');
      $view.hide().empty();
      $inp.val('').show();
      mapperTreeBindAc($inp);
      mapperTreeSyncClearBtn($tr);
      mapperTreeSyncRemoveButtons();
      mapperTreeCompactReceptorRowsAfterInput();
      mapperTreeScheduleRedraw();
      return;
    }
    $hid.val(sid);
    $view.html(mapperTreeResolvedDisplay(sid));
    $inp.val('').hide();
    $view.show();
    mapperTreeBindAc($inp);
    $tr.removeClass('mapper20-row-invalid');
    $tr.removeData('mapper20UnmatchedRaw');
    mapperTreeSyncClearBtn($tr);
    mapperTreeSyncRemoveButtons();
    mapperTreeCompactReceptorRowsAfterInput();
    if (!suppressRedraw) {
      mapperTreeFocusInnerCell($tr);
    }
    mapperTreeScheduleRedraw();
  }

  function mapperTreeFilterLocal(term, limit) {
    var t = (term || '').trim().toUpperCase();
    if (!t || !window.receptorSelect2Data) {
      return [];
    }
    var filtered = window.receptorSelect2Data.filter(function (item) {
      var st = (item.search_text || item.text || '').toUpperCase();
      return st.indexOf(t) !== -1 || (item.id && String(item.id).toUpperCase().indexOf(t) !== -1);
    });
    var lim = limit || 80;
    if (filtered.length > lim) {
      filtered = filtered.slice(0, lim);
    }
    return filtered.map(function (item) {
      return {
        label: item.name_plain || item.text || item.id,
        value: item.id,
        id: item.id,
        text: item.text,
        name_html: item.name_html || '',
        name_plain: item.name_plain || ''
      };
    });
  }

  function mapperTreeBindAc($inp) {
    mapperTreeDestroyAc($inp);
    $inp.autocomplete({
      minLength: 1,
      source: function (request, response) {
        response(mapperTreeFilterLocal(request.term, 80));
      },
      focus: function () {
        return false;
      },
      select: function (event, ui) {
        mapperTreeSetResolved($inp.closest('tr'), ui.item.id);
        event.preventDefault();
      }
    });
    var w = $inp.data('ui-autocomplete');
    if (w) {
      w._renderItem = function (ul, item) {
        var inner =
          item.name_html ||
          $('<span/>')
            .text(item.label || '')
            .html();
        return $('<li>')
          .append($('<div class="mapper20-ac-item-label">').html(inner))
          .appendTo(ul);
      };
    }

    $inp.on('keyup', function () {
      var $tr = $inp.closest('tr');
      if (!$tr.find('.mapper20-receptor-entry').val()) {
        $tr.removeData('mapper20UnmatchedRaw');
        $tr.removeClass('mapper20-row-invalid');
      }
      mapperTreeSyncClearBtn($tr);
    });
    $inp.on('blur.mapper-tree', function () {
      var $tr = $inp.closest('tr');
      window.setTimeout(function () {
        if (!$inp.is(':visible')) {
          return;
        }
        var raw = ($inp.val() || '').trim();
        if (!raw || $tr.find('.mapper20-receptor-entry').val()) {
          return;
        }
        if (window.mapper20ResolveEntry) {
          var canon = window.mapper20ResolveEntry(raw);
          if (canon) {
            mapperTreeSetResolved($tr, canon);
          } else if (window.receptorSelect2Data) {
            var hit = window.receptorSelect2Data.filter(function (x) {
              return String(x.id) === raw;
            });
            if (hit.length) {
              mapperTreeSetResolved($tr, hit[0].id);
              return;
            }
            var up = raw.toUpperCase();
            if (window.MAPPER20_RESOLVE && window.MAPPER20_RESOLVE[up]) {
              mapperTreeSetResolved($tr, window.MAPPER20_RESOLVE[up]);
              return;
            }
            var known = window.receptorSelect2Data.some(function (x) {
              return String(x.id) === raw;
            });
            if (!known && raw) {
              $tr.addClass('mapper20-row-invalid');
              $tr.data('mapper20UnmatchedRaw', raw);
            }
          }
        }
        mapperTreeSyncClearBtn($tr);
      }, 170);
    });
  }

  function mapperTreeCreateReceptorTd($td) {
    var $hid = $('<input type="hidden" class="mapper20-receptor-entry" value="">');
    var $wrap = $('<div class="mapper20-receptor-input-wrap is-empty">');
    var $inp = $(
      '<textarea class="form-control input-sm mapper20-in-receptor" rows="1" autocomplete="off" spellcheck="false"></textarea>'
    );
    var $view = $('<div class="form-control input-sm mapper20-receptor-html-view" tabindex="0"></div>');
    var $clr = $('<button type="button" class="mapper20-receptor-clear" aria-label="Clear receptor">&times;</button>');
    $wrap.append($inp, $view, $clr);
    $td.append($hid, $wrap);
    $view.hide();
    $clr.on('click', function () {
      mapperTreeSetResolved($clr.closest('tr'), '');
    });
    return $inp;
  }

  function mapperTreeRowBlank($tr) {
    var entry = ($tr.find('.mapper20-receptor-entry').val() || '').trim();
    var inner = (($tr.find('.mapper-tree-inner').val() || '') + '').trim();
    var typed = (($tr.find('.mapper20-in-receptor').val() || '') + '').trim();
    var unmatched = ($tr.data('mapper20UnmatchedRaw') || '') + '';
    var outerHas = ['.mapper-tree-o1', '.mapper-tree-o2', '.mapper-tree-o3', '.mapper-tree-o4'].some(function (sel) {
      return (($tr.find(sel).val() || '') + '').trim() !== '';
    });
    return !entry && !inner && !typed && !String(unmatched).trim() && !outerHas;
  }

  function mapperTreeDestroyRowAc($tr) {
    mapperTreeDestroyAc($tr.find('.mapper20-in-receptor'));
    $tr.find('.mapper20-row-color-picker').each(function () {
      mapperTreeDestroyRowColorSpectrum($(this));
    });
  }

  function mapperTreeAppendRow(skipTrail) {
    var tr = $('<tr>');
    tr.append(
      $('<td class="mapper20-remove-cell is-remove-hidden">').append(
        $('<button type="button" class="mapper20-remove-row" aria-label="Remove row">&times;</button>')
      )
    );
    var $tdR = $('<td class="mapper20-receptor-cell">');
    var $inp = mapperTreeCreateReceptorTd($tdR);
    tr.append($tdR);
    tr.append(
      $('<td class="mapper-tree-cell-inner mapper20-value-cell">').append(
        $('<input type="text" class="form-control input-sm mapper-tree-inner" autocomplete="off">')
      )
    );
    $.each(['o1', 'o2', 'o3', 'o4'], function (_, suf) {
      tr.append(
        $('<td class="mapper-tree-outer-cell mapper20-value-cell">').append(
          $('<input type="text" class="form-control input-sm mapper-tree-' + suf + '" autocomplete="off">')
        )
      );
    });
    tr.append(
      $('<td class="mapper20-swatch-cell">').append(
        '<input type="text" class="mapper20-label-swatch mapper20-row-color-picker" readonly="readonly" aria-label="Label color">'
      )
    );
    $('#mapper-tree-input-tbody').append(tr);
    mapperTreeBindAc($inp);
    mapperTreeSyncClearBtn(tr);
    mapperTreeSyncRemoveButtons();
    if (!skipTrail) {
      mapperTreeEnsureTrailingBlankRow();
    }
  }

  function mapperTreeEnsureTrailingBlankRow() {
    var $tb = $('#mapper-tree-input-tbody');
    while ($tb.children().length >= 2) {
      var $last = $tb.children().last();
      var $prev = $last.prev();
      if (mapperTreeRowBlank($last) && mapperTreeRowBlank($prev)) {
        mapperTreeDestroyRowAc($last);
        $last.remove();
        continue;
      }
      break;
    }
    if (!$tb.children().length) {
      mapperTreeAppendRow(true);
    }
    var $lastOne = $tb.children().last();
    if (!mapperTreeRowBlank($lastOne) && $tb.children().length < MAPPER_TREE_MAX_ROWS) {
      mapperTreeAppendRow(true);
    }
    mapperTreeSyncRemoveButtons();
  }

  function mapperTreePasteSplit(line) {
    var parts = line.split(/\t+/);
    if (parts.length > 2) {
      return { r: (parts[0] || '').trim(), rest: parts.slice(1) };
    }
    if (parts.length === 2) {
      return { r: (parts[0] || '').trim(), rest: [(parts[1] || '').trim()] };
    }
    if (line.indexOf(';') !== -1) {
      var si = line.indexOf(';');
      return { r: line.slice(0, si).trim(), rest: line.slice(si + 1).split(';').map(function (x) { return x.trim(); }) };
    }
    return { r: (parts[0] || '').trim(), rest: [] };
  }

  function mapperTreeNormalizeSortText(value) {
    var text = $('<div/>').html(String(value || '')).text();
    return text
      .replace(/α|Α/g, 'a')
      .replace(/β|Β/g, 'b')
      .replace(/γ|Γ/g, 'g')
      .replace(/δ|Δ/g, 'd')
      .replace(/κ|Κ/g, 'k')
      .replace(/μ|Μ/g, 'm')
      .replace(/&[a-z]+;/gi, '')
      .replace(/<[^>]*>/g, '')
      .replace(/\s+/g, ' ')
      .trim()
      .toLowerCase();
  }

  function mapperTreeNaturalCompare(a, b) {
    if (!mapperTreeNaturalCompare.collator && window.Intl && Intl.Collator) {
      mapperTreeNaturalCompare.collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
    }
    if (mapperTreeNaturalCompare.collator) {
      return mapperTreeNaturalCompare.collator.compare(a, b);
    }
    return a < b ? -1 : a > b ? 1 : 0;
  }

  var mapperTreeSortState = { col: null, dir: 'asc' };

  function mapperTreeUpdateSortHeaders() {
    $('#mapper-tree-input-table th.mapper20-sortable-head').each(function () {
      var $th = $(this);
      var col = $th.attr('data-mapper-tree-sort-col');
      var active = mapperTreeSortState.col === col;
      var dir = active ? mapperTreeSortState.dir : null;
      $th.attr('aria-sort', active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none');
      $th.find('.mapper20-sort-indicator').text(active ? (dir === 'asc' ? '↑' : '↓') : '↕');
    });
  }

  function mapperTreeSerializeRows() {
    var rows = [];
    $('#mapper-tree-input-tbody tr').each(function () {
      var $tr = $(this);
      rows.push({
        entry: ($tr.find('.mapper20-receptor-entry').val() || '').trim(),
        receptorText: (($tr.find('.mapper20-in-receptor').val() || '') + '').trim(),
        unmatched: ($tr.data('mapper20UnmatchedRaw') || '') + '',
        invalid: $tr.hasClass('mapper20-row-invalid'),
        inner: (($tr.find('.mapper-tree-inner').val() || '') + '').trim(),
        o1: (($tr.find('.mapper-tree-o1').val() || '') + '').trim(),
        o2: (($tr.find('.mapper-tree-o2').val() || '') + '').trim(),
        o3: (($tr.find('.mapper-tree-o3').val() || '') + '').trim(),
        o4: (($tr.find('.mapper-tree-o4').val() || '') + '').trim()
      });
    });
    return rows;
  }

  function mapperTreeRowHasContent(row) {
    return !!(
      row.entry ||
      row.receptorText ||
      String(row.unmatched || '').trim() ||
      row.inner ||
      row.o1 ||
      row.o2 ||
      row.o3 ||
      row.o4
    );
  }

  function mapperTreeSortKey(row, col) {
    if (col === 'inner') {
      var num = mapperTreeParseNumLoose(row.inner);
      return num == null ? mapperTreeNormalizeSortText(row.inner) : num;
    }
    if (row.entry) {
      return mapperTreeNormalizeSortText(mapperTreeResolvedDisplay(row.entry));
    }
    return mapperTreeNormalizeSortText(row.receptorText || row.unmatched || '');
  }

  function mapperTreePopulateRow($tr, row) {
    if (row.entry) {
      mapperTreeSetResolved($tr, row.entry);
    } else {
      var $inp = $tr.find('.mapper20-in-receptor');
      $inp.val(row.receptorText || row.unmatched || '');
      if (row.unmatched) {
        $tr.data('mapper20UnmatchedRaw', row.unmatched);
      }
      $tr.toggleClass('mapper20-row-invalid', !!row.invalid);
      mapperTreeSyncClearBtn($tr);
    }
    $tr.find('.mapper-tree-inner').val(row.inner || '');
    $tr.find('.mapper-tree-o1').val(row.o1 || '');
    $tr.find('.mapper-tree-o2').val(row.o2 || '');
    $tr.find('.mapper-tree-o3').val(row.o3 || '');
    $tr.find('.mapper-tree-o4').val(row.o4 || '');
  }

  function mapperTreeApplySerializedRows(rows) {
    suppressRedraw = true;
    mapperTreeDestroyAllRows();
    $('#mapper-tree-input-tbody').empty();
    rows.forEach(function (row) {
      mapperTreeAppendRow(true);
      mapperTreePopulateRow($('#mapper-tree-input-tbody tr').last(), row);
    });
    mapperTreeEnsureTrailingBlankRow();
    mapperTreeRefreshInnerSwatches();
    mapperTreeCompactReceptorRowsAfterInput();
    mapperTreeUpdateSortHeaders();
    suppressRedraw = false;
  }

  function mapperTreeSortSerializedRows(col) {
    if (mapperTreeSortState.col === col) {
      mapperTreeSortState.dir = mapperTreeSortState.dir === 'asc' ? 'desc' : 'asc';
    } else {
      mapperTreeSortState.col = col;
      mapperTreeSortState.dir = 'asc';
    }
    var rows = mapperTreeSerializeRows();
    var filled = rows.filter(mapperTreeRowHasContent);
    filled.sort(function (a, b) {
      var ak = mapperTreeSortKey(a, col);
      var bk = mapperTreeSortKey(b, col);
      var cmp;
      if (typeof ak === 'number' && typeof bk === 'number') {
        cmp = ak - bk;
      } else {
        cmp = mapperTreeNaturalCompare(String(ak), String(bk));
      }
      return mapperTreeSortState.dir === 'desc' ? -cmp : cmp;
    });
    mapperTreeApplySerializedRows(filled);
  }

  function mapperTreeDestroyAllRows() {
    $('#mapper-tree-input-tbody tr').each(function () {
      mapperTreeDestroyRowAc($(this));
    });
  }

  function mapperTreeFocusReceptorCell($tr) {
    var $inp = $tr.find('.mapper20-in-receptor:visible').first();
    if ($inp.length) {
      $inp.focus();
      return;
    }
    $tr.find('.mapper20-receptor-html-view:visible').first().focus();
  }

  function mapperTreeFocusableCellsForRow($tr) {
    var selectors = ['.mapper20-in-receptor:visible', '.mapper20-receptor-html-view:visible', '.mapper-tree-inner:visible'];
    if (!mapperTreeIsTextMode()) {
      selectors.push('.mapper-tree-o1:visible', '.mapper-tree-o2:visible', '.mapper-tree-o3:visible', '.mapper-tree-o4:visible');
    }
    return $tr.find(selectors.join(','));
  }

  function mapperTreeFocusNextTableCellFrom($target) {
    var $tr = $target.closest('tr');
    var $cells = mapperTreeFocusableCellsForRow($tr);
    var idx = $cells.index($target);
    if (idx > -1 && idx < $cells.length - 1) {
      $cells.eq(idx + 1).focus().select();
      return;
    }
    var $next = $tr.next('tr');
    if (!$next.length) {
      mapperTreeEnsureTrailingBlankRow();
      $next = $tr.next('tr');
    }
    if (!$next.length) {
      return;
    }
    if (mapperTreeRowBlank($next)) {
      mapperTreeFocusReceptorCell($next);
    } else {
      mapperTreeFocusInnerCell($next);
    }
  }

  function mapperTreeFindBlankRow() {
    var $hit = $();
    $('#mapper-tree-input-tbody tr').each(function () {
      if (mapperTreeRowBlank($(this))) {
        $hit = $(this);
        return false;
      }
    });
    return $hit;
  }

  function mapperTreeSetDemoReceptor($tr, receptor) {
    var raw = String(receptor || '').trim();
    var resolved = null;
    if (raw && window.mapper20ResolveEntry) {
      resolved = window.mapper20ResolveEntry(raw);
    }
    if (!resolved && raw && window.MAPPER20_RESOLVE) {
      resolved = window.MAPPER20_RESOLVE[raw.toUpperCase()] || null;
    }
    if (resolved) {
      mapperTreeSetResolved($tr, resolved);
      return;
    }
    $tr.find('.mapper20-in-receptor').val(raw);
    $tr.find('.mapper20-receptor-entry').val('');
    $tr.removeData('mapper20UnmatchedRaw');
    $tr.removeClass('mapper20-row-invalid');
    mapperTreeSyncClearBtn($tr);
  }

  function mapperTreeFillDemoRows() {
    var textMode = mapperTreeIsTextMode();
    suppressRedraw = true;
    mapperTreeDestroyAllRows();
    $('#mapper-tree-input-tbody').empty();
    $('#mapper-tree-messages').empty();
    MAPPER_TREE_DEMO_ROWS.forEach(function (row) {
      mapperTreeAppendRow(true);
      var $tr = $('#mapper-tree-input-tbody tr').last();
      mapperTreeSetDemoReceptor($tr, row.receptor);
      if (textMode) {
        $tr.find('.mapper-tree-inner').val(row.text || '');
        $tr.find('.mapper-tree-o1, .mapper-tree-o2, .mapper-tree-o3, .mapper-tree-o4').val('');
        if (row.text && !window.MAPPER20_LABEL_COLORS[row.text]) {
          window.MAPPER20_LABEL_COLORS[row.text] = mapperTreeDefaultHexForLabelKey(row.text);
        }
      } else {
        var values = row.numeric || [];
        $tr.find('.mapper-tree-inner').val(values[0] != null ? values[0] : '');
        $tr.find('.mapper-tree-o1').val(values[1] != null ? values[1] : '');
        $tr.find('.mapper-tree-o2').val(values[2] != null ? values[2] : '');
        $tr.find('.mapper-tree-o3').val(values[3] != null ? values[3] : '');
        $tr.find('.mapper-tree-o4').val(values[4] != null ? values[4] : '');
      }
    });
    suppressRedraw = false;
    mapperTreeEnsureTrailingBlankRow();
    mapperTreeRefreshInnerSwatches();
    mapperTreeSyncRemoveButtons();
    mapperTreeCompactReceptorRowsAfterInput();
    $('#mapper-tree-clear-rows').removeClass('mapper20-clear-clean');
    mapperTreeRedrawNow();
  }

  function mapperBoot() {
    var boot = window.MAPPER_TREE_BOOT || {};
    ORIG_SKEL = boot.tree_skeleton;
    if (typeof ORIG_SKEL === 'string') {
      ORIG_SKEL = JSON.parse(ORIG_SKEL);
    }
    ORIG_OPTS = boot.tree_options;
    if (typeof ORIG_OPTS === 'string') {
      ORIG_OPTS = JSON.parse(ORIG_OPTS);
    }

    ServerReceptorDict =
      typeof boot.receptor_dict === 'string'
        ? JSON.parse(boot.receptor_dict || '{}')
        : boot.receptor_dict || {};

    ServerGeneDict =
      typeof boot.gene_dict === 'string' ? JSON.parse(boot.gene_dict || '{}') : boot.gene_dict || {};

    if (!ORIG_OPTS) {
      ORIG_OPTS = {};
    }
    ORIG_OPTS.anchor = ORIG_OPTS.anchor || 'tree_plot';

    mapperTreeBuildStemLabelDicts();
    mapperTreeLeafLabelLookupBuild();

    Tree_circles = {};
    Tree_colors = {
      Inner: ['#FFFFFF', '#000000'],
      Outer1: ['#FFFFFF', '#0000FF'],
      Outer2: ['#FFFFFF', '#FF0000'],
      Outer3: ['#FFFFFF', '#22c7b1'],
      Outer4: ['#FFFFFF', '#008000'],
      Outer5: ['#FFFFFF', '#FFA500']
    };

    Tree_circle_styling_dict = {
      Inner: 'One',
      Outer1: 'One',
      Outer2: 'One',
      Outer3: 'One',
      Outer4: 'One',
      Outer5: 'One'
    };

    Label_dict = {
      Inner: 'Inner',
      Outer1: 'Outer 1',
      Outer2: 'Outer 2',
      Outer3: 'Outer 3',
      Outer4: 'Outer 4',
      Outer5: 'Outer 5'
    };

    window.mapperTreeNumericDatatypes = {
      Inner: 'Continuous',
      Outer1: 'Continuous',
      Outer2: 'Continuous',
      Outer3: 'Continuous',
      Outer4: 'Continuous',
      Outer5: 'Continuous'
    };
    window.mapperTreeDiscreteDatatypes = {
      Inner: 'Discrete',
      Outer1: 'Discrete',
      Outer2: 'Discrete',
      Outer3: 'Discrete',
      Outer4: 'Discrete',
      Outer5: 'Discrete'
    };
    Tree_datatypes_dict = $.extend({}, window.mapperTreeNumericDatatypes);

    Tree_textlegend_styling = {
      layoutMode: 'row',
      columns: 2,
      sortDirection: 'Vertically',
      TreeLegendPosition: 'Top',
      Fontsize: '14px'
    };

    ShowLegend = true;
    TreeLegendPosition = 'Top';

    styling_circles = {
      starter: 1,
      clean: true,
      gradient: true,
      circle_spacer: 22,
      circle_size: 12,
      mode: 'Numeric',
      skipNumericViewBoxAdjust: true
    };

    maxLeafNodeLength_scaler = 10;

    window.TREE_UI = window.TREE_UI || { layout: 'Tree - Circular', leafLabelType: 'Protein' };
    $('#mapper-tree-input-tbody').empty();
    mapperTreeAppendRow();
    mapperTreeSetInputMode('numeric');
    mapperTreeSyncLeafLabelUi('IUPHAR');
    mapperTreeUpdateSortHeaders();

    /** Legend toggle */
    $('#legendToggleBtn')
      .off('click.mapperTree')
      .on('click.mapperTree', function () {
        ShowLegend = !ShowLegend;
        $(this).text(ShowLegend ? 'Shown' : 'Hidden');
        $(this).toggleClass('btn-success', ShowLegend);
        $(this).toggleClass('btn-danger', !ShowLegend);
        mapperTreeRedrawNow();
      });

    $('#toggleLegendPosition')
      .off('click.mapperTree')
      .on('click.mapperTree', function () {
        var cur = TreeLegendPosition === 'Bottom' ? 'Bottom' : 'Top';
        var next = cur === 'Top' ? 'Bottom' : 'Top';
        TreeLegendPosition = next;
        if (Tree_textlegend_styling) {
          Tree_textlegend_styling.TreeLegendPosition = next;
        }
        $(this).text(next);
        mapperTreeRedrawNow();
      });
    $('#toggleLegendPosition').text(TreeLegendPosition || 'Top');

    $('#mapper-tree-circle-size-slider')
      .off('input.mapperTree')
      .on('input.mapperTree', function () {
        $('#mapper-tree-circle-size-val').text(String($(this).val()));
        mapperTreeScheduleRedraw();
      });
    $('#mapper-tree-circle-spacer-slider')
      .off('input.mapperTree')
      .on('input.mapperTree', function () {
        $('#mapper-tree-circle-spacer-val').text(String($(this).val()));
        mapperTreeScheduleRedraw();
      });

    $('#classFontSizeSlider, #ligandTypeFontSizeSlider, #receptorFamilyFontSizeSlider, #receptorFontSizeSlider')
      .off('input.mapperTree')
      .on('input.mapperTree', function () {
        var idBase = $(this).attr('id').replace('Slider', 'Value');
        $('#' + idBase).text($(this).val());
        mapperTreeScheduleRedraw();
      });

    $(document).off(
      'input.mapperTree blur.mapperTree change.mapperTree',
      '#mapper-tree-input-tbody input, #mapper-tree-input-tbody textarea'
    ).on(
      'input.mapperTree blur.mapperTree change.mapperTree',
      '#mapper-tree-input-tbody input, #mapper-tree-input-tbody textarea',
      function () {
        $('#mapper-tree-clear-rows').removeClass('mapper20-clear-clean');
        mapperTreeEnsureTrailingBlankRow();
        mapperTreeRefreshInnerSwatches();
        mapperTreeSyncRemoveButtons();
        mapperTreeCompactReceptorRowsAfterInput();
        mapperTreeScheduleRedraw();
      }
    );

    $('#mapper-tree-clear-rows')
      .off('click.mapperTree')
      .on('click.mapperTree', function () {
        var $clearButton = $(this);
        mapperTreeDestroyAllRows();
        $('#mapper-tree-input-tbody').empty();
        $('#mapper-tree-messages').empty();
        mapperTreeAppendRow();
        mapperTreeCompactReceptorRowsAfterInput();
        $clearButton.addClass('mapper20-clear-clean').blur();
        mapperTreeRedrawNow();
      });

    $('#mapper-tree-mode-numeric-btn')
      .off('click.mapperTree')
      .on('click.mapperTree', function () {
        mapperTreeSetInputMode('numeric');
      });
    $('#mapper-tree-mode-labels-btn')
      .off('click.mapperTree')
      .on('click.mapperTree', function () {
        mapperTreeSetInputMode('text');
      });

    $('#mapper-tree-demo-rows')
      .off('click.mapperTree')
      .on('click.mapperTree', function (eDemo) {
        eDemo.preventDefault();
        mapperTreeFillDemoRows();
      });

    $('#mapper-tree-input-table')
      .off('click.mapperTreeRemove', '.mapper20-remove-row')
      .on('click.mapperTreeRemove', '.mapper20-remove-row', function () {
        var $trRemove = $(this).closest('tr');
        if (mapperTreeRowBlank($trRemove) && $trRemove.is(':last-child')) {
          return;
        }
        mapperTreeDestroyRowAc($trRemove);
        $trRemove.remove();
        $('#mapper-tree-clear-rows').removeClass('mapper20-clear-clean');
        mapperTreeEnsureTrailingBlankRow();
        mapperTreeRefreshInnerSwatches();
        mapperTreeCompactReceptorRowsAfterInput();
        mapperTreeScheduleRedraw();
      });

    $('#mapper-tree-input-table')
      .off('click.mapperTreeSort', 'th.mapper20-sortable-head')
      .on('click.mapperTreeSort', 'th.mapper20-sortable-head', function () {
        mapperTreeSortSerializedRows($(this).attr('data-mapper-tree-sort-col'));
      });

    $('#mapper-tree-input-table')
      .off('keydown.mapperTreeSort', 'th.mapper20-sortable-head')
      .on('keydown.mapperTreeSort', 'th.mapper20-sortable-head', function (eSort) {
        if (eSort.which === 13 || eSort.which === 32) {
          eSort.preventDefault();
          mapperTreeSortSerializedRows($(this).attr('data-mapper-tree-sort-col'));
        }
      });

    $('#mapper-tree-input-table')
      .off('keydown.mapperTreeTab', '.mapper20-in-receptor, .mapper20-receptor-html-view, .mapper-tree-inner, .mapper-tree-o1, .mapper-tree-o2, .mapper-tree-o3, .mapper-tree-o4')
      .on('keydown.mapperTreeTab', '.mapper20-in-receptor, .mapper20-receptor-html-view, .mapper-tree-inner, .mapper-tree-o1, .mapper-tree-o2, .mapper-tree-o3, .mapper-tree-o4', function (eTab) {
        if (eTab.which !== 9 || eTab.shiftKey) {
          return;
        }
        if ($(this).hasClass('mapper20-in-receptor') && $(this).data('ui-autocomplete')) {
          var $menu = $(this).autocomplete('widget');
          if ($menu && $menu.is(':visible') && $menu.find('.ui-state-focus, .ui-state-active').length) {
            return;
          }
        }
        eTab.preventDefault();
        mapperTreeFocusNextTableCellFrom($(this));
      });

    $('.mapper-tree-leaf-btn').on('click.mapperTreeLeaf', function (eX) {
      eX.preventDefault();
      var v = ($(this).attr('data-value') || 'IUPHAR').trim();
      mapperTreeSyncLeafLabelUi(v);
      mapperTreeScheduleRedraw();
    });

    $('#mapper-tree-input-table').on(
      'paste.mapperTreePaste',
      function (ePz) {
        var evPz = ePz.originalEvent || ePz;
        var textPz = evPz.clipboardData ? evPz.clipboardData.getData('text/plain') : '';
        if (!textPz || textPz.indexOf('\t') === -1) {
          return;
        }
        if (typeof textPz === 'undefined') {
          return;
        }
        ePz.preventDefault();
        $('#mapper-tree-clear-rows').removeClass('mapper20-clear-clean');
        suppressRedraw = true;
        textPz.split(/\r?\n/).forEach(function (ln) {
          if (!ln.trim()) {
            return;
          }
          var $trPZ = mapperTreeFindBlankRow();
          if (!$trPZ.length) {
            mapperTreeAppendRow(true);
            $trPZ = $('#mapper-tree-input-tbody tr').last();
          }
          var ps = mapperTreePasteSplit(ln);
          mapperTreeDestroyAc($trPZ.find('.mapper20-in-receptor'));
          $trPZ.find('.mapper20-receptor-input-wrap .mapper20-in-receptor').remove();
          var $tx = $(
            '<textarea class="form-control input-sm mapper20-in-receptor" rows="1" autocomplete="off" spellcheck="false"></textarea>'
          ).val(ps.r || '');
          $trPZ.find('.mapper20-receptor-input-wrap').prepend($tx);
          mapperTreeBindAc($tx);

          var restPZ = [];
          if (Array.isArray(ps.rest)) {
            restPZ = ps.rest;
          } else if (ps.rest != null && String(ps.rest).trim()) {
            restPZ = String(ps.rest)
              .split(';')
              .map(function (s) {
                return s.trim();
              });
          }
          if (restPZ[0]) {
            $trPZ.find('.mapper-tree-inner').val(restPZ[0]);
          }
          if (restPZ[1]) {
            $trPZ.find('.mapper-tree-o1').val(restPZ[1]);
          }
          if (restPZ[2]) {
            $trPZ.find('.mapper-tree-o2').val(restPZ[2]);
          }
          if (restPZ[3]) {
            $trPZ.find('.mapper-tree-o3').val(restPZ[3]);
          }
          if (restPZ[4]) {
            $trPZ.find('.mapper-tree-o4').val(restPZ[4]);
          }

          var upU = ps.r ? ps.r.trim().toUpperCase() : '';
          if (upU && window.MAPPER20_RESOLVE && window.MAPPER20_RESOLVE[upU]) {
            mapperTreeSetResolved($trPZ, window.MAPPER20_RESOLVE[upU]);
          }
        });
        suppressRedraw = false;
        mapperTreeEnsureTrailingBlankRow();
        mapperTreeSyncRemoveButtons();
        mapperTreeCompactReceptorRowsAfterInput();
        mapperTreeScheduleRedraw();
      }
    );

    if (typeof window.mapper20InitGpcromePickerModal === 'function') {
      window.mapper20InitGpcromePickerModal({
        pickerRows: $.isArray(window.MAPPER20_GPCROME_PICKER_ROWS) ? window.MAPPER20_GPCROME_PICKER_ROWS : [],
        maxRows: MAPPER_TREE_MAX_ROWS,
        onAdd: function (entryIds) {
          suppressRedraw = true;
          for (var ix = 0; ix < (entryIds || []).length; ix++) {
            var idPz = entryIds[ix];
            if ($('#mapper-tree-input-tbody tr').length >= MAPPER_TREE_MAX_ROWS && !mapperTreeFindBlankRow().length) {
              break;
            }
            var $rowPz = mapperTreeFindBlankRow();
            if (!$rowPz.length) {
              mapperTreeAppendRow(true);
              $rowPz = $('#mapper-tree-input-tbody tr').last();
            }
            mapperTreeSetResolved($rowPz, idPz);
          }
          suppressRedraw = false;
          $('#mapper-tree-clear-rows').removeClass('mapper20-clear-clean');
          mapperTreeEnsureTrailingBlankRow();
          mapperTreeCompactReceptorRowsAfterInput();
          mapperTreeScheduleRedraw();
        }
      });
    }

    mapperTreeRedrawNow();

    /** Resume editing receptor from resolved HTML capsule (delegated once). */
    $(document)
      .off('click.mapperTreeHtmlEdit', '#mapper-tree-input-tbody .mapper20-receptor-html-view')
      .on(
        'click.mapperTreeHtmlEdit',
        '#mapper-tree-input-tbody .mapper20-receptor-html-view',
        function () {
          var $trEv = $(this).closest('tr');
          mapperTreeDestroyAc($trEv.find('.mapper20-in-receptor'));
          var hidEv = ($trEv.find('.mapper20-receptor-entry').val() || '').trim();
          $(this).hide().empty();
          var $inp2 = $(
            '<textarea class="form-control input-sm mapper20-in-receptor" rows="1" autocomplete="off" spellcheck="false"></textarea>'
          );
          var metaEv = hidEv && window.MAPPER20_ENTRY_META && window.MAPPER20_ENTRY_META[hidEv];
          $inp2.val((metaEv && metaEv.name_plain) || '');
          $trEv.find('.mapper20-receptor-input-wrap .mapper20-in-receptor').remove();
          $trEv.find('.mapper20-receptor-input-wrap').prepend($inp2);
          $trEv.find('.mapper20-receptor-entry').val('');
          mapperTreeBindAc($inp2);
          $inp2.show().focus();
          mapperTreeSyncClearBtn($trEv);
          mapperTreeSyncRemoveButtons();
          mapperTreeCompactReceptorRowsAfterInput();
        }
      );
  }

  if (typeof window.mapper20ResolveEntry !== 'function' && window.MAPPER20_RESOLVE) {
    window.mapper20ResolveEntry = function (raw) {
      if (!raw || !String(raw).trim()) {
        return null;
      }
      return window.MAPPER20_RESOLVE[String(raw).trim().toUpperCase()] || null;
    };
  }

  $(function () {
    if (!window.MAPPER_TREE_BOOT) {
      window.MAPPER_TREE_BOOT = {};
    }
    mapperBoot();
  });
})(jQuery);
