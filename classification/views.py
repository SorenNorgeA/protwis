from django.conf import settings
from django.core.cache import cache, caches
from django.db.models import Case, F, IntegerField, Max, Prefetch, Q, When
from django.db.models.functions import Greatest, Least
from django.http import JsonResponse
from django.views import View
from django.views.generic import TemplateView

from classification.models import ClusterCoord, ReceptorSimilarity, StructureSimilarity
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

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE


try:
    cache_alignment = caches["alignments"]
except Exception:
    cache_alignment = cache

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
        col_class = pick('Class')
        col_chemotype = pick('Chemotype')
        col_family = pick('Receptor family')
        col_modality = pick('Modality')

        # Build a normalized dataframe with standard column names
        normalized_cols = {}
        if col_uni:
            normalized_cols['GPCRs (UniProt)'] = df[col_uni]
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
        required_cols = ['GPCRs (UniProt)', 'Class', 'Chemotype', 'Receptor family', 'Modality']
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
        modality_labels = ["Orphan receptors", "Peptide receptors", "Protein receptors", "Small molecule receptors"]
        for m in modality_labels:
            key = m
            label = m
            # Filter by Modality column directly (case-insensitive exact match)
            m_series = df["Modality"].apply(lambda v: (self._clean_cell(v) or ""))
            m_df = df[m_series.str.lower() == m.lower()].copy()
            if len(m_df) == 0:
                continue
            nested_cf = self._build_nested_class_family(m_df, class_to_symbol)
            tree_sets["Modality"]["options"].append({"key": key, "label": label})
            tree_sets["Modality"]["plots"][key] = {
                "tree": self._nested_class_family_to_tree(nested_cf),
                "tree_options": dict(base_tree_options, **{"colorMode": "class"}),
                "meta": {"title": label, "liftClassLayer": False, "collapseLabels": []},
            }
        # Sort modality dropdown alphabetically, but keep Orphan receptors last.
        def _mod_sort(o):
            lbl = str(o.get("label", "")).strip()
            is_orphan = (lbl.lower() == "orphan receptors")
            return (1 if is_orphan else 0, lbl.lower())
        tree_sets["Modality"]["options"].sort(key=_mod_sort)

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

class CrossClassSimilarity(TemplateView):
    template_name = 'classification/CrossClassSimilarity.html'

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
    }

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
    def _resolve_family_ids(self, slug_codes):
        qs = ProteinFamily.objects.filter(slug__in=slug_codes).only('id', 'slug', 'name')
        return {f.slug: f.id for f in qs}

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

        # 1) Build display list (no extra/non-human groups)
        base_names   = list(self.CLASS_ORDER)
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

class StructureSim(TemplateView):
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
            if str(clazz).strip().upper() == "OTHER GPCRS":
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

    def _build_sequence_dataset_db(self, pf_class_map, plot_type):
        """
        Load persisted coordinates for the sequence dataset from ClusterCoord.
        """
        rows = (
            ClusterCoord.objects
            .filter(
                dataset_type=ClusterCoord.DATASET_SEQUENCE,
                plot_type=plot_type,
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

        # Avoid N+1 for gene lookups: build a single mapping.
        protein_ids = list(rows.values_list('protein_id', flat=True))
        gene_map = self._first_gene_map(protein_ids)

        points = []
        for r in rows.iterator():
            p = getattr(r, 'protein', None)
            stem = self._entry_stem(getattr(p, 'entry_name', None))
            gene = gene_map.get(getattr(p, 'id', None), "")
            gtop = (getattr(p, 'name', None) or stem or "")
            ann = self._protein_annotations_db(p, pf_class_map)
            points.append({
                "label": gtop,
                "gene": gene,
                "uniprot": stem,
                "x": float(r.x),
                "y": float(r.y),
                "cluster": None,
                "dataset": "sequence",
                **ann,
            })

        method = "tsne" if plot_type == ClusterCoord.PLOT_TSNE else "pca_tsne"
        return {"method": method, "points": points, "n": len(points)}

    def _build_structure_dataset_db(self, state, pf_class_map, plot_type):
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
                "label": gtop,
                "gene": gene,
                "uniprot": stem,
                "pdb": pdb,
                "x": float(r.x),
                "y": float(r.y),
                "cluster": None,
                "dataset": f"struct_{state}",
                **ann,
            })

        method = "tsne" if plot_type == ClusterCoord.PLOT_TSNE else "pca_tsne"
        return {"method": method, "points": points, "n": len(points), "state": state}

    def _build_payload_db(self, plot_type):
        pf_class_map = self._get_pf_classification_map_db()
        payload = {
            "sequence": self._build_sequence_dataset_db(pf_class_map, plot_type),
            "structure": {
                "inactive": self._build_structure_dataset_db("inactive", pf_class_map, plot_type),
                "active": self._build_structure_dataset_db("active", pf_class_map, plot_type),
            },
        }
        # DB-only mode: coordinates must exist; otherwise instruct user to build them.
        missing = []
        if not (payload.get("sequence", {}).get("n") or 0):
            missing.append("sequence")
        if not (payload.get("structure", {}).get("inactive", {}).get("n") or 0):
            missing.append("structure_inactive")
        if not (payload.get("structure", {}).get("active", {}).get("n") or 0):
            missing.append("structure_active")
        if missing:
            raise ValueError(
                "Missing ClusterCoord datasets: %s (plot_type=%s). Run: python manage.py build_clustercoord"
                % (", ".join(missing), plot_type)
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
                        "pca": ClusterCoord.PLOT_PCA_TSNE,
                        "pca_tsne": ClusterCoord.PLOT_PCA_TSNE,
                        "pca-tsne": ClusterCoord.PLOT_PCA_TSNE,
                    }
                    for p in plots:
                        t0 = time.time()
                        print(f"[StructureSim] Calculating {p.upper()}…")
                        try:
                            plot_type = key_to_plot_type.get(p, ClusterCoord.PLOT_TSNE)
                            out_key = "tsne" if plot_type == ClusterCoord.PLOT_TSNE else "pca_tsne"
                            out[out_key] = self._build_payload_db(plot_type)
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
                    "pca": ClusterCoord.PLOT_PCA_TSNE,
                    "pca_tsne": ClusterCoord.PLOT_PCA_TSNE,
                    "pca-tsne": ClusterCoord.PLOT_PCA_TSNE,
                }
                plot_type = key_to_plot_type.get(plot, ClusterCoord.PLOT_TSNE)
                payload = self._build_payload_db(plot_type)
            except Exception as e:
                return JsonResponse({"error": str(e)}, status=400)
            return JsonResponse(payload, safe=True)

        return super(StructureSim, self).get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        """
        Expose a single URL that JS can fetch combined tsne data from.
        """
        ctx = super(StructureSim, self).get_context_data(**kwargs)
        base = self.request.build_absolute_uri(self.request.path)
        ctx['embed_url'] = "%s?format=json" % base
        return ctx
