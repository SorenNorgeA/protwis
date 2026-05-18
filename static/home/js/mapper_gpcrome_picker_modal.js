/**
 * GPCRome wheel — receptor picker modal (DataTables + Norges filters).
 * Expects globals: CreateColumnFilters, createDropdownFilters (NorgesDTFilterBuilder.js), jQuery, DataTables.
 */
(function ($) {
  'use strict';

  var pickerState = {
    initialized: false,
    dt: null
  };

  function syncSelectAllCheckbox() {
    if (!pickerState.dt) {
      return;
    }
    var dt = pickerState.dt;
    var rows = dt.rows({ search: 'applied' });
    var checked = 0;
    rows.every(function () {
      var $cb = $(this.node()).find('.mapper20-gpcrome-pick-cb');
      if ($cb.prop('checked')) {
        checked += 1;
      }
    });
    var total = rows.count();
    var $master = $('#mapper20-gpcrome-picker-select-all');
    if (!total || checked === 0) {
      $master.prop({ checked: false, indeterminate: false });
    } else if (checked === total) {
      $master.prop({ checked: true, indeterminate: false });
    } else {
      $master.prop({ checked: false, indeterminate: true });
    }
  }

  function collectCheckedEntryIds() {
    if (!pickerState.dt) {
      return [];
    }
    var out = [];
    pickerState.dt.rows({ search: 'applied' }).every(function () {
      var $cb = $(this.node()).find('.mapper20-gpcrome-pick-cb');
      if ($cb.prop('checked')) {
        var v = $cb.val();
        if (v != null && v !== '') {
          out.push(String(v));
        }
      }
    });
    return out;
  }

  function escapeAttr(val) {
    return String(val == null ? '' : val).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
  }

  function buildDataTable(rows) {
    var columns = [
      {
        data: 'id',
        name: '_sel',
        orderable: false,
        searchable: false,
        render: function (entryId, type /*, row*/) {
          if (type !== 'display') {
            return '';
          }
          var vid = escapeAttr(entryId == null ? '' : entryId);
          return (
            '<input type="checkbox" class="mapper20-gpcrome-pick-cb" value="' +
            vid +
            '" aria-label="Select receptor">'
          );
        }
      },
      {
        data: 'gpcrdb_link',
        name: 'GPCRdb',
        orderable: false,
        searchable: false,
        defaultContent: '',
        render: function (link, type /*, row*/) {
          if (type !== 'display') {
            return '';
          }
          if (!link) {
            return '';
          }
          return (
            '<a href="' +
            escapeAttr(link) +
            '" target="_blank" rel="noopener" title="GPCRdb receptor page">' +
            '<img class="gpcrdb-link" src="/static/home/logo/gpcr/main.png" width="12" height="12" alt="GPCRdb"></a>'
          );
        }
      },
      {
        data: 'name_plain',
        name: 'GtoPdb',
        render: function (plain, type, row) {
          if (type === 'display') {
            if (row && row.name_html) {
              return row.name_html;
            }
            return plain || (row && row.id) || '';
          }
          return plain || '';
        }
      },
      { data: 'gene', name: 'Gene' },
      {
        data: 'uniprot',
        name: 'UniProt',
        render: function (d, type, row) {
          if (type !== 'display') {
            return d || '';
          }
          if (!d) {
            return '-';
          }
          var href =
            (row && row.uniprot_link) || ('https://www.uniprot.org/uniprot/' + d);
          return '<a href="' + escapeAttr(href) + '" target="_blank" rel="noopener">' + d + '</a>';
        }
      },
      { data: 'family', name: 'family' },
      { data: 'class', name: 'class' }
    ];

    var columnDefs = [
      { targets: '_all', className: 'dt-head-center dt-body-center dt-center' },
      { targets: [0], width: '38px', orderable: false, searchable: false },
      {
        targets: [1],
        width: '36px',
        orderable: false,
        searchable: false,
        className: 'dt-head-center dt-body-center dt-center'
      }
    ];

    pickerState.dt = $('#mapper20-gpcrome-picker-table').DataTable({
      dom: "<'row'<'col-sm-12'tr>>" + "<'row'<'col-sm-12'i>>",
      data: rows || [],
      columns: columns,
      columnDefs: columnDefs,
      autoWidth: true,
      processing: false,
      deferRender: true,
      paging: false,
      scrollX: true,
      scrollY: '52vh',
      scrollCollapse: true,
      order: [[4, 'asc']]
    });
    var dt = pickerState.dt;
    var columnFilters = [];
    columnFilters = columnFilters.concat(CreateColumnFilters(dt, 2, 1, 'Multi-select-exact'));
    columnFilters = columnFilters.concat(CreateColumnFilters(dt, 3, 1, 'Multi-select-exact'));
    columnFilters = columnFilters.concat(CreateColumnFilters(dt, 4, 1, 'Multi-select-exact'));
    columnFilters = columnFilters.concat(CreateColumnFilters(dt, 5, 1, 'Multi-select-exact'));
    columnFilters = columnFilters.concat(CreateColumnFilters(dt, 6, 1, 'Multi-select-exact'));
    createDropdownFilters(dt, columnFilters);

    $('#mapper20-gpcrome-picker-table').on('draw.dt', syncSelectAllCheckbox);
    $('#mapper20-gpcrome-picker-table tbody').on('change', '.mapper20-gpcrome-pick-cb', syncSelectAllCheckbox);
  }

  window.mapper20InitGpcromePickerModal = function (opts) {
    opts = opts || {};
    if (!$('#mapper20-gpcrome-picker-table').length) {
      return;
    }

    var rows = $.isArray(opts.pickerRows) ? opts.pickerRows : [];

    $('#mapper20-gpcrome-picker-modal').on('shown.bs.modal', function () {
      if (!pickerState.initialized) {
        buildDataTable(rows);
        pickerState.initialized = true;
      }
      if (pickerState.dt) {
        pickerState.dt.columns.adjust().draw(false);
      }
      syncSelectAllCheckbox();
    });

    $('#mapper20-gpcrome-picker-modal').on('hidden.bs.modal', function () {
      $('#mapper20-gpcrome-picker-select-all').prop({ checked: false, indeterminate: false });
      if (pickerState.dt) {
        pickerState.dt.$('.mapper20-gpcrome-pick-cb').prop('checked', false);
      }
    });

    $('#mapper20-gpcrome-picker-modal').on(
      'change',
      '#mapper20-gpcrome-picker-select-all',
      function () {
        if (!pickerState.dt) {
          return;
        }
        var on = $(this).prop('checked');
        pickerState.dt.rows({ search: 'applied' }).every(function () {
          $(this.node()).find('.mapper20-gpcrome-pick-cb').prop('checked', on);
        });
        syncSelectAllCheckbox();
      }
    );

    $('#mapper20-gpcrome-picker-add').on('click', function () {
      if (!pickerState.dt) {
        return;
      }
      var ids = collectCheckedEntryIds();
      if (!ids.length) {
        alert('Select at least one receptor (filtered rows — use tick boxes).');
        return;
      }
      if (typeof opts.onAdd === 'function') {
        opts.onAdd(ids);
      }
      $('#mapper20-gpcrome-picker-modal').modal('hide');
    });
  };
})(jQuery);
