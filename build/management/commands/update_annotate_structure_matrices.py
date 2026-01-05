import os
from collections import defaultdict

import pandas as pd

from django.conf import settings
from django.core.management.base import BaseCommand

from structure.models import Structure, StructureModel


class Command(BaseCommand):
    """
    Read structure distance matrices (CSV), rename labels from PDB -> entryPrefix_PDB
    (e.g. 8PJK -> 5ht1a_8PJK), and save as Excel (and optionally CSV).
    """

    help = "Annotate structure distance matrices with entry_name-based labels."

    # Keep these in sync with StructureSim
    DATA_FOLDER = 'structure_data'
    FILES = {
        'inactive': 'GPCR_structure_clustering_inactiveRep.csv',
        'active':   'GPCR_structure_clustering_activeStructuresRep.csv',
    }

    def add_arguments(self, parser):
        parser.add_argument(
            '--overwrite-csv',
            action='store_true',
            default=False,
            help='Also write new CSV files with updated labels (originals untouched otherwise).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Do everything except writing files.',
        )

    # ---- helpers ----

    def _data_path(self, filename: str) -> str:
        return os.path.join(settings.DATA_DIR, self.DATA_FOLDER, filename)

    def _canonical_protein_from_structure(self, s, models_by_template):
        """
        Same logic as in StructureSim._canonical_protein_from_structure:

          1) structure's protein if it has accession
          2) else its parent if it has accession
          3) else StructureModel(main_template=s) protein with accession
          4) else fallback to structure's protein
        """
        p = s.protein_conformation.protein
        if getattr(p, 'accession', None):
            return p

        parent = getattr(p, 'parent', None)
        if parent and getattr(parent, 'accession', None):
            return parent

        for sm in models_by_template.get(s.id, ()):
            mp = sm.protein
            if getattr(mp, 'accession', None):
                return mp

        return p

    def _build_label_mapping(self, labels):
        """
        labels: list of PDB codes (as in CSV index/columns)

        Returns:
          mapping: {original_label -> new_label}
        """
        # Normalize PDB codes => uppercase for lookup
        codes_upper = [str(lab).strip().upper() for lab in labels if str(lab).strip()]
        codes_upper = list(dict.fromkeys(codes_upper))  # unique, preserve order

        # Fetch structures and related proteins
        structures_qs = (
            Structure.objects
            .filter(pdb_code__index__in=codes_upper)
            .select_related(
                'pdb_code',
                'protein_conformation__protein',
                'protein_conformation__protein__parent',
            )
        )
        structures = list(structures_qs)
        by_pdb = {s.pdb_code.index.upper(): s for s in structures}

        # Preload StructureModel for canonical fallback
        template_ids = [s.id for s in structures]
        models_by_template = defaultdict(list)
        if template_ids:
            for sm in (
                StructureModel.objects
                .filter(main_template_id__in=template_ids)
                .select_related('protein')
            ):
                models_by_template[sm.main_template_id].append(sm)

        mapping = {}
        missing_structures = []
        missing_entry_names = []

        for original in labels:
            raw = str(original).strip()
            if not raw:
                continue

            pdb = raw.upper()
            s = by_pdb.get(pdb)
            if not s:
                missing_structures.append(raw)
                continue

            p = self._canonical_protein_from_structure(s, models_by_template)
            entry_name = getattr(p, 'entry_name', None)
            if not entry_name:
                missing_entry_names.append(pdb)
                continue

            # entry_name is like "5ht1a_human" -> we want "5ht1a_8PJK"
            entry_prefix = entry_name.split('_')[0].lower()  # keep lower-case like GPCRdb entry_names
            new_label = f"{entry_prefix}_{pdb}"

            mapping[original] = new_label

        # Log issues
        if missing_structures:
            self.stderr.write(
                f"WARNING: No Structure found for {len(missing_structures)} labels: "
                f"{', '.join(sorted(set(missing_structures)))}"
            )
        if missing_entry_names:
            self.stderr.write(
                f"WARNING: Missing entry_name for {len(missing_entry_names)} PDB codes: "
                f"{', '.join(sorted(set(missing_entry_names)))}"
            )

        return mapping

    def _apply_mapping(self, df, mapping):
        """
        Rename index & columns using mapping dict.
        Labels without mapping are left unchanged.
        """
        new_index = [mapping.get(lbl, lbl) for lbl in df.index]
        new_cols = [mapping.get(col, col) for col in df.columns]
        df.index = new_index
        df.columns = new_cols
        return df

    # ---- main ----

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        overwrite_csv = options['overwrite_csv']

        for state, filename in self.FILES.items():
            path = self._data_path(filename)
            if not os.path.exists(path):
                self.stderr.write(f"[{state}] File not found: {path}")
                continue

            self.stdout.write(f"[{state}] Reading CSV: {path}")
            df = pd.read_csv(path, index_col=0)

            if df.shape[0] != df.shape[1]:
                self.stderr.write(
                    f"[{state}] ERROR: distance matrix is not square "
                    f"({df.shape[0]}x{df.shape[1]}). Skipping."
                )
                continue

            labels = df.index.astype(str).tolist()

            # Build label mapping
            self.stdout.write(f"[{state}] Building PDB -> entryPrefix_PDB mapping...")
            mapping = self._build_label_mapping(labels)

            # Apply mapping
            self.stdout.write(f"[{state}] Applying mapping to rows/columns...")
            df = self._apply_mapping(df, mapping)

            # Prepare output filenames
            base, _ = os.path.splitext(filename)
            xlsx_name = f"{base}_entry_pdb.xlsx"
            xlsx_path = self._data_path(xlsx_name)

            csv_name = f"{base}_entry_pdb.csv"
            csv_path = self._data_path(csv_name)

            if dry_run:
                self.stdout.write(
                    f"[{state}] DRY RUN: would write Excel to {xlsx_path}"
                    + (f" and CSV to {csv_path}" if overwrite_csv else "")
                )
                continue

            # Write Excel
            self.stdout.write(f"[{state}] Writing Excel: {xlsx_path}")
            df.to_excel(xlsx_path)

            # Optionally write new CSV
            if overwrite_csv:
                self.stdout.write(f"[{state}] Writing CSV: {csv_path}")
                df.to_csv(csv_path)

        self.stdout.write(self.style.SUCCESS("Done annotating structure matrices."))
