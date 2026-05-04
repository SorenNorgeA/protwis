from django.conf import settings
from django.core.cache import cache, caches
from django.db.models import Case, F, IntegerField, Max, Prefetch, Q, When
from django.db.models.functions import Greatest, Least, Upper
from django.http import Http404, JsonResponse
from django.urls import reverse
from django.utils.text import slugify
from django.views import View
from django.views.generic import TemplateView

from classification.family_tree import (
    SUPERFAMILY_TREE_GROUP_KEY,
    SUPERFAMILY_TREE_VARIANT,
    build_superfamily_tree_payload,
)
from classification.models import ClusterCoord, ReceptorSimilarity, StructureSimilarity, TreeNetwork
from common.models import WebLink
from mapper.views import DataMapperHome
from protein.models import Gene, Protein, ProteinFamily, ProteinFamilyClassification, ProteinState
from collections import OrderedDict, defaultdict
import math
import time
import json
import os
import re
from string import Template
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import scipy.cluster.hierarchy as sch
import scipy.spatial.distance as ssd
from sklearn.manifold import TSNE


try:
    cache_alignment = caches["alignments"]
except Exception:
    cache_alignment = cache


class ClassificationVisualizationMixin:
    GLOBAL_GROUP_KEY = "global"
    CLASS_GROUP_PREFIX = "class:"
    BROWSER_CLASS_ORDER = ["A", "B1", "B2", "C", "F", "T2", "O1", "O2", "U"]
    MODALITY_GROUP_LABELS = [
        "Orphan receptors",
        "Polypeptide receptors",
        "Small molecule receptors",
    ]
    ORPHAN_MODALITY_KEYS = {"orphan receptors"}
    POLYPEPTIDE_MODALITY_KEYS = {"peptide receptors", "protein receptors", "polypeptide receptors"}
    ORPHAN_MODALITY_GROUP_LABEL = "Orphan receptors"
    ORPHAN_SEARCH_SPLIT_LABELS = {"orphan receptors", "orphans receptors"}
    UNCLASSIFIED_BROWSER_LABEL = "Unclassified / Other GPCRs"
    UNCLASSIFIED_CLASS_NAME_CANDIDATES = ("Unclassified", "Classless", "Other GPCRs", "Unclassified / Other GPCRs")
    CLASS_KEY_ALIASES = {
        "UNCLASSIFIED": "U",
        "CLASSLESS": "U",
        "OTHERGPCRS": "U",
        "UNCLASSIFIEDOTHERGPCRS": "U",
    }
    CLASS_VISUALIZATION_CONFIG = OrderedDict([
        ("A", {"label": "Class A", "title": "Class A (Rhodopsin)", "slug": "001"}),
        ("B1", {"label": "Class B1", "title": "Class B1 (Secretin)", "slug": "002"}),
        ("B2", {"label": "Class B2", "title": "Class B2 (Adhesion)", "slug": "003"}),
        ("C", {"label": "Class C", "title": "Class C (Glutamate)", "slug": "004"}),
        ("F", {"label": "Class F", "title": "Class F (Frizzled)", "slug": "006"}),
        ("T2", {"label": "Class T2", "title": "Class T2 (Taste 2)", "slug": "009"}),
        ("O1", {"label": "Class O1", "title": "Class O1 (Fish-like olfactory receptors)", "slug": "007"}),
        ("O2", {"label": "Class O2", "title": "Class O2 (Tetrapod-specific olfactory receptors)", "slug": "008"}),
        ("U", {"label": "Unclassified", "title": "Unclassified", "slug": "010"}),
    ])
    TREE_DISABLED_CLASS_KEYS = {"O1", "O2", "U"}

    @classmethod
    def normalize_visualization_class_key(cls, raw_value):
        value = str(raw_value or "").strip().upper()
        if value in cls.CLASS_VISUALIZATION_CONFIG:
            return value
        compact_value = re.sub(r"[^A-Z0-9]", "", value)
        return cls.CLASS_KEY_ALIASES.get(compact_value)

    @classmethod
    def get_visualization_class_config(cls, class_key):
        key = cls.normalize_visualization_class_key(class_key)
        if not key:
            return None
        config = dict(cls.CLASS_VISUALIZATION_CONFIG[key])
        config["key"] = key
        config["group_key"] = cls.class_group_key(key)
        config["has_tree"] = key not in cls.TREE_DISABLED_CLASS_KEYS
        return config

    @classmethod
    def list_visualization_classes(cls):
        return [cls.get_visualization_class_config(key) for key in cls.CLASS_VISUALIZATION_CONFIG.keys()]

    @classmethod
    def class_group_key(cls, class_key):
        return f"{cls.CLASS_GROUP_PREFIX}{class_key}"

    @classmethod
    def class_key_from_group_key(cls, group_key):
        value = str(group_key or "").strip()
        if not value.startswith(cls.CLASS_GROUP_PREFIX):
            return None
        return cls.normalize_visualization_class_key(value[len(cls.CLASS_GROUP_PREFIX):])

    @staticmethod
    def _classification_df():
        return Classification_tree()._load_df()

    def _group_modality_label(self, modality):
        key = str(modality or "").strip().lower()
        if key in self.ORPHAN_MODALITY_KEYS:
            return "Orphan receptors"
        if key in self.POLYPEPTIDE_MODALITY_KEYS:
            return "Polypeptide receptors"
        return "Small molecule receptors"

    @classmethod
    def _class_title_to_key_map(cls):
        mapping = {}
        legacy_title_aliases = {
            "Class O1 (fish-like)": "O1",
            "Class O1 (fish-like odorant)": "O1",
            "Class O1 (fish-like olfactory receptor)": "O1",
            "Class O1 (fish-like olfactory receptors)": "O1",
            "Class O2 (tetrapod specific)": "O2",
            "Class O2 (tetrapod specific odorant)": "O2",
            "Class O2 (tetrapod-specific olfactory receptor)": "O2",
            "Class O2 (tetrapod-specific olfactory receptors)": "O2",
            "Unclassified": "U",
            "Classless": "U",
            "Other GPCRs": "U",
            "Unclassified / Other GPCRs": "U",
        }
        for key, cfg in cls.CLASS_VISUALIZATION_CONFIG.items():
            title = cfg["title"]
            mapping[title] = key
            mapping[title.lower()] = key
        for alias, key in legacy_title_aliases.items():
            mapping[alias] = key
            mapping[alias.lower()] = key
        return mapping

    @staticmethod
    def _natural_sort_key(value):
        chunks = re.split(r"(\d+)", str(value or "").strip().lower())
        key = []
        for chunk in chunks:
            if chunk == "":
                continue
            if chunk.isdigit():
                key.append((0, int(chunk)))
            else:
                key.append((1, chunk))
        return tuple(key)

    @classmethod
    def _browser_class_key(cls, class_label, class_key=None):
        if class_key:
            return class_key
        label = str(class_label or "").strip()
        if not label:
            return None
        return "U"

    @classmethod
    def _browser_class_label(cls, browser_class_key):
        if browser_class_key == "U":
            return cls.UNCLASSIFIED_BROWSER_LABEL
        config = cls.get_visualization_class_config(browser_class_key)
        if config:
            return config["title"]
        return str(browser_class_key or "").strip()

    def _build_receptor_family_catalog(self):
        df = self._classification_df()
        clean = Classification_tree._clean_cell
        split = Classification_tree._split_uniprot_cell
        class_title_to_key = self._class_title_to_key_map()

        normalized_rows = []
        orphan_family_classes = defaultdict(OrderedDict)
        for _, row in df.iterrows():
            class_label = clean(row.get("Class"))
            chemotype = clean(row.get("Chemotype"))
            family_name = clean(row.get("Receptor family"))
            modality = clean(row.get("Modality"))
            receptors = split(row.get("GPCRs (UniProt)"))
            if not class_label or not chemotype or not family_name:
                continue
            modality_group = self._group_modality_label(modality)
            normalized_rows.append({
                "class_label": class_label,
                "chemotype": chemotype,
                "family_name": family_name,
                "modality_group": modality_group,
                "receptors": receptors,
            })
            if modality_group == self.ORPHAN_MODALITY_GROUP_LABEL:
                orphan_family_classes[family_name][class_label] = True

        family_rows = OrderedDict()
        hierarchy = OrderedDict()
        for row in normalized_rows:
            class_label = row["class_label"]
            chemotype = row["chemotype"]
            family_name = row["family_name"]
            modality_group = row["modality_group"]
            receptors = row["receptors"]
            class_key = class_title_to_key.get(class_label) or class_title_to_key.get(str(class_label).lower())
            browser_class_key = self._browser_class_key(class_label, class_key)
            class_config = self.get_visualization_class_config(class_key) if class_key else None
            split_orphan_by_class = (
                modality_group == self.ORPHAN_MODALITY_GROUP_LABEL
                and len(orphan_family_classes.get(family_name, {})) > 1
            )
            family_storage_key = (
                "{}::{}".format(family_name, class_label)
                if split_orphan_by_class else family_name
            )
            family_label = family_name
            if split_orphan_by_class and str(family_name).strip().lower() in self.ORPHAN_SEARCH_SPLIT_LABELS:
                family_label = "{} ({})".format(
                    family_name,
                    class_config["label"] if class_config else class_label,
                )

            entry = family_rows.setdefault(family_storage_key, {
                "storage_key": family_storage_key,
                "name": family_name,
                "label": family_label,
                "browser_label": family_name,
                "modality_groups": OrderedDict(),
                "classes": OrderedDict(),
                "chemotypes": OrderedDict(),
                "receptors": OrderedDict(),
                "split_by_class": split_orphan_by_class,
                "split_class_label": class_label if split_orphan_by_class else None,
                "split_class_key": class_key if split_orphan_by_class else None,
                "class_slug_prefix": class_config["slug"] if split_orphan_by_class and class_config else None,
            })
            entry["classes"][class_label] = True
            entry["chemotypes"][chemotype] = True
            entry["modality_groups"][modality_group] = True
            for receptor in receptors:
                entry["receptors"][receptor] = True

            if not browser_class_key:
                continue
            class_bucket = hierarchy.setdefault(browser_class_key, {
                "label": self._browser_class_label(browser_class_key),
                "chemotypes": OrderedDict(),
            })
            chemotype_bucket = class_bucket["chemotypes"].setdefault(chemotype, {
                "modality_group": modality_group,
                "families": OrderedDict(),
            })
            chemotype_bucket["modality_group"] = modality_group
            chemotype_bucket["families"][family_name] = family_storage_key

        key_counts = defaultdict(int)
        family_entries = []
        family_lookup = OrderedDict()
        family_by_storage_key = {}
        for family_storage_key in sorted(
            family_rows.keys(),
            key=lambda value: (
                self._natural_sort_key(family_rows[value]["name"]),
                self._natural_sort_key(family_rows[value].get("split_class_label") or ""),
            ),
        ):
            raw = family_rows[family_storage_key]
            base_key_parts = [raw["name"]]
            if raw.get("split_class_key"):
                base_key_parts.append(raw["split_class_key"])
            elif raw.get("split_class_label"):
                base_key_parts.append(raw["split_class_label"])
            base_key = slugify("-".join(base_key_parts)) or "family"
            key_counts[base_key] += 1
            family_key = base_key if key_counts[base_key] == 1 else f"{base_key}-{key_counts[base_key]}"
            class_labels = list(raw["classes"].keys())
            chemotype_labels = list(raw["chemotypes"].keys())
            receptor_labels = list(raw["receptors"].keys())
            modality_groups = list(raw["modality_groups"].keys())
            class_keys = []
            for label in class_labels:
                lookup_key = class_title_to_key.get(label) or class_title_to_key.get(str(label).lower())
                if lookup_key:
                    class_keys.append(lookup_key)
                else:
                    class_keys.append("U")
            entry = {
                "key": family_key,
                "name": raw["name"],
                "label": raw["label"],
                "browser_label": raw["browser_label"],
                "url": reverse("classification-visualizations-family", kwargs={"family_key": family_key}),
                "class_labels": class_labels,
                "class_keys": class_keys,
                "split_by_class": raw["split_by_class"],
                "split_class_label": raw["split_class_label"],
                "split_class_key": raw["split_class_key"],
                "class_slug_prefix": raw["class_slug_prefix"],
                "chemotypes": chemotype_labels,
                "modality_groups": modality_groups,
                "receptor_labels": receptor_labels,
                "receptor_count": len(receptor_labels),
            }
            family_entries.append(entry)
            family_lookup[family_key] = entry
            family_by_storage_key[family_storage_key] = entry

        browser_nodes = []
        browser_order = {key: idx for idx, key in enumerate(self.BROWSER_CLASS_ORDER)}
        for browser_class_key in sorted(hierarchy.keys(), key=lambda value: browser_order.get(value, 999)):
            class_bucket = hierarchy[browser_class_key]
            grouped_chemotypes = []
            aggregated_families = OrderedDict()

            for chemotype in sorted(class_bucket["chemotypes"].keys(), key=self._natural_sort_key):
                families = []
                chemotype_node = class_bucket["chemotypes"][chemotype]
                modality_group = chemotype_node.get("modality_group") or "Small molecule receptors"
                modality_theme = slugify(modality_group)
                for family_name in sorted(chemotype_node["families"].keys(), key=self._natural_sort_key):
                    family_storage_key = chemotype_node["families"].get(family_name)
                    family_entry = family_by_storage_key.get(family_storage_key)
                    if not family_entry:
                        continue
                    family_node = {
                        "key": family_entry["key"],
                        "label": family_entry.get("browser_label") or family_name,
                        "url": family_entry["url"],
                        "receptor_count": family_entry["receptor_count"],
                    }
                    families.append(family_node)
                    aggregated_families[family_node["key"]] = family_node
                if families:
                    grouped_chemotypes.append({
                        "label": chemotype,
                        "modality_group": modality_group,
                        "modality_theme": modality_theme,
                        "families": families,
                    })

            if grouped_chemotypes:
                browser_nodes.append({
                    "label": class_bucket["label"],
                    "class_key": browser_class_key,
                    "interactive_chemotypes": browser_class_key == "A",
                    "chemotypes": grouped_chemotypes,
                    "families": sorted(
                        aggregated_families.values(),
                        key=lambda family: self._natural_sort_key(family.get("label") or "")
                    ),
                })

        return {
            "entries": family_entries,
            "lookup": family_lookup,
            "browser_nodes": browser_nodes,
        }

    def get_receptor_family_entry(self, family_key):
        catalog = self._build_receptor_family_catalog()
        return catalog["lookup"].get(str(family_key or "").strip()), catalog

    def _visualization_class_name_candidates(self, class_label):
        label = str(class_label or "").strip()
        if not label:
            return []
        if label.lower() in {"unclassified", "classless", "other gpcrs", "unclassified / other gpcrs"}:
            return list(self.UNCLASSIFIED_CLASS_NAME_CANDIDATES)
        return [label]

    def _visualization_class_family_q(self, class_key, family_path_prefix):
        config = self.get_visualization_class_config(class_key)
        if not config:
            return Q()

        prefix = str(family_path_prefix or "")
        q_obj = Q(**{prefix + "slug": config["slug"]})
        for candidate in self._visualization_class_name_candidates(config["title"]):
            q_obj |= Q(**{prefix + "name__iexact": candidate})
        return q_obj

    def _visualization_class_protein_q(self, class_key):
        return self._visualization_class_family_q(class_key, "family__parent__parent__parent__")

    def _visualization_class_clustercoord_q(self, class_key):
        return self._visualization_class_family_q(class_key, "protein__family__parent__parent__parent__")

    def _visualization_receptor_label_q(self, receptor_labels):
        q_obj = Q()
        for label in receptor_labels:
            clean_label = str(label or "").strip()
            if not clean_label:
                continue
            q_obj |= Q(accession__iexact=clean_label)
            q_obj |= Q(entry_name__istartswith="{}_".format(clean_label))
        return q_obj

    def _visualization_family_queryset_class_q(self, class_slug_prefix=None, class_label=None):
        q_obj = Q()
        if class_slug_prefix:
            q_obj |= Q(family_slug__startswith=class_slug_prefix)
        if class_label:
            for candidate in self._visualization_class_name_candidates(class_label):
                q_obj |= Q(family__parent__parent__parent__name__iexact=candidate)
        return q_obj

    def _get_visualization_family_queryset(self, family_name, class_slug_prefix=None, class_label=None, exact_family_name_only=False):
        qs = (
            Protein.objects
            .annotate(
                family_slug=F("family__slug"),
                entry_name_upper=Upper("entry_name"),
                accession_upper=Upper("accession"),
            )
            .filter(
                parent_id__isnull=True,
                species__common_name__iexact="Human",
            )
            .exclude(accession=None)
            .select_related("family__parent__parent__parent")
            .prefetch_related(
                Prefetch(
                    "genes",
                    queryset=Gene.objects.only("name", "position").order_by("position"),
                    to_attr="primary_genes_self",
                )
            )
            .order_by("entry_name")
            .distinct()
        )
        if exact_family_name_only:
            qs = qs.filter(
                Q(family__parent__name__iexact=family_name)
                | Q(family__name__iexact=family_name)
            )
        else:
            qs = qs.filter(
                Q(family__parent__name__iexact=family_name)
                | Q(family__name__iexact=family_name)
            )
        class_scope_q = self._visualization_family_queryset_class_q(
            class_slug_prefix=class_slug_prefix,
            class_label=class_label,
        )
        if class_scope_q:
            qs = qs.filter(class_scope_q)
        return qs

    def get_visualization_family_proteins(self, family_entry):
        family_name = family_entry["name"]
        receptor_labels = [
            str(label).strip().upper()
            for label in family_entry.get("receptor_labels", [])
            if str(label).strip()
        ]
        split_by_class = bool(family_entry.get("split_by_class"))
        class_slug_prefix = family_entry.get("class_slug_prefix")
        if split_by_class:
            if receptor_labels:
                receptor_q = self._visualization_receptor_label_q(receptor_labels)
                qs = (
                    Protein.objects
                    .annotate(
                        family_slug=F("family__slug"),
                    )
                    .filter(
                        parent_id__isnull=True,
                        species__common_name__iexact="Human",
                    )
                    .exclude(accession=None)
                    .filter(receptor_q)
                    .select_related("family__parent__parent__parent")
                    .prefetch_related(
                        Prefetch(
                            "genes",
                            queryset=Gene.objects.only("name", "position").order_by("position"),
                            to_attr="primary_genes_self",
                        )
                    )
                    .order_by("entry_name")
                    .distinct()
                )
                class_scope_q = self._visualization_family_queryset_class_q(
                    class_slug_prefix=class_slug_prefix,
                    class_label=family_entry.get("split_class_label"),
                )
                if class_scope_q:
                    qs = qs.filter(class_scope_q)
            else:
                qs = self._get_visualization_family_queryset(
                    family_name=family_name,
                    class_slug_prefix=class_slug_prefix,
                    class_label=family_entry.get("split_class_label"),
                    exact_family_name_only=True,
                )
        else:
            qs = self._get_visualization_family_queryset(
                family_name=family_name,
                class_slug_prefix=class_slug_prefix,
                class_label=family_entry.get("split_class_label"),
                exact_family_name_only=False,
            )
            if receptor_labels:
                qs = qs.filter(self._visualization_receptor_label_q(receptor_labels))
        return list(qs)

    def get_requested_visualization_scope(self, request=None, raise_404=False):
        request = request or self.request
        raw_class_key = request.GET.get("class")
        class_key = self.normalize_visualization_class_key(raw_class_key)
        if not raw_class_key:
            return {
                "class_key": None,
                "group_key": self.GLOBAL_GROUP_KEY,
                "config": None,
            }
        if not class_key:
            if raise_404:
                raise Http404("Unknown class visualization")
            raise ValueError("Unknown class visualization")
        config = self.get_visualization_class_config(class_key)
        if not config:
            if raise_404:
                raise Http404("Unknown class visualization")
            raise ValueError("Unknown class visualization")
        return {
            "class_key": class_key,
            "group_key": config["group_key"],
            "config": config,
        }

    @staticmethod
    def normalize_tree_selection_type(raw_value):
        value = str(raw_value or "").strip()
        if value.lower() == "modality":
            return "Modality"
        if value.lower() == "chemotype":
            return "Chemotype"
        return ""

    @staticmethod
    def _format_similarity_display(value):
        if value is None:
            return ""
        try:
            rounded = round(float(value), 1)
        except Exception:
            return ""
        if rounded == int(rounded):
            return str(int(rounded))
        return "{:.1f}".format(rounded)

    def resolve_tree_visualization_selection(self, tree_type, selection):
        normalized_type = self.normalize_tree_selection_type(tree_type)
        raw_selection = str(selection or "").strip()
        if not normalized_type or not raw_selection:
            raise ValueError("Unknown classification tree selection")

        df = self._classification_df()
        clean = Classification_tree._clean_cell
        split = Classification_tree._split_uniprot_cell
        requested_key = raw_selection.lower()
        class_title_to_key = self._class_title_to_key_map()

        rows = []
        normalized_selection = raw_selection
        for _, row in df.iterrows():
            if normalized_type == "Modality":
                raw_modality = clean(row.get("Modality")) or ""
                grouped_modality = self._group_modality_label(raw_modality)
                if (
                    grouped_modality.lower() != requested_key
                    and raw_modality.lower() != requested_key
                ):
                    continue
                normalized_selection = grouped_modality
            else:
                chemotype = clean(row.get("Chemotype")) or ""
                if chemotype.lower() != requested_key:
                    continue
                normalized_selection = chemotype
            rows.append(row)

        if not rows:
            raise ValueError("Unknown classification tree selection")

        class_keys = OrderedDict()
        receptor_labels = OrderedDict()
        entry_names = OrderedDict()
        for row in rows:
            class_label = clean(row.get("Class")) or ""
            class_key = class_title_to_key.get(class_label) or class_title_to_key.get(class_label.lower())
            if class_key:
                class_keys[class_key] = True
            for receptor in split(row.get("GPCRs (UniProt)")):
                label = str(receptor or "").strip().upper()
                if not label:
                    continue
                receptor_labels[label] = True
                entry_names["{}_human".format(label.lower())] = True

        return {
            "type": normalized_type,
            "selection": normalized_selection,
            "class_keys": list(class_keys.keys()),
            "receptor_labels": list(receptor_labels.keys()),
            "entry_names": list(entry_names.keys()),
            "receptor_count": len(receptor_labels),
        }

    def build_tree_selection_matrix_payload(self, selection_info):
        entry_names = list((selection_info or {}).get("entry_names") or [])
        if not entry_names:
            return {"entities": [], "matrix": [], "meta": {"n_points": 0}}

        proteins = list(
            Protein.objects
            .filter(entry_name__in=entry_names)
            .only("id", "entry_name", "name", "accession")
            .prefetch_related(
                Prefetch(
                    "genes",
                    queryset=Gene.objects.only("name", "position").order_by("position"),
                    to_attr="primary_genes_self",
                )
            )
            .order_by("entry_name")
        )
        if not proteins:
            return {"entities": [], "matrix": [], "meta": {"n_points": 0}}

        protein_ids = [protein.id for protein in proteins]
        pair_identity = {}
        pair_similarity = {}
        pair_qs = (
            ReceptorSimilarity.objects
            .filter(protein_ref_id__in=protein_ids, protein_target_id__in=protein_ids)
            .values("protein_ref_id", "protein_target_id", "identity", "similarity")
        )
        for rec in pair_qs.iterator():
            try:
                a = int(rec["protein_ref_id"])
                b = int(rec["protein_target_id"])
            except Exception:
                continue
            if a == b:
                continue
            key = (a, b) if a < b else (b, a)
            try:
                identity = float(rec["identity"])
            except Exception:
                identity = None
            try:
                similarity = float(rec["similarity"])
            except Exception:
                similarity = None
            if identity is not None:
                previous = pair_identity.get(key)
                if previous is None or identity > previous:
                    pair_identity[key] = identity
            if similarity is not None:
                previous = pair_similarity.get(key)
                if previous is None or similarity > previous:
                    pair_similarity[key] = similarity

        entities = []
        for protein in proteins:
            primary_genes = getattr(protein, "primary_genes_self", None) or []
            gene_label = primary_genes[0].name if primary_genes else (
                str(protein.entry_name or "").split("_", 1)[0].upper()
            )
            entities.append({
                "symbol": protein.entry_name,
                "name": protein.short(),
                "short_label": protein.entry_short(),
                "gene_label": gene_label,
            })

        matrix = []
        for i, protein in enumerate(proteins):
            row = []
            for j, other_protein in enumerate(proteins):
                if i == j:
                    identity = 100.0
                    similarity = 100.0
                else:
                    key = (min(protein.id, other_protein.id), max(protein.id, other_protein.id))
                    identity = pair_identity.get(key)
                    similarity = pair_similarity.get(key)
                row.append({
                    "source": protein.entry_name,
                    "target": other_protein.entry_name,
                    "identity": float(identity) if identity is not None else None,
                    "identity_display": self._format_similarity_display(identity),
                    "similarity": float(similarity) if similarity is not None else None,
                    "similarity_display": self._format_similarity_display(similarity),
                })
            matrix.append(row)

        return {
            "entities": entities,
            "matrix": matrix,
            "meta": {
                "n_points": len(entities),
                "selection": (selection_info or {}).get("selection", ""),
                "type": (selection_info or {}).get("type", ""),
            },
        }


class ClassificationVisualizationsLanding(ClassificationVisualizationMixin, TemplateView):
    template_name = "classification/ClassificationVisualizations.html"

    CHEMOTYPE_PLACEHOLDERS = [
        "Adhesion receptors",
        "Alicarboxylic acid receptors",
        "Aminergic receptors",
        "Amino acid receptors",
        "Lipid receptors",
        "Melatonin receptors",
        "Nucleotide receptors",
        "Orphan receptors",
        "Peptide receptors",
        "Protein receptors",
        "Retinal receptors",
        "Steroid receptors",
        "Tastant receptors",
    ]
    TREE_ONLY_EXCLUDED_CHEMOTYPES = {"odorant receptors", "ion receptors"}

    def _tree_only_url(self, tree_type, selection):
        return "{}?{}".format(
            reverse("classification-visualizations-tree"),
            urlencode({
                "type": tree_type,
                "selection": selection,
            }),
        )

    def _build_modality_chemotype_branches(self):
        try:
            df = self._classification_df()
        except Exception:
            return []

        clean = Classification_tree._clean_cell
        chemotype_to_modality = OrderedDict()
        for _, row in df.iterrows():
            chemotype = clean(row.get("Chemotype"))
            modality = clean(row.get("Modality")) or "Other / unknown"
            if not chemotype:
                continue
            if chemotype.strip().lower() in self.TREE_ONLY_EXCLUDED_CHEMOTYPES:
                continue
            if chemotype not in chemotype_to_modality:
                chemotype_to_modality[chemotype] = modality

        grouped = OrderedDict((label, []) for label in self.MODALITY_GROUP_LABELS)
        for chemotype in sorted(chemotype_to_modality.keys(), key=lambda value: str(value).lower()):
            modality_group = self._group_modality_label(chemotype_to_modality[chemotype])
            grouped[modality_group].append({
                "label": chemotype,
                "url": self._tree_only_url("Chemotype", chemotype),
                "modality_theme": slugify(modality_group),
            })

        branches = []
        for modality, children in grouped.items():
            if not children:
                continue
            branches.append({
                "label": modality,
                "url": self._tree_only_url("Modality", modality),
                "theme_key": slugify(modality),
                "chemotypes": children,
            })
        return branches

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        class_buttons = []
        for config in self.list_visualization_classes():
            class_buttons.append({
                "key": config["key"],
                "label": config["key"],
                "title": config["title"],
                "url": reverse(
                    "classification-visualizations-class",
                    kwargs={"class_key": config["key"]},
                ),
            })
        ctx["class_buttons"] = class_buttons
        modality_branches = self._build_modality_chemotype_branches()
        ctx["modality_branches"] = modality_branches
        family_catalog = self._build_receptor_family_catalog()
        ctx["receptor_family_entries"] = family_catalog["entries"]
        ctx["receptor_family_browser"] = family_catalog["browser_nodes"]
        ctx["receptor_family_entries_json"] = json.dumps({
            item["label"]: {
                "key": item["key"],
                "label": item["label"],
                "url": item["url"],
                "receptor_count": item["receptor_count"],
            }
            for item in family_catalog["entries"]
        })
        superfamily_url = reverse("classification-visualizations-superfamily")
        ctx["superfamily_url"] = superfamily_url
        ctx["landing_payload_json"] = json.dumps({
            "superfamily": {
                "label": "GPCR superfamily",
                "note": "(all classes)",
                "url": superfamily_url,
            },
            "classButtons": class_buttons,
            "receptorFamilies": {
                "entries": family_catalog["entries"],
                "entriesByLabel": {
                    item["label"]: {
                        "key": item["key"],
                        "label": item["label"],
                        "url": item["url"],
                        "receptor_count": item["receptor_count"],
                    }
                    for item in family_catalog["entries"]
                },
                "browserNodes": family_catalog["browser_nodes"],
            },
            "modalityBranches": modality_branches,
        })
        return ctx


class ClassificationVisualizationDetail(ClassificationVisualizationMixin, TemplateView):
    template_name = "classification/ClassificationVisualizationDetail.html"

    def dispatch(self, request, *args, **kwargs):
        class_key = kwargs.get("class_key")
        self.class_config = self.get_visualization_class_config(class_key)
        if not self.class_config:
            raise Http404("Unknown class visualization")
        return super(ClassificationVisualizationDetail, self).dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        class_key = self.class_config["key"]
        cluster_query = urlencode({
            "class": class_key,
            "embed": "1",
        })
        ctx["class_key"] = class_key
        ctx["class_title"] = self.class_config["title"]
        ctx["class_label"] = self.class_config["label"]
        ctx["has_tree"] = self.class_config["has_tree"]
        ctx["cluster_url"] = "{}?{}".format(
            reverse("classification-structuresim"),
            cluster_query,
        )
        if self.class_config["has_tree"]:
            tree_query = urlencode({
                "type": "Class",
                "selection": class_key,
                "locked": "1",
                "embed": "1",
            })
            ctx["tree_url"] = "{}?{}".format(reverse("classification-tree"), tree_query)
        else:
            ctx["tree_url"] = ""
            ctx["tree_note"] = (
                "Due to the unclassified nature of these receptors, a classification tree is unavailable."
            )
        return ctx


class ClassificationTreeVisualizationDetail(ClassificationVisualizationMixin, TemplateView):
    template_name = "classification/ClassificationTreeVisualizationDetail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        raw_type = self.request.GET.get("type")
        raw_selection = self.request.GET.get("selection")
        try:
            selection_info = self.resolve_tree_visualization_selection(raw_type, raw_selection)
        except ValueError as e:
            raise Http404(str(e))

        tree_query = urlencode({
            "type": selection_info["type"],
            "selection": selection_info["selection"],
            "locked": "1",
            "embed": "1",
        })
        class_keys = selection_info["class_keys"]
        has_cluster = len(class_keys) == 1 and selection_info["receptor_count"] >= 2
        cluster_url = ""
        if has_cluster:
            cluster_query = urlencode({
                "class": class_keys[0],
                "filter_type": selection_info["type"],
                "filter_selection": selection_info["selection"],
                "embed": "1",
            })
            cluster_url = "{}?{}".format(reverse("classification-structuresim"), cluster_query)

        cluster_note = ""
        if not has_cluster:
            if len(class_keys) > 1:
                cluster_note = (
                    "Cluster is unavailable because this selection spans multiple GPCR classes."
                )
            elif selection_info["receptor_count"] < 2:
                cluster_note = (
                    "Cluster is unavailable because this selection has fewer than two receptors."
                )
            else:
                cluster_note = (
                    "Cluster is unavailable because this selection could not be mapped to a single GPCR class."
                )

        ctx["page_title"] = selection_info["selection"]
        ctx["tree_type"] = selection_info["type"]
        ctx["tree_selection"] = selection_info["selection"]
        ctx["tree_url"] = "{}?{}".format(reverse("classification-tree"), tree_query)
        ctx["has_cluster"] = has_cluster
        ctx["cluster_url"] = cluster_url
        ctx["cluster_note"] = cluster_note
        ctx["matrix_payload_json"] = json.dumps(self.build_tree_selection_matrix_payload(selection_info))
        return ctx


class SuperfamilyCircularTree(TemplateView):
    template_name = "classification/ClassificationSuperfamilyTree.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        variant = str(self.request.GET.get("variant") or "").strip().lower() or "max"
        if variant not in ClassSimilarityDataMixin.SUMMARY_VARIANTS:
            variant = "max"
        persisted_row = None
        if variant == SUPERFAMILY_TREE_VARIANT:
            persisted_row = (
                TreeNetwork.objects
                .filter(group_key=SUPERFAMILY_TREE_GROUP_KEY)
                .only("payload")
                .first()
            )
        if persisted_row and persisted_row.payload:
            tree_payload = persisted_row.payload
        else:
            payload = ClassSimilarityDataMixin()._build_class_cluster_tree_payload()
            tree_payload = build_superfamily_tree_payload(payload, variant_key=variant)
        ctx["data_json"] = json.dumps(tree_payload)
        ctx["tree_variant"] = variant
        ctx["tree_embed_mode"] = str(self.request.GET.get("embed") or "").strip().lower() in {"1", "true", "yes"}
        ctx["tree_page_title"] = str(self.request.GET.get("title") or "").strip() or "GPCR superfamily tree"
        ctx["tree_intro"] = (
            str(self.request.GET.get("intro") or "").strip()
            or "Phylogenetic-tree renderer applied to the GPCR superfamily using classification sequence-similarity distances."
        )
        return ctx


class GPCRSuperfamilyVisualizationDetail(TemplateView):
    template_name = "classification/ClassificationSuperfamilyDetail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["page_title"] = "GPCR superfamily"
        ctx["wheel_classic_url"] = "{}?{}".format(
            reverse("classification-wheel"),
            urlencode({"embed": "1", "wheel": "classic"}),
        )
        ctx["wheel_odorant_url"] = "{}?{}".format(
            reverse("classification-wheel"),
            urlencode({"embed": "1", "wheel": "odorant"}),
        )
        ctx["cluster_url"] = "{}?{}".format(
            reverse("classification-newclassclustertree"),
            urlencode({
                "variant": "max",
                "cluster_only": "1",
                "embed": "1",
                "layout": "superfamily",
                "title": "GPCR superfamily cluster",
                "intro": (
                    "This page summarizes GPCR superfamily relationships using the maximum "
                    "sequence similarity observed between each class pair."
                ),
            }),
        )
        ctx["matrix_url"] = "{}?{}".format(
            reverse("classification-crossclass"),
            urlencode({"embed": "1"}),
        )
        return ctx

class Classification(TemplateView):
    template_name = "classification/Classification.html"

    # classification Excel
    CLASSIFICATION_FOLDER = 'protein_data'
    CLASSIFICATION_FILE = 'Classification.xlsx'

    # mapping from "Excel Class" → (symbol, display_name)
    CLASS_MAPPING = {
        "Class A (Rhodopsin)":                      ("A",  "Rhodopsin"),
        "Class B1 (Secretin)":                      ("B1", "Secretin"),
        "Class B2 (Adhesion)":                      ("B2", "Adhesion"),
        "Class C (Glutamate)":                      ("C",  "Glutamate"),
        "Class F (Frizzled)":                       ("F",  "Frizzled"),
        "Class T2 (Taste 2)":                       ("T2", "Taste 2"),
        "Class O1 (fish-like odorant)":             ("O1", "Olfactory-polyfunctional 1"),
        "Class O2 (tetrapod specific odorant)":     ("O2", "Olfactory-polyfunctional 2"),
        "Other GPCRs":                              ("Cl", "Classless"),
        # non-human:
        "Class D (Fungal pheromone)":               ("D1", "Fungal pheromone 1"),
        "Class E (Yeast cAMP)":                     ("E",  "Slime mold cAMP"),
        "Class V? (Vomeronasal/pheromone?)":        ("V?", "Vomeronasal or pheromone?"),
        # you can add V1/V2 etc later if they exist in backbone
    }

    # Static class information for the Classes table
    CLASSES_TABLE_DATA = [
        {"code": "A", "name": "Rhodopsin", "species": "Yes", "non_sensory_share": "Majority", "sensory_function": "Vision & light-sensing"},
        {"code": "B1", "name": "Secretin", "species": "Yes", "non_sensory_share": "All", "sensory_function": "-"},
        {"code": "B2", "name": "Adhesion", "species": "Yes", "non_sensory_share": "Majority", "sensory_function": "-"},
        {"code": "C", "name": "Glutamate", "species": "Yes", "non_sensory_share": "Majority", "sensory_function": "Taste (sweet/umami)"},
        {"code": "D1", "name": "Fungal pheromone 1", "species": "Fungi", "non_sensory_share": "-", "sensory_function": "Pheromone-sensing"},
        {"code": "D2", "name": "Fungal pheromone 2", "species": "Fungi", "non_sensory_share": "-", "sensory_function": "Pheromone-sensing"},
        {"code": "E", "name": "Slime mold cAMP", "species": "Slime molds, amoebas", "non_sensory_share": "-", "sensory_function": "Pheromone-sensing (cAMP, in chemotaxis)"},
        {"code": "F", "name": "Frizzled", "species": "Yes", "non_sensory_share": "All", "sensory_function": "-"},
        {"code": "OP1", "name": "Olfactory-polyfunctional 1", "species": "Yes", "non_sensory_share": "Minority", "sensory_function": "Olfaction"},
        {"code": "OP2", "name": "Olfactory-polyfunctional 2", "species": "Yes", "non_sensory_share": "Minority", "sensory_function": "Olfaction"},
        {"code": "T2", "name": "Taste 2", "species": "Yes", "non_sensory_share": "-", "sensory_function": "Taste (bitter)"},
        {"code": "V1", "name": "Vomeronasal 1", "species": "Amphibia, reptiles & non-primate mammals", "non_sensory_share": "-", "sensory_function": "Pheromone-sensing"},
        {"code": "V2", "name": "Vomeronasal 2", "species": "Amphibia, reptiles & non-primate mammals", "non_sensory_share": "-", "sensory_function": "Pheromone-sensing"},
        {"code": "Cl", "name": "Classless", "species": "Yes", "non_sensory_share": "Unknown", "sensory_function": "Unknown"},
    ]


    def _classification_path(self):
        """
        Path to Classification.xlsx.
        """
        return os.path.join(
            settings.DATA_DIR,
            self.CLASSIFICATION_FOLDER,
            self.CLASSIFICATION_FILE,
        )

    def _load_df(self):
        """
        Load Classification.xlsx and return a dataframe with normalized column names.
        Uses the same column picking logic as StructureSim for robustness.
        """
        path = self._classification_path()
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")

        df = pd.read_excel(path)

        # tolerant column name picker (handles embedded newlines etc.)
        def pick(*cands):
            names = set([str(c).strip() for c in cands])
            for col in df.columns:
                s = str(col).strip()
                if s in names:
                    return col
            return None

        # Find the columns we need
        col_gene = pick('GPCRs (Gene name)', 'GPCRs\n(Gene name)', 'GPCRs (Gene name)')
        col_uni = pick('GPCRs (UniProt)', 'GPCRs\n(UniProt)', 'GPCRs (UniProt)')
        col_class = pick('Class')
        col_family = pick('Receptor family')
        col_chemotype = pick('Chemotype')
        col_modality = pick('Modality')
        col_sense = pick('Sense')

        # Build a normalized dataframe with standard column names
        normalized_cols = {}
        if col_gene:
            normalized_cols['GPCRs (Gene name)'] = df[col_gene]
        if col_uni:
            normalized_cols['GPCRs (UniProt)'] = df[col_uni]
        if col_class:
            normalized_cols['Class'] = df[col_class]
        if col_family:
            normalized_cols['Receptor family'] = df[col_family]
        if col_chemotype:
            normalized_cols['Chemotype'] = df[col_chemotype]
        if col_modality:
            normalized_cols['Modality'] = df[col_modality]
        if col_sense:
            normalized_cols['Sense'] = df[col_sense]

        df_normalized = pd.DataFrame(normalized_cols)

        # Ensure all required columns exist (fill with empty if missing)
        required_cols = ['GPCRs (Gene name)', 'GPCRs (UniProt)', 'Class',
                        'Receptor family', 'Chemotype', 'Modality', 'Sense']
        for col in required_cols:
            if col not in df_normalized.columns:
                df_normalized[col] = None

        return df_normalized

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        try:
            df = self._load_df()
        except FileNotFoundError as e:
            ctx["error"] = f"File not found: {e}"
            return ctx
        except Exception as e:
            ctx["error"] = f"Error loading Classification.xlsx: {e}"
            return ctx

        # Check if required columns exist
        required_cols = ["Class", "Receptor family", "Chemotype", "Modality", "Sense"]
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            ctx["error"] = f"Missing required columns in Classification.xlsx: {', '.join(missing_cols)}"
            return ctx

        # only keep backbone columns we care about
        cols_needed = [
            "GPCRs (Gene name)",
            "GPCRs (UniProt)",
            "Class",
            "Receptor family",
            "Chemotype",
            "Modality",
            "Sense",
        ]
        # Only include columns that actually exist
        cols_needed = [col for col in cols_needed if col in df.columns]
        df = df[cols_needed].copy()

        # map Excel class → symbol (A, B1, O1, …)
        class_to_symbol = {}
        for excel_cls, (symbol, _name) in self.CLASS_MAPPING.items():
            class_to_symbol[excel_cls] = symbol

        # Handle NaN values in Class column before mapping
        df["Class_symbol"] = df["Class"].apply(
            lambda x: class_to_symbol.get(str(x).strip(), None)
            if pd.notna(x) and str(x).strip() and str(x).strip().lower() != "nan"
            else None
        )

        # drop rows that don't map to a symbol (just to be safe)
        df = df.dropna(subset=["Class_symbol"])

        # ---------- 1) Ligand type table ----------
        # Using Chemotype as ligand_type and Modality as ligand_group (from Excel directly)
        lt_agg = {}  # chemotype -> {"group": modality, "classes": set([...])}
        for _, row in df.iterrows():
            chemotype_val = row["Chemotype"]
            if pd.isna(chemotype_val):
                continue
            chemotype = str(chemotype_val).strip()
            if not chemotype or chemotype.lower() == "nan":
                continue

            modality_val = row["Modality"]
            modality = "Other / unknown"
            if pd.notna(modality_val):
                modality = str(modality_val).strip()
                if not modality or modality.lower() == "nan":
                    modality = "Other / unknown"

            symbol = row["Class_symbol"]
            if pd.isna(symbol):
                continue

            entry = lt_agg.setdefault(chemotype, {"group": modality, "classes": set()})
            # If modality differs for same chemotype, keep the first one
            # (or could use the most common one, but keeping first for simplicity)
            entry["classes"].add(symbol)

        # nice ordered list of classes
        class_order = ["A", "B1", "B2", "C", "D1", "D2", "E", "F",
                       "T2", "O1", "O2", "V1", "V2", "Cl"]
        def sort_classes(s):
            return sorted(s, key=lambda x: (class_order.index(x)
                                            if x in class_order else 999, x))

        ligand_type_rows = []
        for chemotype in sorted(lt_agg.keys(), key=str.lower):
            entry = lt_agg[chemotype]
            cls_list = sort_classes(entry["classes"])
            ligand_type_rows.append({
                "ligand_type": chemotype,
                "ligand_group": entry["group"],
                "classes": ", ".join(cls_list),
            })

        # ---------- 2) Receptor families: non-sensory vs sensory vs orphan ----------
        # Using Sense column directly:
        # - "non-sensory" -> non-sensory
        # - "unknown" -> orphan
        # - any other non-empty Sense -> sensory

        non_sens_triples = set()       # (class_symbol, family, chemotype)
        sensory_map = {}              # (family, chemotype) -> set(classes)
        orphan_map = {}               # (family, chemotype) -> set(classes)

        for _, row in df.iterrows():
            fam_val = row["Receptor family"]
            if pd.isna(fam_val):
                continue
            fam = str(fam_val).strip()
            if not fam or fam.lower() == "nan":
                continue

            chemotype_val = row["Chemotype"]
            if pd.isna(chemotype_val):
                continue
            chemotype = str(chemotype_val).strip()
            if not chemotype or chemotype.lower() == "nan":
                continue

            symbol = row["Class_symbol"]
            if pd.isna(symbol):
                continue

            sense_val = row["Sense"]
            sense = ""
            if pd.notna(sense_val):
                sense = str(sense_val).strip().lower()

            # Check if sense is non-sensory (case-insensitive)
            if sense == "non-sensory":
                non_sens_triples.add((symbol, fam, chemotype))
            elif sense == "unknown":
                key = (fam, chemotype)
                orphan_map.setdefault(key, set()).add(symbol)
            elif sense:  # Any other non-empty sense value is considered sensory
                key = (fam, chemotype)
                sensory_map.setdefault(key, set()).add(symbol)
            # If sense is empty/NaN, skip it (could be "unknown" or missing data)

        # non-sensory: Class / Receptor family / Chemotype
        rf_non_rows = [
            {
                "class_symbol": cs,
                "receptor_family": fam,
                "ligand_type": chemotype,  # Using chemotype as ligand_type for template
            }
            for (cs, fam, chemotype) in sorted(
                non_sens_triples,
                key=lambda t: (class_order.index(t[0])
                               if t[0] in class_order else 999,
                               t[0].lower(), t[1].lower())
            )
        ]

        # sensory: Receptor family / Chemotype / Found in classes
        rf_sens_rows = []
        for (fam, chemotype), classes in sensory_map.items():
            cls_list = sort_classes(classes)
            rf_sens_rows.append({
                "receptor_family": fam,
                "ligand_type": chemotype,  # Using chemotype as ligand_type for template
                "classes": ", ".join(cls_list),
            })

        rf_sens_rows.sort(key=lambda r: (r["receptor_family"].lower(),
                                         r["ligand_type"].lower()))

        # orphan: Receptor family / Chemotype / Found in classes
        rf_orphan_rows = []
        for (fam, chemotype), classes in orphan_map.items():
            cls_list = sort_classes(classes)
            rf_orphan_rows.append({
                "receptor_family": fam,
                "ligand_type": chemotype,  # Using chemotype as ligand_type for template
                "classes": ", ".join(cls_list),
            })

        rf_orphan_rows.sort(key=lambda r: (r["receptor_family"].lower(),
                                           r["ligand_type"].lower()))

        # ---------- 3) Classes table ----------
        # Use static data for the classes table, pre-sorted by Code
        classes_rows = self.CLASSES_TABLE_DATA.copy()

        # Custom sort function for Code (A, B1, B2, C, D1, D2, E, F, OP1, OP2, T2, V1, V2, Cl)
        def sort_code_key(item):
            code = item["code"]
            # Extract letter prefix and number suffix
            match = re.match(r'([A-Za-z]+)(\d*)', code)
            if match:
                letter_part = match.group(1)
                num_part = match.group(2)
                # Handle special cases
                if letter_part.upper() == "CL":
                    return (999, 0)  # Classless goes last
                # Convert letter part to sortable value
                letter_order = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6,
                               "OP": 7, "O": 7, "T": 8, "V": 9}
                letter_key = letter_order.get(letter_part.upper(), 999)
                # Convert number part to integer (empty string = 0)
                num_key = int(num_part) if num_part else 0
                return (letter_key, num_key)
            # Fallback for unexpected formats
            return (999, 0)

        # Sort by Code
        classes_rows.sort(key=sort_code_key)

        # Serialize to JSON strings for template (using |safe filter)
        ctx["classes_rows"] = json.dumps(classes_rows)
        ctx["ligand_type_rows"] = json.dumps(ligand_type_rows)
        ctx["rf_non_rows"] = json.dumps(rf_non_rows)
        ctx["rf_sens_rows"] = json.dumps(rf_sens_rows)
        ctx["rf_orphan_rows"] = json.dumps(rf_orphan_rows)

        return ctx


class Classification_tree(TemplateView):
    template_name = "classification/Classification_tree.html"

    # Reuse classification Excel path constants from Classification class
    CLASSIFICATION_FOLDER = 'protein_data'
    CLASSIFICATION_FILE = 'Classification.xlsx'

    def _classification_path(self):
        """
        Path to Classification.xlsx.
        """
        return os.path.join(
            settings.DATA_DIR,
            self.CLASSIFICATION_FOLDER,
            self.CLASSIFICATION_FILE,
        )

    def _load_df(self):
        """
        Load Classification.xlsx and return a dataframe with normalized column names.
        Reuses the same logic as Classification class, but only keeps columns needed
        for the classification tree datasets.
        """
        path = self._classification_path()
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")

        df = pd.read_excel(path)

        # tolerant column name picker (handles embedded newlines etc.)
        def pick(*cands):
            names = set([str(c).strip() for c in cands])
            for col in df.columns:
                s = str(col).strip()
                if s in names:
                    return col
            return None

        # Find the columns we need
        col_uni = pick('GPCRs (UniProt)', 'GPCRs\n(UniProt)', 'GPCRs (UniProt)')
        col_gene = pick('GPCRs (Gene name)', 'GPCRs\n(Gene name)', 'GPCRs (Gene name)')
        col_class = pick('Class')
        col_chemotype = pick('Chemotype')
        col_family = pick('Receptor family')
        col_modality = pick('Modality')

        # Build a normalized dataframe with standard column names
        normalized_cols = {}
        if col_uni:
            normalized_cols['GPCRs (UniProt)'] = df[col_uni]
        if col_gene:
            normalized_cols['GPCRs (Gene name)'] = df[col_gene]
        if col_class:
            normalized_cols['Class'] = df[col_class]
        if col_chemotype:
            normalized_cols['Chemotype'] = df[col_chemotype]
        if col_family:
            normalized_cols['Receptor family'] = df[col_family]
        if col_modality:
            normalized_cols['Modality'] = df[col_modality]

        df_normalized = pd.DataFrame(normalized_cols)

        # Ensure all required columns exist (fill with empty if missing)
        required_cols = ['GPCRs (UniProt)', 'GPCRs (Gene name)', 'Class', 'Chemotype', 'Receptor family', 'Modality']
        for col in required_cols:
            if col not in df_normalized.columns:
                df_normalized[col] = None

        return df_normalized

    @staticmethod
    def _clean_cell(val):
        if pd.isna(val):
            return None
        s = str(val).strip()
        if not s or s.lower() == "nan":
            return None
        return s

    @staticmethod
    def _split_uniprot_cell(val):
        """
        Split UniProt cell into list of UniProt IDs.
        Supports comma/semicolon-separated values.
        """
        s = Classification_tree._clean_cell(val)
        if not s:
            return []
        return [x.strip() for x in re.split(r'[,;]\s*', s) if x.strip()]

    @staticmethod
    def _split_gene_cell(val):
        s = Classification_tree._clean_cell(val)
        if not s:
            return []
        return [x.strip() for x in re.split(r'[,;]\s*', s) if x.strip()]

    def _build_leaf_label_lookup(self, df):
        lookup = {}
        if df is None or "GPCRs (UniProt)" not in df.columns:
            return lookup

        for _, row in df.iterrows():
            uniprots = self._split_uniprot_cell(row.get("GPCRs (UniProt)"))
            if not uniprots:
                continue
            genes = self._split_gene_cell(row.get("GPCRs (Gene name)"))
            for idx, uid in enumerate(uniprots):
                key = str(uid or "").strip().upper()
                if not key:
                    continue
                gene = ""
                if len(genes) == len(uniprots):
                    gene = genes[idx]
                elif len(genes) == 1:
                    gene = genes[0]
                entry = lookup.setdefault(key, {"UniProt": key, "Gene": "", "Protein": ""})
                if gene and not entry["Gene"]:
                    entry["Gene"] = gene

        entry_names = ["{}_human".format(key.lower()) for key in lookup.keys()]
        proteins = (
            Protein.objects
            .filter(entry_name__in=entry_names)
            .only("entry_name", "name")
            .prefetch_related(
                Prefetch(
                    "genes",
                    queryset=Gene.objects.only("name", "position").order_by("position"),
                    to_attr="primary_genes_self",
                )
            )
        )
        for protein in proteins:
            key = str(protein.entry_name or "").split("_", 1)[0].upper()
            if key not in lookup:
                continue
            protein_label = ""
            try:
                protein_label = protein.short()
            except Exception:
                protein_label = getattr(protein, "name", "") or ""
            if protein_label and not lookup[key]["Protein"]:
                lookup[key]["Protein"] = protein_label
            if not lookup[key]["Gene"]:
                genes = getattr(protein, "primary_genes_self", None) or []
                if genes:
                    lookup[key]["Gene"] = genes[0].name

        for key, entry in lookup.items():
            entry["Protein"] = entry.get("Protein") or key
            entry["Gene"] = entry.get("Gene") or key
            entry["UniProt"] = entry.get("UniProt") or key
        return lookup

    @staticmethod
    def _build_nested(df):
        """
        Build nested mapping: Chemotype → Receptor family → sorted(set(UniProt)).
        """
        nested = {}
        for _, row in df.iterrows():
            chem = Classification_tree._clean_cell(row.get("Chemotype")) or "Other / unknown"
            fam = Classification_tree._clean_cell(row.get("Receptor family")) or "Other / unknown"
            uids = Classification_tree._split_uniprot_cell(row.get("GPCRs (UniProt)"))
            if not uids:
                continue
            fam_map = nested.setdefault(chem, {})
            uid_set = fam_map.setdefault(fam, set())
            for uid in uids:
                uid_set.add(uid)

        # sort deterministically
        nested_sorted = OrderedDict()
        for chem in sorted(nested.keys(), key=lambda x: str(x).lower()):
            fams = nested[chem]
            fams_sorted = OrderedDict()
            for fam in sorted(fams.keys(), key=lambda x: str(x).lower()):
                fams_sorted[fam] = sorted(fams[fam], key=lambda x: str(x).upper())
            nested_sorted[chem] = fams_sorted
        return nested_sorted

    @staticmethod
    def _build_nested_family(df):
        """
        Build nested mapping: Receptor family → sorted(set(UniProt)).
        """
        nested = {}
        for _, row in df.iterrows():
            fam = Classification_tree._clean_cell(row.get("Receptor family")) or "Other / unknown"
            uids = Classification_tree._split_uniprot_cell(row.get("GPCRs (UniProt)"))
            if not uids:
                continue
            uid_set = nested.setdefault(fam, set())
            for uid in uids:
                uid_set.add(uid)

        nested_sorted = OrderedDict()
        for fam in sorted(nested.keys(), key=lambda x: str(x).lower()):
            nested_sorted[fam] = sorted(nested[fam], key=lambda x: str(x).upper())
        return nested_sorted

    @staticmethod
    def _build_nested_class_family(df, class_to_symbol):
        """
        Build nested mapping: Class_symbol → Receptor family → sorted(set(UniProt)).
        """
        nested = {}
        for _, row in df.iterrows():
            cls = Classification_tree._clean_cell(row.get("Class"))
            sym = class_to_symbol.get(cls) if cls else None
            if not sym:
                continue
            fam = Classification_tree._clean_cell(row.get("Receptor family")) or "Other / unknown"
            uids = Classification_tree._split_uniprot_cell(row.get("GPCRs (UniProt)"))
            if not uids:
                continue
            fam_map = nested.setdefault(sym, {})
            uid_set = fam_map.setdefault(fam, set())
            for uid in uids:
                uid_set.add(uid)

        nested_sorted = OrderedDict()
        for sym in sorted(nested.keys(), key=lambda x: str(x).lower()):
            fams = nested[sym]
            fams_sorted = OrderedDict()
            for fam in sorted(fams.keys(), key=lambda x: str(x).lower()):
                fams_sorted[fam] = sorted(fams[fam], key=lambda x: str(x).upper())
            nested_sorted[sym] = fams_sorted
        return nested_sorted

    @staticmethod
    def _nested_to_tree(class_label, nested):
        """
        Convert nested mapping (Chemotype → Family → [UniProt]) into a D3-like node dict.
        Shape matches what the existing radial tree renderer expects.
        """
        def leaf_node(name):
            return OrderedDict([("name", name), ("value", 0), ("color", "")])

        def inner_node(name, children):
            return OrderedDict([("name", name), ("value", 0), ("color", ""), ("children", children)])

        class_children = []
        for chem, fams in nested.items():
            fam_children = []
            for fam, uids in fams.items():
                fam_children.append(inner_node(fam, [leaf_node(uid) for uid in uids]))
            class_children.append(inner_node(chem, fam_children))

        class_node = inner_node(class_label, class_children)
        root = OrderedDict([("name", ""), ("value", 3000), ("color", ""), ("children", [class_node])])
        return root

    @staticmethod
    def _nested_family_to_tree(class_label, nested_family):
        """
        Convert nested mapping (Family → [UniProt]) into a D3-like node dict:
        root('') → Class → Family → UniProt
        """
        def leaf_node(name):
            return OrderedDict([("name", name), ("value", 0), ("color", "")])

        def inner_node(name, children):
            return OrderedDict([("name", name), ("value", 0), ("color", ""), ("children", children)])

        fam_children = []
        for fam, uids in nested_family.items():
            fam_children.append(inner_node(fam, [leaf_node(uid) for uid in uids]))

        class_node = inner_node(class_label, fam_children)
        root = OrderedDict([("name", ""), ("value", 3000), ("color", ""), ("children", [class_node])])
        return root

    @staticmethod
    def _nested_class_family_to_tree(nested_class_family):
        """
        Convert nested mapping (Class_symbol → Family → [UniProt]) into a D3-like node dict:
        root('') → Class_symbol → Family → UniProt
        """
        def leaf_node(name):
            return OrderedDict([("name", name), ("value", 0), ("color", "")])

        def inner_node(name, children):
            return OrderedDict([("name", name), ("value", 0), ("color", ""), ("children", children)])

        class_children = []
        for sym, fams in nested_class_family.items():
            fam_children = []
            for fam, uids in fams.items():
                fam_children.append(inner_node(fam, [leaf_node(uid) for uid in uids]))
            class_children.append(inner_node(sym, fam_children))

        root = OrderedDict([("name", ""), ("value", 3000), ("color", ""), ("children", class_children)])
        return root

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        try:
            df = self._load_df()
        except FileNotFoundError as e:
            ctx["error"] = f"File not found: {e}"
            return ctx
        except Exception as e:
            ctx["error"] = f"Error loading Classification.xlsx: {e}"
            return ctx

        # Check if required columns exist
        required_cols = ["Class", "Chemotype", "Modality", "Receptor family", "GPCRs (UniProt)"]
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            ctx["error"] = f"Missing required columns in Classification.xlsx: {', '.join(missing_cols)}"
            return ctx

        # Define classes to process
        class_configs = {
            "A": {
                "class_names": ["Class A (Rhodopsin)"],
                "family_name": "Class A (Rhodopsin)",
                "display_name": "Class A"
            },
            "B1": {
                "class_names": ["Class B1 (Secretin)"],
                "family_name": "Class B1 (Secretin)",
                "display_name": "Class B1"
            },
            "B2": {
                "class_names": ["Class B2 (Adhesion)"],
                "family_name": "Class B2 (Adhesion)",
                "display_name": "Class B2"
            },
            "C": {
                "class_names": ["Class C (Glutamate)"],
                "family_name": "Class C (Glutamate)",
                "display_name": "Class C"
            },
            "F": {
                "class_names": ["Class F (Frizzled)"],
                "family_name": "Class F (Frizzled)",
                "display_name": "Class F"
            },
            "T2": {
                "class_names": ["Class T2 (Taste 2)"],
                "family_name": "Class T2 (Taste 2)",
                "display_name": "Class T2"
            }
        }

        # Generate data for each class.
        # Only Class A is split into non-orphan/orphan by Chemotype == "Orphan receptors".
        classes_data = {}

        # Map Excel class string -> symbol (A, B1, ...)
        class_to_symbol = {}
        for class_key, config in class_configs.items():
            for nm in config["class_names"]:
                class_to_symbol[nm] = class_key

        def class_mask_for(config):
            return df["Class"].apply(
                lambda x: str(x).strip() in config["class_names"]
                if pd.notna(x) and str(x).strip() and str(x).strip().lower() != "nan"
                else False
            )

        # Base tree options; JS will compute exact depth/branch lengths.
        base_tree_options = {
            "branch_trunc": 0,
            "leaf_offset": 30,
            "anchor": "",
            "label_free": [],
            # For renderer behavior:
            "centerBadgeR": 0,
            "centerBadgePadding": 0,
            "firstRingExtra": 45,
        }

        # Build new unified tree_sets payload for the new UI.
        tree_sets = {
            "Class": {"options": [], "plots": {}},
            "Modality": {"options": [], "plots": {}},
            "Chemotype": {"options": [], "plots": {}},
        }
        leaf_label_lookup = self._build_leaf_label_lookup(df)

        for class_key, config in class_configs.items():
            class_data = {}

            class_df = df[class_mask_for(config)].copy()
            tree_options = dict(base_tree_options)

            if class_key == "A":
                orphan_key = "Orphan receptors"
                chem_series = class_df["Chemotype"].apply(lambda v: self._clean_cell(v) or "")
                orphan_df = class_df[chem_series.str.lower() == orphan_key.lower()].copy()
                non_orphan_df = class_df[chem_series.str.lower() != orphan_key.lower()].copy()

                if len(non_orphan_df) > 0:
                    nested = self._build_nested(non_orphan_df)
                    class_data["non_orphan"] = {"tree": self._nested_to_tree(config["class_names"][0], nested),
                                                "tree_options": tree_options}
                else:
                    class_data["non_orphan"] = None

                if len(orphan_df) > 0:
                    nested = self._build_nested(orphan_df)
                    class_data["orphan"] = {"tree": self._nested_to_tree(config["class_names"][0], nested),
                                            "tree_options": tree_options}
                else:
                    class_data["orphan"] = None
            else:
                # No split for other classes
                if len(class_df) > 0:
                    if class_key == "C":
                        # Special rule: Class C has NO Chemotype layer (Class → Family → Receptor).
                        nested_fam = self._build_nested_family(class_df)
                        class_data["non_orphan"] = {"tree": self._nested_family_to_tree(config["class_names"][0], nested_fam),
                                                    "tree_options": tree_options}
                    else:
                        nested = self._build_nested(class_df)
                        class_data["non_orphan"] = {"tree": self._nested_to_tree(config["class_names"][0], nested),
                                                    "tree_options": tree_options}
                else:
                    class_data["non_orphan"] = None
                class_data["orphan"] = None

            classes_data[class_key] = {
                "data": class_data,
                "display_name": config["display_name"]
            }

            # ----- tree_sets["Class"] options/plots -----
            if class_key == "A":
                # Gather orphan leaf labels (UniProt) for the Class A legend (alphabetical).
                orphan_leaf_labels = []
                try:
                    if class_df is not None and "Chemotype" in class_df.columns:
                        orphan_key = "Orphan receptors"
                        chem_series = class_df["Chemotype"].apply(lambda v: self._clean_cell(v) or "")
                        orphan_df = class_df[chem_series.str.lower() == orphan_key.lower()].copy()
                        uid_set = set()
                        for _, row in orphan_df.iterrows():
                            for uid in self._split_uniprot_cell(row.get("GPCRs (UniProt)")):
                                uid_set.add(uid)
                        orphan_leaf_labels = sorted(uid_set, key=lambda x: str(x).upper())
                except Exception:
                    orphan_leaf_labels = []

                if class_data.get("non_orphan") and class_data["non_orphan"].get("tree"):
                    key = "A"
                    label = "Class A"
                    tree_sets["Class"]["options"].append({"key": key, "label": label})
                    tree_sets["Class"]["plots"][key] = {
                        "tree": class_data["non_orphan"]["tree"],
                        "tree_options": dict(tree_options, **{"colorMode": "chemotype"}),
                        "meta": {
                            "title": label,
                            "liftClassLayer": True,
                            "collapseLabels": ["Chemotype", "Family"],
                            # Render an orphan legend under this plot (SVG extension).
                            "orphanLeafLabels": orphan_leaf_labels,
                        },
                    }
            else:
                if class_data.get("non_orphan") and class_data["non_orphan"].get("tree"):
                    key = class_key
                    label = config["display_name"]
                    # Class C is fixed-color (no chemotype layer); other classes use chemotype coloring.
                    if class_key == "C":
                        # Keep in sync with CLASS_COLORS["C"] in the template
                        tree_opts = dict(tree_options, **{"colorMode": "fixed", "fixedColor": "#d62728"})
                        meta = {"title": label, "liftClassLayer": True, "collapseLabels": ["Family"]}
                    else:
                        tree_opts = dict(tree_options, **{"colorMode": "chemotype"})
                        meta = {"title": label, "liftClassLayer": True, "collapseLabels": ["Chemotype", "Family"]}
                    tree_sets["Class"]["options"].append({"key": key, "label": label})
                    tree_sets["Class"]["plots"][key] = {"tree": class_data["non_orphan"]["tree"], "tree_options": tree_opts, "meta": meta}

        # Sort Class options in the desired order
        class_order = ["A", "B1", "B2", "C", "F", "T2"]
        tree_sets["Class"]["options"].sort(key=lambda o: (class_order.index(o["key"]) if o["key"] in class_order else 999, o["label"]))

        # ----- tree_sets["Modality"] -----
        m_series = df["Modality"].apply(lambda v: (self._clean_cell(v) or ""))
        modality_groups = [
            ("Orphan receptors", m_series.str.lower() == "orphan receptors"),
            ("Polypeptide receptors", m_series.str.lower().isin(["peptide receptors", "protein receptors", "polypeptide receptors"])),
            ("Small molecule receptors", (m_series != "") & ~m_series.str.lower().isin(["orphan receptors", "peptide receptors", "protein receptors", "polypeptide receptors"])),
        ]
        for key, mask in modality_groups:
            label = key
            m_df = df[mask].copy()
            if len(m_df) == 0:
                continue
            nested_cf = self._build_nested_class_family(m_df, class_to_symbol)
            tree_sets["Modality"]["options"].append({"key": key, "label": label})
            tree_sets["Modality"]["plots"][key] = {
                "tree": self._nested_class_family_to_tree(nested_cf),
                "tree_options": dict(base_tree_options, **{"colorMode": "class"}),
                "meta": {"title": label, "liftClassLayer": False, "collapseLabels": []},
            }
        modality_order = ["Orphan receptors", "Polypeptide receptors", "Small molecule receptors"]
        tree_sets["Modality"]["options"].sort(
            key=lambda o: modality_order.index(o["key"]) if o["key"] in modality_order else 999
        )

        # ----- tree_sets["Chemotype"] -----
        # Exclude chemotypes that do not make sense as a standalone Chemotype dataset.
        excluded_chemotypes = {"odorant receptors", "ion receptors"}
        chemotypes = set()
        for v in df["Chemotype"].values.tolist():
            c = self._clean_cell(v)
            if c:
                if str(c).strip().lower() in excluded_chemotypes:
                    continue
                chemotypes.add(c)
        for chem in sorted(chemotypes, key=lambda x: str(x).lower()):
            c_series = df["Chemotype"].apply(lambda v: (self._clean_cell(v) or ""))
            c_df = df[c_series.str.lower() == str(chem).lower()].copy()
            if len(c_df) == 0:
                continue
            nested_cf = self._build_nested_class_family(c_df, class_to_symbol)
            # Decide coloring:
            # - If this chemotype spans multiple classes, color by class.
            # - If it is only in a single class (or collapses away), color uniformly by chemotype.
            class_syms = set()
            for v in c_df["Class"].values.tolist():
                cls = self._clean_cell(v)
                sym = class_to_symbol.get(cls) if cls else None
                if sym:
                    class_syms.add(sym)
            if len(class_syms) > 1:
                tree_opts = dict(base_tree_options, **{"colorMode": "class"})
            else:
                tree_opts = dict(base_tree_options, **{"colorMode": "chemotype", "forceChemotype": chem})
            tree_sets["Chemotype"]["options"].append({"key": chem, "label": chem})
            tree_sets["Chemotype"]["plots"][chem] = {
                "tree": self._nested_class_family_to_tree(nested_cf),
                "tree_options": tree_opts,
                # Collapse singleton layers for cleaner plots:
                # - If a chemotype exists only in 1 class, collapse Class.
                # - If that class has only 1 family for this chemotype, collapse Family too.
                "meta": {"title": chem, "liftClassLayer": False, "collapseLabels": ["Class", "Family"]},
            }

        # Pass data to template as JSON
        ctx["classes_data"] = json.dumps(classes_data)  # legacy (test template / backwards compat)
        ctx["tree_sets"] = json.dumps(tree_sets)
        ctx["tree_leaf_label_lookup"] = json.dumps(leaf_label_lookup)
        requested_type = str(self.request.GET.get("type") or "Class").strip()
        if requested_type not in tree_sets:
            requested_type = "Class"
        available_options = tree_sets.get(requested_type, {}).get("options", [])
        available_keys = [str(opt.get("key")) for opt in available_options]
        requested_selection = str(self.request.GET.get("selection") or "").strip()
        if requested_selection not in available_keys:
            if requested_type == "Class" and "A" in available_keys:
                requested_selection = "A"
            else:
                requested_selection = available_keys[0] if available_keys else ""
        locked = str(self.request.GET.get("locked") or "").strip().lower() in {"1", "true", "yes"}
        embed_mode = str(self.request.GET.get("embed") or "").strip().lower() in {"1", "true", "yes"}
        ctx["tree_initial_type"] = requested_type
        ctx["tree_initial_selection"] = requested_selection
        ctx["tree_locked"] = locked and bool(requested_selection)
        ctx["tree_embed_mode"] = embed_mode
        ctx["tree_locked_type"] = requested_type if ctx["tree_locked"] else ""
        ctx["tree_locked_selection"] = requested_selection if ctx["tree_locked"] else ""

        return ctx

class GPCRBrowser(TemplateView):
    template_name = "classification/GPCRBrowser.html"

    # classification Excel (same file as Classification/StructureSim use)
    CLASSIFICATION_FOLDER = 'protein_data'
    CLASSIFICATION_FILE = 'Classification.xlsx'

    def _classification_path(self):
        """
        Path to Classification.xlsx.
        """
        return os.path.join(
            settings.DATA_DIR,
            self.CLASSIFICATION_FOLDER,
            self.CLASSIFICATION_FILE,
        )

    def _load_df(self):
        """
        Load Classification.xlsx and return a dataframe with normalized column names.
        """
        path = self._classification_path()
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")

        df = pd.read_excel(path)

        # tolerant column name picker (handles embedded newlines etc.)
        def pick(*cands):
            names = set([str(c).strip() for c in cands])
            for col in df.columns:
                s = str(col).strip()
                if s in names:
                    return col
            return None

        col_uni = pick('GPCRs (UniProt)', 'GPCRs\n(UniProt)', 'GPCRs (UniProt)')
        col_gene = pick('GPCRs (Gene name)', 'GPCRs\n(Gene name)', 'GPCRs (Gene name)')
        col_class = pick('Class')
        col_family = pick('Receptor family')
        col_chemotype = pick('Chemotype')
        col_modality = pick('Modality')
        col_sense = pick('Sense')

        normalized_cols = {}
        if col_uni:
            normalized_cols['GPCRs (UniProt)'] = df[col_uni]
        if col_gene:
            normalized_cols['GPCRs (Gene name)'] = df[col_gene]
        if col_class:
            normalized_cols['Class'] = df[col_class]
        if col_family:
            normalized_cols['Receptor family'] = df[col_family]
        if col_modality:
            normalized_cols['Modality'] = df[col_modality]
        if col_chemotype:
            normalized_cols['Chemotype'] = df[col_chemotype]
        if col_sense:
            normalized_cols['Sense'] = df[col_sense]

        df_normalized = pd.DataFrame(normalized_cols)

        # Ensure required columns exist (fill with empty if missing)
        required_cols = [
            'GPCRs (UniProt)',
            'GPCRs (Gene name)',
            'Class',
            'Receptor family',
            'Modality',
            'Chemotype',
            'Sense',
        ]
        for col in required_cols:
            if col not in df_normalized.columns:
                df_normalized[col] = None

        return df_normalized

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Always provide a JSON value to the template (even on error)
        context["gpcr_rows"] = "[]"

        try:
            df = self._load_df()
        except FileNotFoundError as e:
            context["error"] = f"File not found: {e}"
            return context
        except Exception as e:
            context["error"] = f"Error loading Classification.xlsx: {e}"
            return context

        # Build per-UniProt mapping from Excel (handle multiple IDs in one cell)
        uni_to_excel = {}
        for _, row in df.iterrows():
            uni_val = row.get("GPCRs (UniProt)")
            if pd.isna(uni_val):
                continue
            uni_str = str(uni_val).strip()
            if not uni_str or uni_str.lower() == "nan":
                continue

            gene_val = row.get("GPCRs (Gene name)")
            gene_str = ""
            if pd.notna(gene_val):
                gene_str = str(gene_val).strip()
                if gene_str.lower() == "nan":
                    gene_str = ""

            rec = {
                "Class": "" if pd.isna(row.get("Class")) else str(row.get("Class")).strip(),
                "Receptor family": "" if pd.isna(row.get("Receptor family")) else str(row.get("Receptor family")).strip(),
                "Modality": "" if pd.isna(row.get("Modality")) else str(row.get("Modality")).strip(),
                "Chemotype": "" if pd.isna(row.get("Chemotype")) else str(row.get("Chemotype")).strip(),
                "Sense": "" if pd.isna(row.get("Sense")) else str(row.get("Sense")).strip(),
                "GPCRs (Gene name)": gene_str,
            }

            for uid in re.split(r'[,;]\s*', uni_str):
                uid = uid.strip()
                if not uid:
                    continue
                if uid.lower() == "nan":
                    continue
                # Keep first-seen record; fill missing fields opportunistically
                existing = uni_to_excel.get(uid)
                if existing is None:
                    uni_to_excel[uid] = rec
                else:
                    for k, v in rec.items():
                        if (not existing.get(k)) and v:
                            existing[k] = v

        # Bulk fetch proteins and their primary gene
        entry_names = [("%s_human" % u.lower()) for u in uni_to_excel.keys()]
        proteins = (Protein.objects
                    .filter(entry_name__in=entry_names)
                    .only("entry_name", "name", "sequence")
                    .prefetch_related(
                        Prefetch(
                            "genes",
                            queryset=Gene.objects.only("name", "position").order_by("position"),
                        )
                    ))
        protein_by_entry = {p.entry_name: p for p in proteins}

        # Build rows for DataTables (keep stable order by UniProt code)
        def strip_tags(s):
            if not s:
                return ""
            return re.sub(r"<[^>]+>", "", str(s)).strip()

        rows = []
        for uni in sorted(uni_to_excel.keys(), key=lambda x: str(x).lower()):
            entry_name = "%s_human" % str(uni).lower()
            p = protein_by_entry.get(entry_name)

            # primary gene: first by Gene.position (Meta ordering)
            gene_db = ""
            if p is not None:
                try:
                    g0 = next(iter(getattr(p, "genes").all()), None)
                except Exception:
                    g0 = None
                if g0 is not None and getattr(g0, "name", None):
                    gene_db = g0.name

            excel = uni_to_excel.get(uni, {})
            gene = gene_db or excel.get("GPCRs (Gene name)", "") or ""

            prot_name_html = ""
            if p is not None and getattr(p, "name", None):
                prot_name_html = p.name

            seq = ""
            if p is not None and getattr(p, "sequence", None):
                seq = p.sequence

            rows.append({
                "uniprot": uni,
                "entry_name": entry_name,
                "gene": gene,
                "protein_name_html": prot_name_html,
                "protein_name_text": strip_tags(prot_name_html),
                "class": excel.get("Class", "") or "",
                "family": excel.get("Receptor family", "") or "",
                "modality": excel.get("Modality", "") or "",
                "chemotype": excel.get("Chemotype", "") or "",
                "sense": excel.get("Sense", "") or "",
                "sequence": seq,
            })

        context["gpcr_rows"] = json.dumps(rows)
        return context



class ClassificationWheel(TemplateView):
    template_name = 'classification/ClassificationWheel.html'

    # classification Excel (same file as Classification/GPCRBrowser use)
    CLASSIFICATION_FOLDER = 'protein_data'
    CLASSIFICATION_FILE = 'Classification.xlsx'

    def _classification_path(self):
        """
        Path to Classification.xlsx.
        """
        return os.path.join(
            settings.DATA_DIR,
            self.CLASSIFICATION_FOLDER,
            self.CLASSIFICATION_FILE,
        )

    def _load_df(self):
        """
        Load Classification.xlsx and return a dataframe with normalized column names.
        Only keeps columns needed for wheel annotation.
        """
        path = self._classification_path()
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")

        df = pd.read_excel(path)

        # tolerant column name picker (handles embedded newlines etc.)
        def pick(*cands):
            names = set([str(c).strip() for c in cands])
            for col in df.columns:
                s = str(col).strip()
                if s in names:
                    return col
            return None

        col_uni = pick('GPCRs (UniProt)', 'GPCRs\n(UniProt)', 'GPCRs (UniProt)')
        col_class = pick('Class')
        col_family = pick('Receptor family')
        # Some legacy exports used "Ligand type"; current file uses "Chemotype"
        col_chemotype = pick('Chemotype')
        col_ligand_type = pick('Ligand type', 'Ligand\n type', 'Ligand type ')
        col_modality = pick('Modality')
        col_sense = pick('Sense')

        normalized_cols = {}
        if col_uni:
            normalized_cols['GPCRs (UniProt)'] = df[col_uni]
        if col_class:
            normalized_cols['Class'] = df[col_class]
        if col_family:
            normalized_cols['Receptor family'] = df[col_family]
        if col_chemotype:
            normalized_cols['Chemotype'] = df[col_chemotype]
        if col_ligand_type:
            normalized_cols['Ligand type'] = df[col_ligand_type]
        if col_modality:
            normalized_cols['Modality'] = df[col_modality]
        if col_sense:
            normalized_cols['Sense'] = df[col_sense]

        df_normalized = pd.DataFrame(normalized_cols)

        # Ensure required columns exist (fill with None if missing)
        required_cols = [
            'GPCRs (UniProt)',
            'Class',
            'Receptor family',
            'Chemotype',
            'Ligand type',
            'Modality',
            'Sense',
        ]
        for col in required_cols:
            if col not in df_normalized.columns:
                df_normalized[col] = None

        return df_normalized

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["embed_mode"] = str(self.request.GET.get("embed") or "").strip().lower() in {"1", "true", "yes"}
        requested_wheel = str(self.request.GET.get("wheel") or "").strip().lower()
        context["selected_wheel"] = requested_wheel if requested_wheel in {"classic", "odorant"} else ""

        # --- Step 1: Load Excel metadata (Classification.xlsx) ---
        meta_lookup = {}

        def _clean_cell(val):
            if pd.isna(val):
                return ""
            s = str(val).strip()
            if not s or s.lower() == "nan":
                return ""
            return s

        def _normalize_uniprot_key(raw):
            """
            Normalize a UniProt-style key to match wheel's EntryName.
            Wheel EntryName is the uppercased stem (e.g. ADRB2 from adrb2_human).
            """
            s = _clean_cell(raw).upper()
            if not s:
                return ""
            # Common formats we may encounter
            s = s.replace(" ", "")
            if s.endswith("_HUMAN") or s.endswith("-HUMAN"):
                s = s[:-6]
            if "_" in s:
                s = s.split("_", 1)[0]
            if "-" in s:
                s = s.split("-", 1)[0]
            return s

        try:
            df = self._load_df()
        except FileNotFoundError as e:
            # Wheel can still render; it will just miss annotations
            context["error"] = f"File not found: {e}"
            df = None
        except Exception as e:
            context["error"] = f"Error loading Classification.xlsx: {e}"
            df = None

        if df is not None:
            for _, row in df.iterrows():
                uni_val = row.get("GPCRs (UniProt)")
                uni_str = _clean_cell(uni_val)
                if not uni_str:
                    continue

                chemotype = _clean_cell(row.get("Chemotype")) or _clean_cell(row.get("Ligand type"))
                rec = {
                    "Class": _clean_cell(row.get("Class")),
                    # Keep legacy key ("Ligand type") for backwards compatibility,
                    # but also provide the new explicit field ("Chemotype").
                    "Ligand type": chemotype,
                    "Chemotype": chemotype,
                    "Receptor family": _clean_cell(row.get("Receptor family")),
                    "Modality": _clean_cell(row.get("Modality")),
                    "Sense": _clean_cell(row.get("Sense")),
                }

                # One cell may contain multiple UniProt mnemonics (comma/semicolon separated)
                for token in re.split(r'[,;]\s*', uni_str):
                    key = _normalize_uniprot_key(token)
                    if not key:
                        continue

                    existing = meta_lookup.get(key)
                    if existing is None:
                        # store a copy so multiple keys from one row don't share the same dict
                        meta_lookup[key] = rec.copy()
                    else:
                        # Fill missing fields opportunistically
                        for k, v in rec.items():
                            if (not existing.get(k)) and v:
                                existing[k] = v

        # --- Step 2: Helper to inject metadata into wheel structure ---
        def enrich_wheel_with_metadata(wheelstructure):
            def recurse(node, current_class=None):
                if isinstance(node, dict):
                    for k, v in node.items():
                        if isinstance(v, dict):
                            # If we're inside a Circle_X, the keys here are actual classes (A, B1, etc.)
                            if k.startswith("Circle_"):
                                recurse(v, current_class=None)  # reset class at start of a circle
                            elif current_class is None and not "EntryName" in v:
                                # This k is the class code (A, B1, etc.)
                                recurse(v, current_class=k)
                            elif "EntryName" in v:
                                entry_code = str(v.get("EntryName", "")).strip().upper()
                                meta = meta_lookup.get(entry_code, {})
                                v.update(meta)

                                # Add the class from one level above (A, B1, etc.)
                                v["Class"] = current_class

                                if "Color" not in v:
                                    v["Color"] = "#FFFFFF"
                                if "Data" not in v:
                                    v["Data"] = ""
                            else:
                                recurse(v, current_class=current_class)
                elif isinstance(node, list):
                    for item in node:
                        recurse(item, current_class=current_class)

            recurse(wheelstructure.get("Data", {}))
            return wheelstructure


        # --- Step 3: Build the wheels ---
        odorant_wheel = DataMapperHome.GenerateGPCRomeDataStructure(data_type="Odorant")
        classic_wheel = DataMapperHome.GenerateGPCRomeDataStructure(data_type="Classic")

        # Inject metadata into both
        updated_odorant = enrich_wheel_with_metadata(odorant_wheel)
        updated_classic = enrich_wheel_with_metadata(classic_wheel)

        # --- Step 4: Pass to template ---
        context['GPCRomeData'] = json.dumps(updated_classic['Data'])
        context['GPCRomeOdorantData'] = json.dumps(updated_odorant['Data'])

        return context

class ClassSimilarityDataMixin:
    # Core fixed classes (original order)
    CLASS_ORDER = [
        "Class A (Rhodopsin)",
        "Class B1 (Secretin)",
        "Class B2 (Adhesion)",
        "Class C (Glutamate)",
        "Class F (Frizzled)",
        "Class O1 (fish-like)",
        "Class O2 (tetrapod specific)",
        "Class T2 (Taste 2)",
        "Unclassified",
    ]

    # Map display names -> top-level family slug codes
    CLASS_CODE_BY_NAME = {
        "Class A (Rhodopsin)": "001",
        "Class B1 (Secretin)": "002",
        "Class B2 (Adhesion)": "003",
        "Class C (Glutamate)": "004",
        "Class F (Frizzled)":  "006",
        "Class O1 (fish-like)": "007",
        "Class O2 (tetrapod specific)": "008",
        "Class T2 (Taste 2)": "009",
        "Unclassified": "010",
    }

    CLASS_SYMBOL_BY_NAME = {
        "Class A (Rhodopsin)": "A",
        "Class B1 (Secretin)": "B1",
        "Class B2 (Adhesion)": "B2",
        "Class C (Glutamate)": "C",
        "Class F (Frizzled)": "F",
        "Class O1 (fish-like)": "O1",
        "Class O2 (tetrapod specific)": "O2",
        "Class T2 (Taste 2)": "T2",
        "Unclassified": "U",
    }

    CLASS_COLOR_BY_SYMBOL = {
        "A": "#1f78b4",
        "B1": "#33a02c",
        "B2": "#6A3D9A",
        "C": "#d62728",
        "F": "#FF7F0E",
        "O1": "#17becf",
        "O2": "#bc80bd",
        "T2": "#F7B6D2",
        "U": "#9e9e9e",
    }

    SUMMARY_VARIANTS = OrderedDict([
        ("max", "Max similarity"),
        ("top3_mean", "Top-3 mean similarity"),
        ("top5_mean", "Top-5 mean similarity"),
        ("top10_mean", "Top-10 mean similarity"),
    ])
    CLASS_CLUSTER_PAYLOAD_CACHE_KEY = "classclustertree:payload:v7"
    CLASS_CLUSTER_PAYLOAD_CACHE_TIMEOUT = 60 * 60 * 24
    CLASS_CLUSTER_PAYLOAD_VERSION = 3
    CLASS_CLUSTER_DATASET_KEY = "superfamily"
    CLASS_CLUSTER_SELECTION_KEY = "global"

    def _resolve_family_ids(self, slug_codes):
        qs = ProteinFamily.objects.filter(slug__in=slug_codes).only('id', 'slug', 'name')
        return {f.slug: f.id for f in qs}

    @staticmethod
    def _pair_summary_similarity(similarities, summary_key):
        values = []
        for score in similarities or []:
            try:
                values.append(float(score))
            except Exception:
                continue
        if not values:
            return None
        values = sorted(values, reverse=True)
        if summary_key == "top3_mean":
            subset = values[:min(3, len(values))]
            return float(sum(subset) / len(subset))
        if summary_key == "top5_mean":
            subset = values[:min(5, len(values))]
            return float(sum(subset) / len(subset))
        if summary_key == "top10_mean":
            subset = values[:min(10, len(values))]
            return float(sum(subset) / len(subset))
        return float(values[0])

    @staticmethod
    def _format_similarity_display(similarity):
        if similarity is None:
            return "n/a"
        try:
            rounded = round(float(similarity), 1)
        except Exception:
            return "n/a"
        if not math.isfinite(rounded):
            return "n/a"
        if float(int(rounded)) == float(rounded):
            return "{}%".format(int(rounded))
        return "{:.1f}%".format(rounded)

    def _build_class_only_similarity_data(self):
        code_to_famid = self._resolve_family_ids(list(self.CLASS_CODE_BY_NAME.values()))
        classes = []
        for display_name in self.CLASS_ORDER:
            slug_code = self.CLASS_CODE_BY_NAME.get(display_name)
            family_id = code_to_famid.get(slug_code)
            if not family_id:
                continue
            symbol = self.CLASS_SYMBOL_BY_NAME.get(display_name, display_name)
            classes.append({
                "name": display_name,
                "symbol": symbol,
                "slug": slug_code,
                "family_id": family_id,
                "color": self.CLASS_COLOR_BY_SYMBOL.get(symbol, "#808080"),
            })

        allowed_class_ids = [row["family_id"] for row in classes]
        pair_scores = defaultdict(list)
        pair_qs = (
            ReceptorSimilarity.objects
            .filter(ref_class_id__in=allowed_class_ids, target_class_id__in=allowed_class_ids)
            .annotate(
                pair_a=Least('ref_class_id', 'target_class_id'),
                pair_b=Greatest('ref_class_id', 'target_class_id'),
            )
            .values_list('pair_a', 'pair_b', 'similarity')
        )
        for pair_a, pair_b, similarity in pair_qs.iterator():
            if not pair_a or not pair_b or pair_a == pair_b:
                continue
            pair_scores[(pair_a, pair_b)].append(similarity)

        n_classes = len(classes)
        variants = OrderedDict()
        for summary_key, summary_label in self.SUMMARY_VARIANTS.items():
            similarity_matrix = [[100.0 if i == j else None for j in range(n_classes)] for i in range(n_classes)]
            distance_matrix = np.full((n_classes, n_classes), np.nan, dtype=float)
            np.fill_diagonal(distance_matrix, 0.0)

            seen_distances = []
            for i in range(n_classes):
                for j in range(i + 1, n_classes):
                    pair_key = (min(classes[i]["family_id"], classes[j]["family_id"]), max(classes[i]["family_id"], classes[j]["family_id"]))
                    similarity = self._pair_summary_similarity(pair_scores.get(pair_key), summary_key)
                    if similarity is None:
                        continue

                    distance = max(0.0, 100.0 - similarity)
                    similarity_matrix[i][j] = similarity
                    similarity_matrix[j][i] = similarity
                    distance_matrix[i, j] = distance
                    distance_matrix[j, i] = distance
                    seen_distances.append(distance)

            fill_distance = float(max(seen_distances)) if seen_distances else 100.0

            missing_pairs = 0
            for i in range(n_classes):
                for j in range(i + 1, n_classes):
                    if np.isnan(distance_matrix[i, j]):
                        distance_matrix[i, j] = fill_distance
                        distance_matrix[j, i] = fill_distance
                        missing_pairs += 1

            matrix_rows = []
            for i, class_row in enumerate(classes):
                row_values = []
                for j, other_row in enumerate(classes):
                    similarity = similarity_matrix[i][j]
                    distance = float(distance_matrix[i, j])
                    row_values.append({
                        "source": class_row["symbol"],
                        "target": other_row["symbol"],
                        "similarity": float(similarity) if similarity is not None else None,
                        "similarity_display": self._format_similarity_display(similarity),
                        "distance": distance,
                    })
                matrix_rows.append(row_values)

            variants[summary_key] = {
                "key": summary_key,
                "label": summary_label,
                "matrix": matrix_rows,
                "distance_matrix": distance_matrix,
                "missing_pairs": missing_pairs,
                "fill_distance": fill_distance,
            }

        return {
            "classes": classes,
            "variants": variants,
            "default_variant": "max",
        }

    @staticmethod
    def _manual_tsne_fallback(distance_matrix):
        n_items = int(distance_matrix.shape[0])
        if n_items <= 0:
            return np.zeros((0, 2), dtype=float)
        if n_items == 1:
            return np.array([[0.0, 0.0]], dtype=float)
        angles = np.linspace(0.0, 2.0 * math.pi, num=n_items, endpoint=False)
        return np.column_stack((np.cos(angles), np.sin(angles)))

    def _compute_tsne_coords(self, distance_matrix):
        try:
            perplexity = max(1.0, min(3.0, float(distance_matrix.shape[0] - 1) - 1e-3))
            tsne = TSNE(
                n_components=2,
                metric="precomputed",
                perplexity=perplexity,
                random_state=42,
                init="random",
                learning_rate="auto",
                square_distances=True,
            )
            return tsne.fit_transform(distance_matrix)
        except Exception:
            return self._manual_tsne_fallback(distance_matrix)

    def _build_projection_points(self, classes, coords):
        points = []
        for idx, class_row in enumerate(classes):
            points.append({
                "id": idx,
                "symbol": class_row["symbol"],
                "label": class_row["name"],
                "color": class_row["color"],
                "x": float(coords[idx, 0]),
                "y": float(coords[idx, 1]),
            })
        return points

    def _build_scatter_methods(self, classes, distance_matrix):
        return OrderedDict([
            ("tsne", {
                "label": "t-SNE",
                "points": self._build_projection_points(classes, self._compute_tsne_coords(distance_matrix)),
            }),
        ])

    def _build_class_tip_annotations(self, classes):
        annotations = OrderedDict()
        for class_row in classes:
            symbol = str(class_row.get("symbol") or "").strip()
            if not symbol:
                continue
            annotations[symbol] = {
                "symbol": symbol,
                "label": str(class_row.get("name") or symbol),
                "color": str(class_row.get("color") or "#808080"),
                "slug": str(class_row.get("slug") or ""),
                "family_id": int(class_row.get("family_id") or 0),
            }
        return annotations

    def _build_class_tree_storage_meta(self):
        return {
            "payload_version": self.CLASS_CLUSTER_PAYLOAD_VERSION,
            "dataset_key": self.CLASS_CLUSTER_DATASET_KEY,
            "selection_key": self.CLASS_CLUSTER_SELECTION_KEY,
            "entity_type": "gpcr_class",
            "source_model": ReceptorSimilarity._meta.db_table,
            "tree_method": "neighbor_joining_midpoint",
            "rooting_method": "midpoint",
            "distance_metric": "100_minus_similarity",
        }

    def _build_class_tree_render_payload(self, variant_key, variant_label, matrix_rows, tip_annotations,
                                         rooted_tree=None, rooted_newick="", unrooted_tree=None,
                                         unrooted_newick="", meta=None):
        rooted_tree = rooted_tree or {"name": "", "length": 0.0, "children": []}
        unrooted_tree = unrooted_tree or {"name": "", "length": 0.0, "children": []}
        render_meta = dict(self._build_class_tree_storage_meta())
        render_meta.update({
            "variant_key": str(variant_key or ""),
            "variant_label": str(variant_label or ""),
        })
        if meta:
            render_meta.update(meta)
        return {
            "renderer": "classification_superfamily_phylo",
            "default_view_mode": "rooted",
            "trees": {
                "rooted": str(rooted_newick or ""),
                "unrooted": str(unrooted_newick or ""),
            },
            "tree_objects": {
                "rooted": rooted_tree,
                "unrooted": unrooted_tree,
            },
            "annotations": tip_annotations,
            "matrix": matrix_rows,
            "meta": render_meta,
        }

    def _build_class_tree_variant_payload(self, variant_key, variant_label, scatter_methods, matrix_rows,
                                          variant_meta, tip_annotations, tree_payload, render_payload):
        return {
            "key": variant_key,
            "label": variant_label,
            "matrix": matrix_rows,
            "scatter": {
                "default_method": "tsne",
                "methods": scatter_methods,
            },
            "tree": tree_payload,
            "render_payload": render_payload,
            "meta": variant_meta,
        }

    def _linkage_to_newick(self, node, newick, parentdist, leaf_names):
        """Convert scipy linkage tree to Newick format (like contactnetwork getNewick)."""
        if node.is_leaf():
            return "%s:%.4f%s" % (leaf_names[node.id], parentdist - node.dist, newick)
        else:
            branch_len = parentdist - node.dist
            if len(newick) > 0:
                newick = ")0:%.4f%s" % (branch_len, newick)
            else:
                newick = ");"
            newick = self._linkage_to_newick(node.get_left(), newick, node.dist, leaf_names)
            newick = self._linkage_to_newick(node.get_right(), ",%s" % (newick), node.dist, leaf_names)
            newick = "(%s" % (newick)
            return newick

    @staticmethod
    def _tree_dict_to_newick(node, include_length=True):
        name = str((node or {}).get("name") or "")
        children = list((node or {}).get("children") or [])
        if children:
            body = "({})".format(",".join(
                ClassSimilarityDataMixin._tree_dict_to_newick(child, include_length=include_length)
                for child in children
            ))
        else:
            body = name
        if include_length:
            body = "{}:{:.4f}".format(body, float((node or {}).get("length") or 0.0))
        return body

    @staticmethod
    def _clone_tree_dict(node):
        if not node:
            return {"name": "", "length": 0.0, "children": []}
        return {
            "name": str(node.get("name") or ""),
            "length": float(node.get("length") or 0.0),
            "children": [
                ClassSimilarityDataMixin._clone_tree_dict(child)
                for child in list(node.get("children") or [])
            ],
        }

    @staticmethod
    def _tree_leaf_sort_key(node):
        children = list((node or {}).get("children") or [])
        if not children:
            return str((node or {}).get("name") or "")
        return min(
            ClassSimilarityDataMixin._tree_leaf_sort_key(child)
            for child in children
        )

    def _midpoint_root_tree(self, tree):
        tree_copy = self._clone_tree_dict(tree)
        adjacency = defaultdict(list)
        node_meta = {}
        next_id_box = [0]

        def walk(node):
            node_id = next_id_box[0]
            next_id_box[0] += 1
            children = list(node.get("children") or [])
            node_meta[node_id] = {
                "name": str(node.get("name") or ""),
            }
            for child in children:
                child_id = walk(child)
                edge_length = max(0.0, float(child.get("length") or 0.0))
                adjacency[node_id].append((child_id, edge_length))
                adjacency[child_id].append((node_id, edge_length))
            return node_id

        root_id = walk(tree_copy)
        leaf_ids = [
            node_id for node_id, meta in node_meta.items()
            if meta["name"] and len(adjacency.get(node_id, [])) <= 1
        ]
        if len(leaf_ids) < 2:
            return tree_copy

        def farthest_from(start_id):
            stack = [(start_id, None, 0.0)]
            parent_map = {start_id: None}
            distance_map = {start_id: 0.0}
            while stack:
                node_id, parent_id, cur_distance = stack.pop()
                for neighbor_id, edge_length in adjacency.get(node_id, []):
                    if neighbor_id == parent_id:
                        continue
                    next_distance = cur_distance + float(edge_length)
                    parent_map[neighbor_id] = node_id
                    distance_map[neighbor_id] = next_distance
                    stack.append((neighbor_id, node_id, next_distance))
            farthest_id = max(distance_map, key=lambda key: distance_map[key])
            return farthest_id, distance_map, parent_map

        start_leaf = leaf_ids[0]
        far_leaf, _, _ = farthest_from(start_leaf)
        other_leaf, dist_from_far, parent_from_far = farthest_from(far_leaf)
        diameter = float(dist_from_far.get(other_leaf, 0.0))
        if diameter <= 0.0:
            return tree_copy

        path = []
        cursor = other_leaf
        while cursor is not None:
            path.append(cursor)
            cursor = parent_from_far.get(cursor)
        path = list(reversed(path))
        midpoint = diameter / 2.0
        traversed = 0.0
        midpoint_node = path[0]
        split_edge = None

        def edge_between(a_id, b_id):
            for neighbor_id, edge_length in adjacency.get(a_id, []):
                if neighbor_id == b_id:
                    return float(edge_length)
            return 0.0

        for idx in range(len(path) - 1):
            left_id = path[idx]
            right_id = path[idx + 1]
            edge_length = edge_between(left_id, right_id)
            if traversed + edge_length < midpoint - 1e-9:
                traversed += edge_length
                midpoint_node = right_id
                continue
            if abs(midpoint - traversed) <= 1e-9:
                midpoint_node = left_id
                break
            if abs(traversed + edge_length - midpoint) <= 1e-9:
                midpoint_node = right_id
                break
            left_part = midpoint - traversed
            right_part = edge_length - left_part
            split_edge = (left_id, right_id, left_part, right_part)
            midpoint_node = None
            break

        rooted_adjacency = {
            node_id: list(neighbors)
            for node_id, neighbors in adjacency.items()
        }
        rooted_meta = dict(node_meta)
        if split_edge:
            left_id, right_id, left_part, right_part = split_edge
            rooted_root_id = next_id_box[0]
            rooted_meta[rooted_root_id] = {"name": ""}
            rooted_adjacency[rooted_root_id] = [(left_id, left_part), (right_id, right_part)]
            rooted_adjacency[left_id] = [
                (nid, dist) for nid, dist in rooted_adjacency[left_id]
                if nid != right_id
            ] + [(rooted_root_id, left_part)]
            rooted_adjacency[right_id] = [
                (nid, dist) for nid, dist in rooted_adjacency[right_id]
                if nid != left_id
            ] + [(rooted_root_id, right_part)]
        else:
            rooted_root_id = midpoint_node

        def build_oriented(node_id, parent_id=None, incoming_length=0.0):
            child_nodes = []
            for neighbor_id, edge_length in rooted_adjacency.get(node_id, []):
                if neighbor_id == parent_id:
                    continue
                child_nodes.append(build_oriented(neighbor_id, node_id, edge_length))
            child_nodes.sort(key=self._tree_leaf_sort_key)
            return {
                "name": "" if child_nodes else str(rooted_meta[node_id]["name"] or ""),
                "length": max(0.0, float(incoming_length)),
                "children": child_nodes,
            }

        rooted_children = []
        for neighbor_id, edge_length in rooted_adjacency.get(rooted_root_id, []):
            rooted_children.append(build_oriented(neighbor_id, rooted_root_id, edge_length))
        rooted_children.sort(key=self._tree_leaf_sort_key)
        return {
            "name": "",
            "length": 0.0,
            "children": rooted_children,
        }

    def _build_neighbor_joining_tree(self, labels, distance_matrix):
        labels = [str(label or "") for label in labels]
        n_items = len(labels)
        if n_items == 0:
            return {"name": "", "length": 0.0, "children": []}
        if n_items == 1:
            return {"name": labels[0], "length": 0.0, "children": []}

        active = list(range(n_items))
        nodes = {
            idx: {"name": labels[idx], "length": 0.0, "children": []}
            for idx in active
        }
        distances = {
            idx: {
                other_idx: float(distance_matrix[idx, other_idx])
                for other_idx in active
                if other_idx != idx
            }
            for idx in active
        }
        next_id = n_items

        while len(active) > 2:
            n_active = len(active)
            row_sums = {
                idx: sum(float(distances[idx][other_idx]) for other_idx in active if other_idx != idx)
                for idx in active
            }

            best_pair = None
            best_score = None
            for pos, idx in enumerate(active):
                for other_idx in active[pos + 1:]:
                    q_score = ((n_active - 2) * float(distances[idx][other_idx])) - row_sums[idx] - row_sums[other_idx]
                    if best_score is None or q_score < best_score:
                        best_score = q_score
                        best_pair = (idx, other_idx)

            if not best_pair:
                break

            left_id, right_id = best_pair
            pair_distance = float(distances[left_id][right_id])
            if n_active > 2:
                delta = (row_sums[left_id] - row_sums[right_id]) / float(n_active - 2)
            else:
                delta = 0.0

            left_length = max(0.0, 0.5 * (pair_distance + delta))
            right_length = max(0.0, pair_distance - left_length)
            nodes[left_id]["length"] = left_length
            nodes[right_id]["length"] = right_length

            merged_id = next_id
            next_id += 1
            nodes[merged_id] = {
                "name": "",
                "length": 0.0,
                "children": [nodes[left_id], nodes[right_id]],
            }

            distances[merged_id] = {}
            for other_idx in active:
                if other_idx in (left_id, right_id):
                    continue
                merged_distance = max(
                    0.0,
                    0.5 * (
                        float(distances[left_id][other_idx])
                        + float(distances[right_id][other_idx])
                        - pair_distance
                    ),
                )
                distances[merged_id][other_idx] = merged_distance

            for other_idx in active:
                if other_idx in (left_id, right_id):
                    continue
                distances[other_idx][merged_id] = distances[merged_id][other_idx]

            active = [idx for idx in active if idx not in (left_id, right_id)]

            distances.pop(left_id, None)
            distances.pop(right_id, None)
            for other_idx in list(distances.keys()):
                distances[other_idx].pop(left_id, None)
                distances[other_idx].pop(right_id, None)

            active.append(merged_id)

        if len(active) == 1:
            return nodes[active[0]]

        left_id, right_id = active
        root_distance = max(0.0, float(distances[left_id][right_id]) / 2.0)
        nodes[left_id]["length"] = root_distance
        nodes[right_id]["length"] = root_distance
        return {
            "name": "",
            "length": 0.0,
            "children": [nodes[left_id], nodes[right_id]],
        }

    def _build_class_cluster_tree_payload(self):
        cached_payload = cache_alignment.get(self.CLASS_CLUSTER_PAYLOAD_CACHE_KEY)
        if cached_payload:
            return cached_payload

        class_data = self._build_class_only_similarity_data()
        classes = class_data["classes"]
        n_classes = len(classes)
        tip_annotations = self._build_class_tip_annotations(classes)
        variants = OrderedDict()

        if n_classes < 2:
            points = []
            for idx, class_row in enumerate(classes):
                points.append({
                    "id": idx,
                    "symbol": class_row["symbol"],
                    "label": class_row["name"],
                    "color": class_row["color"],
                    "x": 0.0,
                    "y": 0.0,
                })
            base_scatter = OrderedDict([
                ("tsne", {"label": "t-SNE", "points": points}),
            ])
            for variant_key, variant_data in class_data["variants"].items():
                variant_meta = {
                    "n_classes": n_classes,
                    "missing_pairs": variant_data["missing_pairs"],
                    "fill_distance": variant_data["fill_distance"],
                    "summary_label": variant_data["label"],
                }
                empty_tree = {
                    "segments": [],
                    "leaf_positions": [],
                    "newick": "",
                    "phylogram": {"name": "", "length": 0.0, "children": []},
                    "phylogram_newick": "",
                    "phylogram_rooted": {"name": "", "length": 0.0, "children": []},
                    "phylogram_rooted_newick": "",
                    "phylogram_unrooted": {"name": "", "length": 0.0, "children": []},
                    "phylogram_unrooted_newick": "",
                    "max_distance": 0.0,
                    "linkage_method": "average",
                    "tree_method": "neighbor_joining_midpoint",
                    "rooting_method": "midpoint",
                }
                render_payload = self._build_class_tree_render_payload(
                    variant_key,
                    variant_data["label"],
                    variant_data["matrix"],
                    tip_annotations,
                    meta=variant_meta,
                )
                variants[variant_key] = self._build_class_tree_variant_payload(
                    variant_key,
                    variant_data["label"],
                    base_scatter,
                    variant_data["matrix"],
                    variant_meta,
                    tip_annotations,
                    empty_tree,
                    render_payload,
                )
            payload = {
                "dataset": self._build_class_tree_storage_meta(),
                "classes": classes,
                "tip_annotations": tip_annotations,
                "default_variant": class_data["default_variant"],
                "variants": variants,
            }
            cache_alignment.set(
                self.CLASS_CLUSTER_PAYLOAD_CACHE_KEY,
                payload,
                self.CLASS_CLUSTER_PAYLOAD_CACHE_TIMEOUT,
            )
            return payload

        for variant_key, variant_data in class_data["variants"].items():
            distance_matrix = np.array(variant_data["distance_matrix"], dtype=float)
            scatter_methods = self._build_scatter_methods(classes, distance_matrix)

            condensed = ssd.squareform(distance_matrix, checks=False)
            linkage = sch.linkage(condensed, method='average')
            dendro = sch.dendrogram(
                linkage,
                labels=[row["symbol"] for row in classes],
                no_plot=True,
            )

            segments = []
            for xs, ys in zip(dendro.get("dcoord", []), dendro.get("icoord", [])):
                segments.append({
                    "x": [float(x) for x in xs],
                    "y": [float(y) for y in ys],
                })

            leaf_positions = []
            for idx, symbol in enumerate(dendro.get("ivl", [])):
                leaf_positions.append({
                    "symbol": symbol,
                    "y": float(5 + 10 * idx),
                })

            tree_obj = sch.to_tree(linkage, False)
            tree_newick = self._linkage_to_newick(tree_obj, "", tree_obj.dist, [row["symbol"] for row in classes])
            raw_phylogram_tree = self._build_neighbor_joining_tree(
                [row["symbol"] for row in classes],
                distance_matrix,
            )
            phylogram_tree = self._midpoint_root_tree(raw_phylogram_tree)
            phylogram_newick = "{};".format(self._tree_dict_to_newick(phylogram_tree, include_length=True))
            raw_phylogram_newick = "{};".format(self._tree_dict_to_newick(raw_phylogram_tree, include_length=True))
            variant_meta = {
                "n_classes": n_classes,
                "missing_pairs": variant_data["missing_pairs"],
                "fill_distance": variant_data["fill_distance"],
                "summary_label": variant_data["label"],
                "max_distance": float(np.max(dendro.get("dcoord", [0.0])) if dendro.get("dcoord") else 0.0),
            }
            tree_payload = {
                "segments": segments,
                "leaf_positions": leaf_positions,
                "newick": tree_newick,
                "phylogram": phylogram_tree,
                "phylogram_newick": phylogram_newick,
                "phylogram_rooted": phylogram_tree,
                "phylogram_rooted_newick": phylogram_newick,
                "phylogram_unrooted": raw_phylogram_tree,
                "phylogram_unrooted_newick": raw_phylogram_newick,
                "max_distance": variant_meta["max_distance"],
                "linkage_method": "average",
                "tree_method": "neighbor_joining_midpoint",
                "rooting_method": "midpoint",
            }
            render_payload = self._build_class_tree_render_payload(
                variant_key,
                variant_data["label"],
                variant_data["matrix"],
                tip_annotations,
                rooted_tree=phylogram_tree,
                rooted_newick=phylogram_newick,
                unrooted_tree=raw_phylogram_tree,
                unrooted_newick=raw_phylogram_newick,
                meta=variant_meta,
            )
            variants[variant_key] = self._build_class_tree_variant_payload(
                variant_key,
                variant_data["label"],
                scatter_methods,
                variant_data["matrix"],
                variant_meta,
                tip_annotations,
                tree_payload,
                render_payload,
            )

        payload = {
            "dataset": self._build_class_tree_storage_meta(),
            "classes": classes,
            "tip_annotations": tip_annotations,
            "default_variant": class_data["default_variant"],
            "variants": variants,
        }
        cache_alignment.set(
            self.CLASS_CLUSTER_PAYLOAD_CACHE_KEY,
            payload,
            self.CLASS_CLUSTER_PAYLOAD_CACHE_TIMEOUT,
        )
        return payload


class CrossClassSimilarity(ClassSimilarityDataMixin, TemplateView):
    template_name = 'classification/CrossClassSimilarity.html'

    # Five single-protein “Classless” items as separate groups (display order)
    SINGLE_PROTEIN_LABELS = ["GPR107", "GPR137", "TPRA1", "GPR143", "GPR157"]

    # Exact entry_name per label (case-insensitive)
    SINGLE_PROTEIN_ENTRYNAMES = {
        "GPR107": "gp107_human",
        "GPR137": "g137a_human",
        "TPRA1":  "tpra1_human",
        "GPR143": "gp143_human",
        "GPR157": "gp157_human",
    }

    # ---------- helpers ----------
    @staticmethod
    def clean_name(nm: str) -> str:
        if not nm:
            return "-"
        return (nm.replace("receptor", "")
                  .replace("-adrenoceptor", "")
                  .replace("<i>", "").replace("</i>", "")
                  .strip()) or "-"

    @staticmethod
    def primary_gene_of(p: 'Protein') -> str:
        if getattr(p, "primary_genes_self", None):
            return p.primary_genes_self[0].name
        if p.entry_name:
            return p.entry_name.split("_")[0].upper()
        return "-"

    def build_gtop_url(self, wl):
        try:
            return Template(wl.web_resource.url).substitute(index=wl.index)
        except Exception:
            return None

    def pack_hover(self, p: 'Protein') -> dict:
        """Return per-protein metadata for the tooltip."""
        wl = p.gtop_links_self[0] if getattr(p, "gtop_links_self", None) else None
        return {
            "display_name": self.clean_name(p.name),
            "gtopdb_link": self.build_gtop_url(wl) or "",
            "uniprot": p.entry_name or "",
            "gene": self.primary_gene_of(p),
            "gpcrdb_link": f"/protein/{p.entry_name}" if p.entry_name else "",
            "uniprot_link": (f"https://www.uniprot.org/uniprot/{getattr(p, 'accession', '')}"
                             if getattr(p, "accession", None) else ""),
        }

    # ---- resolvers
    def _fetch_by_entry_names(self, entry_names_lower):
        if not entry_names_lower:
            return {}
        qs = (Protein.objects
              .filter(entry_name__in=entry_names_lower)
              .only('id', 'entry_name', 'name', 'accession'))
        return {(p.entry_name or "").lower(): p for p in qs}

    def _resolve_single_proteins(self):
        """Resolve SINGLE_PROTEIN_LABELS by entry_name first, then primary gene."""
        wanted_lc = {
            lbl: (self.SINGLE_PROTEIN_ENTRYNAMES.get(lbl) or "").lower()
            for lbl in self.SINGLE_PROTEIN_LABELS
        }
        entry_to_label = {en: lbl for lbl, en in wanted_lc.items() if en}

        found = {}
        if entry_to_label:
            by_en = self._fetch_by_entry_names(list(entry_to_label.keys()))
            for en, prot in by_en.items():
                lbl = entry_to_label.get(en)
                if lbl:
                    found[lbl] = prot

        missing = [lbl for lbl in self.SINGLE_PROTEIN_LABELS if lbl not in found]
        if missing:
            wanted_genes = set(missing)
            gene_qs = (
                Protein.objects
                .prefetch_related(
                    Prefetch('genes',
                             queryset=Gene.objects.filter(position=0),
                             to_attr='primary_genes_self')
                )
                .only('id', 'entry_name', 'name', 'accession')
            )
            for p in gene_qs:
                g = self.primary_gene_of(p)
                if g in wanted_genes and g not in found:
                    found[g] = p
        return found

    # ---- main
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["embed_mode"] = str(self.request.GET.get("embed") or "").strip().lower() in {"1", "true", "yes"}

        # 1) Build display list (no extra/non-human groups).
        # The matrix keeps classless receptors as toggleable single-protein rows,
        # but does not show "Unclassified" as a standalone class.
        base_names = [name for name in self.CLASS_ORDER if name != "Unclassified"]
        single_names = [f"{lab} (Classless)" for lab in self.SINGLE_PROTEIN_LABELS]
        display_names = base_names + single_names

        # 2) Resolve base class families for fast class↔class aggregation
        code_to_famid = self._resolve_family_ids(list(self.CLASS_CODE_BY_NAME.values()))
        name_to_famid = {
            name: code_to_famid[self.CLASS_CODE_BY_NAME[name]]
            for name in self.CLASS_ORDER
            if self.CLASS_CODE_BY_NAME[name] in code_to_famid
        }
        allowed_class_ids = list(name_to_famid.values())

        # 3) Resolve the 5 classless singles
        resolved_singles = self._resolve_single_proteins()
        classless_name_to_protein = {}
        for lab in self.SINGLE_PROTEIN_LABELS:
            key = f"{lab} (Classless)"
            p = resolved_singles.get(lab)
            if p:
                classless_name_to_protein[key] = p

        # 4) Final groups (skip unresolved safely)
        groups = []
        for name in display_names:
            if name in name_to_famid:
                groups.append({"display": name, "kind": "class", "id": name_to_famid[name]})
            elif name in classless_name_to_protein:
                groups.append({"display": name, "kind": "protein", "id": classless_name_to_protein[name].id})
        n = len(groups)

        # ------------------------------ OPTIMIZED AGGREGATION ------------------------------
        # A) class↔class maxima (2 queries)
        base_pairs = (
            ReceptorSimilarity.objects
            .filter(ref_class_id__in=allowed_class_ids, target_class_id__in=allowed_class_ids)
            .annotate(
                pair_a=Least('ref_class_id', 'target_class_id'),
                pair_b=Greatest('ref_class_id', 'target_class_id'),
            )
            .values('pair_a', 'pair_b')
        )
        cc_max = {
            (row['pair_a'], row['pair_b']): (row['max_id'], row['max_sim'])
            for row in base_pairs.annotate(
                max_id=Max('identity'),
                max_sim=Max('similarity')
            )
        }

        # B) class↔protein maxima (1 query)
        protein_ids = [g['id'] for g in groups if g['kind'] == 'protein']
        cp_max = {}
        if allowed_class_ids and protein_ids:
            qs_cp = (
                ReceptorSimilarity.objects
                .filter(
                    (Q(ref_class_id__in=allowed_class_ids, protein_target_id__in=protein_ids)) |
                    (Q(target_class_id__in=allowed_class_ids, protein_ref_id__in=protein_ids))
                )
                .annotate(
                    canon_class_id=Case(
                        When(ref_class_id__in=allowed_class_ids, then='ref_class_id'),
                        default='target_class_id',
                        output_field=IntegerField()
                    ),
                    canon_protein_id=Case(
                        When(protein_target_id__in=protein_ids, then='protein_target_id'),
                        default='protein_ref_id',
                        output_field=IntegerField()
                    ),
                )
                .values('canon_class_id', 'canon_protein_id')
                .annotate(
                    max_id=Max('identity'),
                    max_sim=Max('similarity')
                )
            )
            cp_max = {
                (row['canon_class_id'], row['canon_protein_id']): (row['max_id'], row['max_sim'])
                for row in qs_cp
            }

        # C) protein↔protein maxima (1 query)
        pp_max = {}
        if len(protein_ids) >= 2:
            qs_pp = (
                ReceptorSimilarity.objects
                .filter(protein_ref_id__in=protein_ids, protein_target_id__in=protein_ids)
                .annotate(
                    pair_a=Least('protein_ref_id', 'protein_target_id'),
                    pair_b=Greatest('protein_ref_id', 'protein_target_id'),
                )
                .values('pair_a', 'pair_b')
                .annotate(
                    max_id=Max('identity'),
                    max_sim=Max('similarity')
                )
            )
            pp_max = {
                (row['pair_a'], row['pair_b']): (row['max_id'], row['max_sim'])
                for row in qs_pp
            }

        # 5) Build value-only matrix
        matrix = [[None for _ in range(n)] for _ in range(n)]

        def best_for(a, b, metric):
            if a['kind'] == 'class' and b['kind'] == 'class':
                key = (min(a['id'], b['id']), max(a['id'], b['id']))
                tup = cc_max.get(key)
            elif a['kind'] == 'class' and b['kind'] == 'protein':
                tup = cp_max.get((a['id'], b['id']))
            elif a['kind'] == 'protein' and b['kind'] == 'class':
                tup = cp_max.get((b['id'], a['id']))
            else:
                key = (min(a['id'], b['id']), max(a['id'], b['id']))
                tup = pp_max.get(key)
            if not tup:
                return None
            return tup[0] if metric == 'identity' else tup[1]

        # Collect tie fetch specs so we can pull them in big batches later
        tie_specs = []
        for i in range(n):
            for j in range(n):
                if i == j:
                    matrix[i][j] = None
                    continue
                a, b = groups[i], groups[j]
                metric = 'identity' if i < j else 'similarity'
                best = best_for(a, b, metric)
                matrix[i][j] = {"value": int(best) if best is not None else None,
                                "type": metric,
                                "items": []}
                if best is not None:
                    if a['kind'] == 'class' and b['kind'] == 'class':
                        tie_specs.append(('cc', (min(a['id'], b['id']), max(a['id'], b['id'])), metric, int(best)))
                    elif a['kind'] == 'class' and b['kind'] == 'protein':
                        tie_specs.append(('cp', (a['id'], b['id']), metric, int(best)))
                    elif a['kind'] == 'protein' and b['kind'] == 'class':
                        tie_specs.append(('cp', (b['id'], a['id']), metric, int(best)))
                    else:
                        tie_specs.append(('pp', (min(a['id'], b['id']), max(a['id'], b['id'])), metric, int(best)))

        # 6) Batch-fetch tie rows (kept separate per metric to avoid mixing)
        def bucket_specs(specs):
            buckets = defaultdict(list)
            for kind, ids, metric, best in specs:
                buckets[(kind, metric)].append((ids, best))
            return buckets

        buckets = bucket_specs(tie_specs)

        # Collectors: dict-of-dicts keyed by metric
        cc_ties = {'identity': defaultdict(list), 'similarity': defaultdict(list)}
        cp_ties = {'identity': defaultdict(list), 'similarity': defaultdict(list)}
        pp_ties = {'identity': defaultdict(list), 'similarity': defaultdict(list)}

        # weblink prefetch (GtoPdb)
        gtop_links_qs = WebLink.objects.select_related('web_resource').filter(web_resource__slug='gtop')

        def fetch_cc(metric):
            pairs = buckets.get(('cc', metric), [])
            if not pairs:
                return
            q = Q()
            for (a_id, b_id), best in pairs:
                cond = (Q(ref_class_id=a_id, target_class_id=b_id) |
                        Q(ref_class_id=b_id, target_class_id=a_id))
                cond &= Q(**{metric: best})
                q |= cond
            if not q.children:
                return
            rows = (
                ReceptorSimilarity.objects
                .filter(q)
                .select_related('protein_ref', 'protein_target')
                .only(
                    'identity', 'similarity',
                    'protein_ref__id', 'protein_ref__entry_name', 'protein_ref__name', 'protein_ref__accession',
                    'protein_target__id', 'protein_target__entry_name', 'protein_target__name', 'protein_target__accession',
                    'ref_class_id', 'target_class_id'
                )
                .prefetch_related(
                    Prefetch('protein_ref__genes',
                             queryset=Gene.objects.filter(position=0),
                             to_attr='primary_genes_self'),
                    Prefetch('protein_target__genes',
                             queryset=Gene.objects.filter(position=0),
                             to_attr='primary_genes_self'),
                    Prefetch('protein_ref__web_links',
                             queryset=gtop_links_qs,
                             to_attr='gtop_links_self'),
                    Prefetch('protein_target__web_links',
                             queryset=gtop_links_qs,
                             to_attr='gtop_links_self'),
                )
            )
            for r in rows:
                a = min(r.ref_class_id, r.target_class_id)
                b = max(r.ref_class_id, r.target_class_id)
                cc_ties[metric][(a, b)].append(r)

        def fetch_cp(metric):
            pairs = buckets.get(('cp', metric), [])
            if not pairs:
                return
            q = Q()
            for (cls_id, prot_id), best in pairs:
                cond = (
                    Q(ref_class_id=cls_id, protein_target_id=prot_id) |
                    Q(target_class_id=cls_id, protein_ref_id=prot_id)
                )
                cond &= Q(**{metric: best})
                q |= cond
            if not q.children:
                return
            rows = (
                ReceptorSimilarity.objects
                .filter(q)
                .select_related('protein_ref', 'protein_target')
                .only(
                    'identity', 'similarity',
                    'protein_ref__id', 'protein_ref__entry_name', 'protein_ref__name', 'protein_ref__accession',
                    'protein_target__id', 'protein_target__entry_name', 'protein_target__name', 'protein_target__accession',
                    'ref_class_id', 'target_class_id', 'protein_ref_id', 'protein_target_id'
                )
                .prefetch_related(
                    Prefetch('protein_ref__genes',
                             queryset=Gene.objects.filter(position=0),
                             to_attr='primary_genes_self'),
                    Prefetch('protein_target__genes',
                             queryset=Gene.objects.filter(position=0),
                             to_attr='primary_genes_self'),
                    Prefetch('protein_ref__web_links',
                             queryset=gtop_links_qs,
                             to_attr='gtop_links_self'),
                    Prefetch('protein_target__web_links',
                             queryset=gtop_links_qs,
                             to_attr='gtop_links_self'),
                )
            )
            for r in rows:
                if r.ref_class_id is not None and r.protein_target_id is not None:
                    key = (r.ref_class_id, r.protein_target_id)
                else:
                    key = (r.target_class_id, r.protein_ref_id)
                cp_ties[metric][key].append(r)

        def fetch_pp(metric):
            pairs = buckets.get(('pp', metric), [])
            if not pairs:
                return
            q = Q()
            for (a_id, b_id), best in pairs:
                cond = (
                    Q(protein_ref_id=a_id, protein_target_id=b_id) |
                    Q(protein_ref_id=b_id, protein_target_id=a_id)
                )
                cond &= Q(**{metric: best})
                q |= cond
            if not q.children:
                return
            rows = (
                ReceptorSimilarity.objects
                .filter(q)
                .select_related('protein_ref', 'protein_target')
                .only(
                    'identity', 'similarity',
                    'protein_ref__id', 'protein_ref__entry_name', 'protein_ref__name', 'protein_ref__accession',
                    'protein_target__id', 'protein_target__entry_name', 'protein_target__name', 'protein_target__accession',
                    'protein_ref_id', 'protein_target_id'
                )
                .prefetch_related(
                    Prefetch('protein_ref__genes',
                             queryset=Gene.objects.filter(position=0),
                             to_attr='primary_genes_self'),
                    Prefetch('protein_target__genes',
                             queryset=Gene.objects.filter(position=0),
                             to_attr='primary_genes_self'),
                    Prefetch('protein_ref__web_links',
                             queryset=gtop_links_qs,
                             to_attr='gtop_links_self'),
                    Prefetch('protein_target__web_links',
                             queryset=gtop_links_qs,
                             to_attr='gtop_links_self'),
                )
            )
            for r in rows:
                a = min(r.protein_ref_id, r.protein_target_id)
                b = max(r.protein_ref_id, r.protein_target_id)
                pp_ties[metric][(a, b)].append(r)

        # Execute the 6 batched tie fetches
        for m in ('identity', 'similarity'):
            fetch_cc(m)
            fetch_cp(m)
            fetch_pp(m)

        # 7) Fill items for tooltips (include identity & similarity per row)
        def pack_rows(rows):
            return [{
                "ref":     self.pack_hover(r.protein_ref),
                "target":  self.pack_hover(r.protein_target),
                "identity": r.identity,
                "similarity": r.similarity,
            } for r in rows]

        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                a, b = groups[i], groups[j]
                metric = matrix[i][j]["type"]            # 'identity' for upper, 'similarity' for lower
                val = matrix[i][j]["value"]
                if val is None:
                    continue

                if a['kind'] == 'class' and b['kind'] == 'class':
                    key = (min(a['id'], b['id']), max(a['id'], b['id']))
                    rows = cc_ties[metric].get(key, [])
                elif a['kind'] == 'class' and b['kind'] == 'protein':
                    rows = cp_ties[metric].get((a['id'], b['id']), [])
                elif a['kind'] == 'protein' and b['kind'] == 'class':
                    rows = cp_ties[metric].get((b['id'], a['id']), [])
                else:
                    key = (min(a['id'], b['id']), max(a['id'], b['id']))
                    rows = pp_ties[metric].get(key, [])

                matrix[i][j]["items"] = pack_rows(rows)

        # 8) Context
        context["classes"] = [g["display"] for g in groups]
        context["matrix_json"] = json.dumps(matrix)
        return context


class NewClassClusterTree(ClassSimilarityDataMixin, TemplateView):
    template_name = 'classification/NewClassClusterTree.html'

    def get(self, request, *args, **kwargs):
        want_json = (
            request.GET.get('format') == 'json'
            or request.GET.get('data') == '1'
            or 'application/json' in request.headers.get('Accept', '')
        )
        if want_json:
            try:
                payload = self._build_class_cluster_tree_payload()
            except Exception as e:
                return JsonResponse({"error": str(e)}, status=400)
            return JsonResponse(payload, safe=True)
        return super(NewClassClusterTree, self).get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super(NewClassClusterTree, self).get_context_data(**kwargs)
        base = self.request.build_absolute_uri(self.request.path)
        params = self.request.GET.copy()
        params.pop("format", None)
        params.pop("data", None)
        params["format"] = "json"
        requested_variant = str(self.request.GET.get("variant") or "").strip()
        if requested_variant not in self.SUMMARY_VARIANTS:
            requested_variant = "max"
        ctx["embed_url"] = "{}?{}".format(base, params.urlencode())
        ctx["ncct_embed_mode"] = str(self.request.GET.get("embed") or "").strip().lower() in {"1", "true", "yes"}
        ctx["ncct_cluster_only"] = str(self.request.GET.get("cluster_only") or "").strip().lower() in {"1", "true", "yes"}
        ctx["ncct_layout_mode"] = str(self.request.GET.get("layout") or "").strip().lower() or "default"
        ctx["ncct_initial_variant"] = requested_variant
        ctx["ncct_page_title"] = str(self.request.GET.get("title") or "").strip() or "Class clusters"
        ctx["ncct_intro"] = (
            str(self.request.GET.get("intro") or "").strip()
            or "This page summarizes the highest sequence similarity between GPCR classes as a compact 2D class-cluster plot and a distance-driven hierarchical dendrogram."
        )
        return ctx


class ReceptorFamilyVisualizationDetail(ClassificationVisualizationMixin, ClassSimilarityDataMixin, TemplateView):
    template_name = "classification/ClassificationFamilyDetail.html"

    def dispatch(self, request, *args, **kwargs):
        family_key = kwargs.get("family_key")
        family_entry, family_catalog = self.get_receptor_family_entry(family_key)
        if not family_entry:
            raise Http404("Unknown receptor family visualization")
        self.family_entry = family_entry
        self.family_catalog = family_catalog
        return super(ReceptorFamilyVisualizationDetail, self).dispatch(request, *args, **kwargs)

    def _normalize_class_display(self, raw_name):
        name = str(raw_name or "").strip()
        if not name:
            return {"key": None, "label": "Unclassified", "color": "#708090"}
        config_map = self.CLASS_VISUALIZATION_CONFIG
        reverse_map = {cfg["title"]: key for key, cfg in config_map.items()}
        key = reverse_map.get(name)
        if key:
            cfg = config_map[key]
            return {
                "key": key,
                "label": cfg["title"],
                "color": ClassSimilarityDataMixin.CLASS_COLOR_BY_SYMBOL.get(key, "#708090"),
            }
        return {"key": None, "label": name, "color": "#708090"}

    def _get_family_proteins(self):
        return self.get_visualization_family_proteins(self.family_entry)

    def _build_family_similarity_dataset(self):
        proteins = list(sorted(self._get_family_proteins(), key=lambda protein: str(protein.entry_name or "")))
        family_name = self.family_entry["name"]
        family_label = self.family_entry.get("label") or family_name
        if not proteins:
            return {"family": family_label, "proteins": [], "distance_matrix": np.zeros((0, 0), dtype=float), "fill_distance": 100.0, "missing_pairs": 0, "pair_map": {}, "identity_pair_map": {}}

        protein_ids = [p.id for p in proteins]
        pair_map = {}
        identity_pair_map = {}
        pair_qs = (
            ReceptorSimilarity.objects
            .filter(protein_ref_id__in=protein_ids, protein_target_id__in=protein_ids)
            .values("protein_ref_id", "protein_target_id", "identity", "similarity")
        )
        for rec in pair_qs.iterator():
            try:
                a = int(rec["protein_ref_id"])
                b = int(rec["protein_target_id"])
            except Exception:
                continue
            if a == b:
                continue
            key = (a, b) if a < b else (b, a)
            try:
                sim = float(rec["similarity"])
            except Exception:
                sim = None
            try:
                identity = float(rec["identity"])
            except Exception:
                identity = None
            if sim is not None:
                prev = pair_map.get(key)
                if prev is None or sim > prev:
                    pair_map[key] = sim
            if identity is not None:
                prev_identity = identity_pair_map.get(key)
                if prev_identity is None or identity > prev_identity:
                    identity_pair_map[key] = identity

        n_points = len(proteins)
        distance_matrix = np.full((n_points, n_points), np.nan, dtype=float)
        np.fill_diagonal(distance_matrix, 0.0)
        seen_distances = []
        for i in range(n_points):
            for j in range(i + 1, n_points):
                key = (min(protein_ids[i], protein_ids[j]), max(protein_ids[i], protein_ids[j]))
                similarity = pair_map.get(key)
                if similarity is None:
                    continue
                distance = max(0.0, 100.0 - similarity)
                distance_matrix[i, j] = distance
                distance_matrix[j, i] = distance
                seen_distances.append(distance)

        fill_distance = float(max(seen_distances)) if seen_distances else 100.0
        missing_pairs = 0
        for i in range(n_points):
            for j in range(i + 1, n_points):
                if np.isnan(distance_matrix[i, j]):
                    distance_matrix[i, j] = fill_distance
                    distance_matrix[j, i] = fill_distance
                    missing_pairs += 1

        return {
            "family": family_label,
            "proteins": proteins,
            "distance_matrix": distance_matrix,
            "fill_distance": fill_distance,
            "missing_pairs": missing_pairs,
            "pair_map": pair_map,
            "identity_pair_map": identity_pair_map,
        }

    def _build_family_cluster_payload(self, similarity_data):
        proteins = similarity_data["proteins"]
        family_label = similarity_data["family"]
        if not proteins:
            return {
                "family": family_label,
                "points": [],
                "meta": {
                    "n_points": 0,
                    "note": "No human receptors were found for this receptor family.",
                    "classes": self.family_entry.get("class_labels", []),
                    "chemotypes": self.family_entry.get("chemotypes", []),
                    "modality_groups": self.family_entry.get("modality_groups", []),
                },
            }

        n_points = len(proteins)
        distance_matrix = similarity_data["distance_matrix"]
        fill_distance = similarity_data["fill_distance"]
        missing_pairs = similarity_data["missing_pairs"]

        coords = (
            self._manual_tsne_fallback(distance_matrix)
            if n_points <= 2
            else self._compute_tsne_coords(distance_matrix)
        )

        points = []
        for idx, protein in enumerate(proteins):
            fam = getattr(protein.family, "parent", None)
            lig = getattr(fam, "parent", None) if fam else None
            cls = getattr(lig, "parent", None) if lig else None
            class_info = self._normalize_class_display(getattr(cls, "name", ""))
            points.append({
                "id": protein.id,
                "label": protein.entry_short(),
                "entry_name": protein.entry_name,
                "display_name": protein.short(),
                "protein_url": f"/protein/{protein.entry_name}",
                "class_label": class_info["label"],
                "class_key": class_info["key"],
                "family": family_label,
                "chemotypes": self.family_entry.get("chemotypes", []),
                "modality_groups": self.family_entry.get("modality_groups", []),
                "color": class_info["color"],
                "x": float(coords[idx, 0]),
                "y": float(coords[idx, 1]),
            })

        return {
            "family": family_label,
            "points": points,
            "meta": {
                "n_points": n_points,
                "missing_pairs": missing_pairs,
                "fill_distance": fill_distance,
                "note": "Sequence-similarity t-SNE computed on load for this receptor family.",
                "classes": self.family_entry.get("class_labels", []),
                "chemotypes": self.family_entry.get("chemotypes", []),
                "modality_groups": self.family_entry.get("modality_groups", []),
            },
        }

    def _build_family_tree_ui_payload(self, tree_payload, similarity_data):
        payload = json.loads(json.dumps(tree_payload or {}))
        proteins = similarity_data["proteins"]
        if not proteins:
            payload.setdefault("tree", "")
            payload.setdefault("annotations", {})
            payload.setdefault("Gprot_coupling", {})
            payload["entities"] = []
            payload["matrix"] = []
            payload["meta"] = dict(payload.get("meta") or {})
            payload["meta"].update({
                "n_points": 0,
                "note": "No human receptors were found for this receptor family.",
                "classes": self.family_entry.get("class_labels", []),
                "chemotypes": self.family_entry.get("chemotypes", []),
                "modality_groups": self.family_entry.get("modality_groups", []),
            })
            return payload

        protein_ids = [protein.id for protein in proteins]
        pair_map = similarity_data["pair_map"]
        identity_pair_map = similarity_data.get("identity_pair_map", {})
        distance_matrix = similarity_data["distance_matrix"]

        entities = []
        for idx, protein in enumerate(proteins):
            fam = getattr(protein.family, "parent", None)
            lig = getattr(fam, "parent", None) if fam else None
            cls = getattr(lig, "parent", None) if lig else None
            class_info = self._normalize_class_display(getattr(cls, "name", ""))
            primary_genes = getattr(protein, "primary_genes_self", None) or []
            gene_label = primary_genes[0].name if primary_genes else (
                str(protein.entry_name or "").split("_", 1)[0].upper()
            )
            entities.append({
                "symbol": protein.entry_name,
                "name": protein.short(),
                "short_label": protein.entry_short(),
                "gene_label": gene_label,
                "subtitle": class_info["label"],
                "slug": getattr(protein.family, "slug", "") or "",
                "color": class_info["color"],
                "protein_url": f"/protein/{protein.entry_name}",
            })

        matrix_rows = []
        for i, protein in enumerate(proteins):
            row = []
            for j, other_protein in enumerate(proteins):
                if i == j:
                    similarity = 100.0
                    identity = 100.0
                else:
                    pair_key = (min(protein_ids[i], protein_ids[j]), max(protein_ids[i], protein_ids[j]))
                    similarity = pair_map.get(pair_key)
                    identity = identity_pair_map.get(pair_key)
                row.append({
                    "source": protein.entry_name,
                    "target": other_protein.entry_name,
                    "identity": float(identity) if identity is not None else None,
                    "identity_display": self._format_similarity_display(identity),
                    "similarity": float(similarity) if similarity is not None else None,
                    "similarity_display": self._format_similarity_display(similarity),
                    "distance": float(distance_matrix[i, j]),
                })
            matrix_rows.append(row)

        payload.setdefault("meta", {})
        payload["meta"].update({
            "n_points": len(proteins),
            "classes": self.family_entry.get("class_labels", []),
            "chemotypes": self.family_entry.get("chemotypes", []),
            "modality_groups": self.family_entry.get("modality_groups", []),
        })
        payload["entities"] = entities
        payload["matrix"] = matrix_rows
        return payload

    def get_context_data(self, **kwargs):
        ctx = super(ReceptorFamilyVisualizationDetail, self).get_context_data(**kwargs)
        similarity_data = self._build_family_similarity_dataset()
        tree_row = (
            TreeNetwork.objects
            .filter(group_key=str(self.family_entry.get("key") or ""))
            .only("payload", "protein_count", "updated_at")
            .first()
        )
        payload = tree_row.payload if tree_row and tree_row.payload else {
            "tree": "",
            "annotations": {},
            "Gprot_coupling": {},
            "meta": {
                "n_points": 0,
                "classes": self.family_entry.get("class_labels", []),
                "chemotypes": self.family_entry.get("chemotypes", []),
                "modality_groups": self.family_entry.get("modality_groups", []),
                "note": "No persisted family tree payload is available yet. Run build_treenetwork during the data build.",
            },
        }
        tree_payload = self._build_family_tree_ui_payload(payload, similarity_data)
        cluster_payload = self._build_family_cluster_payload(similarity_data)
        ctx["page_title"] = self.family_entry["label"]
        ctx["page_description"] = (
            "Persisted receptor-family phylogenetic tree paired with a sequence-similarity cluster view."
        )
        ctx["family_entry_json"] = json.dumps(self.family_entry)
        ctx["family_tree_payload_json"] = json.dumps(tree_payload)
        ctx["family_cluster_payload_json"] = json.dumps(cluster_payload)
        ctx["family_receptor_count"] = tree_row.protein_count if tree_row else self.family_entry.get("receptor_count", 0)
        ctx["family_tree_has_payload"] = bool(tree_payload.get("tree"))
        return ctx


# ----------------------------- Shared helpers ------------------------------

class OrphanSelect2Mixin:
    ORPHAN_LT = 'Orphan receptors'

    @staticmethod
    def _clean_gtop_name(nm):
        if not nm:
            return "-"
        s = (nm
             .replace("receptor", "")
             .replace("-adrenoceptor", "")
             .replace("<i>", "").replace("</i>", "")
             .strip())
        return s or "-"

    def get_orphans_select2(self):
        # pull the whole lineage to read "Class ..."
        orphans_qs = (
            Protein.objects
            .filter(
                parent_id__isnull=True,
                species_id=1,
                family__parent__parent__name__iexact=self.ORPHAN_LT,
            )
            .select_related('family__parent__parent__parent')  # <-- add this
            .prefetch_related(
                Prefetch('genes',
                         queryset=Gene.objects.filter(position=0),
                         to_attr='primary_genes_self')
            )
            .order_by('entry_name')
        )

        data = []
        for p in orphans_qs:
            gene = (
                p.primary_genes_self[0].name
                if getattr(p, 'primary_genes_self', None)
                else (p.entry_name.split('_')[0].upper() if p.entry_name else "-")
            )
            nm = self._clean_gtop_name(p.name)

            # read raw class name from lineage (e.g. "Class A orphans")
            fam = getattr(p.family, 'parent', None)
            lig = getattr(fam, 'parent', None) if fam else None
            cls = getattr(lig, 'parent', None) if lig else None
            raw_class = getattr(cls, 'name', '') or ''

            # normalize directly in Python
            if raw_class.lower().startswith("other gpcr"):
                raw_class = "Classless"
            elif raw_class.lower().startswith("class "):
                # keep only the letter, e.g. "Class A orphans" → "Class A"
                m = re.search(r"class\s*([a-z])", raw_class, re.I)
                raw_class = f"Class {m.group(1).upper()}" if m else raw_class

            data.append({
                "id": p.id,
                "text": nm,
                "name": nm,
                "entry_name": p.entry_name,
                "gene": gene,
                "class": raw_class,
            })
        return data


def _get_ref_class_info(ref_id):
    """
    Return (class_id, class_name) for the reference protein, inferred directly
    from ReceptorSimilarity (works regardless of row direction).
    """
    row = (
        ReceptorSimilarity.objects
        .filter(Q(protein_ref_id=ref_id) | Q(protein_target_id=ref_id))
        .values('protein_ref_id', 'protein_target_id', 'ref_class_id', 'target_class_id')
        .first()
    )
    if not row:
        return (None, None)

    if row['protein_ref_id'] == ref_id:
        class_id = row['ref_class_id']
    else:
        class_id = row['target_class_id']

    class_name = None
    if class_id:
        try:
            cls = ProteinFamily.objects.only('id', 'name').get(id=class_id)
            # Normalize like your Select2 (Classless / "Class X")
            name = (cls.name or '').strip()
            if name.lower().startswith('other gpcr'):
                name = 'Classless'
            else:
                m = re.search(r'class\s*([a-z])', name, re.I)
                if m:
                    name = f'Class {m.group(1).upper()}'
            class_name = name
        except ProteinFamily.DoesNotExist:
            pass

    return (class_id, class_name)

def _build_similarity_rows(ref_id):
    """
    Reuses the exact logic from SimilarityTopAPI to produce the table rows.
    Returns: list[dict] (the 'results' list you already send today)
    """
    from string import Template

    pairs_qs = (
        ReceptorSimilarity.objects
        .filter(Q(protein_ref_id=ref_id) | Q(protein_target_id=ref_id))
        .annotate(
            other_id=Case(
                When(protein_ref_id=ref_id, then=F('protein_target_id')),
                default=F('protein_ref_id'),
                output_field=IntegerField(),
            )
        )
        .order_by('-similarity')
        .values('other_id', 'similarity', 'identity')
    )
    pairs = list(pairs_qs)
    if not pairs:
        return []

    other_ids_all = [p['other_id'] for p in pairs]

    LT_ORPHAN = 'Orphan receptors'
    lt_map = dict(
        Protein.objects
               .filter(id__in=other_ids_all)
               .values_list('id', 'family__parent__parent__name')
    )
    def is_orphan(pid):
        return (lt_map.get(pid) or '').strip().lower() == LT_ORPHAN.lower()

    liganded_rows = [p for p in pairs if not is_orphan(p['other_id'])]

    if liganded_rows:
        cutoff = liganded_rows[9]['similarity'] if len(liganded_rows) >= 10 else liganded_rows[-1]['similarity']
    else:
        cutoff = pairs[min(9, len(pairs) - 1)]['similarity']

    kept = [p for p in pairs if p['similarity'] >= cutoff]
    kept_ids = [p['other_id'] for p in kept]

    gtop_links_qs = WebLink.objects.select_related('web_resource').filter(web_resource__slug='gtop')
    proteins_qs = (
        Protein.objects
        .filter(id__in=kept_ids)
        .select_related('family__parent__parent__parent')
        .prefetch_related(
            Prefetch('genes',
                     queryset=Gene.objects.filter(position=0),
                     to_attr='primary_genes_self'),
            Prefetch('web_links',
                     queryset=gtop_links_qs,
                     to_attr='gtop_links_self'),
        )
    )
    proteins = {p.id: p for p in proteins_qs}

    endo_qs = (
        Endogenous_GTP.objects
        .filter(receptor_id__in=kept_ids)
        .select_related('ligand', 'ligand__ligand_type')
    )
    endo_by_receptor = {}
    for e in endo_qs:
        if e.ligand:
            endo_by_receptor.setdefault(e.receptor_id, []).append(e)

    def clean_iuphar_name(nm):
        if not nm:
            return "-"
        s = nm.replace("receptor", "").replace("-adrenoceptor", "").replace("<i>", "").replace("</i>", "").strip()
        return s or "-"

    def build_gtop_url(wl):
        try:
            return Template(wl.web_resource.url).substitute(index=wl.index)
        except Exception:
            return None

    sim_map = {p['other_id']: p['similarity'] for p in kept}
    idn_map = {p['other_id']: p['identity']   for p in kept}

    results = []
    for row in kept:
        pid = row['other_id']
        p = proteins.get(pid)
        if not p:
            continue

        gene_name = p.primary_genes_self[0].name if getattr(p, 'primary_genes_self', None) else (
            (p.entry_name.split('_')[0].upper()) if p.entry_name else "-")

        entry_name  = p.entry_name or None
        gpcrdb_link = f"/protein/{entry_name}" if entry_name else "-"
        uniprot_link = f"https://www.uniprot.org/uniprot/{p.accession}" if p.accession else None

        wl_self = p.gtop_links_self[0] if getattr(p, 'gtop_links_self', None) else None
        iuphar_link = build_gtop_url(wl_self) if wl_self else None
        iuphar_name = clean_iuphar_name(p.name)

        family_name = getattr(getattr(p.family, "parent", None), "name", None)
        ligand_type = getattr(getattr(getattr(p.family, "parent", None), "parent", None), "name", None)
        clazz       = getattr(getattr(getattr(getattr(p.family, "parent", None), "parent", None), "parent", None), "name", None)

        lig_items = endo_by_receptor.get(pid, [])
        seen, endo_ligands, lig_types = set(), [], set()
        for e in lig_items:
            lig = e.ligand
            if not lig:
                continue
            if lig.id not in seen:
                seen.add(lig.id)
                endo_ligands.append({"id": lig.id, "name": lig.name})
            if lig.ligand_type:
                lig_types.add(lig.ligand_type.name)
        endo_type = "<br>".join(sorted(lig_types)) if lig_types else "-"

        results.append({
            "other_id": pid,
            "Gene": gene_name,
            "entry_name": entry_name,
            "gpcrdb_link": gpcrdb_link,
            "uniprot_link": uniprot_link,
            "iuphar_name": iuphar_name,
            "iuphar_link": iuphar_link,
            "family": family_name,
            "ligand_type": ligand_type,
            "class": clazz,
            "similarity": sim_map.get(pid, 0),
            "identity": idn_map.get(pid, 0),
            "endo_ligands": endo_ligands,
            "endo_type": endo_type,
        })

    return results

def _build_embedding_payload(ref_id, *, top_n=50, metric='identity', exclude_orphans=True):
    """
    Embedding selection logic (per your new rules), then the same t-SNE pipeline.
    Rules:
      - Classless  -> top N across all classes
      - Class C    -> ALL Class C
      - Other      -> top N within the same class as ref
    """
    # 0) Figure out the reference class once (using the RS table)
    ref_class_id, ref_class_name = _get_ref_class_info(ref_id)

    # If we couldn't deduce the class, fall back to the broad top N across all
    if not ref_class_id:
        base_qs = (
            ReceptorSimilarity.objects
            .filter(Q(protein_ref_id=ref_id) | Q(protein_target_id=ref_id))
            .order_by('-similarity')[:max(10, min(200, int(top_n)))]
        )
    else:
        # 1) Build class-based neighbor selection
        #    Use indexed filters directly on RS:
        #    - same-class rows regardless of direction
        same_class_q = (
            Q(protein_ref_id=ref_id, target_class_id=ref_class_id) |
            Q(protein_target_id=ref_id, ref_class_id=ref_class_id)
        )

        if ref_class_name == 'Classless':
            # Top N across all classes
            base_qs = (
                ReceptorSimilarity.objects
                .filter(Q(protein_ref_id=ref_id) | Q(protein_target_id=ref_id))
                .order_by('-similarity')[:max(10, min(200, int(top_n)))]
            )
        elif ref_class_name == 'Class C':
            # ALL Class C (no slice)
            base_qs = (
                ReceptorSimilarity.objects
                .filter(same_class_q)
                .order_by('-similarity')
            )
        else:
            # Same class only, Top N
            base_qs = (
                ReceptorSimilarity.objects
                .filter(same_class_q)
                .order_by('-similarity')[:max(10, min(200, int(top_n)))]
            )

    # 2) Convert to a uniform shape with other_id + values (works both directions)
    pairs = list(
        base_qs.annotate(
            other_id=Case(
                When(protein_ref_id=ref_id, then=F('protein_target_id')),
                default=F('protein_ref_id'),
                output_field=IntegerField(),
            )
        ).values('other_id', 'similarity', 'identity')
    )

    if not pairs:
        return {"points": [], "ref": {"id": ref_id, "label": ""}, "meta": {"note": "No neighbors for ref"}}

    # Note: the old 'exclude_orphans' flag is redundant here because the
    # class-based selection already governs inclusion. Kept for compatibility.

    # 3) Cap the "ranked" set only if we have a slice-less case above (Class C keeps all already)
    ranked = pairs  # already sliced where needed
    kept_ids = [ref_id] + [p["other_id"] for p in ranked]

    # 4) Fetch protein annotation for labels & legend
    proteins = (
        Protein.objects
        .filter(id__in=kept_ids)
        .select_related("family__parent__parent__parent")
    )
    pmap = {p.id: p for p in proteins}
    if ref_id not in pmap:
        return {"error": "Reference protein not found"}

    def _lab(en):
        return (en or "").replace("_human", "")

    labels, ordered_ids = [], []
    for pid in kept_ids:
        p = pmap.get(pid)
        if not p:
            continue
        lab = _lab(p.entry_name or "")
        if lab and lab not in labels:
            labels.append(lab)
            ordered_ids.append(pid)

    N = len(labels)
    if N < 3:
        return {"points": [], "ref": {"id": ref_id, "label": labels[0] if labels else ""}, "meta": {"note": "Too few points", "n_points": N}}

    id_to_idx = {pid: i for i, pid in enumerate(ordered_ids)}

    # 5) Build dense pair set among kept ids for distances
    subpairs = list(
        ReceptorSimilarity.objects
        .filter(protein_ref_id__in=kept_ids, protein_target_id__in=kept_ids)
        .values("protein_ref_id", "protein_target_id", "similarity", "identity")
    )
    pv = {}
    for r in subpairs:
        a, b = r["protein_ref_id"], r["protein_target_id"]
        if a == b:
            continue
        key = (a, b) if a < b else (b, a)
        if key not in pv:
            pv[key] = r

    D = np.full((N, N), np.nan, dtype=float)
    np.fill_diagonal(D, 0.0)

    metric = (metric or "identity").lower()
    if metric not in ("identity", "similarity"):
        metric = "identity"

    def to_dist(rec):
        val = rec["identity"] if metric == "identity" else rec["similarity"]
        try:
            v = float(val)
        except Exception:
            return np.nan
        return max(0.0, min(1.0, 1.0 - v / 100.0))

    for (a, b), rec in pv.items():
        if a in id_to_idx and b in id_to_idx:
            i, j = id_to_idx[a], id_to_idx[b]
            d = to_dist(rec)
            D[i, j] = d
            D[j, i] = d

    if np.isnan(D).any():
        col_med = np.nanmedian(D, axis=0)
        inds = np.where(np.isnan(D))
        D[inds] = np.take(col_med, inds[1])
        D = 0.5 * (D + D.T)
        np.fill_diagonal(D, 0.0)

    perplexity = max(1.0, min(40.0, (N - 1) / 3.0, N - 1 - 1e-9))
    tsne = TSNE(
        n_components=2,
        metric="precomputed",
        perplexity=perplexity,
        random_state=42,
        init="random",
        learning_rate="auto",
        square_distances=True
    )
    coords = tsne.fit_transform(D)
    used_method = "tsne"

    # 6) Values vs ref (for hover/gradient)
    ref_field = "identity" if metric == "identity" else "similarity"
    val_vs_ref = {}
    for r in ranked:
        oid = r["other_id"]
        if oid in id_to_idx:
            p = pmap.get(oid)
            if p:
                lab = _lab(p.entry_name or "")
                val = r.get(ref_field)
                if val is not None:
                    val_vs_ref[lab] = float(val)

    # 7) Build points w/ annotation
    points = []
    for i, pid in enumerate(ordered_ids):
        p = pmap[pid]
        fam = getattr(p.family, "parent", None)
        lig = getattr(fam, "parent", None) if fam else None
        cls = getattr(lig, "parent", None) if lig else None

        clazz = getattr(cls, "name", "") if cls else ""
        lig_t = getattr(lig, "name", "") if lig else ""
        fam_n = getattr(fam, "name", "") if fam else ""
        lab = labels[i]

        # Normalize class label like before
        if clazz.lower().startswith('other gpcr'):
            clazz = 'Classless'
        else:
            m = re.search(r'class\s*([a-z])', clazz, re.I)
            if m:
                clazz = f'Class {m.group(1).upper()}'

        fill = 100.0 if pid == ref_id else val_vs_ref.get(lab)

        points.append({
            "id": pid,
            "label": lab,
            "x": float(coords[i, 0]),
            "y": float(coords[i, 1]),
            "Class": clazz,
            "Ligand type": lig_t,
            "Receptor family": fam_n,
            "fill": float(fill) if fill is not None else None,
            "is_ref": (pid == ref_id),
        })

    return {
        "points": points,
        "ref": {"id": ref_id, "label": labels[0]},
        "meta": {"method": used_method, "metric": metric, "top_n": len(points), "n_points": N, "ref_class": ref_class_name}
    }

# ------------------------------ New merged page -----------------------------

class OrphanSimilarityExplorer(OrphanSelect2Mixin, TemplateView):
    """
    Single page that will host tabs: (1) Neighbor Table, (2) Cluster Embedding.
    The template (to be added) will keep a hidden wrapper, and after a selection
    it will call the bundle API once, show BusyLoad, then reveal the tabs.
    """
    template_name = 'class_similarity/OrphanSimilarityExplorer.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['orphans_select2'] = json.dumps(self.get_orphans_select2())
        return ctx


# ------------------------------- New merged API -----------------------------

class SimilarityBundleAPI(View):
    """
    GET /class_similarity/api/bundle?ref=<protein_id>&top_n=50&metric=identity&exclude_orphans=true
    Returns BOTH:
      - table.results[]   (same structure as SimilarityTopAPI)
      - embedding.{points,ref,meta} (same structure as SimilarityEmbeddingAPI)
    Optional query flags:
      - only=table   -> return only table part
      - only=embed   -> return only embedding part
    """

    @staticmethod
    def _truthy(v):
        return str(v).lower() not in ("", "0", "false", "no", "off", "none")

    def get(self, request):
        # ---- params ----
        ref_raw = request.GET.get('ref')
        try:
            ref_id = int(ref_raw)
        except (TypeError, ValueError):
            return JsonResponse({"error": "Missing or invalid 'ref' parameter"}, status=400)

        top_n = request.GET.get("top_n")
        try:
            top_n = int(top_n) if top_n is not None else 50
        except Exception:
            top_n = 50
        top_n = max(10, min(200, top_n))

        metric = (request.GET.get("metric") or "identity").lower()
        if metric not in ("identity", "similarity"):
            metric = "identity"

        exclude_orphans = self._truthy(request.GET.get("exclude_orphans", "true"))

        only = (request.GET.get("only") or "").strip().lower()

        payload = {}

        # Build parts according to 'only'
        if only in ("", "table"):
            table_rows = _build_similarity_rows(ref_id)
            payload["table"] = {"results": table_rows}

            # If user asked only table, short-circuit
            if only == "table":
                return JsonResponse(payload)

        if only in ("", "embed"):
            embed = _build_embedding_payload(
                ref_id,
                top_n=top_n,
                metric=metric,
                exclude_orphans=exclude_orphans,
            )
            payload["embedding"] = embed

            if only == "embed":
                return JsonResponse(payload)

        return JsonResponse(payload)



# (Removed) legacy Excel export endpoint.

# ------------------------------ Structure similarity -----------------------------

class StructureSim(ClassificationVisualizationMixin, TemplateView):
    """
    Serve combined t-SNE embeddings for:
      - sequence similarity (from `classification.ReceptorSimilarity`)
      - structure distances (from `classification.StructureSimilarity`, filtered by state)

    All points are annotated from database-backed classification metadata
    (ProteinFamily + ProteinFamilyClassification).
    """

    template_name = 'classification/StructureSim.html'

    # ProteinFamilyClassification cache (Chemotype/Modality/Sense lookup)
    PF_CLASS_CACHE_KEY = 'structuresim:pfclass:db:v4'
    PF_CLASS_CACHE_TIMEOUT = 60 * 60 * 24  # 24h

    # -------------------------- DB-backed embedding + annotations --------------------------

    @staticmethod
    def _sequence_only_plot_types():
        return {
            ClusterCoord.PLOT_TSNE_P60,
            ClusterCoord.PLOT_TSNE_P80,
            ClusterCoord.PLOT_TSNE_P100,
        }

    @classmethod
    def _is_sequence_only_plot_type(cls, plot_type):
        return plot_type in cls._sequence_only_plot_types()

    @staticmethod
    def _plot_method_key(plot_type):
        return plot_type

    @staticmethod
    def _neighbor_dataset_types():
        return {
            ClusterCoord.DATASET_SEQUENCE,
        }

    @staticmethod
    def _neighbor_similarity_model(dataset_type):
        return ReceptorSimilarity

    @staticmethod
    def _entry_stem(entry_name):
        if not entry_name:
            return ""
        return str(entry_name).split("_", 1)[0].upper()

    @staticmethod
    def _gene_name(protein):
        """
        Best-effort gene symbol for a protein.
        Uses prefetched `genes` if available to avoid N+1 queries.
        """
        if protein is None:
            return ""
        try:
            cache = getattr(protein, '_prefetched_objects_cache', {}) or {}
            pref = cache.get('genes')
            if pref:
                g0 = pref[0]
                return getattr(g0, 'name', '') or ''
        except Exception:
            pass
        try:
            g = protein.genes.all().first()
            return g.name if g else ""
        except Exception:
            return ""

    @staticmethod
    def _first_gene_map(protein_ids):
        """
        Return {protein_id: gene_name} for the *first* gene per protein (by Gene.position),
        fetched in a single query. This avoids N+1 when iterating with `.iterator()`.
        """
        if not protein_ids:
            return {}
        gene_map = {}
        qs = (
            Gene.objects
            .filter(proteins__id__in=protein_ids, species_id=1)
            .values_list('proteins__id', 'name', 'position')
            .order_by('proteins__id', 'position')
        )
        for prot_id, gene_name, _pos in qs.iterator():
            if prot_id not in gene_map:
                gene_map[prot_id] = gene_name or ""
        return gene_map

    def _build_similarity_context(self, protein_ids, similarity_model, top_n=10, restrict_targets_to_refs=True):
        """
        Return sequence similarity context for StructureSim:
          - top-neighbor rows per protein
        """
        protein_ids = [int(pid) for pid in protein_ids if pid]
        if not protein_ids:
            return {
                "top_neighbors": {},
            }

        ref_ids = set(protein_ids)
        rows_by_ref = defaultdict(list)
        related_ids = set(ref_ids)

        pair_filter = Q(protein_ref_id__in=ref_ids, protein_ref__species_id=1, protein_target__species_id=1)
        if restrict_targets_to_refs:
            pair_filter &= Q(protein_target_id__in=ref_ids)
        else:
            pair_filter = (
                Q(protein_ref_id__in=ref_ids) |
                Q(protein_target_id__in=ref_ids)
            ) & Q(protein_ref__species_id=1, protein_target__species_id=1)

        pair_qs = (
            similarity_model.objects
            .filter(pair_filter)
            .values('protein_ref_id', 'protein_target_id', 'similarity', 'identity')
        )

        for rec in pair_qs.iterator():
            a = rec.get('protein_ref_id')
            b = rec.get('protein_target_id')
            if not a or not b or a == b:
                continue
            related_ids.add(a)
            related_ids.add(b)
            try:
                sim = float(rec.get('similarity'))
            except Exception:
                continue
            try:
                identity = float(rec.get('identity'))
            except Exception:
                identity = 0.0

            rows_by_ref[a].append({
                'other_id': b,
                'similarity': sim,
                'identity': identity,
            })
            rows_by_ref[b].append({
                'other_id': a,
                'similarity': sim,
                'identity': identity,
            })

        if not rows_by_ref:
            return {
                "top_neighbors": {pid: [] for pid in protein_ids},
            }

        proteins = (
            Protein.objects
            .filter(id__in=related_ids)
            .select_related('family__parent__parent__parent')
        )
        protein_map = {p.id: p for p in proteins}
        gene_map = self._first_gene_map(related_ids)

        LT_ORPHAN = 'Orphan receptors'

        def is_orphan(pid):
            p = protein_map.get(pid)
            if not p:
                return False
            try:
                ligand_type = getattr(getattr(getattr(p, 'family', None), 'parent', None), 'parent', None)
                ligand_type_name = getattr(ligand_type, 'name', '') or ''
            except Exception:
                ligand_type_name = ''
            return ligand_type_name.strip().lower() == LT_ORPHAN.lower()

        def sort_key(row):
            try:
                sim = float(row.get('similarity') or 0)
            except Exception:
                sim = 0.0
            try:
                identity = float(row.get('identity') or 0)
            except Exception:
                identity = 0.0
            other_id = row.get('other_id')
            op = protein_map.get(other_id)
            label = (getattr(op, 'name', None) or getattr(op, 'entry_name', None) or '')
            return (-sim, -identity, str(label).lower())

        neighbor_map = {}
        for ref_id in protein_ids:
            rows = sorted(rows_by_ref.get(ref_id, []), key=sort_key)
            if not rows:
                neighbor_map[ref_id] = []
                continue

            liganded_rows = [row for row in rows if not is_orphan(row.get('other_id'))]
            if liganded_rows:
                cutoff_idx = min(top_n - 1, len(liganded_rows) - 1)
                cutoff = liganded_rows[cutoff_idx].get('similarity')
            else:
                cutoff_idx = min(top_n - 1, len(rows) - 1)
                cutoff = rows[cutoff_idx].get('similarity')

            kept = [row for row in rows if row.get('similarity') >= cutoff]
            out = []
            for row in kept:
                other_id = row.get('other_id')
                op = protein_map.get(other_id)
                if not op:
                    continue
                stem = self._entry_stem(getattr(op, 'entry_name', None))
                out.append({
                    'id': other_id,
                    'label': (getattr(op, 'name', None) or stem or ''),
                    'gene': gene_map.get(other_id, ''),
                    'uniprot': stem,
                    'receptor_family': (
                        getattr(getattr(op, 'family', None), 'parent', None).name
                        if getattr(getattr(op, 'family', None), 'parent', None)
                        else ''
                    ),
                    'class_name': (
                        getattr(getattr(getattr(getattr(op, 'family', None), 'parent', None), 'parent', None), 'parent', None).name
                        if getattr(getattr(getattr(getattr(op, 'family', None), 'parent', None), 'parent', None), 'parent', None)
                        else ''
                    ),
                    'similarity': row.get('similarity') or 0,
                    'identity': row.get('identity') or 0,
                })
            neighbor_map[ref_id] = out

        return {
            "top_neighbors": neighbor_map,
        }

    @staticmethod
    def _rep_structure_pdb_map(state_slug, protein_ids):
        """
        Return {protein_id: pdb_code} for the representative structure per receptor/state.

        IMPORTANT: We derive this from `classification.StructureSimilarity`, which already stores
        the (protein ↔ representative structure) linkage used to build the structure distance
        matrices for the clustering datasets. This avoids relying on other “representative” flags
        that may not match the StructureSimilarity build inputs.
        """
        if not protein_ids:
            return {}

        state_obj = ProteinState.objects.only("id").get(slug=state_slug)
        mapping = {}

        # Ref side
        qs_ref = (
            StructureSimilarity.objects
            .filter(state_id=state_obj.id, protein_ref_id__in=protein_ids)
            .values_list("protein_ref_id", "structure_ref__pdb_code__index")
            .distinct()
        )
        for prot_id, pdb in qs_ref.iterator():
            if prot_id not in mapping:
                mapping[prot_id] = (pdb or "")

        # Target side
        qs_tgt = (
            StructureSimilarity.objects
            .filter(state_id=state_obj.id, protein_target_id__in=protein_ids)
            .values_list("protein_target_id", "structure_target__pdb_code__index")
            .distinct()
        )
        for prot_id, pdb in qs_tgt.iterator():
            if prot_id not in mapping:
                mapping[prot_id] = (pdb or "")

        return mapping

    def _get_pf_classification_map_db(self):
        """
        Map ProteinFamily.id -> {'Chemotype','Modality','Sense'} using ProteinFamilyClassification.
        """
        cached = cache_alignment.get(self.PF_CLASS_CACHE_KEY)
        if cached is not None:
            return cached

        mapping = {}
        qs = (
            ProteinFamilyClassification.objects
            .select_related('sense', 'chemotype', 'modality')
            .only('protein_family_id', 'sense__name', 'chemotype__name', 'modality__name')
        )
        for pfc in qs:
            mapping[pfc.protein_family_id] = {
                'Chemotype': (pfc.chemotype.name if pfc.chemotype else ""),
                'Modality': (pfc.modality.name if pfc.modality else ""),
                'Sense': (pfc.sense.name if pfc.sense else ""),
            }

        cache_alignment.set(self.PF_CLASS_CACHE_KEY, mapping, self.PF_CLASS_CACHE_TIMEOUT)
        return mapping

    def _protein_annotations_db(self, protein, pf_class_map):
        """
        Return annotation keys expected by `StructureSim.html` JavaScript:
        Class, Receptor family, Chemotype, Modality, Sense.
        """
        if protein is None:
            return {'Class': "", 'Receptor family': "", 'Chemotype': "", 'Modality': "", 'Sense': ""}

        pf = getattr(protein, 'family', None)

        receptor_family = ""
        clazz = ""
        try:
            receptor_family = pf.parent.name if (pf and pf.parent) else ""
        except Exception:
            receptor_family = ""
        try:
            clazz = (
                pf.parent.parent.parent.name
                if (pf and pf.parent and pf.parent.parent and pf.parent.parent.parent)
                else ""
            )
        except Exception:
            clazz = ""

        chemotype = ""
        modality = ""
        sense = ""

        cur = pf
        for _ in range(12):
            if not cur:
                break
            rec = pf_class_map.get(cur.id)
            if rec:
                chemotype = rec.get('Chemotype', "") or ""
                modality = rec.get('Modality', "") or ""
                sense = rec.get('Sense', "") or ""
                break
            cur = getattr(cur, 'parent', None)

        # Temporary label tweaks (to be removed after DB rebuild)
        try:
            if str(clazz).strip().upper() in {"OTHER GPCRS", "CLASSLESS", "UNCLASSIFIED"}:
                clazz = "Unclassified"
            if str(receptor_family).strip().upper() == "OTHER GPCR ORPHANS":
                receptor_family = "Orphan receptor"
        except Exception:
            pass

        return {
            'Class': clazz,
            'Receptor family': receptor_family,
            'Chemotype': chemotype,
            'Modality': modality,
            'Sense': sense,
        }

    def _build_sequence_dataset_db(self, pf_class_map, plot_type, group_key, restrict_neighbor_targets_to_refs=True):
        """
        Load persisted coordinates for the sequence dataset from ClusterCoord.
        """
        return self._build_clustercoord_sequence_dataset_db(
            pf_class_map=pf_class_map,
            plot_type=plot_type,
            dataset_type=ClusterCoord.DATASET_SEQUENCE,
            point_dataset="sequence",
            group_key=group_key,
            restrict_neighbor_targets_to_refs=restrict_neighbor_targets_to_refs,
            filter_entry_names=None,
        )

    def _build_clustercoord_sequence_dataset_db(
        self,
        pf_class_map,
        plot_type,
        dataset_type,
        point_dataset,
        group_key,
        restrict_neighbor_targets_to_refs,
        filter_entry_names=None,
    ):
        """
        Shared loader for sequence-like datasets stored in ClusterCoord.
        """
        rows = (
            ClusterCoord.objects
            .filter(
                dataset_type=dataset_type,
                plot_type=plot_type,
                group_key=group_key,
                protein__species_id=1,
            )
            .select_related(
                'protein',
                'protein__family',
                'protein__family__parent',
                'protein__family__parent__parent',
                'protein__family__parent__parent__parent',
            )
            .order_by('protein__entry_name')
        )
        fallback_class_key = self.class_key_from_group_key(group_key)
        if fallback_class_key == "U" and not rows.exists():
            rows = (
                ClusterCoord.objects
                .filter(
                    dataset_type=dataset_type,
                    plot_type=plot_type,
                    group_key=self.GLOBAL_GROUP_KEY,
                    protein__species_id=1,
                )
                .filter(self._visualization_class_clustercoord_q(fallback_class_key))
                .select_related(
                    'protein',
                    'protein__family',
                    'protein__family__parent',
                    'protein__family__parent__parent',
                    'protein__family__parent__parent__parent',
                )
                .order_by('protein__entry_name')
            )

        if filter_entry_names:
            rows = rows.filter(protein__entry_name__in=filter_entry_names)

        # Avoid N+1 for gene lookups: build a single mapping.
        protein_ids = list(rows.values_list('protein_id', flat=True))
        gene_map = self._first_gene_map(protein_ids)
        neighbor_context = {
            "top_neighbors": {},
        }
        if dataset_type in self._neighbor_dataset_types():
            similarity_model = self._neighbor_similarity_model(dataset_type)
            neighbor_context = self._build_similarity_context(
                protein_ids,
                similarity_model,
                restrict_targets_to_refs=restrict_neighbor_targets_to_refs,
            )

        points = []
        for r in rows.iterator():
            p = getattr(r, 'protein', None)
            stem = self._entry_stem(getattr(p, 'entry_name', None))
            gene = gene_map.get(getattr(p, 'id', None), "")
            gtop = (getattr(p, 'name', None) or stem or "")
            ann = self._protein_annotations_db(p, pf_class_map)
            points.append({
                "id": getattr(p, 'id', None),
                "label": gtop,
                "gene": gene,
                "uniprot": stem,
                "entry_name": getattr(p, 'entry_name', None) or "",
                "accession": getattr(p, 'accession', None) or "",
                "x": float(r.x),
                "y": float(r.y),
                "cluster": None,
                "dataset": point_dataset,
                "top_neighbors": neighbor_context["top_neighbors"].get(getattr(p, 'id', None), []),
                **ann,
            })

        method = self._plot_method_key(plot_type)
        return {
            "method": method,
            "points": points,
            "n": len(points),
        }

    def _build_structure_dataset_db(self, state, pf_class_map, plot_type, group_key, filter_entry_names=None):
        """
        Load persisted coordinates for the structure dataset (active/inactive)
        from ClusterCoord.
        """
        dataset_type = (
            ClusterCoord.DATASET_STRUCTURE_ACTIVE
            if state == 'active'
            else ClusterCoord.DATASET_STRUCTURE_INACTIVE
        )

        rows = (
            ClusterCoord.objects
            .filter(
                dataset_type=dataset_type,
                plot_type=plot_type,
                group_key=group_key,
                protein__species_id=1,
            )
            .select_related(
                'protein',
                'protein__family',
                'protein__family__parent',
                'protein__family__parent__parent',
                'protein__family__parent__parent__parent',
            )
            .order_by('protein__entry_name')
        )
        fallback_class_key = self.class_key_from_group_key(group_key)
        if fallback_class_key == "U" and not rows.exists():
            rows = (
                ClusterCoord.objects
                .filter(
                    dataset_type=dataset_type,
                    plot_type=plot_type,
                    group_key=self.GLOBAL_GROUP_KEY,
                    protein__species_id=1,
                )
                .filter(self._visualization_class_clustercoord_q(fallback_class_key))
                .select_related(
                    'protein',
                    'protein__family',
                    'protein__family__parent',
                    'protein__family__parent__parent',
                    'protein__family__parent__parent__parent',
                )
                .order_by('protein__entry_name')
            )

        if filter_entry_names:
            rows = rows.filter(protein__entry_name__in=filter_entry_names)

        # Avoid N+1 for gene lookups: build a single mapping.
        protein_ids = list(rows.values_list('protein_id', flat=True))
        gene_map = self._first_gene_map(protein_ids)
        pdb_map = self._rep_structure_pdb_map(state, protein_ids)

        points = []
        for r in rows.iterator():
            p = getattr(r, 'protein', None)
            stem = self._entry_stem(getattr(p, 'entry_name', None))
            gene = gene_map.get(getattr(p, 'id', None), "")
            gtop = (getattr(p, 'name', None) or stem or "")
            pdb = pdb_map.get(getattr(p, 'id', None), "")
            ann = self._protein_annotations_db(p, pf_class_map)
            points.append({
                "id": getattr(p, 'id', None),
                "label": gtop,
                "gene": gene,
                "uniprot": stem,
                "entry_name": getattr(p, 'entry_name', None) or "",
                "accession": getattr(p, 'accession', None) or "",
                "pdb": pdb,
                "x": float(r.x),
                "y": float(r.y),
                "cluster": None,
                "dataset": f"struct_{state}",
                **ann,
            })

        method = self._plot_method_key(plot_type)
        return {"method": method, "points": points, "n": len(points), "state": state}

    def _build_payload_db(self, plot_type, group_key=None, restrict_neighbor_targets_to_refs=True, filter_entry_names=None):
        group_key = group_key or self.GLOBAL_GROUP_KEY
        is_class_scoped = group_key != self.GLOBAL_GROUP_KEY
        filter_entry_names = list(filter_entry_names or [])
        pf_class_map = self._get_pf_classification_map_db()
        payload = {
            "sequence": self._build_clustercoord_sequence_dataset_db(
                pf_class_map=pf_class_map,
                plot_type=plot_type,
                dataset_type=ClusterCoord.DATASET_SEQUENCE,
                point_dataset="sequence",
                group_key=group_key,
                restrict_neighbor_targets_to_refs=restrict_neighbor_targets_to_refs,
                filter_entry_names=filter_entry_names,
            ),
            "structure": {
                "inactive": (
                    {"method": self._plot_method_key(plot_type), "points": [], "n": 0, "state": "inactive"}
                    if self._is_sequence_only_plot_type(plot_type)
                    else self._build_structure_dataset_db("inactive", pf_class_map, plot_type, group_key=group_key, filter_entry_names=filter_entry_names)
                ),
                "active": (
                    {"method": self._plot_method_key(plot_type), "points": [], "n": 0, "state": "active"}
                    if self._is_sequence_only_plot_type(plot_type)
                    else self._build_structure_dataset_db("active", pf_class_map, plot_type, group_key=group_key, filter_entry_names=filter_entry_names)
                ),
            },
        }
        # DB-only mode: coordinates must exist; otherwise instruct user to build them.
        missing = []
        if not (payload.get("sequence", {}).get("n") or 0):
            missing.append("sequence")
        if (
            (not is_class_scoped)
            and (not self._is_sequence_only_plot_type(plot_type))
            and not (payload.get("structure", {}).get("inactive", {}).get("n") or 0)
        ):
            missing.append("structure_inactive")
        if (
            (not is_class_scoped)
            and (not self._is_sequence_only_plot_type(plot_type))
            and not (payload.get("structure", {}).get("active", {}).get("n") or 0)
        ):
            missing.append("structure_active")
        if missing:
            raise ValueError(
                "Missing ClusterCoord datasets: %s (plot_type=%s, group_key=%s). Run: python manage.py build_clustercoord"
                % (", ".join(missing), plot_type, group_key)
            )
        return payload

    def _build_payload(self):
        """
        Build combined payload with:
          - sequence t-SNE
          - structure inactive / active t-SNE
        DB-backed payload (no Excel/CSV).
        """
        return self._build_payload_db(ClusterCoord.PLOT_TSNE)

    # ---------- TemplateView overrides ----------

    def get(self, request, *args, **kwargs):
        """
        - HTML by default
        - JSON when ?format=json or Accept: application/json
        """
        want_json = (
            request.GET.get('format') == 'json'
            or request.GET.get('data') == '1'
            or 'application/json' in request.headers.get('Accept', '')
        )
        try:
            scope = self.get_requested_visualization_scope(request=request, raise_404=False)
        except ValueError as e:
            if want_json:
                return JsonResponse({"error": str(e)}, status=400)
            raise Http404(str(e))
        group_key = scope["group_key"]
        tree_filter = None
        if request.GET.get("filter_type") or request.GET.get("filter_selection"):
            try:
                tree_filter = self.resolve_tree_visualization_selection(
                    request.GET.get("filter_type"),
                    request.GET.get("filter_selection"),
                )
            except ValueError as e:
                if want_json:
                    return JsonResponse({"error": str(e)}, status=400)
                raise Http404(str(e))
        filter_entry_names = tree_filter.get("entry_names", []) if tree_filter else []
        restrict_neighbor_targets_to_refs = scope["class_key"] is None or bool(filter_entry_names)
        if want_json:
            try:
                plots_param = request.GET.get("plots")
                if plots_param:
                    plots = []
                    for raw in str(plots_param).split(","):
                        p = (raw or "").strip().lower()
                        if p and p not in plots:
                            plots.append(p)

                    out = {}
                    errors = {}
                    key_to_plot_type = {
                        "tsne": ClusterCoord.PLOT_TSNE,
                        "tsne_p60": ClusterCoord.PLOT_TSNE_P60,
                        "tsne-p60": ClusterCoord.PLOT_TSNE_P60,
                        "p60": ClusterCoord.PLOT_TSNE_P60,
                        "tsne_p80": ClusterCoord.PLOT_TSNE_P80,
                        "tsne-p80": ClusterCoord.PLOT_TSNE_P80,
                        "p80": ClusterCoord.PLOT_TSNE_P80,
                        "tsne_p100": ClusterCoord.PLOT_TSNE_P100,
                        "tsne-p100": ClusterCoord.PLOT_TSNE_P100,
                        "p100": ClusterCoord.PLOT_TSNE_P100,
                        "pca": ClusterCoord.PLOT_PCA_TSNE,
                        "pca_tsne": ClusterCoord.PLOT_PCA_TSNE,
                        "pca-tsne": ClusterCoord.PLOT_PCA_TSNE,
                    }
                    for p in plots:
                        t0 = time.time()
                        print(f"[StructureSim] Calculating {p.upper()}…")
                        try:
                            plot_type = key_to_plot_type.get(p, ClusterCoord.PLOT_TSNE)
                            out_key = plot_type
                            out[out_key] = self._build_payload_db(
                                plot_type,
                                group_key=group_key,
                                restrict_neighbor_targets_to_refs=restrict_neighbor_targets_to_refs,
                                filter_entry_names=filter_entry_names,
                            )
                        except Exception as e:
                            errors[p] = str(e)
                        finally:
                            dt = time.time() - t0
                            print(f"[StructureSim] {p.upper()} done in {dt:.2f}s")

                    payload = {"plots": out}
                    if errors:
                        payload["plot_errors"] = errors
                    return JsonResponse(payload, safe=True)

                plot = (request.GET.get("plot") or "tsne").strip().lower()
                key_to_plot_type = {
                    "tsne": ClusterCoord.PLOT_TSNE,
                    "tsne_p60": ClusterCoord.PLOT_TSNE_P60,
                    "tsne-p60": ClusterCoord.PLOT_TSNE_P60,
                    "p60": ClusterCoord.PLOT_TSNE_P60,
                    "tsne_p80": ClusterCoord.PLOT_TSNE_P80,
                    "tsne-p80": ClusterCoord.PLOT_TSNE_P80,
                    "p80": ClusterCoord.PLOT_TSNE_P80,
                    "tsne_p100": ClusterCoord.PLOT_TSNE_P100,
                    "tsne-p100": ClusterCoord.PLOT_TSNE_P100,
                    "p100": ClusterCoord.PLOT_TSNE_P100,
                    "pca": ClusterCoord.PLOT_PCA_TSNE,
                    "pca_tsne": ClusterCoord.PLOT_PCA_TSNE,
                    "pca-tsne": ClusterCoord.PLOT_PCA_TSNE,
                }
                plot_type = key_to_plot_type.get(plot, ClusterCoord.PLOT_TSNE)
                payload = self._build_payload_db(
                    plot_type,
                    group_key=group_key,
                    restrict_neighbor_targets_to_refs=restrict_neighbor_targets_to_refs,
                    filter_entry_names=filter_entry_names,
                )
            except Exception as e:
                return JsonResponse({"error": str(e)}, status=400)
            return JsonResponse(payload, safe=True)

        return super(StructureSim, self).get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        """
        Expose a single URL that JS can fetch combined tsne data from.
        """
        ctx = super(StructureSim, self).get_context_data(**kwargs)
        scope = self.get_requested_visualization_scope(raise_404=True)
        tree_filter = None
        if self.request.GET.get("filter_type") or self.request.GET.get("filter_selection"):
            tree_filter = self.resolve_tree_visualization_selection(
                self.request.GET.get("filter_type"),
                self.request.GET.get("filter_selection"),
            )
        base = self.request.build_absolute_uri(self.request.path)
        params = self.request.GET.copy()
        params.pop("format", None)
        params.pop("data", None)
        params["format"] = "json"
        ctx["embed_url"] = "{}?{}".format(base, params.urlencode())
        ctx["structuresim_scope_class"] = scope["class_key"] or ""
        ctx["structuresim_scope_title"] = (
            tree_filter["selection"]
            if tree_filter
            else (scope["config"]["title"] if scope["config"] else "All classes")
        )
        ctx["structuresim_embed_mode"] = str(self.request.GET.get("embed") or "").strip().lower() in {"1", "true", "yes"}
        return ctx
