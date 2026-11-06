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

from collections import Counter, OrderedDict
from copy import copy
import os
import re

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import TableColumn

CLASSIFICATION_XLSX = os.path.join(settings.DATA_DIR, 'protein_data', 'ClassificationV2.xlsx')
OUTPUT_XLSX = 'Receptor_classification_review.xlsx'

# ligand workbook columns (1-based)
L_UNIPROT, L_IUPHAR, L_SPECIES, L_LIGAND, L_GTP_ID, L_STATUS, L_POTENCY, L_CATEGORY, L_MODALITY, L_TARGET, \
    L_COMMENT, L_RANK, L_PROPOSAL = 3, 4, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16, 18
# ClassificationV2 columns (1-based): Chemotype = modality, Modality = modality category
C_UNIPROT, C_GENE, C_FAMILY, C_MODALITY, C_MODALITY_ORDER, C_CATEGORY, C_LAST = 1, 2, 3, 4, 5, 6, 9

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

SAME = 'Same'
NOMENCLATURE = 'Nomenclature only'
CHANGE = 'Change'
MANUAL = 'Manual review'
ORPHANED = 'Orphan (proposed ligands only)'
KEPT = 'Kept – no physiological ligands'
SECONDARY_ROW = 'Secondary (not evaluated)'

NEW_COLUMNS = [
    ('New modality\ncategory', 16),
    ('Category\nrule', 9),
    ('New modality', 24),
    ('Modality\nrule', 9),
    ('Change\nstatus', 18),
    ('Reason', 70),
    ('Physiological ligands', 70),
    ('Ligand modality counts', 40),
]
NEW_HEADER_FILL = PatternFill('solid', fgColor='DDEBF7')
STATUS_FILLS = {
    CHANGE: PatternFill('solid', fgColor='F8CBAD'),
    MANUAL: PatternFill('solid', fgColor='FFE699'),
    ORPHANED: PatternFill('solid', fgColor='FFF2CC'),
}
MANUAL_HEADERS = [
    ('UniProt', 10), ('Gene', 10), ('Receptor family', 30), ('Current modality', 24),
    ('New modality\ncategory', 16), ('Candidate modalities', 40), ('Reason', 60),
    ('Physiological ligands\n[modality, GtP status, potency rank]', 80), ('GtP comment', 60), ('GtP rank potency', 60),
]


def display_name(name):
    return re.sub(SPECIES_SUFFIX_RE.pattern, '', plain(name), flags=re.IGNORECASE)


def counts_text(values):
    return '; '.join('%s ×%d' % item for item in Counter(values).most_common())


def receptor_name_match(ligand, receptor_names):
    """The ligand's (normalised, species-free) name if it occurs as a word in one of the receptor names."""
    name = ligand['key']
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
        parser.add_argument('--endogenous-csv', help='Local endogenous_ligand_detailed.csv instead of the GtP download')

    def handle(self, *args, **options):
        endogenous = AnnotateCommand.read_gtp(
            options['endogenous_csv'] or get_or_create_url_cache(GTP_ENDOGENOUS_URL, GTP_CACHE_TTL))
        targets = AnnotateCommand.label_targets(endogenous)
        receptors = self.read_ligands(options['ligands'], targets)
        self.category_of = {}
        for receptor in receptors.values():
            for ligand in receptor['ligands'].values():
                self.category_of[ligand['modality']] = ligand['category']

        workbook = openpyxl.load_workbook(options['classification'])
        sheet = workbook.active
        results, missing = self.annotate(sheet, receptors)
        self.write_manual_sheet(workbook, sheet, results)
        stats = self.write_summary_sheet(workbook, results, missing)
        workbook.save(options['output'])

        self.stdout.write("Wrote %s" % options['output'])
        for title, counter in stats:
            self.stdout.write("%s: %s" % (title, ', '.join('%s %d' % item for item in sorted(counter.items()))))
        self.stdout.write("Ligand-file receptors not in the classification: %s" % ', '.join(missing))

    # ------------------------------------------------------------------ ligands per receptor

    @staticmethod
    def read_ligands(path, targets):
        """UniProt -> receptor names, GtP comment/rank and ligands (one per name, species variants pooled).

        Only human receptor rows count, as the receptor classification is a human one.
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
            ligand = receptor['ligands'].setdefault(key, {
                'key': key, 'name': display_name(value[L_LIGAND]), 'category': value[L_CATEGORY],
                'modality': value[L_MODALITY], 'status': value[L_STATUS], 'potency': value[L_POTENCY],
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
                    'orphaned': True, 'reason': 'Only GtP Proposed ligands (%s)' % counts_text(
                        ligand['modality'] for ligand in all_ligands)}

        category, category_rule, category_reason = classify(ligands, 'category', receptor_names)
        modality, modality_rule, modality_reason = classify(ligands, 'modality', receptor_names)
        reasons = ['Category – %s' % category_reason, 'Modality – %s' % modality_reason]
        if use_proposed and any(ligand['status'] == PROPOSED for ligand in ligands):
            reasons.append('GtP Proposed ligands counted (receptor not classified as Orphan)')
        if category and modality and self.category_of.get(modality) != category:
            reasons.append('category and modality disagree')
            category = modality = None
        return {'category': category, 'category_rule': category_rule, 'modality': modality,
                'modality_rule': modality_rule, 'orphaned': False, 'reason': ' | '.join(reasons)}

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

        results, seen = [], set()
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
            else:
                # GPCRdb/IUPHAR names are short ("5-HT1B receptor"); the family name spells the ligand out
                family = re.sub(r'\s*receptors?$', '', str(sheet.cell(row, C_FAMILY).value or ''), flags=re.I)
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
            ]
            for offset, value in enumerate(values):
                cell = sheet.cell(row, first_new + offset, value)
                cell._style = copy(sheet.cell(row, C_LAST)._style)
            if result['status'] in STATUS_FILLS:
                sheet.cell(row, first_new + 4).fill = STATUS_FILLS[result['status']]

        self.extend_filter(sheet, len(NEW_COLUMNS))
        missing = sorted(set(receptors) - seen)
        return results, missing

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
            if result['status'] != MANUAL:
                continue
            row += 1
            receptor = result['receptor']
            ligands = [ligand for ligand in receptor['ligands'].values()
                       if result['use_proposed'] or ligand['status'] != PROPOSED]
            values = [
                result['uniprot'], sheet.cell(result['row'], C_GENE).value, sheet.cell(result['row'], C_FAMILY).value,
                result['current'][1], result['category'],
                counts_text(ligand['modality'] for ligand in ligands), result['reason'],
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
        row += 2
        out.cell(row, 1, 'Ligand-file receptors not in the classification').font = bold
        for uniprot in missing:
            row += 1
            out.cell(row, 1, uniprot)
        out.column_dimensions['A'].width = 45
        out.column_dimensions['B'].width = 12
        return stats
