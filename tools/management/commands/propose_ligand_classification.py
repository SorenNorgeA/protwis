"""
Curation proposal on top of the annotate_physiological_ligand_status output: every receptor without a GtP Principal
(or GtP Proposed) ligand gets a "Proposed principal", its other unlabelled ligands a "Proposed secondary". Rules,
first hit wins (several ligands may tie):
  1. Name: a ligand named in the receptor name, the IUPHAR name or the receptor family name, or a ligand whose name
     starts with that name ("5-Hydroxytryptamine receptors" -> 5-hydroxytryptamine, "Kisspeptin receptor" ->
     kisspeptin-10/-13/...); the most specific (longest) match wins. LIGAND_ALIASES covers GtP spellings that
     differ from the family name (SRIF = somatostatin).
  2. Family consensus: a ligand that is Principal at most human receptors of the same family that have one
     (L-glutamic acid for mGlu4, adrenaline + noradrenaline for alpha1B).
  3. GPCRdb potency rank: the best-ranked ligand(s); with no rank at all, every candidate.
In receptors that do have a GtP Principal/Proposed ligand, an unlabelled row becomes Proposed principal when it is
the same ligand as a Principal (other species), else Proposed secondary.

The proposals go into the "GtP classification" column (green), with the reason in a new "Proposal basis" column.
The "Receptors" sheet is rebuilt for human receptors only. classify_receptor_modality reads this workbook.

    python manage.py annotate_physiological_ligand_status
    python manage.py propose_ligand_classification
"""
from django.core.management.base import BaseCommand, CommandError

from protein.models import Protein
from tools.management.commands.annotate_physiological_ligand_status import (
    DATA_DIR, OUTPUT_XLSX as GTP_XLSX, FIRST_DATA_ROW, COL_UNIPROT, COL_SPECIES, COL_LIGAND, COL_STATUS, COL_POTENCY,
    COL_LAST, NEW_COLUMNS as GTP_COLUMNS, RECEPTOR_COLUMNS, RECEPTOR_SHEET, UNCLASSIFIED, UNCLASSIFIED_FILL,
    PRINCIPAL, SECONDARY, PROPOSED, SPECIES_SUFFIX_RE, norm_name, plain)

from collections import Counter, OrderedDict, defaultdict
from copy import copy
import os
import re

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

OUTPUT_XLSX = 'Ligand_classification.xlsx'

PROPOSED_PRINCIPAL = 'Proposed principal'
PROPOSED_SECONDARY = 'Proposed secondary'
HUMAN = 'Human'

COL_IUPHAR = 4
COL_TARGET = COL_LAST + 1
COL_BASIS = COL_LAST + len(GTP_COLUMNS) + 1
PROPOSAL_FILL = PatternFill('solid', fgColor='E2EFDA')

MIN_NAME_MATCH = 3
# Applied to ligand and receptor names alike (after match_key): GtP spellings mapped to the family's, and
# receptor abbreviations spelled out
NAME_ALIASES = [
    (re.compile(r'^srif\b'), 'somatostatin'),
    (re.compile(r'^corticotrophin releasing hormone'), 'corticotropin releasing factor'),
    (re.compile(r'^glp ([12])\b'), r'glucagon like peptide \1'),
    (re.compile(r'^at([12])\b'), r'angiotensin ii type \1'),
    (re.compile(r'^ut\b'), 'urotensin ii'),
]
DISPLAY_SUFFIX_RE = re.compile(SPECIES_SUFFIX_RE.pattern, re.IGNORECASE)
NAME_SUFFIX_RE = re.compile(r'\s+(?:receptors?|family)(?:\s+\d+[a-z]?)?$')

RULE_NAME = 'Name'
RULE_FAMILY = 'Family consensus'
RULE_POTENCY = 'Potency rank'

RECEPTOR_HEADERS = [
    ('GtP\ntarget ID', 10),
    ('Receptor\nstatus', 18),
    ('Principal ligand(s)', 40),
    ('GtP proposed ligand(s)', 30),
    ('Proposed principal(s)', 40),
    ('Proposal basis', 60),
    ('Secondary ligand(s)', 50),
    ('Proposed secondary(s)', 50),
    ('Unclassified ligand(s)', 40),
    ('Ligands', 9),
]
RECEPTOR_GROUPS = [PRINCIPAL, PROPOSED, PROPOSED_PRINCIPAL, None, SECONDARY, PROPOSED_SECONDARY, UNCLASSIFIED]


def match_key(name):
    """Lower-case name with hyphens as spaces, aliases applied."""
    key = re.sub(r'[\s\-]+', ' ', plain(name).lower()).strip()
    for pattern, alias in NAME_ALIASES:
        key = pattern.sub(alias, key)
    return key


def name_stem(name):
    """'Neuromedin-U receptor 1' -> 'neuromedin u', 'Glucagon receptor family' -> 'glucagon'."""
    stem, previous = match_key(name), None
    while stem != previous:
        previous, stem = stem, NAME_SUFFIX_RE.sub('', stem)
    return stem


def name_matches(candidates, receptor_names):
    """Candidate keys named by a receptor/family name, best (longest) match only, with the matching name.

    A ligand named in full beats one that only starts with the name ("urotensin II" over "urotensin II-related
    peptide" for "UT receptor" = "urotensin II receptor").
    """
    hits = {}
    for key in candidates:
        ligand = match_key(key)
        for name in receptor_names:
            text, stem = match_key(name), name_stem(name)
            score = 0
            if len(ligand) >= MIN_NAME_MATCH and \
                    re.search(r'(?<![a-z0-9])%s(?![a-z])' % re.escape(ligand), text):
                score = len(ligand) + 0.5
            if len(stem) >= MIN_NAME_MATCH and re.match(r'%s(?![a-z])' % re.escape(stem), ligand):
                score = max(score, len(stem))
            if score > hits.get(key, (0, None))[0]:
                hits[key] = (score, name)
    if not hits:
        return {}
    best = max(score for score, _ in hits.values())
    return {key: name for key, (score, name) in hits.items() if score == best}


def potency_rank(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class Command(BaseCommand):

    help = "Propose Principal/Secondary ligands for the receptors GtP leaves without a principal"

    def add_arguments(self, parser):
        parser.add_argument('--input', default=os.path.join(DATA_DIR, GTP_XLSX),
                            help='Workbook from annotate_physiological_ligand_status (default: %(default)s)')
        parser.add_argument('--output', default=os.path.join(DATA_DIR, OUTPUT_XLSX),
                            help='Proposal workbook (default: %(default)s)')

    def handle(self, *args, **options):
        if os.path.abspath(options['input']) == os.path.abspath(options['output']):
            raise CommandError('--output must differ from --input')

        workbook = openpyxl.load_workbook(options['input'])
        sheet = workbook.worksheets[0]
        receptors = self.read_receptors(sheet)
        stats = self.propose(sheet, receptors)
        if RECEPTOR_SHEET in workbook.sheetnames:
            del workbook[RECEPTOR_SHEET]
        counts = self.write_receptor_sheet(workbook, sheet, receptors)
        workbook.save(options['output'])

        self.stdout.write("Wrote %s" % options['output'])
        self.stdout.write("Rows labelled:")
        for (label, rule), count in sorted(stats.items()):
            self.stdout.write("  %-20s %-34s %5d" % (label, rule, count))
        self.stdout.write("Human receptors: %s" % ', '.join('%s %d' % item for item in counts.items()))
        for key, receptor in receptors.items():
            if key[RECEPTOR_COLUMNS - 1] == HUMAN and receptor['proposal']:
                self.stdout.write("  %-7s %-16s %s" % (key[2], receptor['proposal']['rule'], '; '.join(
                    receptor['ligands'][lkey]['name'] for lkey in receptor['proposal']['keys'])))

    # ------------------------------------------------------------------ receptors

    @staticmethod
    def read_receptors(sheet):
        """Receptor (Class .. Species) -> rows and ligands (pooled by name without the species suffix)."""
        receptors = OrderedDict()
        for row in range(FIRST_DATA_ROW, sheet.max_row + 1):
            uniprot = sheet.cell(row, COL_UNIPROT).value
            if not uniprot:
                continue
            key = tuple(sheet.cell(row, col).value for col in range(1, RECEPTOR_COLUMNS + 1))
            receptor = receptors.setdefault(key, {
                'uniprot': str(uniprot), 'species': str(sheet.cell(row, COL_SPECIES).value or ''),
                'first_row': row, 'target_id': sheet.cell(row, COL_TARGET).value, 'iuphar': set(),
                'rows': [], 'ligands': OrderedDict(), 'proposal': None})
            if sheet.cell(row, COL_IUPHAR).value:
                receptor['iuphar'].add(plain(sheet.cell(row, COL_IUPHAR).value))
            name = sheet.cell(row, COL_LIGAND).value
            ligand_key = SPECIES_SUFFIX_RE.sub('', norm_name(name))
            ligand = receptor['ligands'].setdefault(ligand_key, {
                'name': DISPLAY_SUFFIX_RE.sub('', plain(name)), 'statuses': set(), 'rank': None})
            status = sheet.cell(row, COL_STATUS).value or None
            ligand['statuses'].add(status)
            rank = potency_rank(sheet.cell(row, COL_POTENCY).value)
            if rank is not None and (ligand['rank'] is None or rank < ligand['rank']):
                ligand['rank'] = rank
            receptor['rows'].append((row, ligand_key, status))
        return receptors

    @staticmethod
    def protein_info():
        """entry_name -> (receptor name, family name, family id); family id -> human entry names."""
        info, family_members = {}, defaultdict(list)
        for entry_name, name, parent_id, parent_name, species in Protein.objects.filter(
                sequence_type__slug='wt', family__parent__isnull=False).values_list(
                'entry_name', 'name', 'family__parent_id', 'family__parent__name', 'species__common_name'):
            info[entry_name] = (plain(name), plain(parent_name), parent_id)
            if species == HUMAN:
                family_members[parent_id].append(entry_name)
        return info, family_members

    def propose(self, sheet, receptors):
        info, family_members = self.protein_info()
        principals_of = {}    # human entry name -> its Principal ligand keys
        for receptor in receptors.values():
            if receptor['species'] == HUMAN:
                principals_of['%s_human' % receptor['uniprot'].lower()] = {
                    lkey for lkey, ligand in receptor['ligands'].items() if PRINCIPAL in ligand['statuses']}

        header = sheet.cell(1, COL_BASIS, 'PROPOSAL')
        header._style = copy(sheet.cell(1, COL_LAST + 1)._style)
        cell = sheet.cell(2, COL_BASIS, 'Proposal\nbasis')
        cell._style = copy(sheet.cell(2, COL_LAST + 1)._style)
        sheet.column_dimensions[get_column_letter(COL_BASIS)].width = 60

        stats = Counter()
        for receptor in receptors.values():
            ligands = receptor['ligands']
            entry_name = '%s_%s' % (receptor['uniprot'].lower(), receptor['species'].lower())
            receptor_name, family_name, family_id = info.get(entry_name, ('', '', None))
            labelled = {lkey for lkey, ligand in ligands.items() if ligand['statuses'] & {PRINCIPAL, PROPOSED}}
            if labelled:
                principals = [lkey for lkey in labelled if PRINCIPAL in ligands[lkey]['statuses']]
                anchor = ', '.join(ligands[lkey]['name'] for lkey in (principals or labelled))
                labels = {}
                for row, lkey, status in receptor['rows']:
                    if status is None:
                        labels[row] = (PROPOSED_PRINCIPAL, 'Same ligand as GtP principal (other species row)') \
                            if lkey in principals else \
                            (PROPOSED_SECONDARY, 'Receptor has GtP %s %s' % (
                                'principal' if principals else 'proposed', anchor))
            else:
                candidates = OrderedDict((lkey, ligand) for lkey, ligand in ligands.items()
                                         if None in ligand['statuses'])
                if not candidates:
                    continue
                name_tiers = [[receptor_name] + sorted(receptor['iuphar']), [family_name]]
                receptor['proposal'] = self.choose(
                    candidates, name_tiers, family_id, family_members, principals_of, entry_name)
                chosen = receptor['proposal']['keys']
                chosen_names = ', '.join(ligands[lkey]['name'] for lkey in chosen)
                labels = {}
                for row, lkey, status in receptor['rows']:
                    if status is None:
                        labels[row] = (PROPOSED_PRINCIPAL, receptor['proposal']['basis']) if lkey in chosen else \
                            (PROPOSED_SECONDARY, 'Proposed principal: %s' % chosen_names)

            for row, (label, basis) in labels.items():
                status_cell = sheet.cell(row, COL_STATUS, label)
                status_cell.fill = PROPOSAL_FILL
                cell = sheet.cell(row, COL_BASIS, basis)
                cell._style = copy(sheet.cell(row, COL_LAST)._style)
                rule = receptor['proposal']['rule'] if receptor['proposal'] else \
                    'species variant' if label == PROPOSED_PRINCIPAL else 'receptor has GtP label'
                stats[(label, rule)] += 1
            for row, lkey, status in receptor['rows']:
                if row in labels:
                    ligands[lkey]['statuses'].discard(None)
                    ligands[lkey]['statuses'].add(labels[row][0])
        return stats

    @staticmethod
    def choose(candidates, name_tiers, family_id, family_members, principals_of, entry_name):
        """{'keys', 'rule', 'basis'} for the proposed principal(s) of a receptor without a GtP principal.

        name_tiers: the receptor's own names first, the family name only when those name no ligand.
        """
        for names in name_tiers:
            named = name_matches(candidates, [name for name in names if name])
            if named:
                source = sorted(set(named.values()))
                return {'keys': list(named), 'rule': RULE_NAME,
                        'basis': 'Name: "%s" names %s' % ('" / "'.join(source),
                                                           ', '.join(candidates[lkey]['name'] for lkey in named))}

        siblings = [principals_of[member] for member in family_members.get(family_id, [])
                    if member != entry_name and principals_of.get(member)]
        if siblings:
            votes = Counter(lkey for principals in siblings for lkey in principals)
            consensus = [lkey for lkey in candidates if votes[lkey] * 2 > len(siblings)]
            if consensus:
                return {'keys': consensus, 'rule': RULE_FAMILY,
                        'basis': 'Family consensus: Principal at %s of %d family receptors with a principal' % (
                            ', '.join('%d (%s)' % (votes[lkey], candidates[lkey]['name']) for lkey in consensus),
                            len(siblings))}

        ranks = [ligand['rank'] for ligand in candidates.values() if ligand['rank'] is not None]
        if ranks:
            best = min(ranks)
            return {'keys': [lkey for lkey, ligand in candidates.items() if ligand['rank'] == best],
                    'rule': RULE_POTENCY, 'basis': 'Potency rank: best GPCRdb potency rank (%d)' % best}
        return {'keys': list(candidates), 'rule': RULE_POTENCY,
                'basis': 'Potency rank: no rank available, all candidate ligands'}

    # ------------------------------------------------------------------ receptor sheet

    def write_receptor_sheet(self, workbook, sheet, receptors):
        """One row per human receptor with its ligands grouped by label; returns the receptor status counts."""
        human = [(key, receptor) for key, receptor in receptors.items() if receptor['species'] == HUMAN]
        counts = OrderedDict((status, 0) for status in (PRINCIPAL, PROPOSED, PROPOSED_PRINCIPAL, UNCLASSIFIED))
        for key, receptor in human:
            groups = defaultdict(list)
            for lkey, ligand in receptor['ligands'].items():
                for status in ligand['statuses']:
                    name = ligand['name']
                    if status is None and ligand['rank'] is not None:
                        name = '%s (potency rank %d)' % (name, ligand['rank'])
                    groups[status or UNCLASSIFIED].append(name)
            receptor['groups'] = groups
            receptor['status'] = next((status for status in (PRINCIPAL, PROPOSED, PROPOSED_PRINCIPAL)
                                       if groups[status]), UNCLASSIFIED)
            counts[receptor['status']] += 1

        out = workbook.create_sheet(RECEPTOR_SHEET)
        header_style = sheet.cell(2, COL_LAST)._style
        bold = Font(bold=True)
        left = Alignment(horizontal='left', vertical='top')

        out.cell(1, 1, 'Human receptors')._style = copy(header_style)
        out.cell(1, 2, 'Count')._style = copy(header_style)
        for row, (status, count) in enumerate(list(counts.items()) + [('Total', len(human))], start=2):
            out.cell(row, 1, status).font = bold
            out.cell(row, 2, count)
        out.cell(2 + list(counts).index(UNCLASSIFIED), 1).fill = UNCLASSIFIED_FILL
        legend_row = 2 + len(counts) + 2
        out.cell(legend_row, 1, "Receptor status: the first of Principal (GtP), Proposed (GtP), Proposed principal "
                                "(curation proposal) the receptor has; Unclassified = none of them.")
        out.cell(legend_row + 1, 1, "Sheet '%s', GtP classification: yellow = stated by GtP (changed), blue = from a "
                                    "GtP potency statement, green = curation proposal (see Proposal basis); Modality: "
                                    "purple = Peptide/Protein changed by the common-name rule." % sheet.title)

        header_row = legend_row + 3
        headers = [(sheet.cell(2, col).value, sheet.column_dimensions[get_column_letter(col)].width or 12)
                   for col in range(1, RECEPTOR_COLUMNS + 1)] + RECEPTOR_HEADERS
        for col, (title, width) in enumerate(headers, start=1):
            out.cell(header_row, col, title)._style = copy(header_style)
            out.column_dimensions[get_column_letter(col)].width = width
        out.column_dimensions['A'].width = 30

        for row, (key, receptor) in enumerate(human, start=header_row + 1):
            for col in range(1, RECEPTOR_COLUMNS + 1):
                out.cell(row, col, key[col - 1])._style = copy(sheet.cell(receptor['first_row'], col)._style)
            groups = receptor['groups']
            basis = receptor['proposal']['basis'] if receptor['proposal'] else None
            values = [receptor['target_id'], receptor['status']] + \
                     [basis if status is None else '; '.join(groups[status]) or None for status in RECEPTOR_GROUPS] + \
                     [len(receptor['ligands'])]
            for col, value in enumerate(values, start=RECEPTOR_COLUMNS + 1):
                out.cell(row, col, value).alignment = left
            status_cell = out.cell(row, RECEPTOR_COLUMNS + 2)
            if receptor['status'] == UNCLASSIFIED:
                status_cell.fill = UNCLASSIFIED_FILL
            elif receptor['status'] == PROPOSED_PRINCIPAL:
                status_cell.fill = PROPOSAL_FILL

        out.auto_filter.ref = 'A%d:%s%d' % (header_row, get_column_letter(len(headers)), header_row + len(human))
        out.freeze_panes = out.cell(header_row + 1, 1)
        return counts
