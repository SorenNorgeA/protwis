from django.db import migrations, models
import django.db.models.deletion
import django.db.models.expressions


class Migration(migrations.Migration):

    dependencies = [
        ("classification", "0003_clustercoord_pca_tsne"),
        ("protein", "0021_protein_family_classification"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReceptorSimilarity2",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("identity", models.PositiveSmallIntegerField()),
                ("similarity", models.PositiveSmallIntegerField()),
                (
                    "protein_ref",
                    models.ForeignKey(
                        db_column="ref",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="receptor_similarity2_as_ref",
                        to="protein.Protein",
                    ),
                ),
                (
                    "protein_target",
                    models.ForeignKey(
                        db_column="target",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="receptor_similarity2_as_target",
                        to="protein.Protein",
                    ),
                ),
                (
                    "ref_class",
                    models.ForeignKey(
                        blank=True,
                        db_column="ref_class",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sim2_as_ref_class",
                        to="protein.ProteinFamily",
                    ),
                ),
                (
                    "target_class",
                    models.ForeignKey(
                        blank=True,
                        db_column="target_class",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sim2_as_target_class",
                        to="protein.ProteinFamily",
                    ),
                ),
            ],
            options={
                "db_table": "classification_receptorsimilarity2",
            },
        ),
        migrations.AddIndex(
            model_name="receptorsimilarity2",
            index=models.Index(fields=["ref_class", "target_class", "identity"], name="crs2_cls_id_idx"),
        ),
        migrations.AddIndex(
            model_name="receptorsimilarity2",
            index=models.Index(fields=["ref_class", "target_class", "similarity"], name="crs2_cls_sim_idx"),
        ),
        migrations.AddIndex(
            model_name="receptorsimilarity2",
            index=models.Index(fields=["target_class", "ref_class", "identity"], name="crs2_cls_id_rev_idx"),
        ),
        migrations.AddIndex(
            model_name="receptorsimilarity2",
            index=models.Index(fields=["target_class", "ref_class", "similarity"], name="crs2_cls_sim_rev_idx"),
        ),
        migrations.AddConstraint(
            model_name="receptorsimilarity2",
            constraint=models.UniqueConstraint(fields=("protein_ref", "protein_target"), name="crs2_uniq_pair"),
        ),
        migrations.AddConstraint(
            model_name="receptorsimilarity2",
            constraint=models.CheckConstraint(
                check=models.Q(_negated=True, protein_ref=django.db.models.expressions.F("protein_target")),
                name="crs2_ref_ne_target",
            ),
        ),
    ]
