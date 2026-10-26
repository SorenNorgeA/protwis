#Base imports
import html
import re

#GPCRdb imports
from classification import classification_db


#############
# GPCR List #
#############

def _plain_text(markup):
    """Text of an HTML snippet: tags removed and entities decoded, e.g. "&beta;<sub>2</sub>-adrenoceptor" -> "β2-adrenoceptor"."""
    return html.unescape(re.sub(r"<[^>]+>", "", markup or "")).strip()


def GpcrListTableRows():
    """Rows for table_provider.GpcrListTable: one per human receptor, ordered by protein name.

    Read uncached from the classification tables, so a rebuild always reflects the current classification.
    """
    rows = classification_db.get_primary_classification_rows(use_cache=False)

    for row in sorted(rows, key=lambda r: _plain_text(r["protein_name"]).lower()):
        yield {
            "uniprot_entry_name": row["entry_name"],
            "protein_name": row["protein_name"],
            "protein_name_text": _plain_text(row["protein_name"]),
            "gene_name": row["gene"] or None,
            "receptor_class": row["class_family_name"] or None,
            "receptor_family": row["receptor_family"] or None,
            "modality": row["modality"] or None,
            "chemotype": row["chemotype"] or None,
            "sense": row["sense"] or None,
            "sequence": row["sequence"] or None,
        }
