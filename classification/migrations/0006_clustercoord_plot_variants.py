from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("classification", "0005_clustercoord_gn_seq"),
    ]

    operations = [
        migrations.AlterField(
            model_name="clustercoord",
            name="plot_type",
            field=models.CharField(
                choices=[
                    ("tsne", "t-SNE"),
                    ("pca_tsne", "PCA→t-SNE"),
                    ("tsne_p60", "t-SNE (p=60)"),
                    ("tsne_p80", "t-SNE (p=80)"),
                    ("tsne_p100", "t-SNE (p=100)"),
                ],
                db_index=True,
                default="tsne",
                max_length=16,
            ),
        ),
    ]
