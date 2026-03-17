from build.management.commands.base_build import Command as BaseBuild

from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.models import F

from protein.models import Protein, ProteinSegment, ProteinFamily, Species
from protein.models import CLASSLESS_PARENT_GPCR_SLUGS
from residue.models import Residue, ResidueGenericNumberEquivalent, ResidueNumberingScheme

from common.alignment import Alignment
from common.selection import SelectionItem

from collections import OrderedDict
import copy
import logging
import time


class Command(BaseBuild):
    help = "Build receptor similarity 2 and identity using TM1-7 + ICL/ECL + H8 (excluding GAIN)."

    def add_arguments(self, parser):
        super(Command, self).add_arguments(parser=parser)
        parser.add_argument("--verbose", help="Prints progress in stdout.", default=False, action="store_true")
        parser.add_argument("--limit", type=int, help="Use only any indicated number of GPCRs per class.", default=False, action="store")

    logger = logging.getLogger(__name__)

    @staticmethod
    def _format_elapsed(seconds):
        seconds = int(round(seconds))
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h} hours {m} mins {s} secs"

    @staticmethod
    def _expected_pairs(n):
        return (n * (n - 1)) // 2

    def _build_chunk_segments(self, proteins, all_gpcr_segments):
        proteins = [protein for protein in proteins if protein is not None]
        if not proteins:
            return []

        numbering_schemes = []
        for protein in proteins:
            scheme = getattr(protein, "residue_numbering_scheme", None)
            if scheme and scheme not in numbering_schemes:
                numbering_schemes.append(scheme)

        seg_ids_all = set(
            Residue.objects.filter(protein_conformation__protein__in=proteins)
            .order_by("protein_segment__id")
            .distinct("protein_segment__id")
            .values_list("protein_segment_id", flat=True)
        )

        filtered_segments = [segment for segment in all_gpcr_segments if segment.id in seg_ids_all]

        if not numbering_schemes and not filtered_segments:
            numbering_schemes = [ResidueNumberingScheme.objects.get(slug="gpcrdba")]
            filtered_segments = list(all_gpcr_segments)

        gn_segment_ids = set()
        if numbering_schemes and filtered_segments:
            gn_segment_ids = set(
                ResidueGenericNumberEquivalent.objects.filter(
                    default_generic_number__protein_segment__in=filtered_segments,
                    scheme__in=numbering_schemes,
                ).values_list("default_generic_number__protein_segment_id", flat=True).distinct()
            )

        selected_segments = []
        for segment in filtered_segments:
            if segment.fully_aligned:
                selected_segments.append(segment)
            elif segment.id in gn_segment_ids:
                segment_copy = copy.copy(segment)
                segment_copy.only_aligned_residues = True
                selected_segments.append(
                    SelectionItem(
                        segment.category,
                        segment_copy,
                        properties={"only_aligned_residues": True},
                    )
                )

        return selected_segments

    def get_parent_gpcr_families(self, exclude_classless_artificial_class=True, include_classless_natural_classes=True):
        parent_family = ProteinFamily.objects.get(slug="000")
        parent_gpcr_families = ProteinFamily.objects.filter(parent_id=parent_family.pk, slug__startswith="0").exclude(pk=parent_family.pk)
        if exclude_classless_artificial_class:
            for slug in CLASSLESS_PARENT_GPCR_SLUGS:
                parent_gpcr_families = parent_gpcr_families.exclude(slug__startswith=slug)
        classless_protein_families = []
        if include_classless_natural_classes:
            classless_protein_families = self.get_classless_bottom_protein_families()
        parent_gpcr_families = list(parent_gpcr_families) + classless_protein_families
        return sorted(parent_gpcr_families, key=lambda f: (int(f.slug.split("_")[0])))

    def get_human_species(self):
        return Species.objects.get(common_name__iexact="Human")

    def __slug_tree_branch(self, slug_parts, slug_tree_dict):
        if len(slug_parts) == 1:
            slug_tree_dict[slug_parts[0]] = None
            return slug_tree_dict
        if slug_parts[0] in slug_tree_dict:
            slug_subtree_dict = slug_tree_dict[slug_parts[0]]
            if slug_subtree_dict is None:
                slug_subtree_dict = {}
        else:
            slug_subtree_dict = {}
        slug_tree_dict[slug_parts[0]] = self.__slug_tree_branch(slug_parts[1:], slug_subtree_dict)
        return slug_tree_dict

    def __sort_slug_tree_branch(self, slug_tree_dict):
        slug_tree_ordered_dict = OrderedDict()
        for slug in sorted(sorted(slug_tree_dict.keys(), key=lambda x: int(x[1:])), key=lambda x: x[0]):
            slug_subtree_dict = slug_tree_dict[slug]
            if slug_subtree_dict is not None:
                slug_tree_ordered_dict[slug] = self.__sort_slug_tree_branch(slug_subtree_dict)
            else:
                slug_tree_ordered_dict[slug] = None
        return slug_tree_ordered_dict

    def __parse_slug_tree_(self, slug_tree_dict, slug_list_list, slug_list):
        for slug, subtree in slug_tree_dict.items():
            slug_list.append(slug)
            if subtree is not None:
                self.__parse_slug_tree_(subtree, slug_list_list, slug_list)
            else:
                slug_list_list.append(slug_list.copy())
            slug_list.pop()

    def get_classless_bottom_protein_families(self):
        classless_parent_gpcrs_slugs_list = sorted(sorted(CLASSLESS_PARENT_GPCR_SLUGS, key=lambda x: int(x[1:])), key=lambda x: x[0])
        parent_gpcr_families = ProteinFamily.objects.filter(slug__startswith=classless_parent_gpcrs_slugs_list[0])

        for slug in classless_parent_gpcrs_slugs_list[1:]:
            parent_gpcr_families = parent_gpcr_families.filter(slug__startswith=slug)

        slug_2_family_dict = {}
        for family in parent_gpcr_families:
            slug_2_family_dict[family.slug] = family
        family_slug_tree_dict = {}
        for slug in slug_2_family_dict.keys():
            slug_parts = slug.split("_")
            self.__slug_tree_branch(slug_parts, family_slug_tree_dict)
        family_slug_tree_ordered_dict = self.__sort_slug_tree_branch(family_slug_tree_dict)
        slug_list_list = []
        slug_list = []
        self.__parse_slug_tree_(family_slug_tree_ordered_dict, slug_list_list, slug_list)
        classless_bottom_slugs_list = ["_".join(slug_list) for slug_list in slug_list_list]
        return [slug_2_family_dict[slug] for slug in classless_bottom_slugs_list]

    def filter_out_non_species_parent_gpcr_families(self, parent_gpcr_families, species):
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
            query = Protein.objects.annotate(family_slug=F("family__slug")).filter(family_slug__startswith=slug, species__in=species2)
            if query.exists():
                new_parent_gpcr_families_slugs_set.add(slug)
        return [family for family in parent_gpcr_families if family.slug in new_parent_gpcr_families_slugs_set]

    def filter_out_non_human_parent_gpcr_families(self, parent_gpcr_families):
        return self.filter_out_non_species_parent_gpcr_families(parent_gpcr_families, self.get_human_species())

    def handle(self, *args, **options):
        try:
            from classification.models import ReceptorSimilarity2
        except ImportError as e:
            raise CommandError("ReceptorSimilarity2 model not available. Did you migrate the classification app?") from e

        start_time = time.time()
        self.logger.info("Computing receptor similarity and populating classification_receptorsimilarity2...")

        try:
            with connection.cursor() as cursor:
                table = ReceptorSimilarity2._meta.db_table
                if connection.vendor == "postgresql":
                    cursor.execute("TRUNCATE TABLE {} RESTART IDENTITY".format(connection.ops.quote_name(table)))
                else:
                    ReceptorSimilarity2.objects.all().delete()
            self.logger.info("Reset receptor similarity 2 table (id restarts at 1).")
        except Exception as e:
            raise CommandError(f"Could not reset receptor similarity 2 table: {e}") from e

        initial_step1 = 380
        initial_step2 = 380

        parent_families = self.get_parent_gpcr_families(
            exclude_classless_artificial_class=True,
            include_classless_natural_classes=True,
        )
        human_parent_gpcr_families = self.filter_out_non_human_parent_gpcr_families(parent_families)
        human_species = self.get_human_species()
        # Use the full GPCR segment set for this build, excluding GAIN and fungal D1 segments.
        all_gpcr_segments = list(
            ProteinSegment.objects.filter(proteinfamily="GPCR")
            .exclude(domain="GAIN")
            .exclude(slug__startswith="D1")
            .order_by("pk")
        )

        human_parent_gpcr_families_protein = {}
        human_parent_gpcr_families_protein_num = {}
        for gpcr_class in human_parent_gpcr_families:
            gpcr_class_proteins = (
                Protein.objects.all()
                .annotate(family_slug=F("family__slug"))
                .filter(species=human_species, family_slug__startswith=gpcr_class.slug)
                .exclude(accession=None)
                .select_related(
                    "family",
                    "family__parent",
                    "family__parent__parent",
                    "family__parent__parent__parent",
                    "residue_numbering_scheme",
                )
                .order_by("family_slug", "entry_name")
            )
            human_parent_gpcr_families_protein[gpcr_class] = list(gpcr_class_proteins)
            human_parent_gpcr_families_protein_num[gpcr_class] = len(human_parent_gpcr_families_protein[gpcr_class])

        selected_parent_gpcr_families = list(human_parent_gpcr_families)
        selected_parent_gpcr_families_protein = human_parent_gpcr_families_protein
        selected_parent_gpcr_families_protein_num = human_parent_gpcr_families_protein_num

        step1 = int(initial_step1)
        step2 = int(initial_step2)
        step_halved = False

        def _limit_n(gpcr_class):
            if options["limit"]:
                return min(selected_parent_gpcr_families_protein_num[gpcr_class], options["limit"])
            return selected_parent_gpcr_families_protein_num[gpcr_class]

        def _to_int_percent(value):
            try:
                return int(round(float(value)))
            except Exception:
                return None

        class_sizes = {cls: _limit_n(cls) for cls in selected_parent_gpcr_families}
        total_pairs_est = 0
        for i, cls_i in enumerate(selected_parent_gpcr_families):
            ni = class_sizes.get(cls_i, 0) or 0
            for j in range(i, len(selected_parent_gpcr_families)):
                cls_j = selected_parent_gpcr_families[j]
                nj = class_sizes.get(cls_j, 0) or 0
                if i == j:
                    total_pairs_est += self._expected_pairs(ni)
                else:
                    total_pairs_est += ni * nj

        buffer = []
        batch_size = 5000
        total_created = 0
        processed_pairs = 0

        if options["verbose"]:
            print("Estimated unique protein pairs to process:", total_pairs_est)

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

                            if options["verbose"]:
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
                            selected_segments = self._build_chunk_segments(proteins, all_gpcr_segments)
                            if not selected_segments:
                                continue

                            cs_alignment = Alignment()
                            cs_alignment.load_proteins(proteins)
                            cs_alignment.load_segments(selected_segments)
                            build_alignment_return_value = cs_alignment.build_alignment()
                            if build_alignment_return_value == "Too large":
                                self.logger.warning("Alignment too large. Retrying with smaller chunks...")
                                while_loop_continue = True
                                break
                            cs_alignment.remove_non_generic_numbers_from_alignment()

                            pconf_by_protein_pk = {pconf.protein.pk: pconf for pconf in cs_alignment.proteins}

                            for protein1 in gpcr_class_proteins:
                                for protein2 in gpcr_class2_proteins:
                                    if protein1.pk == protein2.pk:
                                        continue

                                    if gpcr_class == gpcr_class2 and clim == clim2 and protein1.pk > protein2.pk:
                                        continue

                                    if protein1.pk < protein2.pk:
                                        ref, target = protein1, protein2
                                        ref_class_fk, target_class_fk = gpcr_class, gpcr_class2
                                    else:
                                        ref, target = protein2, protein1
                                        ref_class_fk, target_class_fk = gpcr_class2, gpcr_class

                                    processed_pairs += 1

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
                                        continue

                                    buffer.append(
                                        ReceptorSimilarity2(
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
                                            ReceptorSimilarity2.objects.bulk_create(buffer, batch_size=batch_size)
                                        total_created += len(buffer)
                                        buffer.clear()
                                        if options["verbose"]:
                                            if total_pairs_est:
                                                pct = (100.0 * processed_pairs) / float(total_pairs_est)
                                                print(f"Inserted rows: {total_created} ({pct:.1f}% done)")
                                            else:
                                                print("Inserted rows:", total_created)

                        if while_loop_continue:
                            break

                    if while_loop_continue:
                        if options["verbose"]:
                            print("Halving step1 and step2...")
                        step1 = max(1, step1 // 2)
                        step2 = max(1, step2 // 2)
                        step_halved = True
                        while_loop_continue = False
                        if options["verbose"]:
                            print("Retrying with the new steps...")
                        continue
                    if step_halved:
                        if options["verbose"]:
                            print("Restoring initial step1 and step2...")
                        step1 = initial_step1
                        step2 = initial_step2
                        step_halved = False
                    break

        if buffer:
            with transaction.atomic():
                ReceptorSimilarity2.objects.bulk_create(buffer, batch_size=batch_size)
            total_created += len(buffer)
            buffer.clear()
            if options["verbose"]:
                if total_pairs_est:
                    pct = (100.0 * processed_pairs) / float(total_pairs_est)
                    print(f"Inserted rows: {total_created} ({pct:.1f}% done)")
                else:
                    print("Inserted rows:", total_created)

        self.logger.info("Inserted %s receptor similarity 2 rows.", total_created)

        end_time = time.time()
        elapsed_time = end_time - start_time
        if options["verbose"]:
            print("Execution time:", self._format_elapsed(elapsed_time))
            print("Done.")
        self.logger.info("Execution time: %s", self._format_elapsed(elapsed_time))
        self.logger.info("Done.")
