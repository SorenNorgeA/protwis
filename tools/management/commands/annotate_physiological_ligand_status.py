"""
Test run: re-derive the GtP Principal/Secondary/Proposed status of every row in the hand-curated
physiological-ligand workbook straight from Guide to Pharmacology, so the labels can be curated before the
logic is ported into build_experimental_data_light (whose labeling_principals() reads a comment column GtP
renamed to "List Comment", so it only ever labels single-ligand receptors).

The "GtP classification" column only gets what GtP states explicitly:
  - the receptor has one endogenous ligand -> Principal (Proposed when the comment says so);
  - "X (and Y) is/are the principal endogenous agonist(s)" -> named ligands Principal, the others Secondary;
  - "Proposed ligand(s) ..." -> Proposed (only the named ligand for "X is a proposed ligand").
Where GtP states nothing outright, the column is filled from GtP's potency statements instead (cell shaded
blue rather than yellow, so the two sources stay distinguishable):
  - "X is the most potent ... / with the greatest potency / exhibits greater potency than ...";
  - the top tier of GtP's curated Rank Potency string ("A = B > C >> D");
  - "similar potency" -> all Principal.
Rows without any GtP evidence are left blank (never "None").

A second sheet, "Receptors", collapses the rows per receptor (UniProt + species) with its principal ligand(s),
to show which receptors still have no Principal/Proposed ligand. The Modality column is renamed to the receptor
classification nomenclature (MODALITY_RENAME); classify_receptor_modality builds on this output.

The source workbook is not modified; the result is written next to it with "_gtp" appended.

    python manage.py annotate_physiological_ligand_status
    python manage.py annotate_physiological_ligand_status --endogenous-csv FILE --interactions-csv FILE
"""
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from common.models import WebLink
from common.tools import get_or_create_url_cache
from protein.models import Protein

from collections import Counter, OrderedDict, defaultdict
from copy import copy
import html
import os
import re

import openpyxl
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

DATA_DIR = os.path.join(os.path.abspath(settings.BASE_DIR), 'data', 'ligand_classification')
INPUT_XLSX = 'Physiological_ligand_classification_extra_annotation.xlsx'
OUTPUT_XLSX = 'Physiological_ligand_classification_extra_annotation_gtp.xlsx'

GTP_ENDOGENOUS_URL = 'https://www.guidetopharmacology.org/DATA/endogenous_ligand_detailed.csv'
GTP_INTERACTIONS_URL = 'https://www.guidetopharmacology.org/DATA/interactions.csv'
GTP_CACHE_TTL = 7 * 24 * 3600

PRINCIPAL = 'Principal'
SECONDARY = 'Secondary'
PROPOSED = 'Proposed'

FIRST_DATA_ROW = 3
# 1-based columns of the source sheet
COL_UNIPROT, COL_SPECIES, COL_LIGAND, COL_GTP_ID, COL_STATUS, COL_POTENCY, COL_LAST = 3, 6, 7, 9, 10, 11, 13
RECEPTOR_COLUMNS = 6    # Class .. Species, copied to the Receptors sheet
COL_MODALITY_CATEGORY, COL_MODALITY = 12, 13

# Ligand modality -> the receptor classification nomenclature (Polypeptide split kept at <= 60 aa)
MODALITY_RENAME = {
    'Peptide': 'Peptide (≤60 AAs)',
    'Protein': 'Protein (>60 AAs)',
    'Lipid': 'Small mol – Lipid',
    'Monoamine': 'Small mol – Monoamine',
    'Other small molecule': 'Small mol – Other',
    'Amino acid': 'Small mol – Amino acid',
    'Monoatomic ion': 'Small mol – Monoatomic ion',
}

NEW_COLUMNS = [
    ('GtP\ntarget ID', 10),
    ('GtP comment', 60),
    ('GtP rank potency', 60),
    ('Review\nflags', 30),
]
EXPLICIT_FILL = PatternFill('solid', fgColor='FFF2CC')     # changed, GtP states it outright
SUGGESTED_FILL = PatternFill('solid', fgColor='DDEBF7')    # filled from a GtP potency statement
UNCLASSIFIED_FILL = PatternFill('solid', fgColor='F8CBAD')

RECEPTOR_SHEET = 'Receptors'
RECEPTOR_HEADERS = [
    ('GtP\ntarget ID', 10),
    ('Receptor\nstatus', 16),
    ('Principal ligand(s)', 40),
    ('Proposed ligand(s)', 30),
    ('Secondary ligand(s)', 50),
    ('Unclassified ligand(s)', 40),
    ('Ligands', 9),
]
UNCLASSIFIED = 'Unclassified'

TAG_RE = re.compile(r'<[^>]+>')
PRINCIPAL_RE = re.compile(r'^(.+?)\s+(?:is|are)\s+the\s+principal\b', re.IGNORECASE)
PROPOSED_ONE_RE = re.compile(r'^(.+?)\s+is\s+a\s+proposed\b', re.IGNORECASE)
MOST_POTENT_RES = [
    re.compile(r'^(.+?)\s+(?:is|are)\s+the\s+(?:most\s+potent|endogenous\s+(?:agonists?|ligands?)\s+with\s+the\s+greatest\s+potency)',
               re.IGNORECASE),
    re.compile(r'^(.+?)\s+(?:exhibits|has)\s+(?:a\s+)?(?:greater|higher)\s+potency', re.IGNORECASE),
    re.compile(r'^(.+?)\s+(?:is|are)\s+more\s+potent\s+than', re.IGNORECASE),
]
SPECIES_SUFFIX_RE = re.compile(r'\s*\((?:human|mouse|rat|pig)\)$')
ANTAGONISTS_RE = re.compile(r'endogenous\s+antagonists?\s+(?:are|is)\s+([^.]+)', re.IGNORECASE)
RANK_TIER_RE = re.compile(r'\s*(?:>>|≫|>|≥)\s*')
RANK_NAME_RE = re.compile(r'\s*[,=]\s*')


def plain(text):
    """GtP text without HTML tags/entities and with collapsed whitespace."""
    if not text:
        return ''
    return re.sub(r'\s+', ' ', html.unescape(TAG_RE.sub('', str(text)))).strip()


def norm_name(name):
    name = plain(name).lower()
    return re.sub(r'^\([+-]\)-', '', name)


def split_names(text):
    """'A, B and C' -> ['a', 'b', 'c'] (also handles the Oxford comma)."""
    text = re.sub(r'\s+and\s+', ', ', text)
    return [n for n in (norm_name(part) for part in text.split(',')) if n]


def first_sentence(comment):
    return re.split(r'\.\s', comment)[0].rstrip('.')


def match_names(names, ligands):
    """Ligand ids whose name matches one of names, plus the names that matched nothing.

    A comment name also matches a ligand whose name is a whole-word prefix of it ("C3a anaphylatoxin" -> C3a),
    never the other way round, so "neuropeptide Y" does not pick up "neuropeptide Y-(3-36)".
    """
    matched, unmatched = set(), []
    for name in names:
        hits = {lid for lid, lname in ligands.items() if lname == name}
        if not hits:
            hits = {lid for lid, lname in ligands.items() if name.startswith(lname + ' ')}
        if hits:
            matched |= hits
        else:
            unmatched.append(name)
    return matched, unmatched


def resolve_ligand_id(target, gtp_id, ligand_name):
    """(GtP ligand id of this row within target, flags): the row's GtP id, else a match on the name.

    GPCRdb ligand names carry a species suffix GtP keeps in a separate column, so it is dropped for the name match.
    """
    if gtp_id in target['ligands']:
        return gtp_id, []
    name = SPECIES_SUFFIX_RE.sub('', norm_name(ligand_name))
    by_name = [lid for lid, lname in target['ligands'].items() if lname == name]
    if by_name:
        return by_name[0], ['LIGAND_NOT_IN_GTP_TARGET', 'MATCHED_BY_NAME']
    return gtp_id, ['LIGAND_NOT_IN_GTP_TARGET']


def explicit_status(ligands, comment):
    """{ligand_id: (status, basis)} from what GtP states outright, plus the comment names that matched nothing."""
    sentence = first_sentence(comment)
    if len(set(ligands.values())) == 1:
        status = PROPOSED if re.search(r'propos', comment, re.IGNORECASE) else PRINCIPAL
        return {lid: (status, 'GtP: single endogenous ligand') for lid in ligands}, []

    match = PRINCIPAL_RE.match(sentence)
    if match:
        principals, unmatched = match_names(split_names(match.group(1)), ligands)
        if principals:
            return {lid: (PRINCIPAL if lid in principals else SECONDARY, 'GtP comment: principal')
                    for lid in ligands}, unmatched
        return {}, unmatched

    if re.search(r'propos', sentence, re.IGNORECASE):
        match = PROPOSED_ONE_RE.match(sentence)
        if match and not re.match(r'^proposed\b', sentence, re.IGNORECASE):
            proposed, unmatched = match_names(split_names(match.group(1)), ligands)
            return {lid: (PROPOSED, 'GtP comment: proposed (named)') for lid in proposed}, unmatched
        return {lid: (PROPOSED, 'GtP comment: proposed') for lid in ligands}, []
    return {}, []


def suggested_status(ligands, comment, rank_potency):
    """{ligand_id: (status, basis)} inferred from potency statements, plus the names that matched nothing."""
    sentence = first_sentence(comment)
    for pattern in MOST_POTENT_RES:
        match = pattern.match(sentence)
        if match:
            top, unmatched = match_names(split_names(match.group(1)), ligands)
            if top:
                return {lid: (PRINCIPAL if lid in top else SECONDARY, 'GtP comment: most potent')
                        for lid in ligands}, unmatched

    if rank_potency:
        tiers = [[norm_name(n) for n in RANK_NAME_RE.split(tier) if n.strip()]
                 for tier in RANK_TIER_RE.split(rank_potency)]
        top, _ = match_names(tiers[0], ligands)
        if top:
            ranked, _ = match_names([n for tier in tiers for n in tier], ligands)
            result = {}
            for lid in ligands:
                if lid in top:
                    result[lid] = (PRINCIPAL, 'GtP rank potency: top tier')
                elif lid in ranked:
                    result[lid] = (SECONDARY, 'GtP rank potency: lower tier')
                else:
                    result[lid] = (SECONDARY, 'GtP rank potency: not ranked')
            return result, []

    if re.search(r'similar potency', comment, re.IGNORECASE):
        return {lid: (PRINCIPAL, 'GtP comment: similar potency') for lid in ligands}, []
    return {}, []


class Command(BaseCommand):

    help = "Re-annotate the GtP classification of the physiological-ligand workbook from Guide to Pharmacology"

    def add_arguments(self, parser):
        parser.add_argument('--input', default=os.path.join(DATA_DIR, INPUT_XLSX),
                            help='Source workbook (default: %(default)s)')
        parser.add_argument('--output', default=os.path.join(DATA_DIR, OUTPUT_XLSX),
                            help='Annotated copy (default: %(default)s)')
        parser.add_argument('--endogenous-csv', help='Local endogenous_ligand_detailed.csv instead of the GtP download')
        parser.add_argument('--interactions-csv', help='Local interactions.csv instead of the GtP download')

    def handle(self, *args, **options):
        if os.path.abspath(options['input']) == os.path.abspath(options['output']):
            raise CommandError('--output must differ from --input; the source workbook is never overwritten')

        endogenous = self.read_gtp(options['endogenous_csv'] or get_or_create_url_cache(GTP_ENDOGENOUS_URL, GTP_CACHE_TTL))
        interactions = self.read_gtp(options['interactions_csv'] or get_or_create_url_cache(GTP_INTERACTIONS_URL, GTP_CACHE_TTL))
        targets = self.label_targets(endogenous)
        actions = self.gtp_actions(interactions)

        workbook = openpyxl.load_workbook(options['input'])
        sheet = workbook.active
        stats, records = self.annotate(sheet, targets, actions)
        receptor_counts, unclassified = self.write_receptor_sheet(workbook, sheet, records)
        workbook.save(options['output'])

        self.stdout.write("Wrote %s (%d rows)" % (options['output'], stats['rows']))
        self.stdout.write("GtP classification, previous -> new (source):")
        for (old, new, source), count in sorted(stats['transitions'].items()):
            self.stdout.write("  %-10s -> %-10s %-14s %5d" % (old, new or '(blank)', source, count))
        self.stdout.write("Filled from potency statements:")
        for basis, count in stats['suggestions'].most_common():
            self.stdout.write("  %-32s %5d" % (basis, count))
        self.stdout.write("No GtP evidence (blank): %d" % stats['no_evidence'])
        self.stdout.write("Review flags:")
        for flag, count in stats['flags'].most_common():
            self.stdout.write("  %-28s %5d" % (flag, count))
        for scope, counts in receptor_counts.items():
            self.stdout.write("Receptors (%s): %s" % (scope, ', '.join(
                '%s %d' % (status, counts[status]) for status in (PRINCIPAL, PROPOSED, UNCLASSIFIED))))
        for receptor in unclassified:
            self.stdout.write("  unclassified: %s %s (%s)" % (receptor[2], receptor[5], receptor[3]))

    # ------------------------------------------------------------------ GtP data

    @staticmethod
    def read_gtp(path):
        df = pd.read_csv(path, dtype=str, header=1, keep_default_na=False)
        df.columns = [c.strip().lower().replace(' ', '_') for c in df.columns]
        return df

    @staticmethod
    def label_targets(endogenous):
        """GtP target id -> ligands, comment, rank potency, explicit/suggested labels and antagonists."""
        targets = {}
        for target_id, rows in endogenous.groupby('target_id'):
            ligands = {}
            for ligand_id, name in zip(rows['ligand_id'], rows['ligand_name']):
                if ligand_id and name:
                    ligands.setdefault(ligand_id, norm_name(name))
            comment = plain(rows['list_comment'].iloc[0])
            rank_potency = plain(rows['rank_potency'].iloc[0])
            explicit, explicit_unmatched = explicit_status(ligands, comment)
            suggested, suggested_unmatched = suggested_status(ligands, comment, rank_potency)
            antagonists = set()
            for match in ANTAGONISTS_RE.finditer(comment):
                antagonists |= match_names(split_names(match.group(1)), ligands)[0]
            targets[target_id] = {
                'name': plain(rows['target_name'].iloc[0]),
                'ligands': ligands,
                'comment': comment,
                'rank_potency': rank_potency,
                'explicit': explicit,
                'suggested': suggested,
                'unmatched': explicit_unmatched + suggested_unmatched,
                'antagonists': antagonists,
            }
        return targets

    @staticmethod
    def gtp_actions(interactions):
        """(target id, ligand id) -> 'Agonist (Full agonist); ...' from interactions.csv."""
        actions = defaultdict(list)
        for target_id, ligand_id, kind, action in zip(interactions['target_id'], interactions['ligand_id'],
                                                      interactions['type'], interactions['action']):
            label = kind if action in ('', 'None', kind) else '%s (%s)' % (kind, action)
            if label and label not in actions[(target_id, ligand_id)]:
                actions[(target_id, ligand_id)].append(label)
        return {key: '; '.join(labels) for key, labels in actions.items()}

    @staticmethod
    def receptor_targets():
        """Protein entry_name -> GtP target id, rat/mouse falling back to the human ortholog (same family)."""
        gtop = {}
        for protein_id, index in WebLink.objects.filter(web_resource__slug='gtop', protein__isnull=False) \
                .values_list('protein__id', 'index'):
            gtop.setdefault(protein_id, index)
        proteins = list(Protein.objects.filter(sequence_type__slug='wt').values_list(
            'id', 'entry_name', 'family_id', 'species__common_name'))
        human_by_family = {family_id: pid for pid, _, family_id, species in proteins if species == 'Human'}
        targets = {}
        for pid, entry_name, family_id, _ in proteins:
            target = gtop.get(pid) or gtop.get(human_by_family.get(family_id))
            if target:
                targets[entry_name] = target
        return targets

    # ------------------------------------------------------------------ workbook

    def annotate(self, sheet, targets, actions):
        receptor_targets = self.receptor_targets()
        stats = {'rows': 0, 'transitions': Counter(), 'suggestions': Counter(), 'no_evidence': 0, 'flags': Counter()}
        records = []

        first_new = COL_LAST + 1
        last_new = COL_LAST + len(NEW_COLUMNS)
        sheet.cell(1, first_new, 'GtP ANNOTATION')._style = copy(sheet.cell(1, COL_LIGAND)._style)
        for col in range(first_new + 1, last_new + 1):
            sheet.cell(1, col)._style = copy(sheet.cell(1, COL_LIGAND)._style)
        sheet.merge_cells(start_row=1, start_column=first_new, end_row=1, end_column=last_new)
        for offset, (title, width) in enumerate(NEW_COLUMNS):
            cell = sheet.cell(2, first_new + offset, title)
            cell._style = copy(sheet.cell(2, COL_LAST)._style)
            sheet.column_dimensions[get_column_letter(first_new + offset)].width = width

        for row in range(FIRST_DATA_ROW, sheet.max_row + 1):
            uniprot = sheet.cell(row, COL_UNIPROT).value
            if not uniprot:
                continue
            stats['rows'] += 1
            species = str(sheet.cell(row, COL_SPECIES).value or '').lower()
            gtp_ligand = str(sheet.cell(row, COL_GTP_ID).value or '').strip()
            previous = sheet.cell(row, COL_STATUS).value
            previous = None if previous in (None, '', 'None') else str(previous)

            flags = []
            target_id = receptor_targets.get('%s_%s' % (str(uniprot).lower(), species))
            target = targets.get(target_id)
            ligand_id = gtp_ligand
            status = suggestion = suggestion_basis = None
            if target is None:
                flags.append('NO_GTP_TARGET' if target_id is None else 'TARGET_NOT_IN_GTP_ENDOGENOUS')
            else:
                ligand_id, id_flags = resolve_ligand_id(target, gtp_ligand, sheet.cell(row, COL_LIGAND).value)
                flags.extend(id_flags)
                if target['unmatched']:
                    flags.append('NAME_UNMATCHED')
                if ligand_id in target['antagonists'] or 'Antagonist' in actions.get((target_id, ligand_id), ''):
                    flags.append('ANTAGONIST')
                status = target['explicit'].get(ligand_id, (None, None))[0]
                if status is None:
                    suggestion, suggestion_basis = target['suggested'].get(ligand_id, (None, None))

            modality_cell = sheet.cell(row, COL_MODALITY)
            modality_cell.value = MODALITY_RENAME.get(modality_cell.value, modality_cell.value)

            final = status or suggestion
            if previous == PRINCIPAL and final != PRINCIPAL:
                flags.append('CHANGED_PRINCIPAL')

            status_cell = sheet.cell(row, COL_STATUS)
            status_cell.value = final
            if status and status != previous:
                status_cell.fill = EXPLICIT_FILL
            elif suggestion:
                status_cell.fill = SUGGESTED_FILL

            values = [
                int(target_id) if target_id and target_id.isdigit() else target_id,
                target['comment'] if target else '',
                target['rank_potency'] if target else '',
                '; '.join(flags),
            ]
            for offset, value in enumerate(values):
                cell = sheet.cell(row, first_new + offset, value if value != '' else None)
                cell._style = copy(sheet.cell(row, COL_POTENCY)._style)

            records.append({
                'row': row,
                'receptor': tuple(sheet.cell(row, col).value for col in range(1, RECEPTOR_COLUMNS + 1)),
                'target_id': values[0],
                'ligand': sheet.cell(row, COL_LIGAND).value,
                'potency': sheet.cell(row, COL_POTENCY).value,
                'status': final,
            })
            stats['transitions'][(previous or 'None', final or '', 'GtP statement' if status else
                                  'potency' if suggestion else '')] += 1
            if suggestion:
                stats['suggestions'][suggestion_basis] += 1
            elif status is None:
                stats['no_evidence'] += 1
            for flag in flags:
                stats['flags'][flag] += 1
        return stats, records

    def write_receptor_sheet(self, workbook, sheet, records):
        """One row per receptor (UniProt + species) with its ligands grouped by status; returns status counts."""
        receptors = OrderedDict()
        for record in records:
            receptor = receptors.setdefault(record['receptor'], {
                'first_row': record['row'], 'target_id': record['target_id'], 'ligands': defaultdict(list)})
            ligand = record['ligand']
            if record['status'] is None and record['potency'] not in (None, '', 'None'):
                ligand = '%s (potency rank %s)' % (ligand, record['potency'])
            group = receptor['ligands'][record['status'] or UNCLASSIFIED]
            if ligand not in group:
                group.append(ligand)

        counts = {'All species': Counter(), 'Human': Counter()}
        for key, receptor in receptors.items():
            ligands = receptor['ligands']
            receptor['status'] = PRINCIPAL if ligands[PRINCIPAL] else PROPOSED if ligands[PROPOSED] else UNCLASSIFIED
            counts['All species'][receptor['status']] += 1
            if key[RECEPTOR_COLUMNS - 1] == 'Human':
                counts['Human'][receptor['status']] += 1

        out = workbook.create_sheet(RECEPTOR_SHEET)
        header_style = sheet.cell(2, COL_LAST)._style
        bold = Font(bold=True)
        left = Alignment(horizontal='left', vertical='top')

        # summary block
        for col, title in enumerate(['Receptors (UniProt + species)', 'All species', 'Human'], start=1):
            out.cell(1, col, title)._style = copy(header_style)
        summary_rows = [PRINCIPAL, PROPOSED, UNCLASSIFIED]
        for offset, status in enumerate(summary_rows + ['Total'], start=2):
            out.cell(offset, 1, status).font = bold
            for col, scope in enumerate(['All species', 'Human'], start=2):
                out.cell(offset, col, sum(counts[scope].values()) if status == 'Total' else counts[scope][status])
        out.cell(2 + summary_rows.index(UNCLASSIFIED), 1).fill = UNCLASSIFIED_FILL
        legend_row = 2 + len(summary_rows) + 2
        out.cell(legend_row, 1, "Receptor status: Principal = at least one Principal ligand; Proposed = only "
                                "Proposed ligands; Unclassified = no Principal/Proposed ligand.")
        out.cell(legend_row + 1, 1, "Sheet '%s', GtP classification: yellow = stated by GtP (changed), "
                                    "blue = from a GtP potency statement, no fill = unchanged." % sheet.title)

        # receptor table
        header_row = legend_row + 3
        headers = [(sheet.cell(2, col).value, sheet.column_dimensions[get_column_letter(col)].width or 12)
                   for col in range(1, RECEPTOR_COLUMNS + 1)] + RECEPTOR_HEADERS
        for col, (title, width) in enumerate(headers, start=1):
            out.cell(header_row, col, title)._style = copy(header_style)
            out.column_dimensions[get_column_letter(col)].width = width
        out.column_dimensions['A'].width = 30    # also holds the summary labels

        for row, (key, receptor) in enumerate(receptors.items(), start=header_row + 1):
            for col in range(1, RECEPTOR_COLUMNS + 1):
                out.cell(row, col, key[col - 1])._style = copy(sheet.cell(receptor['first_row'], col)._style)
            ligands = receptor['ligands']
            values = [receptor['target_id'], receptor['status']] + \
                     ['; '.join(ligands[status]) or None for status in (PRINCIPAL, PROPOSED, SECONDARY, UNCLASSIFIED)] + \
                     [sum(len(group) for group in ligands.values())]
            for offset, value in enumerate(values, start=RECEPTOR_COLUMNS + 1):
                out.cell(row, offset, value).alignment = left
            if receptor['status'] == UNCLASSIFIED:
                out.cell(row, RECEPTOR_COLUMNS + 2).fill = UNCLASSIFIED_FILL

        last_col = get_column_letter(len(headers))
        out.auto_filter.ref = 'A%d:%s%d' % (header_row, last_col, header_row + len(receptors))
        out.freeze_panes = out.cell(header_row + 1, 1)
        return counts, [key for key, receptor in receptors.items() if receptor['status'] == UNCLASSIFIED]
