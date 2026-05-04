from build.management.commands.base_build import Command as BaseBuild

from django.core.management.base import CommandError

import logging
import time


class Command(BaseBuild):
    help = (
        "Build persisted receptor-family and superfamily tree payloads and store them in "
        "classification_treenetwork."
    )

    logger = logging.getLogger(__name__)
    BUILD_VERSION = "v1"

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
            "--verbose",
            action="store_true",
            default=False,
            help="Print progress to stdout.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Compute payloads but do not write to the database.",
        )

    def handle(self, *args, **options):
        try:
            from classification.family_tree import (
                BRANCH_MODE,
                SEGMENT_SOURCE,
                SUPERFAMILY_TREE_DISPLAY_NAME,
                SUPERFAMILY_TREE_GROUP_KEY,
                SUPERFAMILY_TREE_SEGMENT_SOURCE,
                SUPERFAMILY_TREE_VARIANT,
                TREE_METHOD,
                build_family_tree_payload,
                build_superfamily_tree_payload,
                family_source_hash,
                resolve_family_network_nodes,
                superfamily_source_hash,
            )
            from classification.models import TreeNetwork
            from classification.views import ClassSimilarityDataMixin, ClassificationVisualizationMixin
        except ImportError as e:
            raise CommandError("Classification family tree helpers are not available.") from e

        verbose = bool(options["verbose"])
        dry_run = bool(options["dry_run"])
        test = bool(options.get("test"))
        if test:
            raise CommandError("This command does not support --test; it writes persisted tree payloads.")

        t0 = time.time()
        helper = ClassificationVisualizationMixin()
        family_entries = helper._build_receptor_family_catalog().get("entries", [])

        if verbose:
            print(f"[treenetwork] family groups: {len(family_entries)}")

        if not dry_run:
            TreeNetwork.objects.all().delete()

        rows = []
        for index, family_entry in enumerate(family_entries, start=1):
            proteins = helper.get_visualization_family_proteins(family_entry)
            try:
                payload = build_family_tree_payload(
                    proteins,
                    family_entry,
                    build_version=self.BUILD_VERSION,
                )
            except Exception as exc:
                raise CommandError(
                    "Failed to build tree payload for '{}' ({})".format(
                        family_entry.get("label") or family_entry.get("name") or family_entry.get("key"),
                        family_entry.get("key"),
                    )
                ) from exc

            family_obj, class_family = resolve_family_network_nodes(proteins, family_entry)
            row = TreeNetwork(
                group_key=str(family_entry.get("key") or ""),
                family=family_obj,
                class_family=class_family,
                display_name=str(family_entry.get("label") or family_entry.get("name") or ""),
                protein_count=len(proteins),
                tree_method=TREE_METHOD,
                segment_source=SEGMENT_SOURCE,
                bootstrap=0,
                branch_mode=BRANCH_MODE,
                tree_newick=str(payload.get("tree") or ""),
                payload=payload,
                build_version=self.BUILD_VERSION,
                source_hash=family_source_hash(proteins),
            )
            rows.append(row)
            if verbose:
                print(
                    "[treenetwork] {:>3}/{:>3} {} -> {} receptors".format(
                        index,
                        len(family_entries),
                        row.group_key,
                        row.protein_count,
                    )
                )

        try:
            class_cluster_payload = ClassSimilarityDataMixin()._build_class_cluster_tree_payload()
            superfamily_payload = build_superfamily_tree_payload(
                class_cluster_payload,
                variant_key=SUPERFAMILY_TREE_VARIANT,
                build_version=self.BUILD_VERSION,
            )
        except Exception as exc:
            raise CommandError("Failed to build persisted superfamily tree payload.") from exc

        superfamily_row = TreeNetwork(
            group_key=SUPERFAMILY_TREE_GROUP_KEY,
            family=None,
            class_family=None,
            display_name=SUPERFAMILY_TREE_DISPLAY_NAME,
            protein_count=len(superfamily_payload.get("classes") or []),
            tree_method=str((superfamily_payload.get("meta") or {}).get("tree_method") or "neighbor_joining_midpoint"),
            segment_source=SUPERFAMILY_TREE_SEGMENT_SOURCE,
            bootstrap=0,
            branch_mode=str((superfamily_payload.get("meta") or {}).get("branch_mode") or BRANCH_MODE),
            tree_newick=str(superfamily_payload.get("tree") or ""),
            payload=superfamily_payload,
            build_version=self.BUILD_VERSION,
            source_hash=superfamily_source_hash(superfamily_payload),
        )
        rows.append(superfamily_row)
        if verbose:
            print(
                "[treenetwork] superfamily {} -> {} classes".format(
                    superfamily_row.group_key,
                    superfamily_row.protein_count,
                )
            )

        if not dry_run and rows:
            TreeNetwork.objects.bulk_create(rows, batch_size=100)

        t1 = time.time()
        self.logger.info(
            "Built TreeNetwork rows=%s in %s",
            len(rows),
            self._format_elapsed(t1 - t0),
        )
        if verbose:
            print("[treenetwork] completed {} groups in {}".format(len(rows), self._format_elapsed(t1 - t0)))
