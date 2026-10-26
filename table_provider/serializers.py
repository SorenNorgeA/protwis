from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from rest_framework import serializers

from common.models import WebResource, WebLink
from table_provider.models import GpcrStructureStatisticsTable, PdbTable

# Server-side filter datatype for each model field class; checked in order.
MODEL_FIELD_DATATYPES = (
    (models.BooleanField, 'boolean'),
    ((models.IntegerField, models.FloatField, models.DecimalField), 'numeric'),
    ((models.CharField, models.TextField), 'text'),
)


class DataTablesSerializerMixin:
    """Server-side column metadata for TableManager tables.

    Meta.model: the model the table is read from; filter datatypes are derived from its field types.
    Meta.datatypes: explicit datatypes, only for fields with no model column (e.g. checkbox).
    Meta.serializer_method_to_filter_field_map: database source for each SerializerMethodField.
    """

    @classmethod
    def source_mapping(cls):
        """Map each serializer field name to the database field used to filter and order it."""
        method_sources = getattr(cls.Meta, 'serializer_method_to_filter_field_map', {})
        mapping = {}
        for name, field in cls._declared_fields.items():
            if isinstance(field, serializers.SerializerMethodField):
                if name not in method_sources:
                    raise ImproperlyConfigured(
                        f"{cls.__name__}.{name}: SerializerMethodFields need a database source in "
                        "Meta.serializer_method_to_filter_field_map")
                mapping[name] = method_sources[name]
            else:
                mapping[name] = field.source or name
        return mapping

    @classmethod
    def column_datatypes(cls):
        """Map each serializer field name to its server-side filter datatype ('text', 'numeric', 'boolean', 'checkbox')."""
        explicit = getattr(cls.Meta, 'datatypes', {})
        result = {}
        for name, source in cls.source_mapping().items():
            if name in explicit:
                result[name] = explicit[name]
                continue
            try:
                model_field = cls.Meta.model._meta.get_field(source)
            except FieldDoesNotExist:
                raise ImproperlyConfigured(
                    f"{cls.__name__}.{name}: no field '{source}' on {cls.Meta.model.__name__}; declare its datatype in Meta.datatypes")
            for field_types, datatype in MODEL_FIELD_DATATYPES:
                if isinstance(model_field, field_types):
                    result[name] = datatype
                    break
            else:
                raise ImproperlyConfigured(
                    f"{cls.__name__}.{name}: unsupported model field type {type(model_field).__name__}; declare its datatype in Meta.datatypes")
        return result

    def serialize_column(self, name, instances):
        """Render a single column for many rows, as [{name: value}, ...], without serializing the other columns."""
        field = self.fields[name]
        return [{name: field.to_representation(instance)} for instance in instances]

    def _web_resource(self, slug):
        # Cached per serializer instance; with many=True one instance serialises every row
        cache = self.__dict__.setdefault('_web_resources', {})
        if slug not in cache:
            cache[slug] = WebResource.objects.get(slug=slug)
        return cache[slug]

    def weblink_anchor(self, content, index, slug):
        """Build the {anchor_content, anchor_href} object rendered by the client's weblinktext type."""
        if not content:
            return None
        href = str(WebLink(index=index, web_resource=self._web_resource(slug))) if index else None
        return {'anchor_content': content, 'anchor_href': href}


class GpcrStatisticsSummarySerializer(DataTablesSerializerMixin, serializers.Serializer):
    """Serializer to generate JSON for the structure statistics data for GPCRs stored in the gpcr_structure_statistics database table."""

    uniprot = serializers.SerializerMethodField()
    protein_name = serializers.CharField(read_only=True)
    receptor_family = serializers.ReadOnlyField()
    receptor_class = serializers.ReadOnlyField(read_only=True, )
    gene = serializers.SerializerMethodField()
    species_common_name = serializers.CharField(read_only=True)

    # Structure counts by state
    st_inact_c = serializers.ReadOnlyField(read_only=True)
    st_interm_c = serializers.ReadOnlyField(read_only=True)
    st_act_c = serializers.ReadOnlyField(read_only=True)

    # Best resolution by state (null when no structure of that state exists; the client chooses the placeholder via nullValuePlaceholder)
    best_res_inact = serializers.ReadOnlyField(read_only=True)
    best_res_interm = serializers.ReadOnlyField(read_only=True)
    best_res_act = serializers.ReadOnlyField(read_only=True)

    # Transducer / extra-protein counts (map to annotated names)
    transd_gio_c = serializers.ReadOnlyField(read_only=True)
    transd_gq11_c = serializers.ReadOnlyField(read_only=True)
    transd_g1213_c = serializers.ReadOnlyField(read_only=True)
    transd_arrest_c = serializers.ReadOnlyField(read_only=True)
    transd_grks_c = serializers.ReadOnlyField(read_only=True)

    # Ligand type counts
    ligand_types_smmol_c = serializers.ReadOnlyField(read_only=True)
    ligand_types_pep_c = serializers.ReadOnlyField(read_only=True)

    # Modality group counts (map to annotated names)
    limodgrp_stim_c = serializers.ReadOnlyField(read_only=True)
    limodgrp_inhib_c = serializers.ReadOnlyField(read_only=True)
    limodgrp_unk_c = serializers.ReadOnlyField(read_only=True)

    # Individual modality counts
    limod_apo_c = serializers.ReadOnlyField(read_only=True)
    limod_ag_c = serializers.ReadOnlyField(read_only=True)
    limod_ag_partial_c = serializers.ReadOnlyField(read_only=True)
    limod_allo_antag_c = serializers.ReadOnlyField(read_only=True)
    limod_antag_c = serializers.ReadOnlyField(read_only=True)
    limod_inv_ag_c = serializers.ReadOnlyField(read_only=True)
    limod_nam_c = serializers.ReadOnlyField(read_only=True)
    limod_pam_c = serializers.ReadOnlyField(read_only=True)
    limod_unk_c = serializers.ReadOnlyField(read_only=True)

    class Meta:
        model = GpcrStructureStatisticsTable
        serializer_method_to_filter_field_map = {
            'uniprot': 'uniprot_entry_name', 'gene': 'gene_name',
        }

    def get_uniprot(self, obj):
        return self.weblink_anchor(obj.uniprot_entry_name, obj.uniprot_accession, 'uniprot')

    def get_gene(self, obj):
        return self.weblink_anchor(obj.gene_name, obj.gene_entrez_id, 'entrez_gene')


class PdbStructureSummarySerializer(DataTablesSerializerMixin, serializers.Serializer):
    """Serializer to generate JSON for the PDB structure data stored in the table_pdb_data database table."""

    checkbox = serializers.SerializerMethodField()
    uniprot = serializers.SerializerMethodField()
    protein_name_short = serializers.CharField(read_only=True)
    receptor_family_short = serializers.ReadOnlyField()
    receptor_class_code = serializers.ReadOnlyField(read_only=True, )
    gene = serializers.SerializerMethodField()
    fraction_of_wt_seq = serializers.ReadOnlyField(read_only=True)
    closest_to_human = serializers.ReadOnlyField(read_only=True)
    species_common_name = serializers.CharField(read_only=True)
    identity_to_human = serializers.ReadOnlyField(read_only=True)

    structure_type = serializers.ReadOnlyField(read_only=True)
    pdb_id = serializers.SerializerMethodField()

    # Resolution (null when not available; the client chooses the placeholder via nullValuePlaceholder)
    resolution = serializers.ReadOnlyField(read_only=True)
    resolution_best = serializers.ReadOnlyField(read_only=True)
    state = serializers.ReadOnlyField(read_only=True)
    query_effector = serializers.ReadOnlyField(read_only=True)

    gprot_bound_likeness = serializers.ReadOnlyField(read_only=True)
    tm6_angle = serializers.ReadOnlyField(read_only=True)

    signal_protein = serializers.ReadOnlyField(read_only=True)
    signal_protein_subtype = serializers.ReadOnlyField(read_only=True)
    signal_protein_note = serializers.ReadOnlyField(read_only=True)
    signal_protein_pcntseq = serializers.ReadOnlyField(read_only=True)

    fusion = serializers.ReadOnlyField(read_only=True)
    antibody = serializers.ReadOnlyField(read_only=True)

    ligand = serializers.ReadOnlyField(read_only=True)
    ligand_role = serializers.ReadOnlyField(read_only=True)

    class Meta:
        model = PdbTable
        datatypes = {'checkbox': 'checkbox'}
        serializer_method_to_filter_field_map = {
           'checkbox': 'checkbox', 'uniprot': 'uniprot_entry_name', 'gene': 'gene_name', 'pdb_id': 'pdb_id',
        }

    def get_checkbox(self, obj):
        return {'row_id': obj.id, 'data': {'pdb_id': obj.pdb_id, 'protein_name_short': obj.protein_name_short}}

    def get_uniprot(self, obj):
        return self.weblink_anchor(obj.uniprot_entry_name, obj.uniprot_accession, 'uniprot')

    def get_gene(self, obj):
        return self.weblink_anchor(obj.gene_name, obj.gene_entrez_id, 'entrez_gene')

    def get_pdb_id(self, obj):
        return self.weblink_anchor(obj.pdb_id, obj.pdb_id, 'pdb')


# Serializer behind each table name used in the configuration files (<table_name>__<variant>.json)
TABLE_SERIALIZERS = {
    'pdb_data_table': PdbStructureSummarySerializer,
    'structure_statistics_table_gpcr': GpcrStatisticsSummarySerializer,
}
