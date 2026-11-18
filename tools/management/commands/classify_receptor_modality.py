"""
Review file for the receptor modality classification (gpcrdb_data/protein_data/ClassificationV2.xlsx), re-derived
from the receptors' physiological ligands in the propose_ligand_classification output.

Rules, in order of priority (run separately for the modality category and the modality):
  1. all physiological ligands of the receptor have the same modality -> use it;
  2. the ligand(s) GtP names as principal ("X (and Y) is/are the principal ...") share one modality -> use it;
  2b. the Proposed principal(s) from propose_ligand_classification (receptors without a GtP principal) share one
     modality -> use it;
  3. the receptor name contains the name of a physiological ligand -> use that ligand's modality (longest match;
     names searched: GPCRdb/IUPHAR receptor name and the ClassificationV2 receptor family);
  4. GtP's potency order ("X is the most potent ..." or the top tier of its Rank Potency) -> top ligand's modality;
  5. otherwise manual review.
Only the human receptor rows of the ligand table count (the classification is a human one). GtP Proposed ligands
count like any other physiological ligand, except for receptors currently classified as Orphan: there they are not
counted, and a receptor with only Proposed ligands stays an Orphan receptor. Receptors without physiological ligands
keep their current classification. Old labels are mapped to the current nomenclature before comparing: the
Peptide/Protein relabel (RECEPTOR_EQUIVALENT) counts as "Same", other renames (RECEPTOR_RENAME) as
"Nomenclature only".

Secondary modalities (order 2, 3, ...) are the other modalities among the same counted ligands: a modality with a
GtP/proposed principal first, then the one with more ligands. They go to review (and the "Manual review" sheet) when
a secondary modality has a principal or at least as many ligands as the primary, and are compared with the current
order-2 rows of the classification.

Ligand modalities come from the ligand export (export_physiological_ligands_classification, v2), GtP statuses from
the ligand workbook. Besides the review workbook, Receptor_classification_v2.xlsx is written: ClassificationV2's
columns with one row per modality of each receptor (order 1, 2, ...), plus Status / Review / Reason. Receptor family
and class come from gpcrdb_data's proteins_and_families.txt (current families, also used by rule 3); a receptor that
moved family is marked for review.

Neither input workbook is modified.

    python manage.py annotate_physiological_ligand_status     # first, refreshes the ligand workbook
    python manage.py propose_ligand_classification            # then adds the proposed principals
    python manage.py classify_receptor_modality
"""
from django.core.management.base import BaseCommand
from django.conf import settings

from protein.models import Protein
from common.tools import get_or_create_url_cache
from tools.management.commands.annotate_physiological_ligand_status import (
    Command as AnnotateCommand, DATA_DIR, GTP_ENDOGENOUS_URL, GTP_CACHE_TTL,
    PRINCIPAL, PROPOSED, SPECIES_SUFFIX_RE, norm_name, plain, resolve_ligand_id)
from tools.management.commands.propose_ligand_classification import (
    OUTPUT_XLSX as LIGAND_XLSX, PROPOSED_PRINCIPAL)
from tools.management.commands.export_physiological_ligands_classification import (
    OUTPUT_XLSX as LIGAND_EXPORT_XLSX, COL_CATEGORY as EXPORT_CATEGORY, COL_MODALITY as EXPORT_MODALITY)

from collections import Counter, OrderedDict, defaultdict
from copy import copy
import csv
import os
import re

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import TableColumn

CLASSIFICATION_XLSX = os.path.join(settings.DATA_DIR, 'protein_data', 'ClassificationV2.xlsx')
FAMILIES_TXT = os.path.join(settings.DATA_DIR, 'protein_data', 'proteins_and_families.txt')
F_ACCESSION = 15    # human UniProt accession field of a proteins_and_families.txt receptor line
OUTPUT_XLSX = 'Receptor_classification_review.xlsx'
OUTPUT_V2_XLSX = 'Receptor_classification_v2.xlsx'

# ligand workbook columns (1-based)
L_UNIPROT, L_IUPHAR, L_SPECIES, L_LIGAND, L_GPCRDB_ID, L_GTP_ID, L_STATUS, L_POTENCY, L_CATEGORY, L_MODALITY, \
    L_TARGET, L_COMMENT, L_RANK, L_PROPOSAL = 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18
# ClassificationV2 columns (1-based): Chemotype = modality, Modality = modality category
C_UNIPROT, C_GENE, C_FAMILY, C_MODALITY, C_MODALITY_ORDER, C_CATEGORY, C_CATEGORY_ORDER, C_CLASS, C_SENSE, C_LAST = \
    1, 2, 3, 4, 5, 6, 7, 8, 9, 9

ORPHAN = 'Orphan'
HUMAN = 'Human'
# Same class under its current label (the aa cutoff is no longer part of the label: < 40 aa Peptide, > 100 aa
# Protein, in between by common name, see export_physiological_ligands_classification) -> reported as "Same"
RECEPTOR_EQUIVALENT = {
    'Peptide (<50 AAs)': 'Peptide',
    'Protein (>50 AAs)': 'Protein',
}
# Labels that are no longer in the scheme -> reported as "Nomenclature only"
RECEPTOR_RENAME = dict(RECEPTOR_EQUIVALENT, **{
    'Steroid receptors': 'Small mol – Lipid',
})
PRINCIPAL_BASES = {'GtP comment: principal'}
POTENCY_BASES = {'GtP comment: most potent', 'GtP rank potency: top tier'}
MIN_NAME_MATCH = 3
ION_NAMES = {'ca2+': 'calcium', 'mg2+': 'magnesium', 'zn2+': 'zinc', 'h+': 'proton'}

SAME = 'Same'
NOMENCLATURE = 'Nomenclature only'
CHANGE = 'Change'
MANUAL = 'Manual review'
ORPHANED = 'Orphan (proposed ligands only)'
KEPT = 'Kept – no physiological ligands'
SECONDARY_ROW = 'Order-2 row (see secondary status)'

# Secondary modalities (order 2+): the other modalities among the counted ligands
PRINCIPAL_STATUSES = {PRINCIPAL, PROPOSED_PRINCIPAL}
SEC_REVIEW = 'Review'
SEC_NEW = 'New'
SEC_SAME = 'Same'
SEC_CHANGED = 'Changed'
SEC_NOT_FOUND = 'Not in ligands'
SEC_NOW_PRIMARY = 'Now primary'
SEC_KEPT = 'Kept – no physiological ligands'
SEC_NONE = '–'

NEW_COLUMNS = [
    ('New modality\ncategory', 16),
    ('Category\nrule', 9),
    ('New modality', 24),
    ('Modality\nrule', 9),
    ('Change\nstatus', 18),
    ('Reason', 70),
    ('Physiological ligands', 70),
    ('Ligand modality counts', 40),
    ('Secondary modalities\n(order 2+)', 30),
    ('Secondary\nstatus', 14),
    ('Secondary reason', 70),
]
NEW_HEADER_FILL = PatternFill('solid', fgColor='DDEBF7')
STATUS_FILLS = {
    CHANGE: PatternFill('solid', fgColor='F8CBAD'),
    MANUAL: PatternFill('solid', fgColor='FFE699'),
    ORPHANED: PatternFill('solid', fgColor='FFF2CC'),
}
SECONDARY_FILLS = {
    SEC_REVIEW: PatternFill('solid', fgColor='FFE699'),
    SEC_NEW: PatternFill('solid', fgColor='DDEBF7'),
    SEC_CHANGED: PatternFill('solid', fgColor='F8CBAD'),
}
V2_NEW_SECONDARY = 'New secondary'
V2_NOT_IN_LIGANDS = 'Not in ligands (kept from ClassificationV2)'
V2_COLUMNS = [('Status', 18), ('Review', 8), ('Reason', 70), ('Ligands with this modality\n[GtP status]', 60),
              ('Family / class update\n(proteins_and_families.txt)', 50)]
REVIEW_FILL = PatternFill('solid', fgColor='FFE699')
MANUAL_HEADERS = [
    ('Review of', 16), ('UniProt', 10), ('Gene', 10), ('Receptor family', 30), ('Current modality', 24),
    ('New modality', 24), ('Secondary modalities\n(order 2+)', 30), ('Candidate modalities', 40), ('Reason', 60),
    ('Physiological ligands\n[modality, GtP status, potency rank]', 80), ('GtP comment', 60), ('GtP rank potency', 60),
]


def display_name(name):
    return re.sub(SPECIES_SUFFIX_RE.pattern, '', plain(name), flags=re.IGNORECASE)


def counts_text(values):
    return '; '.join('%s ×%d' % item for item in Counter(values).most_common())


def secondary_modalities(ligands, primary):
    """[(order, modality, ligands)] for the other modalities among the counted ligands, plus review reasons.

    Order: a modality with a principal (GtP or proposed) first, then more ligands, then name. Review when a
    secondary modality has a principal or at least as many ligands as the primary.
    """
    groups = OrderedDict()
    for ligand in ligands:
        if ligand['modality'] and ligand['modality'] != primary:
            groups.setdefault(ligand['modality'], []).append(ligand)
    ranked = sorted(groups.items(), key=lambda item: (
        not any(ligand['status'] in PRINCIPAL_STATUSES for ligand in item[1]), -len(item[1]), item[0]))
    n_primary = sum(1 for ligand in ligands if ligand['modality'] == primary)
    review = []
    for modality, group in ranked:
        principals = [ligand['name'] for ligand in group if ligand['status'] in PRINCIPAL_STATUSES]
        if principals:
            review.append('%s (%s) is a principal' % (', '.join(principals), modality))
        elif len(group) >= n_primary:
            review.append('%s has as many ligands as the primary (%d vs %d)' % (modality, len(group), n_primary))
    return [(order, modality, group) for order, (modality, group) in enumerate(ranked, start=2)], review


def receptor_name_match(ligand, receptor_names):
    """The ligand's (normalised, species-free) name if it occurs as a word in one of the receptor names.

    Ions are named by symbol, receptors by element ("Calcium-sensing receptors"), so ions match via ION_NAMES.
    """
    name = ION_NAMES.get(ligand['key'], ligand['key'])
    if len(name) < MIN_NAME_MATCH:
        return None
    pattern = re.compile(r'(?<![a-z0-9])%s(?![a-z])' % re.escape(name))
    return next((receptor for receptor in receptor_names if pattern.search(receptor)), None)


def skipped(notes):
    """Why the earlier rules did not decide, appended to a later rule's reason."""
    return ' (earlier rules: %s)' % '; '.join(notes) if notes else ''


def classify(ligands, level, receptor_names):
    """(value, rule, reason) for one level ('category' or 'modality'); value None = manual review."""
    values = [ligand[level] for ligand in ligands]
    if len(set(values)) == 1:
        return values[0], '1', 'Rule 1: all %d ligand(s) %s' % (len(ligands), values[0])

    notes = []
    principals = [ligand for ligand in ligands if ligand['principal_basis'] & PRINCIPAL_BASES]
    if principals:
        found = {ligand[level] for ligand in principals}
        names = ', '.join(ligand['name'] for ligand in principals)
        if len(found) == 1:
            return found.pop(), '2', 'Rule 2: GtP principal %s → %s' % (names, principals[0][level])
        notes.append('GtP principals %s differ (%s)' % (names, counts_text(ligand[level] for ligand in principals)))

    proposed = [ligand for ligand in ligands if PROPOSED_PRINCIPAL in ligand['principal_basis']]
    if proposed:
        found = {ligand[level] for ligand in proposed}
        names = ', '.join(ligand['name'] for ligand in proposed)
        if len(found) == 1:
            basis = proposed[0]['proposal'].split(':')[0] if proposed[0]['proposal'] else 'curation proposal'
            return found.pop(), '2b', 'Rule 2b: proposed principal %s (%s) → %s%s' % (
                names, basis.lower(), proposed[0][level], skipped(notes))
        notes.append('proposed principals %s differ (%s)' % (
            names, counts_text(ligand[level] for ligand in proposed)))

    matches = [(ligand, receptor_name_match(ligand, receptor_names)) for ligand in ligands]
    matches = [(ligand, receptor) for ligand, receptor in matches if receptor]
    if matches:
        longest = max(len(ligand['key']) for ligand, _ in matches)
        top = [(ligand, receptor) for ligand, receptor in matches if len(ligand['key']) == longest]
        found = {ligand[level] for ligand, _ in top}
        if len(found) == 1:
            ligand, receptor = top[0]
            return found.pop(), '3', 'Rule 3: receptor name "%s" contains ligand %s → %s%s' % (
                receptor, ligand['name'], ligand[level], skipped(notes))
        notes.append('receptor name matches ligands of different %s' % level)

    tops = [ligand for ligand in ligands if ligand['principal_basis'] & POTENCY_BASES]
    if tops:
        found = {ligand[level] for ligand in tops}
        names = ', '.join(ligand['name'] for ligand in tops)
        if len(found) == 1:
            return found.pop(), '4', 'Rule 4: most potent per GtP %s → %s%s' % (
                names, tops[0][level], skipped(notes))
        notes.append('GtP top-potency ligands %s differ (%s)' % (names, counts_text(ligand[level] for ligand in tops)))

    return None, '5', 'Rule 5: %s%s' % (counts_text(values), ' – ' + '; '.join(notes) if notes else
                                        ' – no GtP principal, receptor-name match or potency order')


class Command(BaseCommand):

    help = "Re-derive the receptor modality classification from the physiological ligands, as a review workbook"

    def add_arguments(self, parser):
        parser.add_argument('--ligands', default=os.path.join(DATA_DIR, LIGAND_XLSX),
                            help='Ligand workbook from propose_ligand_classification (default: %(default)s)')
        parser.add_argument('--classification', default=CLASSIFICATION_XLSX,
                            help='Receptor classification workbook (default: %(default)s)')
        parser.add_argument('--output', default=os.path.join(DATA_DIR, OUTPUT_XLSX),
                            help='Review workbook (default: %(default)s)')
        parser.add_argument('--families', default=FAMILIES_TXT,
                            help='Current receptor families and classes (default: %(default)s)')
        parser.add_argument('--ligand-export', default=os.path.join(DATA_DIR, LIGAND_EXPORT_XLSX),
                            help='Ligand export whose Modality category / Modality are used (default: %(default)s)')
        parser.add_argument('--output-v2', default=os.path.join(DATA_DIR, OUTPUT_V2_XLSX),
                            help='Receptor classification v2, one row per modality (default: %(default)s)')
        parser.add_argument('--endogenous-csv', help='Local endogenous_ligand_detailed.csv instead of the GtP download')

    def handle(self, *args, **options):
        endogenous = AnnotateCommand.read_gtp(
            options['endogenous_csv'] or get_or_create_url_cache(GTP_ENDOGENOUS_URL, GTP_CACHE_TTL))
        targets = AnnotateCommand.label_targets(endogenous)
        export_terms = self.read_export_terms(options['ligand_export'])
        receptors = self.read_ligands(options['ligands'], targets, export_terms)
        self.category_of = {}
        for receptor in receptors.values():
            for ligand in receptor['ligands'].values():
                self.category_of[ligand['modality']] = ligand['category']

        workbook = openpyxl.load_workbook(options['classification'])
        sheet = workbook.active
        self.family_updates = self.read_family_updates(options['families'], sheet)
        results, missing = self.annotate(sheet, receptors)
        self.write_manual_sheet(workbook, sheet, results)
        stats = self.write_summary_sheet(workbook, results, missing)
        workbook.save(options['output'])
        v2_stats = self.write_v2(options['output_v2'], sheet, results)

        self.stdout.write("Wrote %s" % options['output'])
        for title, counter in stats:
            self.stdout.write("%s: %s" % (title, ', '.join('%s %d' % item for item in sorted(counter.items()))))
        self.stdout.write("Wrote %s" % options['output_v2'])
        for title, counter in v2_stats:
            self.stdout.write("  %s: %s" % (title, ', '.join('%s %d' % item for item in sorted(counter.items()))))
        self.stdout.write("Ligand-file receptors not in the classification: %s" % ', '.join(missing))

    # ------------------------------------------------------------------ ligands per receptor

    @staticmethod
    def read_family_updates(path, sheet):
        """UniProt -> current family/class from proteins_and_families.txt, with what changed vs ClassificationV2.

        The file is indented Class / ligand type / receptor family headers over receptor lines (matched to GPCRdb
        human proteins by accession). A family change is a rename when most receptors of the old family got the
        same new family (Frizzled -> WNT receptors), otherwise a move (GPR4: Orphan -> Proton receptors).
        """
        uniprot_of = {accession: entry_name.split('_')[0].upper() for accession, entry_name in Protein.objects.filter(
            species__common_name=HUMAN).values_list('accession', 'entry_name') if accession}
        current, headers = {}, []
        with open(path, encoding='utf-8') as handle:
            for line in handle:
                line = line.rstrip('\r\n')
                text = line.strip()
                if not text:
                    continue
                if text.startswith('"'):
                    fields = next(csv.reader([text]))
                    uniprot = uniprot_of.get(fields[F_ACCESSION]) if len(fields) > F_ACCESSION else None
                    if uniprot and len(headers) >= 3:
                        current[uniprot] = {'class': headers[0].split(' | ')[0], 'family': plain(headers[-1])}
                else:
                    depth = (len(line) - len(line.lstrip(' '))) // 4
                    headers = headers[:depth] + [text]

        previous = OrderedDict()
        for row in range(2, sheet.max_row + 1):
            uniprot = sheet.cell(row, C_UNIPROT).value
            if uniprot and uniprot not in previous:
                previous[uniprot] = (plain(sheet.cell(row, C_FAMILY).value), sheet.cell(row, C_CLASS).value)
        new_families = defaultdict(Counter)
        for uniprot, (family, _) in previous.items():
            if uniprot in current:
                new_families[family][current[uniprot]['family']] += 1
        updates = {}
        for uniprot, (family, receptor_class) in previous.items():
            if uniprot not in current:
                continue
            update = dict(current[uniprot], notes=[], moved=False)
            if update['family'] != family:
                update['moved'] = new_families[family].most_common(1)[0][0] != update['family']
                update['notes'].append('Family %s: %s → %s' % (
                    'moved' if update['moved'] else 'renamed', family, update['family']))
            if update['class'] != receptor_class:
                update['notes'].append('Class renamed: %s → %s' % (receptor_class, update['class']))
            updates[uniprot] = update
        return updates

    @staticmethod
    def read_export_terms(path):
        """GPCRdb ligand id -> (Modality category, Modality) from the ligand export's Ligands sheet."""
        rows = openpyxl.load_workbook(path, read_only=True)['Ligands'].iter_rows(min_row=2, values_only=True)
        header = list(next(rows))
        cols = [header.index(name) for name in ('GPCRdb ID', EXPORT_CATEGORY, EXPORT_MODALITY)]
        return {row[cols[0]]: (row[cols[1]], row[cols[2]]) for row in rows if row[cols[0]] is not None}

    @staticmethod
    def read_ligands(path, targets, export_terms):
        """UniProt -> receptor names, GtP comment/rank and ligands (one per name, species variants pooled).

        Only human receptor rows count, as the receptor classification is a human one. GtP Principal/Secondary/
        Proposed comes from the ligand workbook; Modality category / Modality from the ligand export (by GPCRdb ID),
        the workbook's own value only for a ligand the export does not have.
        """
        sheet = openpyxl.load_workbook(path).worksheets[0]
        receptors = OrderedDict()
        for row in sheet.iter_rows(min_row=3, values_only=True):
            value = (None,) + row    # 1-based like the column constants
            uniprot = value[L_UNIPROT]
            if not uniprot or value[L_SPECIES] != HUMAN:
                continue
            receptor = receptors.setdefault(uniprot, {
                'iuphar': set(), 'ligands': OrderedDict(), 'comment': value[L_COMMENT],
                'rank_potency': value[L_RANK], 'target_id': None})
            receptor['iuphar'].add(plain(value[L_IUPHAR]).lower())
            target_id = value[L_TARGET]
            if target_id and receptor['target_id'] is None:
                receptor['target_id'] = str(target_id)
            target = targets.get(str(target_id)) if target_id else None

            key = SPECIES_SUFFIX_RE.sub('', norm_name(value[L_LIGAND]))
            category, modality = export_terms.get(value[L_GPCRDB_ID], (value[L_CATEGORY], value[L_MODALITY]))
            ligand = receptor['ligands'].setdefault(key, {
                'key': key, 'name': display_name(value[L_LIGAND]), 'category': category,
                'modality': modality, 'status': value[L_STATUS], 'potency': value[L_POTENCY],
                'principal_basis': set(), 'proposal': None})
            if value[L_STATUS] == PROPOSED_PRINCIPAL:
                ligand['principal_basis'].add(PROPOSED_PRINCIPAL)
                ligand['proposal'] = ligand['proposal'] or value[L_PROPOSAL]
            if target:
                ligand_id = resolve_ligand_id(target, str(value[L_GTP_ID] or '').strip(), value[L_LIGAND])[0]
                for labels in (target['explicit'], target['suggested']):
                    status, basis = labels.get(ligand_id, (None, None))
                    if status == PRINCIPAL:
                        ligand['principal_basis'].add(basis)
        return receptors

    def classify_receptor(self, receptor, receptor_names, use_proposed):
        """New category/modality with rules and reason; None when the receptor has no physiological ligands.

        use_proposed: count GtP Proposed ligands (receptors not currently classified as Orphan).
        """
        all_ligands = list(receptor['ligands'].values())
        ligands = [ligand for ligand in all_ligands if use_proposed or ligand['status'] != PROPOSED]
        if not all_ligands:
            return None
        if not ligands:
            return {'category': ORPHAN, 'category_rule': 'orphan', 'modality': ORPHAN, 'modality_rule': 'orphan',
                    'orphaned': True, 'secondary': [], 'secondary_review': [],
                    'reason': 'Only GtP Proposed ligands (%s)' % counts_text(
                        ligand['modality'] for ligand in all_ligands)}

        category, category_rule, category_reason = classify(ligands, 'category', receptor_names)
        modality, modality_rule, modality_reason = classify(ligands, 'modality', receptor_names)
        reasons = ['Category – %s' % category_reason, 'Modality – %s' % modality_reason]
        if use_proposed and any(ligand['status'] == PROPOSED for ligand in ligands):
            reasons.append('GtP Proposed ligands counted (receptor not classified as Orphan)')
        if category and modality and self.category_of.get(modality) != category:
            reasons.append('category and modality disagree')
            category = modality = None
        secondary, secondary_review = secondary_modalities(ligands, modality) if modality else ([], [])
        return {'category': category, 'category_rule': category_rule, 'modality': modality,
                'modality_rule': modality_rule, 'orphaned': False, 'reason': ' | '.join(reasons),
                'secondary': secondary, 'secondary_review': secondary_review}

    # ------------------------------------------------------------------ workbook

    def annotate(self, sheet, receptors):
        human_names = dict(Protein.objects.filter(
            entry_name__in=['%s_human' % uniprot.lower() for uniprot in receptors]).values_list('entry_name', 'name'))
        header_style = sheet.cell(1, C_LAST)._style
        first_new = C_LAST + 1
        for offset, (title, width) in enumerate(NEW_COLUMNS):
            cell = sheet.cell(1, first_new + offset, title)
            cell._style = copy(header_style)
            cell.fill = NEW_HEADER_FILL
            sheet.column_dimensions[get_column_letter(first_new + offset)].width = width

        # current order-2 modalities per receptor (rows after its first, or with a modality order other than 1)
        current_secondary, first_seen = defaultdict(list), set()
        for row in range(2, sheet.max_row + 1):
            uniprot = sheet.cell(row, C_UNIPROT).value
            if uniprot and (uniprot in first_seen or sheet.cell(row, C_MODALITY_ORDER).value not in (1, None)):
                modality = sheet.cell(row, C_MODALITY).value
                current_secondary[uniprot].append(RECEPTOR_RENAME.get(modality, modality))
            first_seen.add(uniprot)

        results, seen, primary_results = [], set(), {}
        for row in range(2, sheet.max_row + 1):
            uniprot = sheet.cell(row, C_UNIPROT).value
            if not uniprot:
                continue
            current_modality = sheet.cell(row, C_MODALITY).value
            current_category = sheet.cell(row, C_CATEGORY).value
            renamed = (RECEPTOR_RENAME.get(current_category, current_category),
                       RECEPTOR_RENAME.get(current_modality, current_modality))
            equivalent = (RECEPTOR_EQUIVALENT.get(current_category, current_category),
                          RECEPTOR_EQUIVALENT.get(current_modality, current_modality))
            receptor = receptors.get(uniprot)
            ligand_modalities = {ligand['modality'] for ligand in receptor['ligands'].values()} if receptor else set()

            result = {'row': row, 'uniprot': uniprot, 'receptor': receptor, 'current': (current_category, current_modality),
                      'use_proposed': renamed[1] != ORPHAN}
            if uniprot in seen or sheet.cell(row, C_MODALITY_ORDER).value not in (1, None):
                result.update(category=renamed[0], modality=renamed[1], category_rule='-', modality_rule='-',
                              status=SECONDARY_ROW, reason='%s found among the ligands: %s' % (
                                  renamed[1], 'yes' if renamed[1] in ligand_modalities else 'no'))
                result.update(self.order2_row_status(renamed[1], primary_results.get(uniprot)))
            else:
                # GPCRdb/IUPHAR names are short ("5-HT1B receptor"); the family name spells the ligand out
                update = self.family_updates.get(uniprot)
                family = update['family'] if update else sheet.cell(row, C_FAMILY).value
                family = re.sub(r'\s*receptors?$', '', str(family or ''), flags=re.I)
                names = [plain(human_names.get('%s_human' % uniprot.lower(), '')).lower(), plain(family).lower()]
                names += sorted(receptor['iuphar']) if receptor else []
                derived = self.classify_receptor(receptor, [name for name in names if name],
                                                 result['use_proposed']) if receptor else None
                if derived is None:
                    result.update(category=renamed[0], modality=renamed[1], category_rule='-', modality_rule='-',
                                  status=NOMENCLATURE if renamed != equivalent else KEPT,
                                  reason='No physiological ligands in the ligand table')
                else:
                    result.update(derived)
                    new = (derived['category'], derived['modality'])
                    if None in new:
                        result['status'] = MANUAL
                    elif new == equivalent:
                        result['status'] = SAME
                    elif new == renamed:
                        result['status'] = NOMENCLATURE
                    elif derived['orphaned']:
                        result['status'] = ORPHANED
                    else:
                        result['status'] = CHANGE
                result.update(self.secondary_status(result, current_secondary.get(uniprot, [])))
                primary_results[uniprot] = result
            seen.add(uniprot)
            results.append(result)

            ligands = receptor['ligands'].values() if receptor else []
            values = [
                result['category'], result['category_rule'], result['modality'], result['modality_rule'],
                result['status'], result['reason'],
                '; '.join('%s [%s, %s]' % (ligand['name'], ligand['modality'], ligand['status'] or '–')
                          for ligand in ligands) or None,
                counts_text(ligand['modality'] for ligand in ligands
                            if result['use_proposed'] or ligand['status'] != PROPOSED) or None,
                result['secondary_text'], result['secondary_status'], result['secondary_reason'],
            ]
            for offset, value in enumerate(values):
                cell = sheet.cell(row, first_new + offset, value)
                cell._style = copy(sheet.cell(row, C_LAST)._style)
            if result['status'] in STATUS_FILLS:
                sheet.cell(row, first_new + 4).fill = STATUS_FILLS[result['status']]
            if result['secondary_status'] in SECONDARY_FILLS:
                sheet.cell(row, first_new + 9).fill = SECONDARY_FILLS[result['secondary_status']]

        self.extend_filter(sheet, len(NEW_COLUMNS))
        missing = sorted(set(receptors) - seen)
        return results, missing

    @staticmethod
    def secondary_status(result, current):
        """Secondary modalities of a receptor's order-1 row (text, status, reason) against its current order-2 rows."""
        secondary = result.get('secondary') or []
        derived = [modality for _, modality, _ in secondary]
        parts = ['%s (%d): %s [%d]' % (modality, order, ', '.join(ligand['name'] for ligand in group), len(group))
                 for order, modality, group in secondary]
        if current:
            parts.append('current order 2: %s' % ', '.join(current))
        if 'secondary' not in result:    # no physiological ligands
            status = SEC_KEPT if current else SEC_NONE
        elif result['secondary_review']:
            status = SEC_REVIEW
            parts.insert(0, 'Review: %s' % '; '.join(result['secondary_review']))
        elif derived:
            status = SEC_NEW if not current else SEC_SAME if set(derived) == set(current) else SEC_CHANGED
        elif current:
            status = SEC_NOW_PRIMARY if set(current) == {result['modality']} else SEC_NOT_FOUND
        else:
            status = SEC_NONE
        return {'secondary_text': '; '.join('%d: %s' % (order, modality) for order, modality, _ in secondary) or None,
                'secondary_status': status, 'secondary_reason': ' | '.join(parts) or None}

    @staticmethod
    def order2_row_status(modality, primary):
        """Where a current order-2 row's modality ended up in the derived classification."""
        if primary is None or 'secondary' not in primary:
            status = SEC_KEPT
        elif modality == primary['modality']:
            status = SEC_NOW_PRIMARY
        else:
            orders = [order for order, derived, _ in primary['secondary'] if derived == modality]
            status = 'Found (order %d)' % orders[0] if orders else SEC_NOT_FOUND
        return {'secondary_text': None, 'secondary_status': status, 'secondary_reason': None}

    @staticmethod
    def extend_filter(sheet, added):
        """Filter over the original + added columns.

        ClassificationV2 keeps its data in an Excel Table, and a sheet autofilter overlapping a table makes Excel
        "repair" the file, so the table is widened instead (header names in the table's own _x000a_ encoding).
        """
        last_col = sheet.max_column
        ref = 'A1:%s%d' % (get_column_letter(last_col), sheet.max_row)
        if not sheet.tables:
            sheet.auto_filter.ref = ref
            return
        table = next(iter(sheet.tables.values()))
        next_id = max(column.id for column in table.tableColumns) + 1
        for offset, col in enumerate(range(last_col - added + 1, last_col + 1)):
            name = str(sheet.cell(1, col).value).replace('\n', '_x000a_')
            table.tableColumns.append(TableColumn(id=next_id + offset, name=name))
        table.ref = ref
        table.autoFilter.ref = ref
        table.sortState = None    # its sort range refers to the old table size

    # ------------------------------------------------------------------ receptor classification v2

    def v2_rows(self, sheet, result, current_order2):
        """Rows (one per modality) of one receptor for the v2 classification, in modality order."""
        renamed = (RECEPTOR_RENAME.get(result['current'][0], result['current'][0]),
                   RECEPTOR_RENAME.get(result['current'][1], result['current'][1]))
        if 'secondary' not in result:    # no physiological ligands: the current rows, in the new nomenclature
            rows = [{'category': renamed[0], 'modality': renamed[1], 'order': 1,
                     'category_order': sheet.cell(result['row'], C_CATEGORY_ORDER).value or 1,
                     'status': result['status'], 'review': False, 'reason': result['reason'], 'ligands': None}]
            for other in current_order2:
                rows.append({'category': other['category'], 'modality': other['modality'],
                             'order': sheet.cell(other['row'], C_MODALITY_ORDER).value,
                             'category_order': sheet.cell(other['row'], C_CATEGORY_ORDER).value,
                             'status': result['status'], 'review': False,
                             'reason': 'ClassificationV2 order-2 row, no physiological ligands', 'ligands': None})
            return rows

        receptor = result['receptor']
        counted = [ligand for ligand in receptor['ligands'].values()
                   if result['use_proposed'] or ligand['status'] != PROPOSED]

        def ligand_text(modality):
            return '; '.join('%s [%s]' % (ligand['name'], ligand['status'] or '–')
                             for ligand in counted if ligand['modality'] == modality) or None

        rows = [{'category': result['category'] or renamed[0], 'modality': result['modality'] or renamed[1],
                 'order': 1, 'status': result['status'], 'review': result['status'] in (CHANGE, MANUAL, ORPHANED),
                 'reason': result['reason'], 'ligands': ligand_text(result['modality'])}]
        current = {other['modality'] for other in current_order2}
        flagged = result['secondary_status'] == SEC_REVIEW
        for order, modality, group in result['secondary']:
            rows.append({'category': self.category_of.get(modality), 'modality': modality, 'order': order,
                         'status': SAME if modality in current else V2_NEW_SECONDARY, 'review': flagged,
                         'reason': result['secondary_reason'] if flagged else
                         'Secondary: %d of the %d ligands' % (len(group), len(counted)),
                         'ligands': ligand_text(modality)})
        derived = {row['modality'] for row in rows}
        for other in current_order2:
            if other['modality'] not in derived:
                rows.append({'category': other['category'], 'modality': other['modality'],
                             'order': max(row['order'] for row in rows) + 1, 'status': V2_NOT_IN_LIGANDS,
                             'review': True, 'reason': 'ClassificationV2 order-2 modality; no physiological ligand '
                                                       'has it', 'ligands': None})
        categories = []
        for row in rows:    # a category keeps the order of its first (highest) modality, as in ClassificationV2
            if row['category'] not in categories:
                categories.append(row['category'])
            row['category_order'] = categories.index(row['category']) + 1
        return rows

    def write_v2(self, path, sheet, results):
        """Receptor classification v2: ClassificationV2's columns, one row per modality of each receptor."""
        order2 = defaultdict(list)
        for result in results:
            if result['status'] == SECONDARY_ROW:
                order2[result['uniprot']].append(result)

        workbook = openpyxl.Workbook()
        out = workbook.active
        out.title = 'Classification'
        headers = [(sheet.cell(1, col).value, sheet.column_dimensions[get_column_letter(col)].width or 14)
                   for col in range(1, C_LAST + 1)] + V2_COLUMNS
        for col, (title, width) in enumerate(headers, start=1):
            cell = out.cell(1, col, title)
            cell.font = Font(bold=True)
            cell.fill = NEW_HEADER_FILL
            cell.alignment = Alignment(wrap_text=True, vertical='top')
            out.column_dimensions[get_column_letter(col)].width = width

        stats = {'rows': Counter(), 'receptors': Counter()}
        row = 1
        for result in results:
            if result['status'] == SECONDARY_ROW:
                continue
            rows = self.v2_rows(sheet, result, order2.get(result['uniprot'], []))
            update = self.family_updates.get(result['uniprot'])
            family = update['family'] if update else sheet.cell(result['row'], C_FAMILY).value
            receptor_class = update['class'] if update else sheet.cell(result['row'], C_CLASS).value
            notes = '; '.join(update['notes']) if update else 'Not in %s' % os.path.basename(FAMILIES_TXT)
            if update and update['moved']:    # the modality was set for the old family: check it
                for item in rows:
                    item['review'] = True
                rows[0]['reason'] = 'Receptor family moved, check the modality | %s' % rows[0]['reason']
            for note in (update['notes'] if update else []):
                stats['receptors'][note.split(':')[0].lower()] += 1
            stats['receptors']['with %d modalit%s' % (len(rows), 'y' if len(rows) == 1 else 'ies')] += 1
            stats['receptors']['for review'] += any(item['review'] for item in rows)
            for item in rows:
                row += 1
                values = [result['uniprot'], sheet.cell(result['row'], C_GENE).value, family, item['modality'],
                          item['order'], item['category'], item['category_order'], receptor_class,
                          sheet.cell(result['row'], C_SENSE).value,
                          item['status'], 'Y' if item['review'] else None, item['reason'], item['ligands'],
                          notes or None]
                for col, value in enumerate(values, start=1):
                    out.cell(row, col, value)
                if item['status'] in STATUS_FILLS:
                    out.cell(row, C_LAST + 1).fill = STATUS_FILLS[item['status']]
                if item['review']:
                    out.cell(row, C_LAST + 2).fill = REVIEW_FILL
                stats['rows'][item['status']] += 1
                stats['rows']['(review)'] += item['review']
        out.freeze_panes = 'B2'
        out.auto_filter.ref = 'A1:%s%d' % (get_column_letter(len(headers)), row)

        summary = workbook.create_sheet('Summary')
        bold = Font(bold=True)
        line = 1
        for title, key in (('Rows by status', 'rows'), ('Receptors', 'receptors')):
            summary.cell(line, 1, title).font = bold
            for label, count in sorted(stats[key].items()):
                line += 1
                summary.cell(line, 1, label)
                summary.cell(line, 2, count)
            line += 2
        for text in (
                'One row per modality of a receptor, ordered by Modality order; Modality category order keeps the '
                'order of the first modality of that category (as in ClassificationV2).',
                'Order 1: rules 1 all ligands same, 2 GtP principal(s), 2b proposed principal(s), 3 receptor name '
                'contains ligand, 4 GtP potency order, 5 manual (see Receptor_classification_review.xlsx).',
                'Order 2+: the other modalities among the ligands, a modality with a GtP/proposed principal first, '
                'then more ligands.',
                'Ligand modalities from %s; GtP status from %s.' % (LIGAND_EXPORT_XLSX, LIGAND_XLSX),
                'Review = Y: order-1 modality changed from ClassificationV2 (or manual / orphaned), a secondary '
                'modality with a principal or as many ligands as the primary, or a ClassificationV2 order-2 '
                'modality that no ligand has (kept).',
                'Receptors without physiological ligands keep their ClassificationV2 rows (new nomenclature).',
                'Receptor family and class from %s (current GPCRdb families); a receptor whose family moved (not '
                'just renamed) is marked for review.' % os.path.basename(FAMILIES_TXT)):
            summary.cell(line, 1, text)
            line += 1
        summary.column_dimensions['A'].width = 45
        workbook.save(path)
        return [('Rows by status', stats['rows']), ('Receptors', stats['receptors'])]

    @staticmethod
    def write_manual_sheet(workbook, sheet, results):
        out = workbook.create_sheet('Manual review')
        header_style = sheet.cell(1, C_LAST)._style
        for col, (title, width) in enumerate(MANUAL_HEADERS, start=1):
            out.cell(1, col, title)._style = copy(header_style)
            out.column_dimensions[get_column_letter(col)].width = width
        top = Alignment(vertical='top', wrap_text=True)
        row = 1
        for result in results:
            if result['status'] == MANUAL:
                review_of, reason = 'Modality', result['reason']
            elif result['secondary_status'] == SEC_REVIEW:
                review_of, reason = 'Secondary modality', result['secondary_reason']
            else:
                continue
            row += 1
            receptor = result['receptor']
            ligands = [ligand for ligand in receptor['ligands'].values()
                       if result['use_proposed'] or ligand['status'] != PROPOSED]
            values = [
                review_of, result['uniprot'], sheet.cell(result['row'], C_GENE).value,
                sheet.cell(result['row'], C_FAMILY).value, result['current'][1],
                result['modality'] or result['category'], result['secondary_text'],
                counts_text(ligand['modality'] for ligand in ligands), reason,
                '\n'.join('%s [%s, %s, %s]' % (ligand['name'], ligand['modality'], ligand['status'] or '–',
                                                ligand['potency'] if ligand['potency'] not in (None, '') else '–')
                          for ligand in receptor['ligands'].values()),
                receptor['comment'], receptor['rank_potency'],
            ]
            for col, value in enumerate(values, start=1):
                out.cell(row, col, value).alignment = top
        out.freeze_panes = 'A2'
        out.auto_filter.ref = 'A1:%s%d' % (get_column_letter(len(MANUAL_HEADERS)), row)

    @staticmethod
    def write_summary_sheet(workbook, results, missing):
        out = workbook.create_sheet('Summary')
        primary = [result for result in results if result['status'] != SECONDARY_ROW]
        evaluated = [result for result in primary if result['category_rule'] != '-']
        stats = [
            ('Change status (receptors)', Counter(result['status'] for result in primary)),
            ('Category rule', Counter(result['category_rule'] for result in evaluated)),
            ('Modality rule', Counter(result['modality_rule'] for result in evaluated)),
            ('Secondary status (receptors)', Counter(result['secondary_status'] for result in primary)),
        ]
        bold = Font(bold=True)
        row = 1
        for title, counter in stats:
            out.cell(row, 1, title).font = bold
            out.cell(row, 2, 'Receptors').font = bold
            for key, count in sorted(counter.items()):
                row += 1
                out.cell(row, 1, key)
                out.cell(row, 2, count)
            row += 2
        out.cell(row, 1, 'Rules: 1 all ligands same; 2 GtP principal(s); 2b proposed principal(s) (curation '
                         'proposal); 3 receptor name contains ligand; '
                         '4 GtP potency order; 5 manual; orphan = only GtP Proposed ligands. '
                         'GtP Proposed ligands count unless the receptor is currently classified as Orphan.')
        row += 1
        out.cell(row, 1, 'Secondary modalities: the other modalities among the counted ligands, ordered 2, 3, ... '
                         '(a modality with a GtP/proposed principal first, then more ligands). Review when a secondary '
                         'modality has a principal or at least as many ligands as the primary.')
        row += 2
        out.cell(row, 1, 'Ligand-file receptors not in the classification').font = bold
        for uniprot in missing:
            row += 1
            out.cell(row, 1, uniprot)
        out.column_dimensions['A'].width = 45
        out.column_dimensions['B'].width = 12
        return stats
