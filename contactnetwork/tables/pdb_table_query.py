from decimal import Decimal

from django.conf import settings
from django.db.models import Prefetch, ExpressionWrapper, Sum, Case, When, IntegerField, Max, Min, BooleanField, Count, FloatField, Value
from django.db.models.functions import Cast, Coalesce, Length

from structure.models import Structure, StructureExtraProteins
from protein.models import Protein, ProteinConformation, ProteinSegment
from interaction.models import StructureLigandInteraction
from signprot.models import SignprotComplex
from structure.templatetags.structure_extras import only_gproteins, only_arrestins, only_fusions, only_antibodies
Alignment = getattr(__import__('common.alignment_' + settings.SITE_NAME, fromlist=['Alignment']), 'Alignment')

def get_best_resolutions(resolutions):
    # get minimum resolution for every receptor/state pair
    best_resolutions = {}
    for r in resolutions:
        key = '{}_{}'.format(r['protein_conformation__protein__parent'], r['state__name'])
        best_resolutions[key] = r['res']
    return best_resolutions

def get_best_signal_coverage(signal_ps, mode):
    best_signal_p = {}
    for ps in signal_ps:
        if mode=='effector':
            key = '{}_{}_{}'.format(ps['structure__protein_conformation__protein'], ps['structure__protein_conformation__protein__parent'], ps['display_name'])
        else:
            key = '{}_{}'.format(ps['structure__protein_conformation__protein__parent'], ps['display_name'])
        best_signal_p[key] = ps['coverage']
    return best_signal_p

def PdbTableDataSource():
    effector_list = set(StructureExtraProteins.objects.values_list('category', flat=True))
    complex_structure_ids = SignprotComplex.objects.values_list('structure', flat=True)
    
    #Fields used by multiple querysets, to avoid repeating them in each queryset
    common_prefetch = [
        "pdb_code",
        "state",
        "stabilizing_agents",
        "structureligandinteraction_set__ligand__ligand_type",
        "structureligandinteraction_set__ligand_role",
        "structure_type",
        "protein_conformation__protein__parent__parent__parent",
        "protein_conformation__protein__parent__family__parent",
        "protein_conformation__protein__parent__family__parent__parent__parent",
        "protein_conformation__protein__parent",
        "protein_conformation__protein__parent__parent",
        "protein_conformation__protein__family__parent",
        "protein_conformation__protein__family__parent__parent__parent",
        "protein_conformation__protein__species",
        "protein_conformation__protein__genes"
    ]

    signal_ps_values = [
        'structure__pdb_code__index', 'structure__protein_conformation__protein__parent',
        'display_name', 'wt_coverage', 'wt_protein__family__parent__parent__name',
        'wt_protein__family__parent__name', 'category', 'note',
    ]

    ligand_qs = StructureLigandInteraction.objects.filter(annotated=True)

    #Dictionaries to hold query sets for each effector, as well as a dictionary to hold the signal protein coverage for each effector
    query_sets_data = {}
    query_sets_signal = {}
    query_sets_resolutions = {}
    query_sets_best_signal_coverage = {}
    extra_proteins_lookup = {}
    bestresolution_lookup = {}

    # Loop through all effectors and create a query set for each effector, as well as a query set for the signal protein coverage
    # Used by arrestin and g-protein databases
    for effector in effector_list:
        temp_data_qs = Structure.objects.all() \
            .prefetch_related(*common_prefetch,
                              Prefetch("ligands", queryset=ligand_qs.prefetch_related('ligand', 'ligand__ligand_type', 'ligand_role')))
        
        temp_data_qs = temp_data_qs.filter(extra_proteins__category__startswith=effector) \
            .prefetch_related('extra_proteins__protein_conformation','extra_proteins__wt_protein') \
            .order_by('extra_proteins__protein_conformation__protein__parent','state') \
            .annotate(coverage_pct=ExpressionWrapper(
                        Cast(Count("protein_conformation__residue", distinct=True),
                                FloatField()) * 100.0 /
                        Coalesce(
                            Length("protein_conformation__protein__parent__sequence"),
                            Length("protein_conformation__protein__sequence"),
                            Value(1.0)
                        ),
                        output_field=IntegerField(),
                    ),
                      is_complex=Case(When(id__in=complex_structure_ids, then=True), default=False, output_field=BooleanField()))

        temp_signal_qs = StructureExtraProteins.objects.filter(category__startswith=effector) \
            .values('structure__pdb_code__index', 'structure__protein_conformation__protein', *signal_ps_values[1:]) \
            .annotate(coverage = Max('wt_coverage'))
        
        query_sets_data[effector] = temp_data_qs
        query_sets_signal[effector] = temp_signal_qs
        extra_proteins_lookup[effector] = {s['structure__pdb_code__index']:s for s in temp_signal_qs}

        query_sets_resolutions[effector] = Structure.objects.all() \
            .values('protein_conformation__protein__parent','state__name') \
            .annotate(res = Min('resolution'))
        
        bestresolution_lookup[effector] = get_best_resolutions(query_sets_resolutions[effector])

        query_sets_best_signal_coverage[effector] = get_best_signal_coverage(temp_signal_qs, 'effector')
    
    # Create a query set for when no specific effector is requested, restrict to experimental structures (Used by the main GPCR site)
    temp_data_qs = Structure.objects.all() \
        .filter(structure_type__origin = "experiment") \
        .prefetch_related(*common_prefetch, 
                            Prefetch("ligands", 
                                     queryset=ligand_qs.filter(structure__structure_type__origin='experiment') \
                                                       .prefetch_related('ligand', 'ligand__ligand_type', 'ligand_role')))
    
    temp_data_qs = temp_data_qs.prefetch_related('extra_proteins__protein_conformation','extra_proteins__wt_protein') \
        .order_by('extra_proteins__protein_conformation__protein__parent','state') \
        .annotate(coverage_pct=ExpressionWrapper(
                        Cast(Count("protein_conformation__residue", distinct=True),
                                FloatField()) * 100.0 /
                        Coalesce(
                            Length("protein_conformation__protein__parent__sequence"),
                            Length("protein_conformation__protein__sequence"),
                            Value(1.0)
                        ),
                        output_field=IntegerField(),
                    ),
                  is_complex=Case(When(id__in=complex_structure_ids, then=True), default=False, output_field=BooleanField()))
    
    temp_signal_qs = StructureExtraProteins.objects.all() \
        .exclude(category__in=['G beta','G gamma']) \
        .values(*signal_ps_values) \
        .annotate(coverage = Max('wt_coverage'))
    extra_proteins_lookup["no_effector"] = {s['structure__pdb_code__index']:s for s in temp_signal_qs}
    
    query_sets_data["no_effector"] = temp_data_qs
    query_sets_signal["no_effector"] = temp_signal_qs
    query_sets_resolutions["no_effector"] = Structure.objects.all() \
        .filter(structure_type__origin='experiment') \
        .values('protein_conformation__protein__parent','state__name') \
        .annotate(res = Min('resolution'))

    bestresolution_lookup["no_effector"] = get_best_resolutions(query_sets_resolutions["no_effector"])

    query_sets_best_signal_coverage["no_effector"] = get_best_signal_coverage(temp_signal_qs, "no_effector")

    result_sets = {}
    identity_lookup = {}
    for effector in query_sets_data.keys():
        data = query_sets_data[effector]
        ep = extra_proteins_lookup[effector]
        #best_signal_p = query_sets_best_signal_coverage[effector]        

        entries = {}
        for s in data:
            entry = {}
            pdb_id = s.pdb_code.index
            if pdb_id in entries:
                continue

            #Setting a short path that addresses af models and regular structures
            #so we don't have to add infinite try/excepts
            if not s.protein_conformation.protein.parent:
                shorted = s.protein_conformation.protein
            else:
                shorted = s.protein_conformation.protein.parent

            entry['pdb_id'] = pdb_id
            entry['uniprot_entry_name'] = shorted.entry_name
            entry['uniprot_accession'] = shorted.accession
            entry['protein_name_short'] = shorted.short()
            entry['receptor_family_short'] = shorted.family.parent.short()
            entry['receptor_class_code'] = shorted.family.parent.parent.parent.shorter()
            entry['species_common_name'] = s.protein_conformation.protein.species.common_name
            gene_obj = shorted.genes.first()
            entry['gene_name'] = gene_obj.name if gene_obj else "-"
            entry['gene_entrez_id'] = gene_obj.entrez_id if gene_obj else "-"
            entry['state'] = s.state.name

            entry['closest_to_human'] = s.closest_to_human

            entry['identity_to_human'] = 100
            if entry['species_common_name'] != 'Human':
                key = 'identity_to_human_{}_{}'.format(shorted.family.slug,s.protein_conformation.protein.species.pk)
                if key in identity_lookup:
                    entry['identity_to_human'] = identity_lookup[key]
                else:
                    try:
                        a = Alignment()
                        ref_p = Protein.objects.get(family = shorted.family, species__common_name = 'Human', sequence_type__slug = 'wt')
                        a.load_reference_protein(ref_p)
                        a.load_proteins([shorted])
                        a.load_segments(ProteinSegment.objects.filter(slug__in=['TM1', 'TM2', 'TM3', 'TM4','TM5','TM6', 'TM7']))
                        a.build_alignment()
                        a.calculate_similarity()
                        a.calculate_statistics()
                        p = a.proteins[1]
                        entry['identity_to_human'] = int(p.identity)
                    except:
                        entry['identity_to_human'] = 0
                    identity_lookup[key] = entry['identity_to_human']

            entry['fraction_of_wt_seq'] = s.coverage_pct

            a_list = []
            for a in s.stabilizing_agents.all():
                a_list.append(a)
            g_protein = only_gproteins(a_list)
            arrestin = only_arrestins(a_list)
            fusion = only_fusions(a_list)
            antibody = only_antibodies(a_list)

            entry['signal_protein'] = ''
            entry['signal_protein_subtype'] = ''
            entry['signal_protein_note'] = ''
            entry['signal_protein_pcntseq'] = None

            ### StructureExtraProtein data parsing
            if pdb_id in ep:
                if effector == "no_effector":
                    key = '{}_{}'.format(s.protein_conformation.protein.parent.pk,ep[pdb_id]['display_name'])
                else:
                    try:
                        key = '{}_{}_{}'.format(s.protein_conformation.protein.pk, s.protein_conformation.protein.parent.pk, ep[pdb_id]['display_name'])
                    except:
                        key = '{}_{}_{}'.format(s.protein_conformation.protein.pk, s.protein_conformation.protein.parent, ep[pdb_id]['display_name'])
                    

                # if best_signal_p[key] == ep[pdb_id]['wt_coverage']:
                #     # this is the best coverage
                #     entry['signal_protein_seq_cons_color'] = 'green'
                # else:
                #     entry['signal_protein_seq_cons_color'] = 'red'
                if ep[pdb_id]['category'] == "Arrestin":
                    entry['signal_protein'] = ep[pdb_id]['wt_protein__family__parent__parent__name']
                else:
                    entry['signal_protein'] = ep[pdb_id]['wt_protein__family__parent__name']

                # Slight reformatting along the lines of the structure browser
                entry['signal_protein_subtype'] = ep[pdb_id]['display_name']
                if ep[pdb_id]['category'] == "G alpha" and entry['signal_protein_subtype'][0] == 'G':
                    entry['signal_protein_subtype'] = '&alpha;' + entry['signal_protein_subtype'][1:]

                entry['signal_protein_note'] = ep[pdb_id]['note']
                try:
                    entry['signal_protein_pcntseq'] = Decimal(ep[pdb_id]['wt_coverage'])
                except:
                    entry['signal_protein_pcntseq'] = None

            entry['structure_type'] = s.structure_type.type_short()
            try:
                entry['resolution'] = s.resolution
                entry['resolution_best'] = s.resolution == bestresolution_lookup[effector]['{}_{}'.format(shorted.pk, s.state.name)]
            except:
                entry['resolution'] = None
                entry['resolution_best'] = None


            #entry['7tm_distance'] = s.distance
            entry['tm6_angle'] = round(s.tm6_angle) if s.tm6_angle != None else None
            entry['gprot_bound_likeness'] = round(s.gprot_bound_likeness) if s.gprot_bound_likeness != None else None

            #entry['g_protein'] = g_protein
            #entry['arrestin']  = arrestin
            entry['fusion'] = fusion
            # if len(antibody) > 20:
            #     antibody = "<span title='{}'>{}</span>".format(antibody, antibody[:20] + "..")
            entry['antibody'] = antibody

            entry['ligand'] = "-"
            entry['ligand_role'] = "-"
            #entry['ligand_type'] = "-"

            for l in s.ligands.all():
                entry['ligand'] = l.ligand.name
                # if len(entry['ligand'])>20:
                #     entry['ligand'] = entry['ligand'][:20] + ".."
                entry['ligand_role'] = l.ligand_role.name
                # if l.ligand.ligand_type != None:
                #     entry['ligand_type'] = l.ligand.ligand_type.name

            entry['query_effector'] = effector

            entries[pdb_id] = entry          
        result_sets[effector] = entries  
    
    return result_sets