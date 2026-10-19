"""
One-off export of the /ligand/physiological_ligands/ table (Endogenous_GTP) to Excel, with a
per-ligand modality/chemotype classification that is meant to replace the curated "Type" column.

The ligand classification uses the receptor classification vocabulary (ProteinFamilyClassification,
minus " receptors") and is decided once per ligand:
  - modality from the ligand itself: sequence/peptide backbone -> Polypeptide, single atom or ion
    name -> Ion, otherwise Small molecule;
  - Polypeptide chemotype from sequence length (<= PEPTIDE_MAX_LEN aa -> Peptide, else Protein);
  - Small molecule chemotype voted by its receptors with the same modality (one vote per receptor
    family / ortholog group, orphans never vote), falling back to RDKit structural rules.
The receptor classification only covers human receptors; rat/mouse receptors take it from their
human ortholog (GPCRdb puts orthologs in the same receptor-level ProteinFamily), or from their human
paralogs (parent receptor family) when no human ortholog exists.

Debug columns (far right) flag every disagreement with the receptor and with the curated Type.

    python manage.py export_physiological_ligands_classification              # xlsx + ai_input_NN.json
    python manage.py export_physiological_ligands_classification --merge-ai   # + ai_output_NN.json columns
"""
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from ligand.models import Endogenous_GTP, LigandID
from protein.models import Protein, ProteinFamily, ProteinFamilyClassification

from collections import Counter, OrderedDict, defaultdict
import glob
import html
import json
import logging
import os
import re

import xlsxwriter
from xlsxwriter.utility import xl_col_to_name
from rdkit import Chem, RDLogger
from rdkit.Chem.MolStandardize import rdMolStandardize

RDLogger.DisableLog('rdApp.*')

OUTPUT_DIR = os.path.join(os.path.abspath(settings.BASE_DIR), 'data', 'ligand_classification')
OUTPUT_XLSX = 'physiological_ligands_classification.xlsx'

PEPTIDE_MAX_LEN = 60    # <= 60 aa -> Peptide, else Protein (no ligand in the table is 61-67 aa)
LONG_CHAIN_N = 7        # acyclic carbon chain length that makes a small molecule a Lipid
ALICARB_MAX_C = 8       # max carbons of an Alicarboxylic acid (keeps 3-hydroxyoctanoic acid, HCAR3)
AMINO_ACID_MAX_HEAVY = 30
AI_BATCH_SIZE = 70
AI_SEQUENCE_PREVIEW = 120

ORPHAN = 'Orphan'
ION = 'Ion'
SMALL_MOLECULE = 'Small molecule'
POLYPEPTIDE = 'Polypeptide'

# Curated ligand Type (LigandType slug) -> the modality it is compatible with. There is no Ion type,
# so an ion typed small-molecule is reported separately (TYPE≠MOD:ION).
TYPE_MODALITY = {
    'small-molecule': SMALL_MOLECULE,
    'lipid': SMALL_MOLECULE,
    'peptide': POLYPEPTIDE,
    'protein': POLYPEPTIDE,
}

# flag code -> (needs review, description)
FLAGS = OrderedDict([
    ('MOD≠REC', (True, "A non-orphan receptor of this row has no classification with the ligand's modality")),
    ('CHEM≠REC', (True, "This row's receptor chemotype (matching modality) differs from the ligand chemotype")),
    ('TYPE≠MOD:ION', (True, "Classified Ion, but Type says Small molecule (Type has no Ion entry)")),
    ('TYPE≠MOD', (True, "Curated Type is incompatible with the ligand modality")),
    ('TYPE≠LIPID', (True, "Type is Lipid but the chemotype is not Lipid or Steroid")),
    ('TYPE≠LEN', (True, "Type is Protein but the sequence is <= %d aa" % PEPTIDE_MAX_LEN)),
    ('NO_STRUCT', (True, "No sequence and no SMILES; modality/chemotype taken from the receptors")),
    ('UNRESOLVED', (True, "No chemotype from receptors or structure")),
    ('RDKIT≠CHEM', (True, "RDKit structural chemotype disagrees with the receptor vote")),
    ('RDKIT_MISS', (True, "Small molecule with a receptor vote but no RDKit rule hit")),
    ('TIE', (True, "Receptor vote tied")),
    ('REC_UNCLASSIFIED', (True, "Receptor has no classification (no human ortholog, paralogs disagree)")),
    ('AI≠PROPOSED', (True, "AI suggestion differs from the proposed ligand classification")),
    ('AI_INVALID', (True, "AI answer is not in the vocabulary")),
    ('AI_MISSING', (True, "No AI answer for this ligand")),
    ('RDKIT_ONLY', (False, "Chemotype from the RDKit structure rule only (no receptor vote)")),
    ('ORPHAN_ONLY', (False, "Every receptor of this ligand is an orphan")),
    ('ORPHAN_REC', (False, "This row's receptor is an orphan")),
    ('MULTI_TAG', (False, "Matched on the receptor's secondary (order 2) classification")),
    ('REC_NO_HUMAN_ORTHOLOG', (False, "Rat/mouse receptor without a human ortholog; classification from human paralogs")),
    ('TYPE≠CHEM', (False, "Type is Peptide but the chemotype is Protein (> %d aa; GtP types chemokines as Peptide)" % PEPTIDE_MAX_LEN)),
])
WEAK_SOURCE_FLAGS = {'RDKIT_ONLY', 'NO_STRUCT', 'ORPHAN_ONLY', 'UNRESOLVED'}

TAG_RE = re.compile(r'<[^>]+>')
ION_NAME_RE = re.compile(r'^[A-Z][a-z]?\d*[+-]$')   # clean name without spaces: Ca2+, Zn2+, H+, Cl-
RECEPTORS_SUFFIX_RE = re.compile(r'\s*receptors?$', re.IGNORECASE)

# ---- RDKit structural rules (validated against the receptor vote: 91/91 small molecules agree) ----
_S = Chem.MolFromSmarts
_ACID = '[$([CX3](=O)[OX2H1,OX1-]),$([SX4](=O)(=O)[OX2H1,OX1-]),$([PX4](=O)([OX2H1,OX1-])[OX2H1,OX1-])]'
_FREE_N = '[#7;X3,X4+;!a;!$(N[#6,#16,#15]=[O,N,S]);!$(N-a)]'   # basic N: not amide/amidine/sulfonamide/aniline
SMARTS = {
    'peptide_backbone': _S('[NX3][CX4][CX3](=O)[NX3][CX4][CX3](=O)[NX3][CX4][CX3](=O)'),   # >= 3 alpha residues
    'steroid': _S('[#6]1~[#6]~[#6]~[#6]2~[#6](~[#6]~1)~[#6]~[#6]~[#6]1~[#6]~2~[#6]~[#6]~[#6]2~[#6]~[#6]~[#6]~[#6]~1~2'),
    'nucleoside': _S('[#7;a]-[#6;!a]1~[#8;!a]~[#6;!a]~[#6;!a]~[#6;!a]~1'),   # nucleobase N on a furanose (also cAMP)
    'retinoid': _S('[CH3][#6]1=[#6](-[#6]=[#6])[#6]([CH3])([CH3])[CH2][CH2][CH2]1'),
    'carboxyl': _S('[CX3](=O)[OX2H1,OX1-]'),
    'long_chain': _S('~'.join(['[#6;!R;!a]'] * LONG_CHAIN_N)),
    'glycerophosphate': _S('[#8][CH2][CH1]([#8])[CH2][#8]P(~[#8])(~[#8])~[#8]'),   # also catches truncated LPI/PS SMILES
    'n_acyl_tryptamine': _S('[#6](=O)[NX3][CH2][CH2]c1c[nH]c2ccccc12'),
    'aa_alpha': _S('[#7;!a;X3,X4+][CX4]' + _ACID),   # N may be acylated (NAAG)
    'aa_beta': _S(_FREE_N + '[CX4][CX4]' + _ACID),
    'aa_gamma': _S(_FREE_N + '[CX4][CX4][CX4]' + _ACID),
    'aryl_ethylamine': _S(_FREE_N + '[CX4][CX4]a'),
    'choline': _S('[NX4+]([CH3])([CH3])([CH3])[CH2][CH2][OX2]'),
    'free_amine': _S(_FREE_N),
}
_LARGEST_FRAGMENT = rdMolStandardize.LargestFragmentChooser()

RDKIT_RULES_DOC = [
    ('Ion', 'single heavy atom'),
    ('Steroid', 'steroid tetracyclic nucleus (before Lipid: oxysterol side chains)'),
    ('Nucleotide', 'nucleobase N-glycoside on a furanose (nucleotides, nucleosides, cAMP)'),
    ('Retinal', 'beta-ionone ring with polyene (before Lipid)'),
    ('Alicarboxylic acid', 'C/O only, acyclic, COOH, <= %d C, > 2 O (hydroxy/oxo/di-acids)' % ALICARB_MAX_C),
    ('Lipid', '>= %d-carbon acyclic chain, glycerophosphate core, or simple C/O mono-acid' % LONG_CHAIN_N),
    ('Melatonin', 'N-acyl tryptamine'),
    ('Amino acid', 'alpha/beta/gamma amino acid (carboxylic/sulfonic/phosphonic), <= %d heavy atoms' % AMINO_ACID_MAX_HEAVY),
    ('Aminergic', 'aryl-ethylamine, choline ester, or small C/N-only amine'),
]


def clean_text(text):
    return html.unescape(TAG_RE.sub('', text or '')).strip()


def strip_receptors(name):
    return RECEPTORS_SUFFIX_RE.sub('', name or '').strip()


def structural_modality(name, sequence, smiles):
    """(modality or None, rdkit mol or None, basis text) from the ligand itself."""
    if sequence:
        return POLYPEPTIDE, None, 'sequence %d aa' % len(sequence)
    mol = Chem.MolFromSmiles(smiles) if smiles else None
    if mol is not None:
        mol = _LARGEST_FRAGMENT.choose(mol)
        if mol.HasSubstructMatch(SMARTS['peptide_backbone']):
            return POLYPEPTIDE, mol, 'SMILES (peptide backbone)'
        if mol.GetNumHeavyAtoms() <= 1:
            return ION, mol, 'SMILES (single atom)'
        return SMALL_MOLECULE, mol, 'SMILES'
    if ION_NAME_RE.match(name.replace(' ', '')):
        return ION, None, 'ion name'
    return None, None, 'none'


def rdkit_chemotype(mol):
    """Ordered structural rules for small molecules, first match wins; None = no rule hit."""
    def has(key):
        return mol.HasSubstructMatch(SMARTS[key])
    elements = Counter(atom.GetSymbol() for atom in mol.GetAtoms())
    heavy = mol.GetNumHeavyAtoms()
    acyclic = mol.GetRingInfo().NumRings() == 0
    n_cooh = len(mol.GetSubstructMatches(SMARTS['carboxyl']))
    c_o_only = set(elements) <= {'C', 'O'}
    if heavy <= 1:
        return ION
    if has('steroid'):
        return 'Steroid'
    if has('nucleoside'):
        return 'Nucleotide'
    if has('retinoid'):
        return 'Retinal'
    if c_o_only and acyclic and n_cooh >= 1 and elements['C'] <= ALICARB_MAX_C and elements['O'] > 2:
        return 'Alicarboxylic acid'
    if has('long_chain') or has('glycerophosphate') or (c_o_only and acyclic and n_cooh == 1 and elements['O'] == 2):
        return 'Lipid'
    if has('n_acyl_tryptamine'):
        return 'Melatonin'
    if heavy <= AMINO_ACID_MAX_HEAVY and (has('aa_alpha') or has('aa_beta') or has('aa_gamma')):
        return 'Amino acid'
    if has('aryl_ethylamine') or has('choline') or (set(elements) <= {'C', 'N'} and heavy <= 20 and has('free_amine')):
        return 'Aminergic'
    return None


def format_tags(tags):
    if len(tags) == 1:
        return tags[0]['chemotype']
    return '; '.join('%s (%s)' % (t['chemotype'], t['chemotype_order'] or '-') for t in tags)


def format_votes(votes):
    return ', '.join('%s %d' % (chem, n) for chem, n in votes.most_common())


class Command(BaseCommand):

    help = "Export /ligand/physiological_ligands/ to Excel with a per-ligand modality/chemotype classification"

    logger = logging.getLogger(__name__)

    def add_arguments(self, parser):
        parser.add_argument('--output-dir', default=OUTPUT_DIR,
                            help='Folder for the xlsx and the AI json batches (default: %(default)s)')
        parser.add_argument('--merge-ai', action='store_true',
                            help='Merge ai_output_NN.json files from the output folder into the AI columns')

    def handle(self, *args, **options):
        self.output_dir = options['output_dir']
        os.makedirs(self.output_dir, exist_ok=True)

        self.load_vocabulary()
        self.load_pairs()
        self.load_receptor_classification()
        self.classify_ligands()
        self.flag_rows()
        self.write_ai_batches()
        self.ai_stats = self.merge_ai() if options['merge_ai'] else None
        path = os.path.join(self.output_dir, OUTPUT_XLSX)
        try:
            self.save_excel(path)
        except (IOError, OSError) as msg:
            raise CommandError("Could not write %s (is it open in Excel?): %s" % (path, msg))

        review_rows = sum(1 for row in self.rows if row['review'])
        self.stdout.write("Wrote %s: %d rows, %d ligands, %d rows for review" % (
            path, len(self.rows), len(self.ligands), review_rows))
        if self.ai_stats:
            self.stdout.write("AI merge: %(merged)d/%(total)d ligands merged, %(missing)d missing, "
                              "%(invalid)d invalid, %(disagree)d disagree" % self.ai_stats)

    # ------------------------------------------------------------------ data

    def load_vocabulary(self):
        """Ligand vocabulary = receptor classification terms minus ' receptors', Orphan excluded."""
        self.chemotype_modality = {}
        for chem, mod in ProteinFamilyClassification.objects.values_list('chemotype__name', 'modality__name').distinct():
            if chem and mod and strip_receptors(mod) != ORPHAN:
                self.chemotype_modality[strip_receptors(chem)] = strip_receptors(mod)
        self.modality_chemotypes = defaultdict(list)
        for chem in sorted(self.chemotype_modality):
            self.modality_chemotypes[self.chemotype_modality[chem]].append(chem)

    def load_pairs(self):
        """Same rows as PhysiologicalLigands: one per (ligand, receptor), GtP ligands only."""
        gtp_ids = dict(LigandID.objects.filter(web_resource__slug='gtoplig').values_list('ligand', 'index'))
        fields = ['ligand', 'receptor', 'receptor__family_id',
                  'receptor__family__parent__parent__parent__name', 'receptor__family__parent__name',
                  'receptor__entry_name', 'receptor__name', 'receptor__species__common_name',
                  'ligand__name', 'ligand__ligand_type__slug', 'ligand__ligand_type__name',
                  'ligand__smiles', 'ligand__inchikey', 'ligand__sequence',
                  'endogenous_status', 'potency_ranking']
        pairs = OrderedDict()
        for values in Endogenous_GTP.objects.values_list(*fields).distinct():
            data = dict(zip(fields, values))
            if data['ligand'] in gtp_ids:
                pairs.setdefault((data['ligand'], data['receptor']), data)

        receptor_ids = {data['receptor'] for data in pairs.values()}
        genes = {}
        for protein_id, gene in (Protein.objects.filter(id__in=receptor_ids)
                                 .order_by('id', 'genes__position').values_list('id', 'genes__name')):
            genes.setdefault(protein_id, gene or '')

        self.ligands = OrderedDict()
        self.receptors = {}
        self.rows = []
        for (ligand_id, receptor_id), data in pairs.items():
            if receptor_id not in self.receptors:
                self.receptors[receptor_id] = {
                    'id': receptor_id,
                    'family_id': data['receptor__family_id'],
                    'entry_name': data['receptor__entry_name'],
                    'species': data['receptor__species__common_name'],
                    'class': clean_text(data['receptor__family__parent__parent__parent__name']).replace('Class ', ''),
                    'receptor_family': strip_receptors(clean_text(data['receptor__family__parent__name'])),
                    'uniprot': data['receptor__entry_name'].split('_')[0].upper(),
                    'iuphar': clean_text(data['receptor__name']).replace(' receptor', '').replace('-adrenoceptor', ''),
                    'gene': genes.get(receptor_id, ''),
                }
            if ligand_id not in self.ligands:
                type_name = data['ligand__ligand_type__name'] or ''
                smiles = (data['ligand__smiles'] or '').strip()
                self.ligands[ligand_id] = {
                    'id': ligand_id,
                    'gtp_id': gtp_ids[ligand_id],
                    'name': clean_text(data['ligand__name']),
                    'type_slug': data['ligand__ligand_type__slug'] or '',
                    'type': type_name.replace('-', ' ').capitalize(),
                    'smiles': '' if smiles == '-' else smiles,
                    'inchikey': data['ligand__inchikey'] or '',
                    'sequence': (data['ligand__sequence'] or '').strip(),
                    'receptor_ids': [],
                }
            self.ligands[ligand_id]['receptor_ids'].append(receptor_id)
            potency = data['potency_ranking']
            self.rows.append({
                'ligand_id': ligand_id,
                'receptor_id': receptor_id,
                'status': data['endogenous_status'] or '',
                'potency': int(potency) if potency is not None and float(potency).is_integer() else potency,
            })
        # page order: UniProt, then species, then ligand name
        self.rows.sort(key=lambda r: (self.receptors[r['receptor_id']]['uniprot'],
                                      self.receptors[r['receptor_id']]['species'],
                                      self.ligands[r['ligand_id']]['name'].lower()))

    def load_receptor_classification(self):
        """Human-only classification -> every receptor in the table, via human ortholog or paralogs."""
        tags_by_family = defaultdict(list)
        for family_id, chem, chem_order, mod, mod_order in ProteinFamilyClassification.objects.values_list(
                'protein_family_id', 'chemotype__name', 'chemotype_order', 'modality__name', 'modality_order'):
            if chem and mod:
                tags_by_family[family_id].append({
                    'chemotype': strip_receptors(chem), 'chemotype_order': chem_order,
                    'modality': strip_receptors(mod), 'modality_order': mod_order,
                })
        for tags in tags_by_family.values():
            tags.sort(key=lambda t: (t['chemotype_order'] or 9, t['modality_order'] or 9, t['chemotype']))

        human_by_family = defaultdict(list)
        for family_id, entry_name in (Protein.objects.filter(species__common_name='Human', source__name='SWISSPROT')
                                      .order_by('entry_name').values_list('family_id', 'entry_name')):
            human_by_family[family_id].append(entry_name)

        families = {f.id: f for f in ProteinFamily.objects.filter(
            id__in={r['family_id'] for r in self.receptors.values()}).select_related('parent')}

        for receptor in self.receptors.values():
            family_id = receptor['family_id']
            receptor['flags'] = set()
            receptor['tags'] = tags_by_family.get(family_id, [])
            if receptor['species'] == 'Human':
                receptor['via'] = 'self'
            elif human_by_family.get(family_id):
                receptor['via'] = 'human ortholog %s' % human_by_family[family_id][0].upper()
            else:
                receptor['via'] = 'family (no human protein)'
            if not receptor['tags']:
                # no human ortholog: use the human paralogs (sibling receptor families) when they all agree
                parent = families[family_id].parent
                sibling_tags = [tags_by_family[f_id][0] for f_id in
                                ProteinFamily.objects.filter(parent=parent).values_list('id', flat=True)
                                if tags_by_family.get(f_id)]
                primary = {(t['chemotype'], t['modality']) for t in sibling_tags}
                if len(primary) == 1:
                    receptor['tags'] = [dict(sibling_tags[0], chemotype_order=1, modality_order=1)]
                    receptor['via'] = 'human paralogs (%s)' % clean_text(parent.name)
                    receptor['flags'].add('REC_NO_HUMAN_ORTHOLOG')
                else:
                    receptor['via'] = 'none'
                    receptor['flags'].add('REC_UNCLASSIFIED')
            receptor['orphan'] = bool(receptor['tags']) and receptor['tags'][0]['modality'] == ORPHAN

    # ------------------------------------------------------------ classify

    def matching_tag(self, receptor, modality):
        """The receptor's best non-orphan tag with the given modality (order 1 preferred)."""
        for tag in receptor['tags']:
            if tag['modality'] == modality and tag['modality'] != ORPHAN:
                return tag
        return None

    def receptor_votes(self, ligand, modality):
        """One vote per receptor family (= ortholog group); orphans and other modalities never vote."""
        by_family = {}
        for receptor_id in ligand['receptor_ids']:
            receptor = self.receptors[receptor_id]
            tag = self.matching_tag(receptor, modality)
            if tag:
                by_family.setdefault(receptor['family_id'], tag['chemotype'])
        return Counter(by_family.values())

    def classify_ligands(self):
        for ligand in self.ligands.values():
            flags = set()
            modality, mol, basis = structural_modality(ligand['name'], ligand['sequence'], ligand['smiles'])
            rdkit_chem = rdkit_chemotype(mol) if mol is not None and modality in (SMALL_MOLECULE, ION) else None

            if modality is None:
                flags.add('NO_STRUCT')
                modality_votes = Counter()
                for family_id, tag in {self.receptors[r]['family_id']: self.receptors[r]['tags'][0]
                                       for r in ligand['receptor_ids']
                                       if self.receptors[r]['tags'] and not self.receptors[r]['orphan']}.items():
                    modality_votes[tag['modality']] += 1
                modality = modality_votes.most_common(1)[0][0] if modality_votes else ''

            votes = self.receptor_votes(ligand, modality) if modality else Counter()
            chemotype, source = '', 'unresolved'
            if modality == ION:
                chemotype, source = ION, 'ion (structure)'
            elif modality == POLYPEPTIDE and ligand['sequence']:
                length = len(ligand['sequence'])
                chemotype = 'Peptide' if length <= PEPTIDE_MAX_LEN else 'Protein'
                source = 'length (%d aa)' % length
            elif votes:
                top = votes.most_common(1)[0][1]
                tied = sorted(chem for chem, n in votes.items() if n == top)
                if len(tied) > 1:
                    flags.add('TIE')
                    chemotype = rdkit_chem if rdkit_chem in tied else tied[0]
                else:
                    chemotype = tied[0]
                source = 'receptor vote %d/%d' % (top, sum(votes.values()))
                if modality == SMALL_MOLECULE and 'NO_STRUCT' not in flags:
                    if rdkit_chem is None:
                        flags.add('RDKIT_MISS')
                    elif rdkit_chem != chemotype:
                        flags.add('RDKIT≠CHEM')
            elif modality == SMALL_MOLECULE and rdkit_chem:
                chemotype, source = rdkit_chem, 'RDKit'
                flags.add('RDKIT_ONLY')
            elif modality == POLYPEPTIDE and basis.startswith('SMILES'):
                chemotype, source = 'Peptide', 'SMILES peptide backbone'
            if not chemotype:
                flags.add('UNRESOLVED')

            if all(self.receptors[r]['orphan'] for r in ligand['receptor_ids']):
                flags.add('ORPHAN_ONLY')

            # curated Type vs new classification
            type_slug = ligand['type_slug']
            if modality == ION and type_slug == 'small-molecule':
                flags.add('TYPE≠MOD:ION')
            elif type_slug in TYPE_MODALITY and modality and modality != TYPE_MODALITY[type_slug]:
                flags.add('TYPE≠MOD')
            if type_slug == 'lipid' and modality == SMALL_MOLECULE and chemotype not in ('Lipid', 'Steroid'):
                flags.add('TYPE≠LIPID')
            if type_slug == 'protein' and ligand['sequence'] and chemotype == 'Peptide':
                flags.add('TYPE≠LEN')
            if type_slug == 'peptide' and chemotype == 'Protein':
                flags.add('TYPE≠CHEM')

            ligand.update({
                'modality': modality, 'chemotype': chemotype, 'basis': basis,
                'rdkit_chemotype': rdkit_chem or '', 'source': source,
                'votes': format_votes(votes), 'flags': flags,
                'ai': None,
            })

    def flag_rows(self):
        for row in self.rows:
            ligand = self.ligands[row['ligand_id']]
            receptor = self.receptors[row['receptor_id']]
            flags = set(ligand['flags']) | receptor['flags']
            if receptor['orphan']:
                flags.add('ORPHAN_REC')
            elif receptor['tags'] and ligand['modality']:
                tag = self.matching_tag(receptor, ligand['modality'])
                if tag is None:
                    flags.add('MOD≠REC')
                else:
                    if tag is not receptor['tags'][0]:
                        flags.add('MULTI_TAG')
                    if ligand['chemotype'] and tag['chemotype'] != ligand['chemotype']:
                        flags.add('CHEM≠REC')
            row['flags'] = flags
            self.update_review(row)

    @staticmethod
    def update_review(item):
        item['review'] = any(FLAGS[flag][0] for flag in item['flags'])

    # ------------------------------------------------------------------ AI

    def ai_vocabulary(self):
        return OrderedDict((modality, self.modality_chemotypes[modality])
                           for modality in (SMALL_MOLECULE, POLYPEPTIDE, ION) if modality in self.modality_chemotypes)

    def write_ai_batches(self):
        """Distinct ligands for the AI pass. The deterministic proposal is left out on purpose."""
        for path in glob.glob(os.path.join(self.output_dir, 'ai_input_*.json')):
            os.remove(path)
        records = []
        for ligand in self.ligands.values():
            receptors = []
            for receptor_id in ligand['receptor_ids']:
                receptor = self.receptors[receptor_id]
                receptors.append({
                    'receptor': '%s (%s, %s)' % (receptor['iuphar'], receptor['gene'], receptor['species']),
                    'receptor_classification': '; '.join('%s / %s' % (t['modality'], t['chemotype'])
                                                         for t in receptor['tags']),
                })
            sequence = ligand['sequence']
            records.append(OrderedDict([
                ('ligand_id', ligand['id']),
                ('gtp_id', ligand['gtp_id']),
                ('name', ligand['name']),
                ('curated_type', ligand['type']),
                ('sequence_length', len(sequence)),
                ('sequence', sequence[:AI_SEQUENCE_PREVIEW] + ('...' if len(sequence) > AI_SEQUENCE_PREVIEW else '')),
                ('smiles', ligand['smiles']),
                ('receptors', receptors),
            ]))
        vocabulary = self.ai_vocabulary()
        for start in range(0, len(records), AI_BATCH_SIZE):
            batch = records[start:start + AI_BATCH_SIZE]
            path = os.path.join(self.output_dir, 'ai_input_%02d.json' % (start // AI_BATCH_SIZE + 1))
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump(OrderedDict([('vocabulary', vocabulary), ('ligands', batch)]),
                          handle, ensure_ascii=False, indent=1)

    def merge_ai(self):
        answers = {}
        for path in sorted(glob.glob(os.path.join(self.output_dir, 'ai_output_*.json'))):
            with open(path, encoding='utf-8') as handle:
                for record in json.load(handle):
                    answers[int(record['ligand_id'])] = record
        if not answers:
            raise CommandError("--merge-ai: no ai_output_*.json files in %s" % self.output_dir)

        stats = Counter(total=len(self.ligands))
        for ligand in self.ligands.values():
            answer = answers.get(ligand['id'])
            if answer is None:
                ligand['flags'].add('AI_MISSING')
                stats['missing'] += 1
                continue
            modality = (answer.get('ai_modality') or '').strip()
            chemotype = (answer.get('ai_chemotype') or '').strip()
            agrees = modality == ligand['modality'] and chemotype == ligand['chemotype']
            ligand['ai'] = {
                'modality': modality, 'chemotype': chemotype,
                'confidence': (answer.get('confidence') or '').strip(),
                'agrees': 'Y' if agrees else 'N',
                'reasoning': (answer.get('reasoning') or '').strip(),
            }
            stats['merged'] += 1
            if self.chemotype_modality.get(chemotype) != modality:
                ligand['flags'].add('AI_INVALID')
                stats['invalid'] += 1
            if not agrees:
                ligand['flags'].add('AI≠PROPOSED')
                stats['disagree'] += 1
            else:
                stats['agree'] += 1
        unknown = set(answers) - set(self.ligands)
        if unknown:
            self.stdout.write("AI answers for ligand ids not in the table (ignored): %s" % sorted(unknown))

        ai_flags = {'AI_MISSING', 'AI_INVALID', 'AI≠PROPOSED'}
        for row in self.rows:
            row['flags'] |= self.ligands[row['ligand_id']]['flags'] & ai_flags
            self.update_review(row)
        return stats

    def ai_values(self, ligand):
        ai = ligand['ai']
        if not ai:
            return ['', '', '', '', '']
        show_reasoning = ai['agrees'] == 'N' or bool(ligand['flags'] & WEAK_SOURCE_FLAGS)
        return [ai['modality'], ai['chemotype'], ai['confidence'], ai['agrees'],
                ai['reasoning'] if show_reasoning else '']

    # --------------------------------------------------------------- excel

    def save_excel(self, path):
        workbook = xlsxwriter.Workbook(path, {'strings_to_numbers': False, 'strings_to_urls': False,
                                              'strings_to_formulas': False})
        self.formats = {
            'title': workbook.add_format({'bold': True, 'font_size': 13}),
            'bold': workbook.add_format({'bold': True}),
            'header': workbook.add_format({'bold': True, 'text_wrap': True, 'valign': 'top',
                                           'bottom': 1, 'bg_color': '#F2F2F2'}),
            'review': workbook.add_format({'bg_color': '#FADBD8'}),
            'new': workbook.add_format({'bold': True, 'bg_color': '#E8F1FB'}),
        }
        self.section_formats = {
            'RECEPTOR': workbook.add_format({'bold': True, 'align': 'center', 'bg_color': '#D9D9D9', 'border': 1}),
            'PHYSIOLOGICAL LIGANDS': workbook.add_format({'bold': True, 'align': 'center', 'bg_color': '#C5D9F1', 'border': 1}),
            'CLASSIFICATION DEBUG': workbook.add_format({'bold': True, 'align': 'center', 'bg_color': '#FCE4D6', 'border': 1}),
            'AI SUGGESTION': workbook.add_format({'bold': True, 'align': 'center', 'bg_color': '#E2EFDA', 'border': 1}),
        }
        self.write_rows_sheet(workbook)
        self.write_ligands_sheet(workbook)
        self.write_summary_sheet(workbook)
        self.write_rules_sheet(workbook)
        workbook.close()

    def write_cell(self, sheet, row, col, value, cell_format=None):
        if value is None or value == '':
            sheet.write_blank(row, col, None, cell_format)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            sheet.write_number(row, col, value, cell_format)
        else:
            sheet.write_string(row, col, str(value), cell_format)

    def write_table(self, sheet, sections, records, new_columns=()):
        """sections: [(section title, [(header, width), ...]), ...]; records: [(values, review), ...]"""
        col = 0
        headers = []
        for title, columns in sections:
            if len(columns) > 1:
                sheet.merge_range(0, col, 0, col + len(columns) - 1, title, self.section_formats[title])
            else:
                sheet.write_string(0, col, title, self.section_formats[title])
            for header, width in columns:
                sheet.set_column(col, col, width)
                headers.append(header)
                col += 1
        for i, header in enumerate(headers):
            sheet.write_string(1, i, header, self.formats['header'])
        new_columns = {headers.index(h) for h in new_columns}
        for r, values in enumerate(records, start=2):
            for c, value in enumerate(values):
                self.write_cell(sheet, r, c, value, self.formats['new'] if c in new_columns else None)
        last_row = max(len(records) + 1, 2)
        review_col = xl_col_to_name(headers.index('Review'))
        sheet.conditional_format(2, 0, last_row, len(headers) - 1, {
            'type': 'formula', 'criteria': '=$%s3="Y"' % review_col, 'format': self.formats['review']})
        sheet.autofilter(1, 0, last_row, len(headers) - 1)
        sheet.set_row(1, 30)

    def debug_columns(self):
        return [('Structure basis', 18), ('RDKit chemotype', 16), ('Chemotype source', 18),
                ('Receptor votes', 22), ('Flags', 34), ('Review', 8)]

    def ai_columns(self):
        return [('AI modality', 14), ('AI chemotype', 16), ('AI confidence', 12),
                ('AI agrees', 9), ('AI reasoning', 60)]

    def write_rows_sheet(self, workbook):
        sheet = workbook.add_worksheet('Physiological ligands')
        sections = [
            ('RECEPTOR', [('Class', 8), ('Receptor family', 22), ('UniProt', 10), ('IUPHAR', 18),
                          ('Gene', 10), ('Species', 9)]),
            ('PHYSIOLOGICAL LIGANDS', [('Ligand', 30), ('GPCRdb ID', 10), ('GtP ID', 8), ('GtP classification', 12),
                                       ('Potency ranking', 9), ('Ligand modality', 15), ('Ligand chemotype', 18),
                                       ('Type', 14), ('Sequence', 22), ('SMILES', 22), ('InChIKey', 16)]),
            ('CLASSIFICATION DEBUG', [('Classified via', 26), ('Receptor modality', 16), ('Receptor chemotype', 22)]
             + self.debug_columns()),
            ('AI SUGGESTION', self.ai_columns()),
        ]
        records = []
        for row in self.rows:
            ligand = self.ligands[row['ligand_id']]
            receptor = self.receptors[row['receptor_id']]
            receptor_modalities = OrderedDict((t['modality'], None) for t in receptor['tags'])
            records.append([
                receptor['class'], receptor['receptor_family'], receptor['uniprot'], receptor['iuphar'],
                receptor['gene'], receptor['species'],
                ligand['name'], ligand['id'], ligand['gtp_id'], row['status'], row['potency'],
                ligand['modality'], ligand['chemotype'], ligand['type'],
                ligand['sequence'], ligand['smiles'], ligand['inchikey'],
                receptor['via'], '; '.join(receptor_modalities), format_tags(receptor['tags']) if receptor['tags'] else '',
                ligand['basis'], ligand['rdkit_chemotype'], ligand['source'], ligand['votes'],
                ', '.join(f for f in FLAGS if f in row['flags']), 'Y' if row['review'] else 'N',
            ] + self.ai_values(ligand))
        self.write_table(sheet, sections, records, new_columns=('Ligand modality', 'Ligand chemotype'))
        sheet.freeze_panes(2, 3)

    def write_ligands_sheet(self, workbook):
        sheet = workbook.add_worksheet('Ligands')
        sections = [
            ('PHYSIOLOGICAL LIGANDS', [('Ligand', 30), ('GPCRdb ID', 10), ('GtP ID', 8), ('Ligand modality', 15),
                                       ('Ligand chemotype', 18), ('Type', 14), ('Sequence length', 9),
                                       ('Sequence', 22), ('SMILES', 22), ('InChIKey', 16), ('Receptors', 40)]),
            ('CLASSIFICATION DEBUG', self.debug_columns()),
            ('AI SUGGESTION', self.ai_columns()),
        ]
        row_flags = defaultdict(set)
        for row in self.rows:
            row_flags[row['ligand_id']] |= row['flags']
        records = []
        for ligand in sorted(self.ligands.values(), key=lambda l: l['name'].lower()):
            flags = row_flags[ligand['id']] | ligand['flags']
            receptors = sorted({'%s (%s)' % (self.receptors[r]['uniprot'], self.receptors[r]['species'])
                                for r in ligand['receptor_ids']})
            records.append([
                ligand['name'], ligand['id'], ligand['gtp_id'], ligand['modality'], ligand['chemotype'],
                ligand['type'], len(ligand['sequence']) or '', ligand['sequence'], ligand['smiles'],
                ligand['inchikey'], ', '.join(receptors),
                ligand['basis'], ligand['rdkit_chemotype'], ligand['source'], ligand['votes'],
                ', '.join(f for f in FLAGS if f in flags), 'Y' if any(FLAGS[f][0] for f in flags) else 'N',
            ] + self.ai_values(ligand))
        self.write_table(sheet, sections, records, new_columns=('Ligand modality', 'Ligand chemotype'))
        sheet.freeze_panes(2, 1)

    def write_summary_sheet(self, workbook):
        sheet = workbook.add_worksheet('Summary')
        bold, title = self.formats['bold'], self.formats['title']
        sheet.set_column(0, 0, 30)
        sheet.set_column(1, 3, 12)
        sheet.set_column(4, 4, 90)
        r = 0
        sheet.write_string(r, 0, 'Physiological ligands - ligand classification', title)
        r += 2
        species = Counter(self.receptors[row['receptor_id']]['species'] for row in self.rows)
        via = Counter(re.sub(r' \(.*\)$| [A-Z0-9_]+$', '', rec['via']) for rec in self.receptors.values())
        totals = [
            ('Rows (ligand-receptor pairs)', len(self.rows)),
            ('Distinct ligands', len(self.ligands)),
            ('Distinct receptors', len(self.receptors)),
            ('Rows needing review', sum(1 for row in self.rows if row['review'])),
        ] + [('Rows - %s' % sp, n) for sp, n in species.most_common()] \
          + [('Receptors classified via %s' % v, n) for v, n in via.most_common()]
        for label, value in totals:
            sheet.write_string(r, 0, label)
            sheet.write_number(r, 1, value)
            r += 1
        if self.ai_stats:
            r += 1
            sheet.write_string(r, 0, 'AI suggestion', bold)
            r += 1
            for key in ('total', 'merged', 'missing', 'invalid', 'agree', 'disagree'):
                sheet.write_string(r, 0, 'Ligands - AI %s' % key)
                sheet.write_number(r, 1, self.ai_stats[key])
                r += 1

        r += 1
        for c, header in enumerate(['Flag', 'Ligands', 'Rows', 'Review', 'Meaning']):
            sheet.write_string(r, c, header, self.formats['header'])
        r += 1
        for flag, (review, description) in FLAGS.items():
            ligands = {row['ligand_id'] for row in self.rows if flag in row['flags']}
            rows = sum(1 for row in self.rows if flag in row['flags'])
            sheet.write_string(r, 0, flag)
            sheet.write_number(r, 1, len(ligands))
            sheet.write_number(r, 2, rows)
            sheet.write_string(r, 3, 'Y' if review else 'N (info)')
            sheet.write_string(r, 4, description)
            r += 1

        r += 1
        for c, header in enumerate(['Ligand modality / chemotype', 'Ligands', 'Rows']):
            sheet.write_string(r, c, header, self.formats['header'])
        r += 1
        ligand_counts = Counter((l['modality'] or '-', l['chemotype'] or '(unresolved)') for l in self.ligands.values())
        row_counts = Counter((self.ligands[row['ligand_id']]['modality'] or '-',
                              self.ligands[row['ligand_id']]['chemotype'] or '(unresolved)') for row in self.rows)
        for (modality, chemotype), n in sorted(ligand_counts.items(), key=lambda x: (x[0][0], -x[1])):
            sheet.write_string(r, 0, '%s / %s' % (modality, chemotype))
            sheet.write_number(r, 1, n)
            sheet.write_number(r, 2, row_counts[(modality, chemotype)])
            r += 1

    def write_rules_sheet(self, workbook):
        sheet = workbook.add_worksheet('Rules')
        bold, title = self.formats['bold'], self.formats['title']
        sheet.set_column(0, 0, 28)
        sheet.set_column(1, 1, 100)
        lines = [
            ('Ligand classification rules', None, title),
            ('', None, None),
            ('1. Modality (from the ligand itself)', None, bold),
            ('Polypeptide', 'Has a sequence, or SMILES with a >= 3-residue peptide backbone', None),
            ('Ion', 'Single heavy atom, or no SMILES and a name like Ca2+ / Zn2+ / H+', None),
            ('Small molecule', 'Any other parseable SMILES (largest fragment)', None),
            ('(no structure)', 'Modality = majority of the non-orphan receptors; flagged NO_STRUCT', None),
            ('', None, None),
            ('2. Chemotype', None, bold),
            ('Ion', 'Always Ion', None),
            ('Polypeptide', 'Peptide if sequence <= %d aa, else Protein (receptor vote when there is no sequence)' % PEPTIDE_MAX_LEN, None),
            ('Small molecule', 'Majority vote of its receptors with the same modality; RDKit rule when no receptor votes', None),
            ('Receptor vote', 'One vote per receptor family (human/rat/mouse orthologs vote once); orphans never vote; '
                              'multi-tag receptors use the tag with the ligand modality, order 1 first; ties -> RDKit if tied, else alphabetical', None),
            ('Rat/mouse receptors', 'Classification only covers human receptors: taken from the human ortholog (same receptor-level '
                                    'family), else from the human paralogs (parent family) if they all agree', None),
            ('', None, None),
            ('3. RDKit structural chemotype (ordered, first match wins)', None, bold),
        ] + [(chem, rule, None) for chem, rule in RDKIT_RULES_DOC] + [
            ('', None, None),
            ('4. Curated Type -> compatible modality', None, bold),
            ('Small molecule', 'Small molecule (an Ion is reported as TYPE≠MOD:ION - Type has no Ion entry)', None),
            ('Lipid', 'Small molecule, chemotype Lipid or Steroid', None),
            ('Peptide', 'Polypeptide (chemotype Protein only reported as info TYPE≠CHEM)', None),
            ('Protein', 'Polypeptide, chemotype Protein (Peptide-length sequence -> TYPE≠LEN)', None),
            ('Unknown / None', 'Not checked', None),
            ('', None, None),
            ('5. Vocabulary (modality -> chemotypes)', None, bold),
        ] + [(modality, ', '.join(chems), None) for modality, chems in self.ai_vocabulary().items()]
        for r, (label, text, cell_format) in enumerate(lines):
            if label:
                sheet.write_string(r, 0, label, cell_format)
            if text:
                sheet.write_string(r, 1, text)
