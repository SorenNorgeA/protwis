from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("classification", "0004_receptorsimilarity2"),
    ]

    operations = [
        migrations.AlterField(
            model_name="clustercoord",
            name="dataset_type",
            field=models.CharField(
                choices=[
                    ("sequence", "Sequence"),
                    ("gn_seq", "GN Sequence"),
                    ("structure_active", "Structure (active)"),
                    ("structure_inactive", "Structure (inactive)"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
