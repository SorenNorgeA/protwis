from django.db import models, connection

# Create your models here.

class AlignmentConsensus(models.Model):
    slug = models.SlugField(max_length=100, unique=True)
    alignment = models.BinaryField()
    gn_consensus = models.BinaryField(blank=True) # Store conservation calculation for each GN


class CustomReceptorSimilarityManager(models.Manager):
    def truncate_table(self):
        with connection.cursor() as cursor:
            cursor.execute(f'TRUNCATE TABLE "{self.model._meta.db_table}" CASCADE')

class ReceptorSimilarity(models.Model):
    protein_ref = models.ForeignKey(
        'protein.Protein',
        on_delete=models.CASCADE,
        related_name='receptor_similarity_as_ref',
        db_column='ref',
        db_index=True,
    )
    protein_target = models.ForeignKey(
        'protein.Protein',
        on_delete=models.CASCADE,
        related_name='receptor_similarity_as_target',
        db_column='target',
        db_index=True,
    )
    identity = models.PositiveSmallIntegerField()
    similarity = models.PositiveSmallIntegerField()

    # Top-level class FKs (nullable for backfill)
    ref_class = models.ForeignKey(
        'protein.ProteinFamily',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='sim_as_ref_class',
        db_column='ref_class',
        db_index=True,
    )
    target_class = models.ForeignKey(
        'protein.ProteinFamily',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='sim_as_target_class',
        db_column='target_class',
        db_index=True,
    )

    objects = models.Manager()
    custom_objects = CustomReceptorSimilarityManager()

    class Meta:
        db_table = 'alignment_receptorsimilarity'
        constraints = [
            models.UniqueConstraint(
                fields=['protein_ref', 'protein_target'],
                name='uniq_receptor_similarity_pair',
            ),
            models.CheckConstraint(
                check=~models.Q(protein_ref=models.F('protein_target')),
                name='check_ref_ne_target',
            ),
        ]
        indexes = [
            models.Index(fields=['ref_class', 'target_class', 'identity'], name='rs_cls_id_idx'),
            models.Index(fields=['ref_class', 'target_class', 'similarity'], name='rs_cls_sim_idx'),
            models.Index(fields=['target_class', 'ref_class', 'identity'], name='rs_cls_id_rev_idx'),
            models.Index(fields=['target_class', 'ref_class', 'similarity'], name='rs_cls_sim_rev_idx'),
        ]

    def __str__(self):
        return f'{self.protein_ref} vs {self.protein_target}: sim={self.similarity} id={self.identity}'


class _StructureSimilarityBase(models.Model):
    """
    Pairwise structural distance between two representative structures.

    Stored in one canonical direction only (ref != target) and intended to be
    populated by a build management command.
    """

    structure_ref = models.ForeignKey(
        'structure.Structure',
        on_delete=models.CASCADE,
        related_name='+',
        db_index=True,
    )
    structure_target = models.ForeignKey(
        'structure.Structure',
        on_delete=models.CASCADE,
        related_name='+',
        db_index=True,
    )

    # Raw (unnormalized) distance and normalized distance (as used by clustering default)
    distance = models.FloatField()
    distance_normalized = models.FloatField(default=0.0)

    # Canonical (parent) receptor protein IDs for convenience
    protein_ref = models.ForeignKey(
        'protein.Protein',
        on_delete=models.CASCADE,
        related_name='+',
        db_index=True,
    )
    protein_target = models.ForeignKey(
        'protein.Protein',
        on_delete=models.CASCADE,
        related_name='+',
        db_index=True,
    )

    class Meta:
        abstract = True

    def __str__(self):
        return f'{self.structure_ref_id} vs {self.structure_target_id}: d={self.distance}'


class StructureSimilarityActive(_StructureSimilarityBase):
    class Meta(_StructureSimilarityBase.Meta):
        db_table = 'alignment_structuresimilarityactive'
        constraints = [
            models.UniqueConstraint(
                fields=['structure_ref', 'structure_target'],
                name='uniq_structsim_active_pair',
            ),
            models.CheckConstraint(
                check=~models.Q(structure_ref=models.F('structure_target')),
                name='check_structsim_active_ref_ne_target',
            ),
        ]
        indexes = [
            models.Index(fields=['protein_ref', 'protein_target'], name='ss_active_prot_pair_idx'),
            models.Index(fields=['structure_ref', 'structure_target'], name='ss_active_struct_pair_idx'),
            models.Index(fields=['distance'], name='ss_active_dist_idx'),
            models.Index(fields=['distance_normalized'], name='ss_active_distn_idx'),
        ]


class StructureSimilarityInactive(_StructureSimilarityBase):
    class Meta(_StructureSimilarityBase.Meta):
        db_table = 'alignment_structuresimilarityinactive'
        constraints = [
            models.UniqueConstraint(
                fields=['structure_ref', 'structure_target'],
                name='uniq_structsim_inactive_pair',
            ),
            models.CheckConstraint(
                check=~models.Q(structure_ref=models.F('structure_target')),
                name='check_structsim_inactive_ref_ne_target',
            ),
        ]
        indexes = [
            models.Index(fields=['protein_ref', 'protein_target'], name='ss_inactive_prot_pair_idx'),
            models.Index(fields=['structure_ref', 'structure_target'], name='ss_inactive_struct_pair_idx'),
            models.Index(fields=['distance'], name='ss_inactive_dist_idx'),
            models.Index(fields=['distance_normalized'], name='ss_inactive_distn_idx'),
        ]
