from build.management.commands.base_build import Command as BaseBuild

from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.models import F, Q

from protein.models import Protein, ProteinSegment, ProteinFamily, Species
from protein.models import CLASSLESS_PARENT_GPCR_SLUGS

from common.alignment import Alignment

from collections import OrderedDict
import logging

import time

class Command(BaseBuild):
    help = 'Build receptor similarity and identity.'


    def add_arguments(self, parser):
        super(Command, self).add_arguments(parser=parser)
        parser.add_argument('--verbose', help='Prints progress in stdout.', default=False, action='store_true')
        parser.add_argument('--limit',type=int, help='Use only any indicated number of GPCRs per class.', default=False, action='store')

    logger = logging.getLogger(__name__)

    def get_parent_gpcr_families(self,exclude_classless_artificial_class=True,include_classless_natural_classes=True):
        parent_family = ProteinFamily.objects.get(slug='000')
        parent_gpcr_families = ProteinFamily.objects.filter(parent_id=parent_family.pk, slug__startswith='0').exclude(pk=parent_family.pk)
        if exclude_classless_artificial_class:
            for slug in CLASSLESS_PARENT_GPCR_SLUGS:
                parent_gpcr_families = parent_gpcr_families.exclude(slug__startswith=slug)
        classless_protein_families = []
        if include_classless_natural_classes:
            classless_protein_families = self.get_classless_bottom_protein_families()
        parent_gpcr_families = list(parent_gpcr_families)+classless_protein_families
        return sorted(parent_gpcr_families,key=lambda f: (int(f.slug.split('_')[0])))

    def get_human_species(self):
        return Species.objects.get(common_name__iexact='Human')

    def __slug_tree_branch(self, slug_parts,slug_tree_dict):
        if len(slug_parts) == 1:
            slug_tree_dict[slug_parts[0]] = None
            return slug_tree_dict
        else:
            if slug_parts[0] in slug_tree_dict:
                slug_subtree_dict = slug_tree_dict[slug_parts[0]]
                if slug_subtree_dict is None:
                    slug_subtree_dict = {}
            else:
                slug_subtree_dict = {}
            slug_tree_dict[slug_parts[0]] = self.__slug_tree_branch(slug_parts[1:],slug_subtree_dict)
        return slug_tree_dict

    def __sort_slug_tree_branch(self, slug_tree_dict):
        slug_tree_ordered_dict = OrderedDict()
        for slug in sorted(sorted(slug_tree_dict.keys(),key = lambda x: int(x[1:])),key = lambda x: x[0]):
            slug_subtree_dict = slug_tree_dict[slug]
            if slug_subtree_dict is not None:
                slug_tree_ordered_dict[slug] = self.__sort_slug_tree_branch(slug_subtree_dict)
            else:
                slug_tree_ordered_dict[slug] = None
        return slug_tree_ordered_dict

    def __parse_slug_tree_(self, slug_tree_dict,slug_list_list,slug_list):
        for slug,subtree in slug_tree_dict.items():
            slug_list.append(slug)
            if subtree is not None:
                self.__parse_slug_tree_(subtree,slug_list_list,slug_list)
            else:
                slug_list_list.append(slug_list.copy())
            slug_list.pop()

    def get_classless_bottom_protein_families(self):
        classless_parent_gpcrs_slugs_list = sorted(sorted(CLASSLESS_PARENT_GPCR_SLUGS,key = lambda x: int(x[1:])),key = lambda x: x[0])
        parent_gpcr_families = ProteinFamily.objects.filter(slug__startswith=classless_parent_gpcrs_slugs_list[0])

        for slug in classless_parent_gpcrs_slugs_list[1:]:
            parent_gpcr_families = parent_gpcr_families.filter(slug__startswith=slug)

        slug_2_family_dict = {}
        for f in parent_gpcr_families:
            slug_2_family_dict[f.slug] = f
        family_slug_tree_dict = {}
        for slug in slug_2_family_dict.keys():
            slug_parts = slug.split('_')
            self.__slug_tree_branch(slug_parts,family_slug_tree_dict)
        family_slug_tree_ordered_dict = self.__sort_slug_tree_branch(family_slug_tree_dict)
        slug_list_list = []
        slug_list = []
        self.__parse_slug_tree_(family_slug_tree_ordered_dict,slug_list_list,slug_list)
        classless_bottom_slugs_list = ['_'.join(slug_list) for slug_list in slug_list_list]
        return [slug_2_family_dict[slug] for slug in classless_bottom_slugs_list]

    def filter_out_non_species_parent_gpcr_families(self,parent_gpcr_families,species):
        """ Filters out parent GPCR families as a list of ProteinFamily objects that belong to a species.
            parent_gpcr_families: a list of protein.ProteinFamily objects
            species: protein.Species object
        """
        new_parent_gpcr_families_slugs_set = set()
        species2 = species
        try:
            iter(species)
        except TypeError:
             species2 = [species]
        parent_gpcr_families_slugs = []
        for family in parent_gpcr_families:
            parent_gpcr_families_slugs.append(family.slug)

        for slug in parent_gpcr_families_slugs:
            q = Protein.objects.annotate(family_slug=F('family__slug')).filter(family_slug__startswith=slug,species__in=species2)
            if q.exists():
                new_parent_gpcr_families_slugs_set.add(slug)
        return [f for f in parent_gpcr_families if f.slug in new_parent_gpcr_families_slugs_set]


    def filter_out_non_human_parent_gpcr_families(self,parent_gpcr_families):
        return self.filter_out_non_species_parent_gpcr_families(parent_gpcr_families,self.get_human_species())

    def handle(self, *args, **options):
        try:
            from alignment.models import ReceptorSimilarity
        except ImportError as e:
            raise CommandError("ReceptorSimilarity model not available. Did you migrate the alignment app?") from e

        start_time = time.time()
        self.logger.info("Computing receptor similarity and populating alignment_receptorsimilarity...")

        # Reset table so SQL id starts at 1 on each run.
        try:
            with connection.cursor() as cursor:
                table = ReceptorSimilarity._meta.db_table
                if connection.vendor == 'postgresql':
                    cursor.execute('TRUNCATE TABLE {} RESTART IDENTITY'.format(connection.ops.quote_name(table)))
                else:
                    ReceptorSimilarity.objects.all().delete()
            self.logger.info('Reset receptor similarity table (id restarts at 1).')
        except Exception as e:
            raise CommandError(f"Could not reset receptor similarity table: {e}") from e

        initial_step1 = 380  # If alignment fails, please, set this to a lower value
        initial_step2 = 380  # If alignment fails, please, set this to a lower value

        parent_families = self.get_parent_gpcr_families(
            exclude_classless_artificial_class=True,
            include_classless_natural_classes=True,
        )
        human_parent_gpcr_families = self.filter_out_non_human_parent_gpcr_families(parent_families)
        human_species = self.get_human_species()

        gpcr_segments = ProteinSegment.objects.filter(
            Q(proteinfamily='GPCR') & (Q(slug__regex='TM[1-7]') | Q(slug='H8'))
        )

        human_parent_gpcr_families_protein = {}
        human_parent_gpcr_families_protein_num = {}
        for gpcr_class in human_parent_gpcr_families:
            gpcr_class_proteins = (
                Protein.objects.all()
                .annotate(family_slug=F('family__slug'))
                .filter(species=human_species, family_slug__startswith=gpcr_class.slug)
                .exclude(accession=None)
                .order_by('family_slug', 'entry_name')
            )
            human_parent_gpcr_families_protein[gpcr_class] = list(gpcr_class_proteins)
            human_parent_gpcr_families_protein_num[gpcr_class] = len(human_parent_gpcr_families_protein[gpcr_class])

        selected_parent_gpcr_families = list(human_parent_gpcr_families)
        selected_parent_gpcr_families_protein = human_parent_gpcr_families_protein
        selected_parent_gpcr_families_protein_num = human_parent_gpcr_families_protein_num

        step1 = int(initial_step1)  # If alignment fails, please, set this to a lower value
        step2 = int(initial_step2)  # If alignment fails, please, set this to a lower value
        step_halved = False

        def _limit_n(gpcr_class):
            if options['limit']:
                return min(selected_parent_gpcr_families_protein_num[gpcr_class], options['limit'])
            return selected_parent_gpcr_families_protein_num[gpcr_class]

        def _to_int_percent(v):
            try:
                return int(round(float(v)))
            except Exception:
                return None

        buffer = []
        batch_size = 5000
        total_created = 0

        # Iterate class pairs once (A,B) with B starting at A to avoid duplicate work,
        # while still processing all chunks for large classes.
        for i, gpcr_class in enumerate(selected_parent_gpcr_families):
            protein_num1 = _limit_n(gpcr_class)
            proteins1_all = selected_parent_gpcr_families_protein[gpcr_class][:protein_num1]

            for j in range(i, len(selected_parent_gpcr_families)):
                gpcr_class2 = selected_parent_gpcr_families[j]
                protein_num2 = _limit_n(gpcr_class2)
                proteins2_all = selected_parent_gpcr_families_protein[gpcr_class2][:protein_num2]

                while_loop_continue = False
                while True:
                    for clim in range(0, protein_num1, step1):
                        gpcr_class_proteins = proteins1_all[clim:clim + step1]

                        clim2_start = clim if gpcr_class == gpcr_class2 else 0
                        for clim2 in range(clim2_start, protein_num2, step2):
                            gpcr_class2_proteins = proteins2_all[clim2:clim2 + step2]

                            if options['verbose']:
                                print(
                                    gpcr_class, "from:" + str(clim + 1),
                                    "to:" + str(min(clim + step1, protein_num1)),
                                    "(of:{})".format(protein_num1),
                                    "vs", gpcr_class2, "from:" + str(clim2 + 1),
                                    "to:" + str(min(clim2 + step2, protein_num2)),
                                    "(of:{})".format(protein_num2),
                                )

                            proteins = (
                                gpcr_class_proteins
                                if (gpcr_class == gpcr_class2 and clim == clim2)
                                else (gpcr_class_proteins + gpcr_class2_proteins)
                            )

                            cs_alignment = Alignment()
                            cs_alignment.load_proteins(proteins)
                            cs_alignment.load_segments(gpcr_segments)
                            build_alignment_return_value = cs_alignment.build_alignment()
                            if build_alignment_return_value == "Too large":
                                self.logger.warning("Alignment too large. Retrying with smaller chunks...")
                                while_loop_continue = True
                                break
                            cs_alignment.remove_non_generic_numbers_from_alignment()

                            # Map Protein.pk -> ProteinConformation (used by pairwise_similarity)
                            pconf_by_protein_pk = {pconf.protein.pk: pconf for pconf in cs_alignment.proteins}

                            for protein1 in gpcr_class_proteins:
                                for protein2 in gpcr_class2_proteins:
                                    if protein1.pk == protein2.pk:
                                        continue

                                    # Avoid producing both (a,b) and (b,a) when comparing a chunk to itself.
                                    if gpcr_class == gpcr_class2 and clim == clim2 and protein1.pk > protein2.pk:
                                        continue

                                    # Store one canonical direction to satisfy uniq(protein_ref, protein_target)
                                    # and avoid target<->ref duplicates.
                                    if protein1.pk < protein2.pk:
                                        ref, target = protein1, protein2
                                        ref_class_fk, target_class_fk = gpcr_class, gpcr_class2
                                    else:
                                        ref, target = protein2, protein1
                                        ref_class_fk, target_class_fk = gpcr_class2, gpcr_class

                                    pconf_ref = pconf_by_protein_pk.get(ref.pk)
                                    pconf_target = pconf_by_protein_pk.get(target.pk)
                                    if not pconf_ref or not pconf_target:
                                        continue

                                    calc_values = cs_alignment.pairwise_similarity(pconf_ref, pconf_target)
                                    if not calc_values:
                                        continue
                                    identity_value = _to_int_percent(calc_values[0])
                                    similarity_value = _to_int_percent(calc_values[1])
                                    if identity_value is None or similarity_value is None:
                                        continue
                                    if identity_value < 0 or similarity_value < 0:
                                        # No aligned residues; skip
                                        continue

                                    buffer.append(
                                        ReceptorSimilarity(
                                            protein_ref=ref,
                                            protein_target=target,
                                            ref_class=ref_class_fk,
                                            target_class=target_class_fk,
                                            identity=identity_value,
                                            similarity=similarity_value,
                                        )
                                    )

                                    if len(buffer) >= batch_size:
                                        with transaction.atomic():
                                            ReceptorSimilarity.objects.bulk_create(buffer, batch_size=batch_size)
                                        total_created += len(buffer)
                                        buffer.clear()
                                        if options['verbose']:
                                            print("Inserted rows:", total_created)

                        if while_loop_continue:
                            break

                    if while_loop_continue:
                        if options['verbose']:
                            print("Halving step1 and step2...")
                        step1 = max(1, step1 // 2)
                        step2 = max(1, step2 // 2)
                        step_halved = True
                        while_loop_continue = False
                        if options['verbose']:
                            print("Retrying with the new steps...")
                        continue
                    if step_halved:
                        if options['verbose']:
                            print("Restoring initial step1 and step2...")
                        step1 = initial_step1
                        step2 = initial_step2
                        step_halved = False
                    break

        if buffer:
            with transaction.atomic():
                ReceptorSimilarity.objects.bulk_create(buffer, batch_size=batch_size)
            total_created += len(buffer)
            buffer.clear()

        self.logger.info("Inserted %s receptor similarity rows.", total_created)

        end_time = time.time()
        elapsed_time = end_time - start_time
        if options['verbose']:
            print('Execution time:', elapsed_time, 'seconds')
            print('Done.')
        self.logger.info('Execution time: '+str(elapsed_time)+' seconds')
        self.logger.info('Done.')
