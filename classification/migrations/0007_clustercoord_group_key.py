from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("classification", "0006_clustercoord_plot_variants"),
    ]

    operations = [
        migrations.AddField(
            model_name="clustercoord",
            name="group_key",
            field=models.CharField(db_index=True, default="global", max_length=32),
        ),
        migrations.RemoveConstraint(
            model_name="clustercoord",
            name="ccoord_uniq_prot_dataset_plot",
        ),
        migrations.RemoveIndex(
            model_name="clustercoord",
            name="ccoord_ds_plot_idx",
        ),
        migrations.RemoveIndex(
            model_name="clustercoord",
            name="ccoord_prot_ds_idx",
        ),
        migrations.AddConstraint(
            model_name="clustercoord",
            constraint=models.UniqueConstraint(
                fields=("protein", "dataset_type", "plot_type", "group_key"),
                name="ccoord_uniq_prot_ds_plot_grp",
            ),
        ),
        migrations.AddIndex(
            model_name="clustercoord",
            index=models.Index(
                fields=["dataset_type", "plot_type", "group_key"],
                name="ccoord_ds_plot_grp_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="clustercoord",
            index=models.Index(
                fields=["protein", "dataset_type", "group_key"],
                name="ccoord_prot_ds_grp_idx",
            ),
        ),
    ]
