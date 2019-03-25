window.zoomHeatmap = {};


function toggleFullScreen(fullScreenElement) {
    if (!document.mozFullScreen && !document.webkitFullScreen) {
        if (fullScreenElement.mozRequestFullScreen) {
            fullScreenElement.mozRequestFullScreen();
        } else {
            fullScreenElement.webkitRequestFullScreen(Element.ALLOW_KEYBOARD_INPUT);
        }
    } else {
        if (document.mozCancelFullScreen) {
            document.mozCancelFullScreen();
        } else {
            document.webkitCancelFullScreen();
        }
    }
}

function initializeGoButton(selector, generic=false) {
    $(selector + ' .go-button').click(function() {
        var pdb = JSON.parse($('#pdb-input').val());
        //var pdb = ['2rh1', '3SN6', '5JQH', '3PDS', '3NYA', '3NY9', '3NY8', '6MXT', '5D5A', '5D5B', '4LDL', '4LDO', '4LDE', '4QKX', '3D4S', '5D6L', '3P0G', '5X7D', '4GBR', '4GBR']

        //var segments = JSON.parse($(selector + ' .segments-input').val());
        var segments = ['TM1','TM2','TM3','TM4','TM5','TM6','TM7','TM1','ICL1','ECL1','ICL2','ECL2','ICL3','ECL3','N-term','C-term'];
        if (pdb.length > 0 && segments.length > 0) {
            renderTable(pdb);
        }
    });
}

function initializeFullscreenButton(selector) {
    var fullScreenElement = $(selector + ' .heatmap-container').get(0);
    $(selector + ' .btn-fullscreen').click(function() {
        toggleFullScreen(fullScreenElement);
    });
}

function thisPDB(elem) {
    var group = $(elem).closest('.tableview').attr('group-number');
    var ReceptorName = $(elem).attr('long');
    var pdbName = $(elem).attr('id');
    //$('.pdb_selected').not(elem).prop("checked",false);

    var pdbs = [];
    if (group==0){
      $('.pdb_selected:checked', oTable["firsttable"].cells().nodes()).each(function() {
          pdbs.push($(this).attr('id'));
      });
      if (pdbs.length==1 && $(elem).prop("checked")) {
          // Update view
          $(".crystal-count:visible").html(ReceptorName + ' - ' + pdbName + ' selected.');
      } else if (pdbs.length>=1){
        // Update view
        $(".crystal-count:visible").html(pdbs.length + ' structures selected.');
      } else {
          // Update view
          $(".crystal-count:visible").html('No structure selected.');
      }
      $('#pdb-input').val(JSON.stringify(pdbs));
    } else {
      $('.pdb_selected:checked', oTable["secondtable"].cells().nodes()).each(function() {
          pdbs.push($(this).attr('id'));
      });
      if (pdbs.length==1 && $(elem).prop("checked")) {
          // Update view
          $(".crystal-count:visible").html(ReceptorName + ' - ' + pdbName + ' selected.');
      } else if (pdbs.length>=1){
        // Update view
        $(".crystal-count:visible").html(pdbs.length + ' structures selected.');
      } else {
          // Update view
          $(".crystal-count:visible").html('No structure selected.');
      }
      $('#second-input').val(JSON.stringify(pdbs));
    }
}

$.fn.dataTable.ext.order['dom-checkbox'] = function  ( settings, col )
    {
        return this.api().column( col, {order:'index'} ).nodes().map( function ( td, i ) {
            return $('input', td).prop('checked') ? '1' : '0';
        } );
    };

var oTable = [];
function showPDBtable(element, table) {
    if ( ! $.fn.DataTable.isDataTable( element+' .tableview table' ) ) {
        oTable[table] = $(element+' .tableview table').DataTable({
        'scrollX': true,
        // 'autoWidth': true,
        scrollY:        '80vh',
        // scrollCollapse: true,
        paging:         false,
        columnDefs: [
            { targets: 'no-sort', orderable: false }
        ],
        "aaSorting": [],
            "columns": [
                        null,
                        null,
                        null,
                        null,
                        null,
                        null,
                        null,
                        null,
                        { "orderDataType": "dom-checkbox" }
                    ]
        });

        yadcf.init(oTable[table],
        [
            {
                column_number : 0,
                filter_type: "multi_select",
                select_type: 'select2',
                filter_default_label: "Class",
                filter_reset_button_text: false,
            },
            {
                column_number : 1,
                filter_type: "multi_select",
                select_type: 'select2',
                select_type_options: {
                    width: '70px'
                },
                filter_default_label: "PDB",
                filter_reset_button_text: false,
            },
            {
                column_number : 2,
                filter_type: "multi_select",
                select_type: 'select2',
                column_data_type: "html",
                html_data_type: "text",
                filter_default_label: "Receptor",
                filter_match_mode : "exact",
                filter_reset_button_text: false,
            },
            {
                column_number : 3,
                filter_type: "multi_select",
                select_type: 'select2',
                html_data_type: "text",
                select_type_options: {
                    width: '150px'
                },
                filter_default_label: "Family",
                filter_match_mode : "exact",
                filter_reset_button_text: false,
            },
            {
                column_number : 4,
                filter_type: "multi_select",
                select_type: 'select2',
                filter_default_label: "Species",
                filter_reset_button_text: false,
            },
            {
                column_number : 5,
                filter_type: "multi_select",
                select_type: 'select2',
                select_type_options: {
                    minimumResultsForSearch: -1 // remove search box
                },
                filter_default_label: "State",
                column_data_type: "html",
                html_data_type: "text",
                filter_match_mode : "exact",
                filter_reset_button_text: false,

            },
            {
                column_number : 6,
                filter_type: "multi_select",
                select_type: 'select2',
                filter_default_label: "Representative",
                filter_reset_button_text: false,

            },
        ],
        {
            cumulative_filtering: false
        }
    );

    yadcf.exResetAllFilters(oTable[table]);

    };
}


function rgb2hex(r,g,b) {

    if (r.length == 1)
        r = '0' + r;

    if (g.length == 1)
        g = '0' + g;

    if (b.length == 1)
        b = '0' + b;

    return '#' + r + g + b;
}

function numberToColor(max, value, neg_and_pos = false) {
    if (neg_and_pos) {
      value = value + max
      max = max*2
    }

    if (value > max)
      value = max

    var hexc = 255/max;

    if(value<(max/3)){
        return rgb2hex("00","00",Math.floor((max-value)*hexc).toString(16));
    } else if (value < ((2*max)/3)) {
        return rgb2hex(Math.floor(value*hexc).toString(16),Math.floor((max-Math.abs(value-max/2))*hexc).toString(16),Math.floor((max-value)*hexc).toString(16));
    } else {
        return rgb2hex(Math.floor(value*hexc).toString(16),"00","00");
    }
}

function numberToColor3(max,value, neg_and_pos = false) {
    if (neg_and_pos) {
      value = value + max
      max = max*2
    }

    if (value > max)
      value = max
    if (value < 0)
      value = 0

    return colorGradient(value/max, {red:255, green:0, blue: 0}, {red:255, green:255, blue: 255}, {red:0, green:0, blue: 255})
}

function numberToColor2(max,value, neg_and_pos = false) {
    if (neg_and_pos) {
      value = value + max
      max = max*2
    }

    if (value > max)
      value = max
    if (value < 0)
      value = 0

    return colorGradient(value/max, {red:255, green:255, blue: 255}, {red:0, green:0, blue: 255})
}

function colorGradient(fadeFraction, rgbColor1, rgbColor2, rgbColor3) {
    var color1 = rgbColor1;
    var color2 = rgbColor2;
    var fade = fadeFraction;

    // Do we have 3 colors for the gradient? Need to adjust the params.
    if (rgbColor3) {
      fade = fade * 2;

      // Find which interval to use and adjust the fade percentage
      if (fade >= 1) {
        fade -= 1;
        color1 = rgbColor2;
        color2 = rgbColor3;
      }
    }

    var diffRed = color2.red - color1.red;
    var diffGreen = color2.green - color1.green;
    var diffBlue = color2.blue - color1.blue;

    var gradient = {
      red: parseInt(Math.floor(color1.red + (diffRed * fade)), 10),
      green: parseInt(Math.floor(color1.green + (diffGreen * fade)), 10),
      blue: parseInt(Math.floor(color1.blue + (diffBlue * fade)), 10),
    };

    //return 'rgb(' + gradient.red + ',' + gradient.green + ',' + gradient.blue + ')';
    return rgb2hex(gradient.red.toString(16),gradient.green.toString(16),gradient.blue.toString(16));
  }



var stage = [];
var color_schemes = [];
var schemeId_grey
var chain_selection = ""
function createNGLview(mode,pdb, pdb2, pdbs = false) {
    var gpcr_rep
    $("#ngl-"+mode).html("");
    stage[mode] = new NGL.Stage( "ngl-"+mode, { backgroundColor: "white" } );

    var pdb_data
    var blue_colors = ['#f7fcf0','#e0f3db','#ccebc5', '#a8ddb5',    '#7bccc4',    '#4eb3d3', '#2b8cbe',    '#0868ac',    '#084081']
    var reps = {} // store ngl representations
    var original_o

    $.getJSON( "/contactnetwork/pdb/"+pdb,
        function( data ) {
        var highlight = ['TM1', 'TM2', 'TM3', 'TM4', 'TM5', 'TM6', 'TM7', 'H8'];
        var segments_sets = {}
        highlight.forEach( function(e){
            segments_sets[e] = ((e in data['segments']) ? data['segments'][e].join(", ") : "")
        });

        pdb_data = data;
        color_schemes['blue'] = NGL.ColormakerRegistry.addSelectionScheme([
                [blue_colors[1], segments_sets[highlight[0]]],
                [blue_colors[2], segments_sets[highlight[1]]],
                [blue_colors[3], segments_sets[highlight[2]]],
                [blue_colors[4], segments_sets[highlight[3]]],
                [blue_colors[5], segments_sets[highlight[4]]],
                [blue_colors[6], segments_sets[highlight[5]]],
                [blue_colors[7], segments_sets[highlight[6]]],
                [blue_colors[8], segments_sets[highlight[7]]],
                [ "white", "*" ]
                ])

        color_schemes['grey'] = NGL.ColormakerRegistry.addSelectionScheme([
                ["#ccc", segments_sets[highlight[0]]],
                ["#bbb", segments_sets[highlight[1]]],
                ["#aaa", segments_sets[highlight[2]]],
                ["#888", segments_sets[highlight[3]]],
                ["#666", segments_sets[highlight[4]]],
                ["#444", segments_sets[highlight[5]]],
                ["#333", segments_sets[highlight[6]]],
                ["#111", segments_sets[highlight[7]]],
                [ "white", "*" ]
                ])

        var bbangle_color = [];
        var scangle_color = [];
        var hsecolor = [];
        var sasacolor = [];
        var phicolor = [];
        var psicolor = [];
        var phipsicolor = [];
        var thetacolor = [];
        var taucolor = [];

        var colorstring;
        var ctemp;
        var medmax;
        var score = [0,0,0,0,0];
        var axis = 100; //something larger than possible
        var j = 0;


//         make variable
        var wlength = 5;
        var cutoff = 100;

        wlength = $('#window-input').val();
        cutoff  = $('#score-input').val();

//         var max = 0;
//         residue_data.forEach(function(e){
//             if (max < e[3]){
//                 max = e[3]
//             }
//         }

        // groups
        residue_data.forEach(function(e){
          if (Array.isArray(e[2])){
            if (pdb_data["gn_map"].indexOf(e[0]) >= 0) {
              gn = pdb_data["gn_map"].indexOf(e[0])
              ngl_selection = ":" + pdb_data['chain'] + " and " + pdb_data["only_gn"][gn]

              if (Math.abs(e[2][2]-e[2][0])>20)
                bbangle_color.push([numberToColor2(90,Math.abs(e[2][2]-e[2][0])), ngl_selection])
              if (Math.abs(e[3][2]-e[3][0])>20){
                scangle_color.push([numberToColor2(90,Math.abs(e[3][2]-e[3][0])), ngl_selection])
              }
              if (Math.abs(e[4][2]-e[4][0])>5)
                hsecolor.push([numberToColor3(10, e[4][2]-e[4][0], true), ngl_selection])
              if (Math.abs(e[5][2]-e[5][0])>20)
                sasacolor.push([numberToColor3(100, e[5][2]-e[5][0], true), ngl_selection])
              if (Math.abs(e[6][2]-e[6][0])>20)
                phicolor.push([numberToColor3(90, e[6][2]-e[6][0], true), ngl_selection])
              if (Math.abs(e[7][2]-e[7][0])>20)
                psicolor.push([numberToColor3(90, e[7][2]-e[7][0], true), ngl_selection])
              if (Math.abs(e[8][2]-e[8][0])>10)
                thetacolor.push([numberToColor3(45, e[8][2]-e[8][0], true), ngl_selection])
              if (Math.abs(e[9][2]-e[9][0])>10)
                taucolor.push([numberToColor3(45, e[9][2]-e[9][0], true), ngl_selection])
            }
          } else {
            bbangle_color.push([numberToColor2(140,e[2]-40) , chain_selection+e[1]])
            scangle_color.push([numberToColor2(140,e[3]-40) , chain_selection+e[1]])
            hsecolor.push([numberToColor(40, e[4]) , chain_selection+e[1]])
            sasacolor.push([numberToColor(100, e[5]) , chain_selection+e[1]])

            // TESTING : z-scoring
            //e[6] = (e[6]+67.479)/15 //20.658
            tmp = e[6]
            if (tmp<-70) //3-10 helix
              tmp = 3
            /*else if (tmp<-60) //pi helix
              tmp = -3*/
            else
              tmp = 0

            if (Math.abs(tmp)>1){
              phicolor.push([numberToColor3(3, tmp, true) , chain_selection+e[1]])
              phipsicolor.push([numberToColor3(3, tmp, true) , chain_selection+e[1]])
            }

            // TESTING : z-scoring
            //e[7] = (e[7]+33.478)/30 //34.245
            tmp = e[7]
            if (tmp>-10) //3-10 helix
              tmp = 3
            else if (tmp<-60) //pi helix
              tmp = -3
            else
              tmp = 0

            if (Math.abs(tmp)>1){
              psicolor.push([numberToColor3(3, tmp, true) , chain_selection+e[1]])
              phipsicolor.push([numberToColor3(3, tmp, true) , chain_selection+e[1]])
            }

            // TODO: normalize all values before coloring -> we can utilize the same scheme for all values
            if (e[8]!=null){
              // TESTING : z-scoring
              tmp = (e[8]-93.686)/7.462

              // emphasize negative values - diff. distribution -> zscore not sufficient
              if (tmp < 0){
                tmp = tmp * 1.2
              }
              if (Math.abs(tmp)>1){
                thetacolor.push([numberToColor3(3, tmp, true) , chain_selection+e[1]])
              }
            }

            if (e[9]!=null){
              // TESTING: absolute for tau + diff max + correction
              tmp = (e[9]-44.066)/25 // was 35.866
              if (Math.abs(tmp)>1)
                taucolor.push([numberToColor3(3, tmp, true) , chain_selection+e[1]])
            }
          }
        });

        // Base coloring -> white
        bbangle_color.push([ "white", "*" ]);
        scangle_color.push([ "white", "*" ]);
        hsecolor.push([ "white", "*" ]);
        sasacolor.push([ "white", "*" ]);
        phicolor.push([ "white", "*" ]);
        psicolor.push([ "white", "*" ]);
        phipsicolor.push([ "white", "*" ]);
        thetacolor.push([ "white", "*" ]);
        taucolor.push([ "white", "*" ]);

        // Add to coloring schemes
        color_schemes['BB-angles'] = NGL.ColormakerRegistry.addSelectionScheme(bbangle_color)
        color_schemes['SC-angles'] = NGL.ColormakerRegistry.addSelectionScheme(scangle_color)
        color_schemes['hse'] = NGL.ColormakerRegistry.addSelectionScheme(hsecolor)
        color_schemes['sasa'] = NGL.ColormakerRegistry.addSelectionScheme(sasacolor)
        color_schemes['phi'] = NGL.ColormakerRegistry.addSelectionScheme(phicolor)
        color_schemes['psi'] = NGL.ColormakerRegistry.addSelectionScheme(psicolor)
        color_schemes['phi_psi'] = NGL.ColormakerRegistry.addSelectionScheme(phipsicolor)
        color_schemes['theta'] = NGL.ColormakerRegistry.addSelectionScheme(thetacolor)
        color_schemes['tau'] = NGL.ColormakerRegistry.addSelectionScheme(taucolor)

        if (pdb2.length > 0) {

            $.get('angledata?pdbs[]='+pdb2[0], function(secondArray) {
                var second_residues = secondArray["data"];

                scnd_angle = []
                scnd_scangle = []
                scnd_hse = []
                scnd_sasa = []
                scnd_phi = []
                scnd_psi = []
                scnd_theta = []
                scnd_tau = []

                second_residues.forEach(function(scnd){
                    residue_data.forEach(function(e){
                        if (e[0] == scnd[0]){
                            if (Math.abs(e[2] - scnd[2])>=15)
                              scnd_angle.push([numberToColor3(50, e[2] - scnd[2], true) , ""+e[1]]);
                            if (Math.abs(e[3] - scnd[3])>=15)
                              scnd_scangle.push([numberToColor3(50, e[3] - scnd[3], true) , ""+e[1]]);
                            if (Math.abs(e[4] - scnd[4])>=3)
                              scnd_hse.push([numberToColor3(10, e[4] - scnd[4], true) , ""+e[1]]);
                            if (Math.abs(e[5] - scnd[5])>=10)
                              scnd_sasa.push([numberToColor3(50, e[5] - scnd[5], true) , ""+e[1]]);
                            if (Math.abs(e[6] - scnd[6])>=15)
                              scnd_phi.push([numberToColor3(60, e[6] - scnd[6], true) , ""+e[1]]);
                            if (Math.abs(e[7] - scnd[7])>=15)
                              scnd_psi.push([numberToColor3(60, e[7] - scnd[7], true) , ""+e[1]]);
                            if (Math.abs(e[8] - scnd[8])>=15)
                              scnd_theta.push([numberToColor3(60, e[8] - scnd[8], true) , ""+e[1]]);
                            if (Math.abs(e[9] - scnd[9])>=15)
                              scnd_tau.push([numberToColor3(60, e[9] - scnd[9], true) , ""+e[1]]);
                        }
                    });
                });

                scnd_angle.push([ "white", "*" ]);
                scnd_scangle.push([ "white", "*" ]);
                scnd_hse.push([ "white", "*" ]);
                scnd_sasa.push([ "white", "*" ]);
                scnd_phi.push([ "white", "*" ]);
                scnd_psi.push([ "white", "*" ]);
                scnd_theta.push([ "white", "*" ]);
                scnd_tau.push([ "white", "*" ]);

                color_schemes['scnd_angle'] = NGL.ColormakerRegistry.addSelectionScheme(scnd_angle)
                color_schemes['scnd_scangle'] = NGL.ColormakerRegistry.addSelectionScheme(scnd_scangle)
                color_schemes['scnd_hse'] = NGL.ColormakerRegistry.addSelectionScheme(scnd_hse)
                color_schemes['scnd_sasa'] = NGL.ColormakerRegistry.addSelectionScheme(scnd_sasa)
                color_schemes['scnd_phi'] = NGL.ColormakerRegistry.addSelectionScheme(scnd_phi)
                color_schemes['scnd_psi'] = NGL.ColormakerRegistry.addSelectionScheme(scnd_psi)
                color_schemes['scnd_theta'] = NGL.ColormakerRegistry.addSelectionScheme(scnd_theta)
                color_schemes['scnd_tau'] = NGL.ColormakerRegistry.addSelectionScheme(scnd_tau)

            });

        }


        var stringBlob = new Blob( [ pdb_data['pdb'] ], { type: 'text/plain'} );
        stage[mode].loadFile( stringBlob, { ext: "pdb" }  ).then( function( o ){
            original_o = o
            chain_selection = ":" + pdb_data['chain'] + " and "

            gpcr_rep = o.addRepresentation( "cartoon", {
              sele: ":"+pdb_data['chain']+" and ("+pdb_data['only_gn'].join(", ")+") and (.CA)",
              // radiusType: '',
              radiusSize: 1,
              radiusScale: 0.7,
              // color: "atomindex",
              // colorScale: "Accent",
              // color: "residueindex",
              // colorScale: "greys",
              color: color_schemes['grey'],
              metalness: 0,
              colorMode: "hcl",
              roughness: 1,
              opacity: 1,
              depthWrite: true
            });

            reps.ball_all = o.addRepresentation("ball+stick", {
                sele: ":"+pdb_data['chain']+" and sidechainAttached",
                visible: false
                })

            reps.ball = o.addRepresentation("ball+stick", {
                sele: ":"+pdb_data['chain']+" and ("+pdb_data['only_gn'].join(", ")+") and sidechainAttached",
                visible: false
                })

            o.autoView(":"+pdb_data['chain']+" and ("+pdb_data['only_gn'].join(", ")+") and (.CA)")

            // mousover and click on datatable row to highlight residue in NGL viewer
            var temprepr;
            $("#single-table-tab-table tbody").on("mouseover", "tr", function(event){
                gn = pdb_data["gn_map"].indexOf(residuetable.row(this).data()[0])
                if (gn > -1) {
                  ngl_selection = ":" + pdb_data['chain'] + " and " + pdb_data["only_gn"][gn]
                  temprepr = o.addRepresentation("ball+stick", {sele: ngl_selection});
                }
            }).mouseout(function(event){
                o.removeRepresentation(temprepr)
            });

            var repr_dict = {}
            $("#single-table-tab-table tbody").on("click", "tr", function(event){
                if(residuetable.row(this).data()[0] in repr_dict){
                    o.removeRepresentation(repr_dict[residuetable.row(this).data()[0]])
                    delete repr_dict[residuetable.row(this).data()[0]]
                    $(this).removeClass("table-selected")
                }else{
                  gn = pdb_data["gn_map"].indexOf(residuetable.row(this).data()[0])
                  if (gn > -1) {
                    ngl_selection = ":" + pdb_data['chain'] + " and " + pdb_data["only_gn"][gn]
                    repr_dict[residuetable.row(this).data()[0]] = o.addRepresentation("ball+stick", {sele: ngl_selection});
                    $(this).addClass("table-selected")
                  }
                }
            });


        } );

    });

    var newDiv = document.createElement("div");
    newDiv.setAttribute("style", "position: absolute; top: 50px; left: 20px")
    var controls = '<div class="controls">'
                    + '<h3>Controls</h3>';

    var optional = ''
    if (pdb2.length >0){
        optional = '<option value="scnd_angle">Δ BB-angle</option>' +
                  '<option value="scnd_scangle">Δ SC-angle</option>' +
                  '<option value="scnd_hse">Δ HSE</option>' +
                  '<option value="scnd_sasa">Δ SASA</option>' +
                  '<option value="scnd_phi">Δ phi</option>' +
                  '<option value="scnd_psi">Δ psi</option>' +
                  '<option value="scnd_theta">Δ theta</option>' +
                  '<option value="scnd_tau">Δ tau</option>'
    }

    if (pdbs){
            controls += '<p>Structure: <select id="ngl_pdb_'+mode+'_ref">';
            for (var i = 0; i < pdbs.length; i++){
                if (pdbs[i]==pdb)
                    controls += '<option value="'+pdbs[i]+'" SELECTED>'+pdbs[i]+'</option>';
                else
                    controls += '<option value="'+pdbs[i]+'">'+pdbs[i]+'</option>';
            }
            controls += '</select></p>';
    }

    controls += '<p>Colors: <select id="ngl_color"><option value="grey">greys</option><option value="blue">blue</option><option value="BB-angles">BB-angle</option><option value="SC-angles">SC-angle</option><option value="hse">hse</option><option value="sasa">sasa</option><option value="phi">phi</option><option value="psi">psi</option><option value="phi_psi">phi + psi</option><option value="theta">theta</option><option value="tau">tau</option>'+ optional +'</select></p>'
                        +'<p>Only GNs: <input type=checkbox id="ngl_only_gns" checked></p>'
                        +'<p>Show all side-chains: <input type=checkbox id="toggle_sidechains"></p>'
                        +'</div>';

    newDiv.innerHTML = controls;

    $("#ngl-"+mode).append(newDiv);

    $("#ngl_pdb_"+mode+"_ref").change(function(e){
        createNGLview(mode, $(this).val(), pdbs);
    });

    $("#"+mode+"-NGL-link").click(function(e){
        $(function() {
            stage[mode].handleResize();
        });
    });


    $("#ngl-"+mode+" #ngl_color").change(function(e){
        gpcr_rep.setParameters({
            color: color_schemes[$(this).val()]
        });
    });

    // TODO: cleanup all NGL toggles and make generic function to handle toggle-dependent showing/hiding
    $("#ngl-"+mode+" #ngl_only_gns").change(function(e){
        if ($(this).prop('checked')) {
            sele = ":"+pdb_data['chain']+" and ("+pdb_data['only_gn'].join(", ")+")";
            // toggle CA spheres
            if ($("#ngl-"+mode+" #highlight_res").prop('checked')){
              reps.int_res_gn.setVisibility(true);
              reps.int_res.setVisibility(false);
            }
            // toggle sidechains
            if ($("#ngl-"+mode+" #toggle_sidechains").prop('checked')){
              reps.ball.setVisibility(true);
              reps.ball_all.setVisibility(false);
            }
        } else {
            sele = ":"+pdb_data['chain'];
            // toggle CA spheres
            if ($("#ngl-"+mode+" #highlight_res").prop('checked')){
              reps.int_res_gn.setVisibility(false);
              reps.int_res.setVisibility(true);
            }

            // toggle sidechains
            if ($("#ngl-"+mode+" #toggle_sidechains").prop('checked')){
              reps.ball.setVisibility(false);
              reps.ball_all.setVisibility(true);
            }
        }

        gpcr_rep.setSelection(sele);
        original_o.autoView();
    });

    $("#ngl-"+mode+" #highlight_res").change(function(e){
        // TODO check the GN toggle
        if ($(this).prop('checked')) {
            if ($("#ngl-"+mode+" #ngl_only_gns").prop('checked'))
            reps.int_res_gn.setVisibility(true);
            else
            reps.int_res.setVisibility(true);
        } else {!$("#ngl-"+mode+" #toggle_interactions").prop('checked')
            reps.int_res_gn.setVisibility(false);
            reps.int_res.setVisibility(false);
        }
    });

    $("#ngl-"+mode+" #toggle_sidechains").change(function(e){
        if ($(this).prop('checked')) {
            if ($("#ngl-"+mode+" #ngl_only_gns").prop('checked'))
            reps.ball.setVisibility(true);
            else
            reps.ball_all.setVisibility(true);
        } else {
            reps.ball_all.setVisibility(false);
            reps.ball.setVisibility(false);
        }
    });

    $("#ngl-"+mode+" #toggle_sidechains_int").change(function(e){
        if ($(this).prop('checked')) {
            if ($("#ngl-"+mode+" #ngl_only_gns").prop('checked'))
            reps.ball_int_gn.setVisibility(true);
            else
            reps.ball_int.setVisibility(true);
        } else {
            reps.ball_int.setVisibility(false);
            reps.ball_int_gn.setVisibility(false);
        }
    });

}

var residue_data
function renderTable(pdb) {
    // corrected ugly hack with ugly hack - just for debugging purposes
    query = 'angledata?'
    for (i in pdb)
      query += "pdbs[]=" + pdb[i] + "&"

    // TODO if multiple values -> show (rounded) range

    $.get(query, function(newDataArray) {
      residue_data = newDataArray["data"]
      residuetable.clear();

      // TODO clean residue data
      row_data = $.extend(true, [], residue_data)
      if (Array.isArray(row_data[0][2])) {
        for (i in row_data) {
          for (j=2; j < row_data[0].length; j++) {
            row_data[i][j] = Math.round(row_data[i][j][2]-row_data[i][j][0])
          }
        }
      }
      residuetable.rows.add(row_data);
      residuetable.draw();

      var second = JSON.parse($('#second-input').val());
      createNGLview("single",pdb[0], second);
    });

}


function initializeResidueTable() {
    residuetable = $("#single-table-tab-table").DataTable();
    yadcf.init(residuetable,
    [
        {
            column_number : 0,
            filter_type: "multi_select",
            select_type: 'select2',
            filter_default_label: "Gen. number",
            filter_reset_button_text: false,
        },
        {
            column_number : 1,
            filter_type: "multi_select",
            select_type: 'select2',
            filter_default_label: "Seq. number",
            filter_reset_button_text: false,
        },
        {
            column_number : 2,
            filter_type: "multi_select",
            select_type: 'select2',
            filter_default_label: "BB-angle",
            filter_reset_button_text: false,
        },
        {
            column_number : 3,
            filter_type: "multi_select",
            select_type: 'select2',
            filter_default_label: "SC-angle",
            filter_reset_button_text: false,
        },
        {
            column_number : 4,
            filter_type: "multi_select",
            select_type: 'select2',
            filter_default_label: "HSE",
            filter_reset_button_text: false,
        },
        {
            column_number : 5,
            filter_type: "multi_select",
            select_type: 'select2',
            filter_default_label: "SASA",
            filter_reset_button_text: false,
        },
        {
            column_number : 6,
            filter_type: "multi_select",
            select_type: 'select2',
            filter_default_label: "phi",
            filter_reset_button_text: false,
        },
        {
            column_number : 7,
            filter_type: "multi_select",
            select_type: 'select2',
            filter_default_label: "psi",
            filter_reset_button_text: false,
        },
        {
            column_number : 8,
            filter_type: "multi_select",
            select_type: 'select2',
            filter_default_label: "theta",
            filter_reset_button_text: false,
        },
        {
            column_number : 9,
            filter_type: "multi_select",
            select_type: 'select2',
            filter_default_label: "tau",
            filter_reset_button_text: false,
        }
    ],
    {
        cumulative_filtering: false
    });

    yadcf.exResetAllFilters(residuetable);
}

$('#single-crystal-pdb-modal-table').on('shown.bs.modal', function (e) {
    showPDBtable('#single-crystal-pdb-modal-table', "firsttable");
})

$('#second-structure-pdb-modal-table').on('shown.bs.modal', function (e) {
    showPDBtable('#second-structure-pdb-modal-table',"secondtable");
})


function initializePdbChooserTables() {
    $.get('/contactnetwork/pdbtabledata', function ( data ) {
      $('#single-crystal-pdb-modal-table .tableview').html(data);
      $('#second-structure-pdb-modal-table .tableview').html(data);

      pdbtabledata = data;
      secondtable = data;
    });
}

function initalizeSingleCrystalView() {

    initializeGoButton('#single-crystal-tab');
    initializeFullscreenButton('#single-crystal-tab');
}

var selectortable
var residuetable
$(document).ready(function() {
    // residue Table
    initializeResidueTable();

    // Get PDBs for table build
    initializePdbChooserTables();

    // Single PDB files
    initalizeSingleCrystalView();
});
