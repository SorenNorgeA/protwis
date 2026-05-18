/**
 * Mapper 2.0 classification tree — client-side keep_by_names filter, circles payload,
 * debounced redraw (mapper_classification_tree + DrawCircles).
 */
(function ($) {
  'use strict';

  var DEBOUNCE_MS = 140;
  var redrawTimer;

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
      if (meta.name_plain) {
        iuphar = meta.name_plain;
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
      if (rd && rd.length) {
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
        return {
          Protein: (meta.name_plain || item.text || id).trim(),
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
    var baseOptsIn = $.extend(deepClone(ORIG_OPTS), {
      fontSize: fontMerge,
      fontFamily: ORIG_OPTS.fontFamily || 'Palatino',
      radialLeafLabelGap:
        ORIG_OPTS.radialLeafLabelGap != null && isFinite(Number(ORIG_OPTS.radialLeafLabelGap))
          ? Number(ORIG_OPTS.radialLeafLabelGap)
          : 18,
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

    /** Circle size / spacer from Mapper-specific sliders */
    var csEl = $('#mapper-tree-circle-size-slider');
    if (csEl.length) {
      styling_circles.circle_size = 3 + 1 * Number(csEl.val());
    }
    var spEl = $('#mapper-tree-circle-spacer-slider');
    if (spEl.length) {
      styling_circles.circle_spacer =
        styling_circles.circle_size * (2 + 0.5 * Number(spEl.val())) + 1;
    }

    var dictStem = mapperTreeActiveStemDict(btnVal);

    custom_changeLeavesLabels(
      'tree_plot',
      btnVal === 'UniProt' ? 'UniProt' : btnVal === 'Gene' ? 'Gene' : 'IUPHAR',
      dictStem,
      styling_circles
    );

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
  }

  window.mapperTreeRedrawNow = mapperTreeRedrawNow;

  function mapperTreeScheduleRedraw() {
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

  function mapperTreeRefreshInnerSwatches() {
    if (!mapperTreeIsTextMode()) {
      $('#mapper-tree-input-tbody .mapper20-label-swatch').css('background-color', 'transparent');
      return;
    }
    $('#mapper-tree-input-tbody tr').each(function () {
      var $sw = $(this).find('.mapper20-label-swatch');
      var inner = (($(this).find('.mapper-tree-inner').val() || '') + '').trim();
      if (!inner) {
        $sw.css('background-color', '#f5f5f5');
        return;
      }
      var hex = window.MAPPER20_LABEL_COLORS[inner] || mapperTreeDefaultHexForLabelKey(inner);
      $sw.css('background-color', hex);
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
    var outerHas = ['.mapper-tree-o1', '.mapper-tree-o2', '.mapper-tree-o3', '.mapper-tree-o4'].some(function (sel) {
      return (($tr.find(sel).val() || '') + '').trim() !== '';
    });
    return !entry && !inner && !typed && !outerHas;
  }

  function mapperTreeDestroyRowAc($tr) {
    mapperTreeDestroyAc($tr.find('.mapper20-in-receptor'));
  }

  function mapperTreeAppendRow(skipTrail) {
    var tr = $('<tr>');
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
    tr.append($('<td class="mapper20-swatch-cell">').append('<span class="mapper20-label-swatch"></span>'));
    $('#mapper-tree-input-tbody').append(tr);
    mapperTreeBindAc($inp);
    mapperTreeSyncClearBtn(tr);
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

    $(document).on(
      'input.mapperTree blur.mapperTree change.mapperTree',
      '#mapper-tree-input-tbody input, #mapper-tree-input-tbody textarea',
      function () {
        mapperTreeEnsureTrailingBlankRow();
        mapperTreeRefreshInnerSwatches();
        mapperTreeScheduleRedraw();
      }
    );

    $('.mapper-tree-clear-btn')
      .off('click.mapperTree')
      .on('click.mapperTree', function () {
        $('#mapper-tree-input-tbody').empty();
        $('#mapper-tree-messages').empty();
        mapperTreeAppendRow();
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
        textPz.split(/\r?\n/).forEach(function (ln) {
          if (!ln.trim()) {
            return;
          }
          mapperTreeAppendRow(true);
          var $trPZ = $('#mapper-tree-input-tbody tr').last();
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
        mapperTreeEnsureTrailingBlankRow();
        mapperTreeScheduleRedraw();
      }
    );

    if (typeof window.mapper20InitGpcromePickerModal === 'function') {
      window.mapper20InitGpcromePickerModal({
        pickerRows: $.isArray(window.MAPPER20_GPCROME_PICKER_ROWS) ? window.MAPPER20_GPCROME_PICKER_ROWS : [],
        maxRows: MAPPER_TREE_MAX_ROWS,
        onAdd: function (entryIds) {
          function findBlank() {
            var $hit = $();
            $('#mapper-tree-input-tbody tr').each(function () {
              if (mapperTreeRowBlank($(this))) {
                $hit = $(this);
                return false;
              }
            });
            return $hit;
          }
          for (var ix = 0; ix < (entryIds || []).length; ix++) {
            var idPz = entryIds[ix];
            if ($('#mapper-tree-input-tbody tr').length >= MAPPER_TREE_MAX_ROWS && !findBlank().length) {
              break;
            }
            var $rowPz = findBlank();
            if (!$rowPz.length) {
              mapperTreeAppendRow(true);
              $rowPz = $('#mapper-tree-input-tbody tr').last();
            }
            mapperTreeSetResolved($rowPz, idPz);
          }
          mapperTreeEnsureTrailingBlankRow();
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
            '<textarea class="form-control input-sm mapper20-in-receptor" rows="1" autocomplete="off" spellcheck="false">'
          );
          var metaEv = hidEv && window.MAPPER20_ENTRY_META && window.MAPPER20_ENTRY_META[hidEv];
          $inp2.val((metaEv && metaEv.name_plain) || '');
          $trEv.find('.mapper20-receptor-input-wrap .mapper20-in-receptor').remove();
          $trEv.find('.mapper20-receptor-input-wrap').prepend($inp2);
          $trEv.find('.mapper20-receptor-entry').val('');
          mapperTreeBindAc($inp2);
          $inp2.show().focus();
          mapperTreeSyncClearBtn($trEv);
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
