from django.http import HttpResponse, JsonResponse
from django.db.models import Q
from django.views.generic import TemplateView, View
from protein.models import Protein, ProteinFamily
from common.phylogenetic_tree import PhylogeneticTreeGenerator
from classification.models import ClusterCoord

import json
from copy import deepcopy
from collections import OrderedDict
import sys


try:
    import importlib.metadata
except ImportError:
    sys.modules['importlib.metadata'] = __import__('importlib_metadata')

import pandas as pd
import numpy as np
from sklearn.manifold import TSNE
from sklearn.cluster import KMeans
import re

class DataMapperHome(TemplateView):

    @staticmethod
    def keep_by_names(data, names_to_keep):
        data_copy = deepcopy(data)
        if isinstance(data_copy, list):
            # Process each item in the list
            kept_items = [DataMapperHome.keep_by_names(item, names_to_keep) for item in data_copy]
            # Return only non-None items
            return [item for item in kept_items if item is not None]
        elif isinstance(data_copy, OrderedDict):
            name = data_copy.get('name')
            if name not in names_to_keep.keys():
                if 'children' in data_copy:
                    # Recursively process children
                    data_copy['children'] = DataMapperHome.keep_by_names(data_copy['children'], names_to_keep)
                    # Remove the 'children' key if it's empty after processing
                    if not data_copy['children']:
                        return None
                else:
                    return None
            else:
                # If the name is in the keep list, update the 'value' field
                if 'Inner' in names_to_keep[name]:
                    data_copy['value'] = names_to_keep[name]['Inner']
                # Process children if present
                if 'children' in data_copy:
                    data_copy['children'] = DataMapperHome.keep_by_names(data_copy['children'], names_to_keep)
                    if not data_copy['children']:
                        del data_copy['children']
            return data_copy
        return data_copy

    @staticmethod
    def GenerateGPCRomeDataStructure(data_type: str = "Classic"):

        # Determine which proteins and families to use based on type
        if data_type == "Classic":
            all_proteins = Protein.objects.filter(
                species_id=1,
                parent_id__isnull=True,
                accession__isnull=False,
                family_id__slug__startswith='0'
            ).exclude(
                Q(family_id__slug__startswith='007') |
                Q(family_id__slug__startswith='008')
            )

            families = ProteinFamily.objects.exclude(slug='000')

            # Get valid names (for pruning) from all_proteins
            valid_names = set(all_proteins.values_list('name', flat=True))

            # Build name-to-entry_name mapping
            proteins = Protein.objects.filter(
                species_id=1,
                name__in=valid_names
            ).values(
                'entry_name', 'name'
            ).order_by('entry_name')

            name_to_entry = {item['name']: item['entry_name'] for item in proteins}

            # Create flat list of entry names
            entry_names = [item['entry_name'] for item in proteins]

            # Fetch proteins with prefetch on genes
            protein_genes = Protein.objects.prefetch_related('genes').filter(entry_name__in=entry_names)

            # Create mapping from entry_name to gene name (position == 0 only)
            entry_to_gene = {
                protein.entry_name: next((g.name for g in protein.genes.all() if g.position == 0), None)
                for protein in protein_genes
            }

        elif data_type == "Odorant":
            all_proteins = Protein.objects.filter(
                species_id=1,
                parent_id__isnull=True,
                accession__isnull=False
            ).filter(
                Q(family_id__slug__startswith='007') | Q(family_id__slug__startswith='008')
            )

            families = ProteinFamily.objects.filter(
                Q(slug__startswith='007') | Q(slug__startswith='008')
            )

            valid_names = set(all_proteins.values_list('name', flat=True))

            proteins = Protein.objects.filter(
                species_id=1,
                name__in=valid_names
            ).values(
                'entry_name', 'name'
            ).order_by('entry_name')

            name_to_entry = {item['name']: item['entry_name'] for item in proteins}

            entry_names = [item['entry_name'] for item in proteins]

            # Fetch proteins with prefetch on genes
            protein_genes = Protein.objects.prefetch_related('genes').filter(entry_name__in=entry_names)

            # Create mapping from entry_name to gene name (position == 0 only)
            entry_to_gene = {
                protein.entry_name: next((g.name for g in protein.genes.all() if g.position == 0), None)
                for protein in protein_genes
            }

        else:
            raise ValueError(f"Unsupported data structure type: {type}")

        # Tree building
        def build_family_tree(families):
            datatree = {}
            slug_to_name = {fam.slug: fam.name for fam in families}

            for item in families:
                slug_parts = item.slug.split('_')
                current_level = datatree

                for i in range(len(slug_parts)):
                    full_slug = '_'.join(slug_parts[:i + 1])
                    name = item.name if full_slug == item.slug else None

                    if i == len(slug_parts) - 1:
                        if len(slug_parts) == 4:
                            if item.name in name_to_entry:
                                entry_name = name_to_entry[item.name]
                                entry_code = entry_name.split('_')[0].upper()
                                gene_symbol = entry_to_gene.get(entry_name)  # Entrez name, if available
                                current_level[item.name] = {
                                    "Data": "Empty",
                                    "EntryName": entry_code,
                                    "Entrez": gene_symbol if gene_symbol else "UNKNOWN",
                                    "Color": "#FFFFFF"
                                }
                        else:
                            current_level.setdefault(name, {})
                    else:
                        if name:
                            current_level = current_level.setdefault(name, {})
                        else:
                            current_level = current_level.setdefault(full_slug, {})  # Temp key

            def convert_keys(tree):
                new_tree = {}
                for key, value in tree.items():
                    new_key = slug_to_name.get(key, key)
                    new_tree[new_key] = convert_keys(value) if isinstance(value, dict) else value
                return new_tree

            return convert_keys(datatree)

        # Prune tree to remove anything not in valid_names
        def prune_tree(tree, valid_leaves):
            pruned = {}
            for key, value in tree.items():
                if isinstance(value, dict):
                    if 'Data' in value:
                        if key in valid_leaves:
                            pruned[key] = value
                    else:
                        pruned_subtree = prune_tree(value, valid_leaves)
                        if pruned_subtree:
                            pruned[key] = pruned_subtree
            return pruned if pruned else None

        # Build and prune the tree
        datatree = build_family_tree(families)
        GPCRomeStructureDict = prune_tree(datatree, valid_names)

        # Class renaming
        class_rename_map = {
            "Class A (Rhodopsin)": "A",
            "Class B1 (Secretin)": "B1",
            "Class B2 (Adhesion)": "B2",
            "Class C (Glutamate)": "C",
            "Class F (Frizzled)": "F",
            "Class O1 (fish-like odorant)": "O1",
            "Class O2 (tetrapod specific odorant)": "O2",
            "Class T2 (Taste 2)": "T2",
            "Other GPCRs": "Classless"
        }

        if data_type == "Classic" and GPCRomeStructureDict:
            GPCRome_dict = {
                "Circle_1": {},
                "Circle_2": {},
                "Circle_3": {},
                "Circle_4": {},
                "Circle_5": {}
            }

            class_A_receptor_families = 0

            for Class, ligand_types in GPCRomeStructureDict.items():
                renamed_class = class_rename_map.get(Class, Class)

                if Class == "Class A (Rhodopsin)":
                    sorted_receptor_families = []

                    for Ligand_type, receptor_families in ligand_types.items():
                        if Ligand_type == "Orphan receptors":
                            continue

                        for Receptor_Family in receptor_families:
                            if Receptor_Family == "Class A Orphans":
                                continue

                            sorted_receptor_families.append(
                                (Ligand_type, Receptor_Family, receptor_families[Receptor_Family])
                            )

                    sorted_receptor_families.sort(key=lambda x: x[1])  # sort by receptor family name

                    for Ligand_type, Receptor_Family, receptors in sorted_receptor_families:
                        target_circle = "Circle_1" if class_A_receptor_families < 43 else "Circle_2"

                        GPCRome_dict.setdefault(target_circle, {}).setdefault(renamed_class, {}).setdefault(Ligand_type, {})[Receptor_Family] = receptors
                        class_A_receptor_families += 1

                    # Add Class A orphans to Circle_2 if present
                    orphans = ligand_types.get("Orphan receptors", {})
                    if "Class A orphans" in orphans:
                        GPCRome_dict["Circle_2"].setdefault(renamed_class, {}).setdefault("Orphan receptors", {})["Class A orphans"] = orphans["Class A orphans"]

                elif Class in ["Class B1 (Secretin)", "Class B2 (Adhesion)"]:
                    GPCRome_dict["Circle_3"].setdefault(renamed_class, {}).update(ligand_types)

                elif Class in ["Class C (Glutamate)", "Class F (Frizzled)"]:
                    GPCRome_dict["Circle_4"].setdefault(renamed_class, {}).update(ligand_types)

                elif Class in ["Class T2 (Taste 2)", "Other GPCRs"]:
                    GPCRome_dict["Circle_5"].setdefault(renamed_class, {}).update(ligand_types)

            # Remove the ligand type layer
            for circle in GPCRome_dict:
                for class_name in list(GPCRome_dict[circle].keys()):
                    new_structure = {}

                    for ligand_type in list(GPCRome_dict[circle][class_name].keys()):
                        for receptor_family, receptors in GPCRome_dict[circle][class_name][ligand_type].items():
                            new_structure[receptor_family] = receptors

                    # Replace the old structure with the flattened one
                    GPCRome_dict[circle][class_name] = new_structure
            # Final return (renamed and sorted into circles)
            return {
                "Data": GPCRome_dict
            }
        if data_type == "Odorant" and GPCRomeStructureDict:
            GPCRome_dict = {
                "Circle_1": {},  # Family 1–4 from Class O2
                "Circle_2": {},  # Family 5–9 from Class O2
                "Circle_3": {},  # Family 10–14 from Class O2
                "Circle_4": {}   # All of Class O1
            }

            for Class, ligand_types in GPCRomeStructureDict.items():
                renamed_class = class_rename_map.get(Class, Class)

                if Class == "Class O2 (tetrapod specific odorant)":
                    sorted_families = []

                    for Ligand_type, receptor_families in ligand_types.items():
                        for Receptor_Family, receptors in receptor_families.items():
                            try:
                                family_number = int(Receptor_Family.replace("Odorant family", "").strip())
                            except ValueError:
                                continue

                            sorted_families.append(
                                (family_number, Ligand_type, Receptor_Family, receptors)
                            )

                    sorted_families.sort(key=lambda x: x[0])  # Sort numerically by family number

                    for family_number, Ligand_type, Receptor_Family, receptors in sorted_families:
                        renamed_family = f"Family {family_number}"
                        if 1 <= family_number <= 4:
                            circle = "Circle_1"
                        elif 5 <= family_number <= 9:
                            circle = "Circle_2"
                        elif 10 <= family_number <= 14:
                            circle = "Circle_3"
                        else:
                            continue  # Skip any outside defined ranges

                        GPCRome_dict.setdefault(circle, {}).setdefault(renamed_class, {}).setdefault(Ligand_type, {})[renamed_family] = receptors

                elif Class == "Class O1 (fish-like odorant)":
                    renamed_ligand_types = {}

                    for Ligand_type, receptor_families in ligand_types.items():
                        renamed_receptor_families = {}

                        for Receptor_Family, receptors in receptor_families.items():
                            try:
                                family_number = int(Receptor_Family.replace("Odorant family", "").strip())
                                renamed_family = f"Family {family_number}"
                            except ValueError:
                                renamed_family = Receptor_Family  # fallback to original if parsing fails

                            renamed_receptor_families[renamed_family] = receptors

                        renamed_ligand_types[Ligand_type] = renamed_receptor_families

                    GPCRome_dict["Circle_4"].setdefault(renamed_class, {}).update(renamed_ligand_types)

            # Flatten ligand_type layer (remove ligand type level)
            for circle in GPCRome_dict:
                for class_name in list(GPCRome_dict[circle].keys()):
                    new_structure = {}
                    for ligand_type in GPCRome_dict[circle][class_name]:
                        for receptor_family, receptors in GPCRome_dict[circle][class_name][ligand_type].items():
                            new_structure[receptor_family] = receptors
                    GPCRome_dict[circle][class_name] = new_structure

            return {
                "Data": GPCRome_dict
            }

    @staticmethod
    def update_nested_GPCRome_data(structure_dict, raw_data):
        def normalize_key(raw_key):
            return raw_key.split("_")[0].upper()

        # Normalize and extract only non-null values
        normalized_raw_data = {
            normalize_key(key): {
                'Data': val.get("Value1"),
                'Color': val.get("Value2")  # Might be None
            }
            for key, val in raw_data.items()
            if isinstance(val, dict) and "Value1" in val
        }

        def recursive_update(d):
            if isinstance(d, dict):
                if "EntryName" in d and "Data" in d:
                    entry = d["EntryName"].upper()
                    if entry in normalized_raw_data:
                        d["Data"] = normalized_raw_data[entry]["Data"]
                        color = normalized_raw_data[entry].get("Color")
                        if color is not None:
                            d["Color"] = color

                for value in d.values():
                    recursive_update(value)
            elif isinstance(d, list):
                for item in d:
                    recursive_update(item)

        recursive_update(structure_dict)
        return structure_dict

    @staticmethod
    def build_gpcrome_receptor_normalization_maps(include_odorant=False):
        """Build lookup tables for receptor normalization.

        Args:
            include_odorant: If True, include odorant receptors (family slugs 007, 008)
                             in the picker / autocomplete / resolve set.
                             The GPCRome Wheel page leaves this False; all other Mapper
                             pages pass True.
        """
        all_proteins = Protein.objects.filter(
            species_id=1,
            parent_id__isnull=True,
            accession__isnull=False,
            family_id__slug__startswith='0',
        ).values_list('entry_name', flat=True).distinct()

        if include_odorant:
            # All human GPCRs including odorant families 007 / 008
            proteins_gpcrome_tree = set(all_proteins)
        else:
            proteins_gpcrome_tree = set(
                Protein.objects.filter(
                    species_id=1,
                    parent_id__isnull=True,
                    accession__isnull=False,
                    family_id__slug__startswith='0',
                ).exclude(
                    family_id__slug__startswith='007',
                ).exclude(
                    family_id__slug__startswith='008',
                ).values_list('entry_name', flat=True).distinct()
            )

        proteins = Protein.objects.prefetch_related('genes').filter(entry_name__in=all_proteins)
        entry_to_gene = {
            protein.entry_name: next((g.name for g in protein.genes.all() if g.position == 0), None)
            for protein in proteins
        }
        gene_to_entry = {v.upper(): k for k, v in entry_to_gene.items() if v}
        entry_name_upper_to_entry = {k.upper(): k for k in entry_to_gene.keys()}
        entry_name_no_species_to_entry = {
            k.split('_')[0].upper(): k for k in entry_to_gene.keys()
        }
        iuphar_name_to_entry = {}
        for p in Protein.objects.filter(entry_name__in=proteins_gpcrome_tree).only('entry_name', 'name'):
            if p.name:
                plain = re.sub(r'<[^>]+>', '', p.name).strip()
                if plain:
                    iuphar_name_to_entry[plain.upper()] = p.entry_name
        return {
            'proteins_gpcrome_tree': proteins_gpcrome_tree,
            'gene_to_entry': gene_to_entry,
            'entry_name_upper_to_entry': entry_name_upper_to_entry,
            'entry_name_no_species_to_entry': entry_name_no_species_to_entry,
            'entry_to_gene': entry_to_gene,
            'iuphar_name_to_entry': iuphar_name_to_entry,
        }

    @staticmethod
    def gpcrome_receptor_select2_options(maps=None):
        if maps is None:
            maps = DataMapperHome.build_gpcrome_receptor_normalization_maps()
        entry_to_gene = maps['entry_to_gene']
        tree = sorted(maps['proteins_gpcrome_tree'])
        name_by_entry = {
            p['entry_name']: p['name']
            for p in Protein.objects.filter(entry_name__in=tree).values('entry_name', 'name')
        }
        options = []
        for entry_name in tree:
            gene = entry_to_gene.get(entry_name) or ''
            raw_name = name_by_entry.get(entry_name) or ''
            plain_name = re.sub(r'<[^>]+>', '', raw_name).strip() if raw_name else ''
            base = entry_name.split('_')[0].upper() if '_' in entry_name else entry_name.upper()
            parts = [plain_name, gene, base]
            label = ' | '.join([x for x in parts if x])
            if not label:
                label = entry_name
            search_parts = [
                entry_name,
                entry_name.upper(),
                base,
                gene.upper() if gene else '',
                plain_name.upper() if plain_name else '',
            ]
            search_text = ' '.join([x for x in search_parts if x]).upper()
            options.append({
                'id': entry_name,
                'text': label,
                'search_text': search_text,
                'name_plain': plain_name,
                'name_html': raw_name if raw_name else plain_name,
                'gene': gene,
                'uniprot': base,
            })
        return options

    @staticmethod
    def gpcrome_receptor_client_resolve_map(maps=None):
        """Uppercased lookup keys -> canonical entry_name (same rules as normalize where possible)."""
        if maps is None:
            maps = DataMapperHome.build_gpcrome_receptor_normalization_maps()
        tree = maps['proteins_gpcrome_tree']
        out = {}
        for en in tree:
            out[en.upper()] = en
            if '_' in en:
                out[en.split('_')[0].upper()] = en
        for gene_upper, en in maps['gene_to_entry'].items():
            if en in tree:
                out[gene_upper] = en
        for name_upper, en in (maps.get('iuphar_name_to_entry') or {}).items():
            if en in tree:
                out[name_upper] = en
        return out

    @staticmethod
    def _gpcrome_strip_markup(s):
        if not s:
            return ''
        return re.sub(r'<[^>]+>', '', str(s)).strip()

    # Same lineage idea as Drugs browser (target__family__parent__...) — prefetch enough FK hops
    # that in-memory traversal matches Protein.get_protein_class / get_protein_family without new queries.
    _GPCROME_FAM_PARENT_CHAIN = 'family__' + '__'.join(['parent'] * 14)

    @staticmethod
    def _gpcrome_class_display_name_after_family_traversal(protein_family):
        """Traversal copy of Protein.get_protein_class; uses cached FK links from select_related()."""
        if protein_family is None:
            return ''
        tmp = protein_family
        while tmp.parent is not None and tmp.parent.parent is not None:
            tmp = tmp.parent
        return tmp.name if tmp.name else ''

    @staticmethod
    def _gpcrome_receptor_family_display_name_after_traversal(protein_family):
        """Receptor family = one hop up from Protein.family (matches family__parent__name)."""
        if protein_family is None or protein_family.parent is None:
            return ''
        return protein_family.parent.name or ''

    @staticmethod
    def _gpcrome_ligand_type_display_name_after_family_traversal(protein_family):
        """Ligand type / chemotype = two hops up from Protein.family (matches family__parent__parent__name)."""
        if (
            protein_family is None
            or protein_family.parent is None
            or protein_family.parent.parent is None
        ):
            return ''
        return protein_family.parent.parent.name or ''

    @staticmethod
    def gpcrome_collapse_nonhuman_only(nonhuman_only):
        """One representative entry per stem (first species encountered), matching the
        receptor input table's collapsed-species convention: a single pick per receptor
        labeled "STEM (no human ortholog)" instead of one row per species suffixed
        "(Mouse only)" / "(Rat only)". Returns (extra_entry_names, stem_by_entry) for
        use with gpcrome_receptor_picker_table_rows.
        """
        seen_stems = set()
        extra_entry_names = []
        stem_by_entry = {}
        for info in nonhuman_only:
            stem = info['stem']
            if stem in seen_stems:
                continue
            seen_stems.add(stem)
            extra_entry_names.append(info['entry'])
            stem_by_entry[info['entry']] = stem.upper()
        return extra_entry_names, stem_by_entry

    @staticmethod
    def gpcrome_receptor_picker_table_rows(maps=None, extra_entry_names=None, entry_label_overrides=None):
        """Plain JSON rows for GPCR picker (wheel receptor set). Single batched Protein query.

        extra_entry_names: optional entry_names to include in addition to the human
            gpcrome tree (e.g. non-human-ortholog-only receptors for Tree/Heatmap/List).
        entry_label_overrides: optional {entry_name: STEM_UPPER} — for those entries the
            GtoPdb name column is fully replaced with "STEM (no human ortholog)", matching
            the receptor input table's collapsed-species convention.
        """
        if maps is None:
            maps = DataMapperHome.build_gpcrome_receptor_normalization_maps()
        entry_to_gene = dict(maps['entry_to_gene'])
        tree = set(maps['proteins_gpcrome_tree'])
        if extra_entry_names:
            tree |= set(extra_entry_names)
        tree = sorted(tree)
        if not tree:
            return []

        missing_gene_entries = [e for e in tree if e not in entry_to_gene]
        if missing_gene_entries:
            for protein in Protein.objects.prefetch_related('genes').filter(
                entry_name__in=missing_gene_entries
            ):
                entry_to_gene[protein.entry_name] = next(
                    (g.name for g in protein.genes.all() if g.position == 0), None
                )

        entry_label_overrides = entry_label_overrides or {}
        NHO_TAG = '(no human ortholog)'

        qs = Protein.objects.filter(entry_name__in=tree).select_related(
            DataMapperHome._GPCROME_FAM_PARENT_CHAIN
        )
        by_entry = {p.entry_name: p for p in qs}

        rows = []
        for entry_name in tree:
            p = by_entry.get(entry_name)
            if not p:
                continue
            gene = entry_to_gene.get(entry_name) or ''
            raw_name = p.name or ''
            plain_name = DataMapperHome._gpcrome_strip_markup(raw_name)

            override_stem = entry_label_overrides.get(entry_name)
            if override_stem:
                plain_name = '{} {}'.format(override_stem, NHO_TAG)
                raw_name = '{} <em>{}</em>'.format(override_stem, NHO_TAG)

            rfam = p.family
            family = DataMapperHome._gpcrome_strip_markup(
                DataMapperHome._gpcrome_receptor_family_display_name_after_traversal(rfam)
            )
            ligand_type = DataMapperHome._gpcrome_strip_markup(
                DataMapperHome._gpcrome_ligand_type_display_name_after_family_traversal(rfam)
            )
            prot_class = DataMapperHome._gpcrome_strip_markup(
                DataMapperHome._gpcrome_class_display_name_after_family_traversal(rfam)
            )

            entry_short = p.entry_short()
            gpcrdb_link = 'https://gpcrdb.org/protein/{}/'.format(entry_name)
            uniprot_link = 'https://www.uniprot.org/uniprot/{}'.format(entry_short) if entry_short else ''
            rows.append({
                'id': entry_name,
                'name_html': raw_name if raw_name else plain_name,
                'name_plain': plain_name or entry_short,
                'gene': gene or '',
                'uniprot': entry_short,
                'uniprot_link': uniprot_link,
                'family': family or '',
                'ligandtype': ligand_type or '',
                'class': prot_class or '',
                'gpcrdb_link': gpcrdb_link,
            })
        return rows

    @staticmethod
    def gpcrome_receptor_info_for_list(maps=None, extra_entry_names=None, entry_label_overrides=None):
        """Returns dict of entry_name → {class, ligandtype, family, name_plain, gene, uniprot}
        for all human non-odorant GPCRs, for building list plot data in the browser.

        extra_entry_names: optional entry_names to include in addition to the human
            gpcrome tree (e.g. non-human-ortholog-only receptors). Without an entry here,
            mapperListBuildData() has no class/ligandtype/family to bucket the receptor
            under and silently drops it from the plot.
        entry_label_overrides: optional {entry_name: STEM_UPPER}, mirrors
            gpcrome_receptor_picker_table_rows — replaces name_plain with
            "STEM (no human ortholog)" for those entries.
        """
        if maps is None:
            maps = DataMapperHome.build_gpcrome_receptor_normalization_maps()
        gpcrome_set = set(maps.get('proteins_gpcrome_tree', set()))
        if extra_entry_names:
            gpcrome_set |= set(extra_entry_names)
        select2_opts = {o['id']: o for o in DataMapperHome.gpcrome_receptor_select2_options(maps=maps)}
        entry_label_overrides = entry_label_overrides or {}
        NHO_TAG = '(no human ortholog)'

        entry_to_gene = dict(maps['entry_to_gene'])
        entry_short_by_name = {}
        missing_gene_entries = [e for e in gpcrome_set if e not in entry_to_gene]
        if missing_gene_entries:
            for protein in Protein.objects.prefetch_related('genes').filter(
                entry_name__in=missing_gene_entries
            ):
                entry_to_gene[protein.entry_name] = next(
                    (g.name for g in protein.genes.all() if g.position == 0), None
                )
                entry_short_by_name[protein.entry_name] = protein.entry_short()

        qs = Protein.objects.filter(
            entry_name__in=list(gpcrome_set)
        ).values_list(
            'entry_name',
            'family__parent__parent__parent__name',  # Class
            'family__parent__parent__name',           # Ligand type
            'family__parent__name',                   # Receptor family
        )

        result = {}
        for entry_name, cls, ligandtype, family in qs:
            opt = select2_opts.get(entry_name, {})
            override_stem = entry_label_overrides.get(entry_name)
            name_plain = '{} {}'.format(override_stem, NHO_TAG) if override_stem else opt.get('name_plain', '')
            result[entry_name] = {
                'class':      cls        or 'Other',
                'ligandtype': ligandtype or 'Other',
                'family':     family     or 'Other',
                'name_plain': name_plain,
                'gene':       opt.get('gene') or entry_to_gene.get(entry_name) or '',
                'uniprot':    opt.get('uniprot') or entry_short_by_name.get(entry_name, ''),
            }
        return result

    @staticmethod
    def build_ortholog_species_map():
        """
        Groups all SWISSPROT GPCR proteins by family to build a per-receptor
        species availability map for the Tree page species feature.
        Returns dict with:
          by_stem:          human_stem -> list of ortholog dicts (all species incl. human)
          nonhuman_only:    list of receptor dicts that have no human equivalent
          entry_to_species: entry_name -> {common, latin, label, is_human}
        """
        proteins = list(
            Protein.objects.filter(
                source__name='SWISSPROT',
                accession__isnull=False,
                parent_id__isnull=True,
                family__slug__startswith='0',
            ).values(
                'entry_name', 'family_id', 'species_id',
                'species__common_name', 'species__latin_name',
            ).order_by('family_id', 'species_id')
        )

        family_map = {}
        for p in proteins:
            fid = p['family_id']
            if fid not in family_map:
                family_map[fid] = []
            family_map[fid].append(p)

        by_stem = {}
        nonhuman_only = []
        entry_to_species = {}

        for _family_id, members in family_map.items():
            human_members = [m for m in members if m['species_id'] == 1]
            member_infos = []
            for m in members:
                common = (m['species__common_name'] or '').strip() or (m['species__latin_name'] or '').strip()
                latin = (m['species__latin_name'] or '').strip()
                if common and latin and common != latin:
                    label = '{} ({})'.format(common, latin)
                elif latin:
                    label = latin
                else:
                    label = m['entry_name']
                is_human = (m['species_id'] == 1)
                stem = m['entry_name'].split('_')[0]
                info = {
                    'entry': m['entry_name'],
                    'stem': stem,
                    'common': common,
                    'latin': latin,
                    'label': label,
                    'is_human': is_human,
                }
                member_infos.append(info)
                entry_to_species[m['entry_name']] = {
                    'common': common,
                    'latin': latin,
                    'label': label,
                    'is_human': is_human,
                }

            if human_members:
                human_stem = human_members[0]['entry_name'].split('_')[0]
                if human_stem not in by_stem:
                    by_stem[human_stem] = member_infos
            else:
                for info in member_infos:
                    info['label_suffix'] = '({} only)'.format(
                        info['common'] or info['latin'] or 'Unknown'
                    )
                    nonhuman_only.append(info)

        return {
            'by_stem': by_stem,
            'nonhuman_only': nonhuman_only,
            'entry_to_species': entry_to_species,
        }

    @staticmethod
    def generate_tree_plot(input_data): #ADD AN INPUT FILTER DICTIONARY
        ### TREE SECTION
        tree = PhylogeneticTreeGenerator()
        class_a_data = tree.get_tree_data(ProteinFamily.objects.get(name='Class A (Rhodopsin)'))
        class_b1_data = tree.get_tree_data(ProteinFamily.objects.get(name__startswith='Class B1 (Secretin)'))
        class_b2_data = tree.get_tree_data(ProteinFamily.objects.get(name__startswith='Class B2 (Adhesion)'))
        class_c_data = tree.get_tree_data(ProteinFamily.objects.get(name__startswith='Class C (Glutamate)'))
        class_f_data = tree.get_tree_data(ProteinFamily.objects.get(name__startswith='Class F (Frizzled)'))
        class_t2_data = tree.get_tree_data(ProteinFamily.objects.get(name__startswith='Class T2 (Taste 2)'))
        class_cl_data = tree.get_tree_data(ProteinFamily.objects.get(name__startswith='Other GPCRs'))
        class_o1_family = ProteinFamily.objects.filter(name__startswith='Class O1').first()
        class_o2_family = ProteinFamily.objects.filter(name__startswith='Class O2').first()
        class_o1_data = tree.get_tree_data(class_o1_family) if class_o1_family else None
        class_o2_data = tree.get_tree_data(class_o2_family) if class_o2_family else None
        ### GETTING NODES
        data_a = class_a_data.get_nodes_dict(None)
        data_b1 = class_b1_data.get_nodes_dict(None)
        data_b2 = class_b2_data.get_nodes_dict(None)
        data_c = class_c_data.get_nodes_dict(None)
        data_f = class_f_data.get_nodes_dict(None)
        data_t2 = class_t2_data.get_nodes_dict(None)
        data_cl = class_cl_data.get_nodes_dict(None)
        data_o1 = class_o1_data.get_nodes_dict(None) if class_o1_data else None
        data_o2 = class_o2_data.get_nodes_dict(None) if class_o2_data else None
        #Collating everything into a single tree
        general_options = {'depth': 4,
                           'branch_length': {1: 'Class A (Rhodopsin)',
                                             2: 'Alicarboxylic acid',
                                             3: 'Gonadotrophin-releasing hormone',
                                             4: ''},
                           'branch_trunc': 0,
                           'leaf_offset': 30,
                           'anchor': "tree_plot",
                           'label_free': [],
                           'fontSize': {
                                'class': "15px",
                                'ligandtype': "14px",
                                'receptorfamily': "13px",
                                'receptor': "12px"
                            }}
        master_dict = OrderedDict([('name', ''),
                                   ('value', 3000),
                                   ('color', ''),
                                   ('children',[])])
        class_a_dict = OrderedDict([('name', 'Class A (Rhodopsin)'),
                                   ('value', 0),
                                   ('color', 'Red'),
                                   ('children',data_a['children'])])
        class_b1_dict = OrderedDict([('name', 'Class B1 (Secretin)'),
                                   ('value', 0),
                                   ('color', 'Green'),
                                   ('children',data_b1['children'])])
        class_b2_dict = OrderedDict([('name', 'Class B2 (Adhesion)'),
                                  ('value', 0),
                                  ('color', 'Blue'),
                                  ('children',data_b2['children'])])
        class_c_dict = OrderedDict([('name', 'Class C (Glutamate)'),
                                  ('value', 0),
                                  ('color', 'Purple'),
                                  ('children',data_c['children'])])
        class_f_dict = OrderedDict([('name', 'Class F (Frizzled)'),
                                  ('value', 0),
                                  ('color', 'Grey'),
                                  ('children',data_f['children'])])
        class_t2_dict = OrderedDict([('name', 'Class T2 (Taste 2)'),
                                  ('value', 0),
                                  ('color', 'Orange'),
                                  ('children',data_t2['children'])])
        class_cl_dict = OrderedDict([('name', 'Classless'),
                                  ('value', 0),
                                  ('color', 'Gold'),
                                  ('children',data_cl['children'])])
        class_o1_dict = OrderedDict([('name', 'Class O1 (fish-like odorant)'),
                                  ('value', 0),
                                  ('color', '#66CDAA'),
                                  ('children', data_o1['children'])]) if data_o1 else None
        class_o2_dict = OrderedDict([('name', 'Class O2 (tetrapod specific odorant)'),
                                  ('value', 0),
                                  ('color', '#3CB371'),
                                  ('children', data_o2['children'])]) if data_o2 else None
        ### APPENDING TO MASTER DICT
        master_dict['children'].append(class_a_dict)
        master_dict['children'].append(class_b1_dict)
        master_dict['children'].append(class_b2_dict)
        master_dict['children'].append(class_c_dict)
        master_dict['children'].append(class_f_dict)
        master_dict['children'].append(class_t2_dict)
        master_dict['children'].append(class_cl_dict)
        if class_o1_dict:
            master_dict['children'].append(class_o1_dict)
        if class_o2_dict:
            master_dict['children'].append(class_o2_dict)

        updated_data = {key.replace('_human', ''): value for key, value in input_data.items()}
        circles = {
            key.replace('_human', '').upper(): {k: v for k, v in value.items()}
            for key, value in input_data.items()
        }

        # Empty payloads: Mapper 2.0 loads the full skeleton client-side — do not prune the tree yet.
        if input_data:
            master_dict = DataMapperHome.keep_by_names(master_dict, updated_data)

        if isinstance(master_dict, dict) and master_dict.get('children') is not None and len(master_dict['children']) == 1:
            master_dict = master_dict['children'][0]
            general_options['depth'] = 3
            general_options['branch_length'] = {1: 'Alicarboxylic acid',
                                             2: 'Gonadotrophin-releasing hormone',
                                             3: ''}
        else:
            pass

        entry_names_list = list(input_data.keys())  # or: input_data.keys()

        # Step 2: Trim protein queries
        whole_receptors = Protein.objects.filter(entry_name__in=entry_names_list).prefetch_related("family", "family__parent__parent__parent")

        protein_genes = Protein.objects.filter(entry_name__in=entry_names_list).prefetch_related('genes')

        entry_to_gene = {
            protein.entry_name: next((g.name for g in protein.genes.all() if g.position == 0), None)
            for protein in protein_genes
        }

        whole_rec_dict = {}
        entrez_label_dict = {}
        for rec in whole_receptors:
            rec_entryname = rec.entry_name
            rec_uniprot = rec.entry_short()
            rec_iuphar = rec.family.name.replace("receptor", '').replace("<i>", "").replace("</i>", "").strip()
            if (rec_iuphar[0].isupper()) or (rec_iuphar[0].isdigit()):
                whole_rec_dict[rec_uniprot] = [rec_iuphar]
            else:
                whole_rec_dict[rec_uniprot] = [rec_iuphar.capitalize()]
            # Entrez label — safely
            gene_name = entry_to_gene.get(rec_entryname)
            if gene_name:
                entrez_label_dict[rec_uniprot] = [gene_name]

        return master_dict, general_options, circles, whole_rec_dict, entrez_label_dict

    @staticmethod
    def recompute_position_layout(data):
        """
        Re-run t-SNE from user-submitted receptor "Position" values — the live
        recompute POSTed by ClusterRender.post whenever every row in the Position
        column is filled in. Class/Ligand type/Receptor family aren't returned —
        the client already holds that metadata (window.MAPPER_CLUSTER_ALL_POSITIONS)
        and merges it back in via getClusterPosMap() (mapper_cluster_page.js).
        """
        labels = [entry_name.replace('_human', '') for entry_name in data.keys()]
        positions = pd.Series(
            [row.get('Value2') for row in data.values()],
            index=labels,
            dtype=float,
        )
        positions = positions.fillna(positions.mean())

        distance_matrix = np.abs(positions.values.reshape(-1, 1) - positions.values.reshape(1, -1))

        n_points = distance_matrix.shape[0]
        perplexity = max(2, min(n_points / 5, 50))
        tsne = TSNE(n_components=2, metric='precomputed', init='random', random_state=42, perplexity=perplexity)
        coords = tsne.fit_transform(distance_matrix)
        clusters = KMeans(n_clusters=5, random_state=42).fit_predict(coords)

        result = pd.DataFrame(coords, columns=['x', 'y'])
        result['cluster'] = clusters
        result['label'] = positions.index
        return result.to_json(orient='records')

    # Global sequence-similarity t-SNE layout for all human GPCRs, sourced from
    # classification.ClusterCoord (built offline by build_clustercoord from ReceptorSimilarity)
    @staticmethod
    def generate_full_matrix():
        rows = (
            ClusterCoord.objects
            .filter(
                dataset_type=ClusterCoord.DATASET_SEQUENCE,
                plot_type=ClusterCoord.PLOT_TSNE,
                group_key='global',
                protein__species_id=1,
            )
            .select_related('protein')
            .order_by('protein__entry_name')
        )
        reduced_df = pd.DataFrame([
            {'label': r.protein.entry_name.replace('_human', ''), 'x': r.x, 'y': r.y}
            for r in rows.iterator()
        ])

        # add class/ligand_type/receptor_family clusters

        # Step 1: Fetch data
        proteins = Protein.objects.filter(
            parent_id__isnull=True, species_id=1
        ).values_list(
            'entry_name',
            "family__parent__parent__parent__name",  # To be renamed as 'Class'
            'family__parent__parent__name',  # To be renamed as 'Ligand type'
            'family__parent__name'  # To be renamed as 'Receptor family'
        )

        # Step 2: Convert to a DataFrame
        proteins_df = pd.DataFrame(list(proteins), columns=['entry_name', 'Class', 'Ligand type', 'Receptor family'])

        # Step 3: Remove '_human' suffix from 'entry_name'
        proteins_df['entry_name'] = proteins_df['entry_name'].str.replace('_human', '')

        # Step 4: Rename 'entry_name' to 'label'
        proteins_df = proteins_df.rename(columns={'entry_name': 'label'})

        # Step 5: Merge with reduced_df on 'label'
        merged_df = pd.merge(reduced_df, proteins_df, on='label', how='left')

        return merged_df

    @staticmethod
    def Label_conversion_info(data):
        # Get list of keys (UniProt-style entry names)
        Name_list = list(data.keys())

        # Fetch IUPHAR names
        names_dict = Protein.objects.filter(entry_name__in=Name_list).values('entry_name', 'name').order_by('entry_name')
        UniProt_to_IUPHAR_converter = {item['entry_name']: item['name'] for item in names_dict}
        IUPHAR_to_UniProt_converter = {item['name']: item['entry_name'] for item in names_dict}

        # Fetch genes with position == 0
        protein_genes = Protein.objects.prefetch_related('genes').filter(entry_name__in=Name_list)
        UniProt_to_Gene_converter = {
            protein.entry_name: next((g.name for g in protein.genes.all() if g.position == 0), None)
            for protein in protein_genes
        }

        # Create IUPHAR to Gene mapping using the two converters above
        IUPHAR_to_Gene_converter = {
            iuphar: UniProt_to_Gene_converter.get(entry)
            for iuphar, entry in IUPHAR_to_UniProt_converter.items()
            if UniProt_to_Gene_converter.get(entry) is not None
        }

        # Final converter dict
        Label_converter = {
            'UniProt_to_IUPHAR_converter': UniProt_to_IUPHAR_converter,
            'IUPHAR_to_UniProt_converter': IUPHAR_to_UniProt_converter,
            'UniProt_to_Gene_converter': UniProt_to_Gene_converter,
            'IUPHAR_to_Gene_converter': IUPHAR_to_Gene_converter
        }

        return Label_converter


class ClusterRender(View):
    def post(self, request, *args, **kwargs):
        Data_json = request.POST.get('Data')

        try:
            # Get data
            Data = json.loads(Data_json)
            # Calculate the plot
            output_seq = DataMapperHome.recompute_position_layout(Data)
            label_converter = DataMapperHome.Label_conversion_info(Data)

            return JsonResponse({
                'cluster_data_seq': json.loads(output_seq),
                'Label_converter': label_converter
            })

        except json.JSONDecodeError:
            return HttpResponse("Invalid JSON data")

class MapperLandingPageView(TemplateView):
    template_name = 'mapper/Mapper_landingPage.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        wheel_maps = DataMapperHome.build_gpcrome_receptor_normalization_maps()
        full_maps = DataMapperHome.build_gpcrome_receptor_normalization_maps(include_odorant=True)
        context['wheel_max_receptors'] = len(wheel_maps['proteins_gpcrome_tree'])
        context['full_max_receptors'] = len(full_maps['proteins_gpcrome_tree'])
        return context


class MapperGPCRomeWheelView(TemplateView):
    template_name = 'mapper/Mapper_GPCRomeWheel.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base = DataMapperHome.GenerateGPCRomeDataStructure(data_type="Classic")
        structure = deepcopy(base["Data"])
        merged = DataMapperHome.update_nested_GPCRome_data(structure, {})
        context['GPCRomeData'] = json.dumps(merged)
        context['PlotType'] = 'Numeric'
        maps = DataMapperHome.build_gpcrome_receptor_normalization_maps()
        context['receptor_select2_json'] = json.dumps(
            DataMapperHome.gpcrome_receptor_select2_options(maps=maps)
        )
        context['gpcrome_resolve_json'] = json.dumps(
            DataMapperHome.gpcrome_receptor_client_resolve_map(maps=maps)
        )
        context['gpcrome_picker_rows_json'] = json.dumps(
            DataMapperHome.gpcrome_receptor_picker_table_rows(maps=maps)
        )
        return context


class MapperTreeView(TemplateView):
    template_name = 'mapper/Mapper_Tree.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        master_dict, general_options, circles, receptors, genes = DataMapperHome.generate_tree_plot({})
        context['tree'] = json.dumps(master_dict)
        context['tree_options'] = json.dumps(general_options)
        context['circles'] = json.dumps(circles if circles else {})
        context['Receptor_dict'] = json.dumps(receptors if receptors else {})
        context['Entrez_dict'] = json.dumps(genes if genes else {})
        context['PlotType'] = 'Numeric'
        context['Data'] = json.dumps({})
        maps = DataMapperHome.build_gpcrome_receptor_normalization_maps(include_odorant=True)
        context['receptor_select2_json'] = json.dumps(
            DataMapperHome.gpcrome_receptor_select2_options(maps=maps)
        )
        context['gpcrome_resolve_json'] = json.dumps(
            DataMapperHome.gpcrome_receptor_client_resolve_map(maps=maps)
        )
        ortholog_map = DataMapperHome.build_ortholog_species_map()
        context['ortholog_species_json'] = json.dumps(ortholog_map)
        nonhuman_entries, nonhuman_stem_by_entry = DataMapperHome.gpcrome_collapse_nonhuman_only(
            ortholog_map['nonhuman_only']
        )
        context['gpcrome_picker_rows_json'] = json.dumps(
            DataMapperHome.gpcrome_receptor_picker_table_rows(
                maps=maps, extra_entry_names=nonhuman_entries, entry_label_overrides=nonhuman_stem_by_entry
            )
        )
        return context


class MapperHeatmapView(TemplateView):
    template_name = 'mapper/Mapper_Heatmap.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        maps = DataMapperHome.build_gpcrome_receptor_normalization_maps(include_odorant=True)
        context['receptor_select2_json'] = json.dumps(
            DataMapperHome.gpcrome_receptor_select2_options(maps=maps)
        )
        context['gpcrome_resolve_json'] = json.dumps(
            DataMapperHome.gpcrome_receptor_client_resolve_map(maps=maps)
        )
        ortholog_map = DataMapperHome.build_ortholog_species_map()
        context['ortholog_species_json'] = json.dumps(ortholog_map)
        nonhuman_entries, nonhuman_stem_by_entry = DataMapperHome.gpcrome_collapse_nonhuman_only(
            ortholog_map['nonhuman_only']
        )
        context['gpcrome_picker_rows_json'] = json.dumps(
            DataMapperHome.gpcrome_receptor_picker_table_rows(
                maps=maps, extra_entry_names=nonhuman_entries, entry_label_overrides=nonhuman_stem_by_entry
            )
        )
        return context


class MapperListView(TemplateView):
    template_name = 'mapper/Mapper_List.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        maps = DataMapperHome.build_gpcrome_receptor_normalization_maps(include_odorant=True)
        context['receptor_select2_json'] = json.dumps(
            DataMapperHome.gpcrome_receptor_select2_options(maps=maps)
        )
        context['gpcrome_resolve_json'] = json.dumps(
            DataMapperHome.gpcrome_receptor_client_resolve_map(maps=maps)
        )
        ortholog_map = DataMapperHome.build_ortholog_species_map()
        context['ortholog_species_json'] = json.dumps(ortholog_map)
        nonhuman_entries, nonhuman_stem_by_entry = DataMapperHome.gpcrome_collapse_nonhuman_only(
            ortholog_map['nonhuman_only']
        )
        context['gpcrome_picker_rows_json'] = json.dumps(
            DataMapperHome.gpcrome_receptor_picker_table_rows(
                maps=maps, extra_entry_names=nonhuman_entries, entry_label_overrides=nonhuman_stem_by_entry
            )
        )
        context['receptor_info_json'] = json.dumps(
            DataMapperHome.gpcrome_receptor_info_for_list(
                maps=maps, extra_entry_names=nonhuman_entries, entry_label_overrides=nonhuman_stem_by_entry
            )
        )
        return context


class MapperClusterView(TemplateView):
    template_name = 'mapper/Mapper_Cluster.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        maps = DataMapperHome.build_gpcrome_receptor_normalization_maps(include_odorant=True)
        context['receptor_select2_json'] = json.dumps(
            DataMapperHome.gpcrome_receptor_select2_options(maps=maps)
        )
        context['gpcrome_resolve_json'] = json.dumps(
            DataMapperHome.gpcrome_receptor_client_resolve_map(maps=maps)
        )
        context['gpcrome_picker_rows_json'] = json.dumps(
            DataMapperHome.gpcrome_receptor_picker_table_rows(maps=maps)
        )
        full_matrix = DataMapperHome.generate_full_matrix()
        context['cluster_all_positions_json'] = full_matrix.to_json(orient='records')
        return context

