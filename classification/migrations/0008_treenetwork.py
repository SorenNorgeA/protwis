from django.contrib.postgres.fields import JSONField
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("classification", "0007_clustercoord_group_key"),
        ("protein", "0021_protein_family_classification"),
    ]

    operations = [
        migrations.CreateModel(
            name="TreeNetwork",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("group_key", models.CharField(max_length=64, unique=True)),
                ("display_name", models.CharField(max_length=200)),
                ("protein_count", models.PositiveSmallIntegerField(default=0)),
                ("tree_method", models.CharField(default="neighbor_joining", max_length=64)),
                ("segment_source", models.CharField(default="generic_conserved", max_length=64)),
                ("bootstrap", models.PositiveSmallIntegerField(default=0)),
                ("branch_mode", models.CharField(default="regular", max_length=32)),
                ("tree_newick", models.TextField(blank=True, default="")),
                ("payload", JSONField(default=dict)),
                ("build_version", models.CharField(default="v1", max_length=32)),
                ("source_hash", models.CharField(blank=True, default="", max_length=40)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "class_family",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="protein.ProteinFamily",
                    ),
                ),
                (
                    "family",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="protein.ProteinFamily",
                    ),
                ),
            ],
            options={
                "db_table": "classification_treenetwork",
            },
        ),
        migrations.AddIndex(
            model_name="treenetwork",
            index=models.Index(fields=["class_family", "group_key"], name="ctn_class_group_idx"),
        ),
        migrations.AddIndex(
            model_name="treenetwork",
            index=models.Index(fields=["family", "group_key"], name="ctn_family_group_idx"),
        ),
    ]
