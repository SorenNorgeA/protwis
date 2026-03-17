from build.management.commands.base_build import Command as BaseBuild

from django.core.management.base import CommandError
from django.core.cache import cache, caches
from django.db import transaction

import logging
import math
import time

import numpy as np
from sklearn.manifold import TSNE


try:
    cache_alignment = caches["alignments"]
except Exception:
    cache_alignment = cache


class Command(BaseBuild):
    help = (
        "Build persisted 2D coordinates for StructureSim plots and store them in "
        "classification_clustercoord (sequence + structure active/inactive)."
    )

    logger = logging.getLogger(__name__)

    @staticmethod
    def _format_elapsed(seconds):
        seconds = int(round(seconds))
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h} hours {m} mins {s} secs"

    def add_arguments(self, parser):
        super(Command, self).add_arguments(parser=parser)
        parser.add_argument(
            '--state',
            choices=['active', 'inactive', 'both'],
            default='both',
            help='Which structure state(s) to build.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=5000,
            help='Bulk insert batch size.',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            default=False,
            help='Print progress to stdout.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Compute counts but do not write to the database.',
        )

    @staticmethod
    def _resolve_perplexity(n, perplexity=None):
        if n < 2:
            return 1.0
        if perplexity is None:
            perplexity = min(40.0, max(1.0, (n - 1) / 3.0))
        else:
            perplexity = float(perplexity)
        if perplexity >= (n - 1):
            perplexity = float(max(1, n - 2))
        return perplexity

    @classmethod
    def _compute_tsne(cls, D, perplexity=None):
        n = D.shape[0]
        if n < 2:
            return np.zeros((n, 2), dtype=float)
        perplexity = cls._resolve_perplexity(n, perplexity=perplexity)

        base_kwargs = dict(
            n_components=2,
            metric="precomputed",
            perplexity=perplexity,
            random_state=42,
            init="random",
        )
        try:
            tsne = TSNE(learning_rate="auto", square_distances=True, **base_kwargs)
        except TypeError:
            tsne = TSNE(learning_rate=200.0, **base_kwargs)
        return tsne.fit_transform(D)

    @staticmethod
    def _delete_dataset(model, *, dataset_type, plot_type):
        model.objects.filter(dataset_type=dataset_type, plot_type=plot_type).delete()

    def _build_similarity_dataset(
        self,
        *,
        label,
        dataset_type,
        similarity_model,
        ClusterCoord,
        Protein,
        batch_size,
        verbose,
        dry_run,
    ):
        qs = similarity_model.objects.filter(
            protein_ref__species_id=1,
            protein_target__species_id=1,
        ).values_list('protein_ref_id', 'protein_target_id', 'similarity')

        prot_ids = set()
        for ref_id, tgt_id, _sim in qs.iterator():
            prot_ids.add(ref_id)
            prot_ids.add(tgt_id)

        proteins = list(
            Protein.objects
            .filter(id__in=prot_ids)
            .order_by('entry_name')
            .only('id', 'entry_name')
        )
        n = len(proteins)
        if verbose:
            print(f"[{label}] proteins: {n}")
        if n == 0:
            return 0

        idx = {p.id: i for i, p in enumerate(proteins)}
        D = np.full((n, n), 100.0, dtype=float)
        np.fill_diagonal(D, 0.0)

        for ref_id, tgt_id, sim in qs.iterator():
            i = idx.get(ref_id)
            j = idx.get(tgt_id)
            if i is None or j is None or i == j:
                continue
            try:
                dist = 100.0 - float(sim)
            except Exception:
                continue
            D[i, j] = dist
            D[j, i] = dist

        coords_tsne = self._compute_tsne(D)

        if dry_run:
            return n

        self._delete_dataset(ClusterCoord, dataset_type=dataset_type, plot_type=ClusterCoord.PLOT_TSNE)

        buffer = []
        total = 0

        def _flush():
            nonlocal total
            if not buffer:
                return
            with transaction.atomic():
                ClusterCoord.objects.bulk_create(buffer, batch_size=batch_size)
            total += len(buffer)
            buffer.clear()
            if verbose:
                print(f"[{label}] inserted rows: {total}")

        for i, p in enumerate(proteins):
            buffer.append(ClusterCoord(
                protein_id=p.id,
                dataset_type=dataset_type,
                plot_type=ClusterCoord.PLOT_TSNE,
                x=float(coords_tsne[i, 0]),
                y=float(coords_tsne[i, 1]),
            ))
            if len(buffer) >= batch_size:
                _flush()
        _flush()
        return total

    def _build_sequence(self, *, ClusterCoord, ReceptorSimilarity, Protein, batch_size, verbose, dry_run):
        return self._build_similarity_dataset(
            label="sequence",
            dataset_type=ClusterCoord.DATASET_SEQUENCE,
            similarity_model=ReceptorSimilarity,
            ClusterCoord=ClusterCoord,
            Protein=Protein,
            batch_size=batch_size,
            verbose=verbose,
            dry_run=dry_run,
        )

    def _build_structure_state(self, state_slug, *, ClusterCoord, StructureSimilarity, ProteinState, Protein, batch_size, verbose, dry_run):
        state_obj = ProteinState.objects.only('id').get(slug=state_slug)

        qs = (
            StructureSimilarity.objects
            .filter(state_id=state_obj.id, protein_ref__species_id=1, protein_target__species_id=1)
            .values_list('protein_ref_id', 'protein_target_id', 'distance')
        )

        prot_ids = set()
        max_dist = 0.0
        pairs = {}

        for a_id, b_id, d in qs.iterator():
            if a_id == b_id:
                continue
            prot_ids.add(a_id)
            prot_ids.add(b_id)
            try:
                dist = float(d)
            except Exception:
                continue
            if math.isnan(dist) or math.isinf(dist):
                continue
            if dist > max_dist:
                max_dist = dist
            key = (a_id, b_id) if a_id < b_id else (b_id, a_id)
            prev = pairs.get(key)
            # If duplicates exist (unexpected), keep the minimum distance
            pairs[key] = dist if (prev is None or dist < prev) else prev

        prot_qs = (
            Protein.objects
            .filter(id__in=prot_ids)
            .order_by('entry_name')
            .only('id', 'entry_name')
        )
        if state_slug == 'inactive':
            prot_qs = prot_qs.exclude(entry_name='ccr9_human')

        proteins = list(prot_qs)
        n = len(proteins)
        if verbose:
            print(f"[{state_slug}] proteins: {n} unique pairs: {len(pairs)}")
        if n == 0:
            return 0

        if max_dist <= 0:
            max_dist = 1.0

        idx = {p.id: i for i, p in enumerate(proteins)}
        D = np.full((n, n), max_dist, dtype=float)
        np.fill_diagonal(D, 0.0)

        for (a_id, b_id), dist in pairs.items():
            i = idx.get(a_id)
            j = idx.get(b_id)
            if i is None or j is None or i == j:
                continue
            D[i, j] = dist
            D[j, i] = dist

        coords_tsne = self._compute_tsne(D)

        if dry_run:
            return n

        dataset_type = (
            ClusterCoord.DATASET_STRUCTURE_ACTIVE
            if state_slug == 'active'
            else ClusterCoord.DATASET_STRUCTURE_INACTIVE
        )
        self._delete_dataset(ClusterCoord, dataset_type=dataset_type, plot_type=ClusterCoord.PLOT_TSNE)

        buffer = []
        total = 0

        def _flush():
            nonlocal total
            if not buffer:
                return
            with transaction.atomic():
                ClusterCoord.objects.bulk_create(buffer, batch_size=batch_size)
            total += len(buffer)
            buffer.clear()
            if verbose:
                print(f"[{state_slug}] inserted rows: {total}")

        for i, p in enumerate(proteins):
            buffer.append(ClusterCoord(
                protein_id=p.id,
                dataset_type=dataset_type,
                plot_type=ClusterCoord.PLOT_TSNE,
                x=float(coords_tsne[i, 0]),
                y=float(coords_tsne[i, 1]),
            ))
            if len(buffer) >= batch_size:
                _flush()
        _flush()
        return total

    def handle(self, *args, **options):
        try:
            from classification.models import ClusterCoord, ReceptorSimilarity, StructureSimilarity
        except ImportError as e:
            raise CommandError("Classification models not available. Did you migrate the classification app?") from e

        from protein.models import Protein, ProteinState

        state_opt = options['state']
        batch_size = int(options['batch_size'])
        verbose = bool(options['verbose'])
        dry_run = bool(options['dry_run'])
        test = bool(options.get('test'))
        if test:
            raise CommandError("This command does not support --test; it writes summary coordinate tables.")

        t0 = time.time()

        # Clear all existing coords on each run (all datasets/plots).
        if not dry_run:
            if verbose:
                print("[clustercoord] clearing classification_clustercoord …")
            ClusterCoord.objects.all().delete()

        # Always rebuild sequence datasets on each run (independent of --state)
        seq_n = self._build_sequence(
            ClusterCoord=ClusterCoord,
            ReceptorSimilarity=ReceptorSimilarity,
            Protein=Protein,
            batch_size=batch_size,
            verbose=verbose,
            dry_run=dry_run,
        )

        targets = []
        if state_opt in ('active', 'both'):
            targets.append('active')
        if state_opt in ('inactive', 'both'):
            targets.append('inactive')

        struct_counts = {}
        for st in targets:
            struct_counts[st] = self._build_structure_state(
                st,
                ClusterCoord=ClusterCoord,
                StructureSimilarity=StructureSimilarity,
                ProteinState=ProteinState,
                Protein=Protein,
                batch_size=batch_size,
                verbose=verbose,
                dry_run=dry_run,
            )

        # Clear StructureSim JSON payload cache (view will rebuild quickly from DB)
        if not dry_run:
            cache_alignment.delete('structuresim:payload:db:v5:clustercoord')

        t1 = time.time()
        self.logger.info(
            "Built ClusterCoord: seq=%s active=%s inactive=%s in %s",
            seq_n,
            struct_counts.get('active', 0),
            struct_counts.get('inactive', 0),
            self._format_elapsed(t1 - t0),
        )

