from django.conf import settings
from django.utils.text import slugify
from django.core.cache import cache

import os
import sys
import yaml
import time
import logging
import re
import urllib
from urllib.parse import quote
from urllib.request import urlopen
from urllib.error import HTTPError
import hashlib
import json
import gzip
from io import BytesIO
from string import Template
from Bio import Entrez, Medline
import xml.etree.ElementTree as etree
from http.client import HTTPException


def test_model_updates(model, master_data, initialize=False, check=False, rerun=False):
    #check if the input is a single model or a list of models
    #and initialize the dictionary with the model name and length (set to 0)
    print(f'Initialize: {initialize}')
    if initialize:
        print('Initializing master database of built models')
        if len(model) == 1:
            if model[0] not in master_data.keys():
                master_data[model[0]] = model[0].objects.all().count()
        else:
            for table in model:
                if table not in master_data.keys():
                    master_data[table] = table.objects.all().count()
    if check:
        print(f'CHECKing... check = {check}')
        CHECK = True
        if len(model) == 1:
            OG = master_data[model[0]]
            NEW = model[0].objects.all().count()
            if NEW != OG:
                master_data[model[0]] = NEW
                print('Changes have been applied to the model: ' + str(model[0]))
                CHECK = True
                if NEW > OG:
                    diff = NEW - OG
                    print(str(diff) + ' records have been added')
                else:
                    diff = OG - NEW
                    print(str(diff) + ' records have been removed')
        else:
            for table in model:
                OG = master_data[table]
                NEW = table.objects.all().count()
                if NEW != OG:
                    master_data[table] = NEW
                    print('Changes have been applied to the model: ' + str(table))
                    CHECK = True
                    if NEW > OG:
                        diff = NEW - OG
                        print(str(diff) + ' records have been added')
                    else:
                        diff = OG - NEW
                        print(str(diff) + ' records have been removed')

        if not CHECK:
            print('EXITING: No module have been updated. Probably some error?')
            sys.exit()

    if rerun:
        print('Checking if changes have happened during a build rerun')
        if len(model) == 1:
            OG = master_data[model[0]]
            NEW = model[0].objects.all().count()
            if NEW != OG:
                print('Something had changed in the record of the model ' + str(model[0]))
                print('Previous number of records: ' +str(OG))
                print('New number of records: ' +str(NEW))
        else:
            for table in model:
                OG = master_data[table]
                NEW = table.objects.all().count()
                if NEW != OG:
                    print('Something had changed in the record of the model ' + str(table))
                    print('Previous number of records: ' +str(OG))
                    print('New number of records: ' +str(NEW))


def save_to_cache(path, file_id, data):
    create_cache_dirs(path)
    cache_dir_path = os.sep.join([settings.BUILD_CACHE_DIR] + path)
    cache_file_path = os.sep.join([cache_dir_path, file_id + '.yaml'])
    with open(cache_file_path, 'w') as cache_file:
        yaml.dump(data, cache_file, default_flow_style=False)

def fetch_from_cache(path, file_id):
    cache_dir_path = os.sep.join([settings.BUILD_CACHE_DIR] + path)
    cache_file_path = os.sep.join([cache_dir_path, file_id + '.yaml'])
    if os.path.isfile(cache_file_path):
        try:
            with open(cache_file_path) as cache_file:
                return yaml.load(cache_file, Loader=yaml.FullLoader)
        except TypeError as msg:
            print('WARNING: cannot properly open {} with TypeError: {}'.format(cache_file_path, msg))
            return None
    else:
        return None

def create_cache_dirs(path):
    cache_file_path = os.sep.join([settings.BUILD_CACHE_DIR] + path)
    os.makedirs(cache_file_path, exist_ok=True)
    intermediate_path = settings.BUILD_CACHE_DIR
    for directory in path:
        intermediate_path = os.sep.join([intermediate_path, directory])
        os.chmod(intermediate_path, 0o777)

def fetch_from_web_api(url, index, cache_dir=False, xml=False, raw=False):
    logger = logging.getLogger('build')

    # slugify the index for the cache filename (some indices have symbols not allowed in file names (e.g. /))
    index_slug= slugify(index)
    cache_file_path = '{}/{}'.format('/'.join(cache_dir), index_slug)

    # try fetching from cache
    if cache_dir:
        # d = cache.get(cache_file_path)
        d = fetch_from_cache(cache_dir, index_slug)
        if d:
            logger.info('Fetched {} from cache'.format(cache_file_path))
            return d

    # if nothing is found in the cache, use the web API
    full_url = Template(url).substitute(index=quote(str(index), safe=''))
    logger.info('Fetching {}'.format(full_url))
    tries = 0
    max_tries = 5
    while tries < max_tries:
        if tries > 0:
            logger.warning('Failed fetching {}, retrying'.format(full_url))

        try:
            req = urlopen(full_url)
            if full_url[-2:]=='gz' and xml:
                try:
                    buf = BytesIO( req.read())
                    f = gzip.GzipFile(fileobj=buf)
                    data = f.read()
                    d = etree.fromstring(data)
                except:
                    return False
            elif xml:
                try:
                    d = etree.fromstring(req.read().decode('UTF-8'))
                except:
                    return False
            elif raw:
                try:
                    d = req.read().decode('UTF-8')
                except:
                    return False
            else:
                d = json.loads(req.read().decode('UTF-8'))
        except HTTPError as e:
            tries += 1
            if e.code == 404:
                logger.warning('Failed fetching {}, 404 - does not exist'.format(full_url))
                tries = max_tries #skip more tries
                return False
            elif e.code == 400:
                logger.warning('Failed fetching {}, 400 - does not exist'.format(full_url))
                tries = max_tries #skip more tries
                return False
            else:
                time.sleep(2)
        except HTTPException as e:
            tries += 1
            time.sleep(2)
        except urllib.error.URLError as e:
            # Catches 101 network is unreachable -- I think it's auto limiting feature
            tries +=1
            time.sleep(2)
        else:
            # save to cache
            if cache_dir:
                save_to_cache(cache_dir, index_slug, d)
                # cache.set(cache_file_path, d, 60*60*24*7) #7 days
                logger.info('Saved entry for {} in cache'.format(cache_file_path))
            return d

    # give up if the lookup fails 5 times
    logger.error('Failed fetching {} {} times, giving up'.format(full_url, max_tries))
    return False

def get_or_create_url_cache(url, validity = -1):
    # Hash the url
    url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()

    # Check the cache if exists
    valid = True
    urlcache_dir = os.sep.join([settings.DATA_DIR, 'common_data','url_cache'])
    cache_file = os.sep.join([urlcache_dir, url_hash])
    if os.path.isfile(cache_file):
        # Check if the data is still valid if set
        if validity > 0:
            seconds = int(time.time()) - int(os.path.getctime(cache_file))
            valid = seconds < validity

        # return cached filepath when still valid
        if valid:
            return cache_file

    # Data not yet exists => collect data and store in cache
    response = urlopen_with_retry(url)
    if response:
        # Write new results to file cache
        with open(cache_file, 'wb') as f:
            f.write(response.read())
            f.close()
        return cache_file
    elif not valid:
        print("WARNING: file cache not valid anymore - but new version could not be downloaded")
        print("WARNING:", url)
        return cache_file

    # Data could not be obtained
    return False

def fetch_from_entrez(index, cache_dir=''):
    logger = logging.getLogger('build')

    # slugify the index for the cache filename (some indices have symbols not allowed in file names (e.g. /))
    index_slug= slugify(index)
    cache_file_path = '{}/{}'.format('/'.join(cache_dir), index_slug)

    # try fetching from cache
    if cache_dir:
        d = fetch_from_cache(cache_dir, index_slug)
        if d:
            logger.info('Fetched {} from cache'.format(cache_file_path))
            return d

    # if nothing is found in the cache, use the web API
    logger.info('Fetching {} from Entrez'.format(index))
    tries = 0
    max_tries = 2
    while tries < max_tries:
        if tries > 0:
            logger.warning('Failed fetching pubmed via Entrez {}, retrying'.format(str(index)))

        try:
            Entrez.email = 'info@gpcrdb.org'
            handle = Entrez.efetch(
                db="pubmed",
                id=str(index),
                rettype="medline",
                retmode="text"
            )
        except:
            tries += 1
            time.sleep(0.2)
        else:
            d = Medline.read(handle)

            # save to cache
            save_to_cache(cache_dir, index_slug, d)
            logger.info('Saved entry for {} in cache'.format(cache_file_path))
            return d

def urlopen_with_retry(url, data = None, retries = 5, sleeptime = 5):
    logger = logging.getLogger('build')

    # Try to request data from URL  for few seconds and retry X times
    for retry in range(retries):
        if retry > 0:
            time.sleep(sleeptime)

        response = None
        try:
            response = urlopen(url, data) #nosec
        except urllib.error.URLError as e:
            logger.warning(f'URLopen error (retry {retry} out of {retries}) {e}')

        if response != None and 200 <= response.code < 300:
            return response
        elif retry == retries:
            return False

def parse_uniprot_file(accession, path_to_file=False, logger=None, local_uniprot_dir=None, remote_uniprot_dir='http://www.uniprot.org/uniprot/', excel_sequences=None, entrez_lookup=None):

    '''
        Parse a Uniprot file with the given accession number, either from a local file or from the Uniprot website.
        The function returns a dictionary with the parsed information, including gene names, protein names,
        accessions, species information, and sequence.
        Parameters:
        - accession: The Uniprot accession number for the protein to be parsed.
        - path_to_file: Path to a local Uniprot file.
        - logger: A logging object for logging information and warnings.
        - local_uniprot_dir: The local directory where Uniprot files are stored
        - remote_uniprot_dir: The remote directory (URL) where Uniprot files can be accessed.
        - excel_sequences: A dictionary containing GPCR protein sequences
        - entrez_lookup: A table for looking up Entrez gene IDs based on gene symbol and species-name/taxon-id
        Returns:
        - up: A dictionary containing the parsed information from the Uniprot file
    '''

    # Define regex patterns for parsing gene names and synonyms from the Uniprot file
    name_tag_pattern = re.compile(r'GN   Name=([A-Za-z0-9.)(_-]+)')
    synonym_tag_pattern = re.compile(r'GN   Synonyms=(?<!..:)((?:[A-Za-z0-9.)(_-]+(?:,\s*)?)+)')
    gene_name_only_pattern = re.compile(r'GN   ([A-Za-z0-9.)(_-]+);')
    trailing_name_pattern = re.compile(r'GN   (?:(?:\{?|ECO).+\}), ([A-Za-z0-9.)(_-]+)')
    leading_name_pattern = re.compile(r'GN   ([A-Za-z0-9.)(_-]+) (?:\{.+\}?)')
    gene_pattern_list = [name_tag_pattern, gene_name_only_pattern, trailing_name_pattern, leading_name_pattern]

    filename = accession + '.txt'
    if not path_to_file:
        local_file_path = os.sep.join([local_uniprot_dir, filename])
    else:
        local_file_path = path_to_file
    remote_file_path = remote_uniprot_dir + filename

    up = {
        'genes': [],
        'names': [],
        'accessions': [],
        'structures': [],
        'entrez_geneids': []
    }

    read_sequence = False
    remote = False

    # record whether organism has been read
    os_read = False

    # should local file be written?
    local_file = False

    try:
        if os.path.isfile(local_file_path):
            uf = open(local_file_path, 'r')
            if logger is not None: logger.info('Reading local file ' + local_file_path)
        else:
            uf = urlopen_with_retry(remote_file_path)
            if uf == False:
                if logger is not None: logger.warning(f'ERROR: UNIPROT file could not be obtained for {accession}')
                return False

            remote = True
            if logger is not None: logger.info('Reading remote file ' + remote_file_path)
            local_file = open(local_file_path, 'w')

        for raw_line in uf:
            # line format
            if remote:
                line = raw_line.decode('UTF-8')
            else:
                line = raw_line

            # write to local file if appropriate
            if local_file:
                local_file.write(line)

            # end of file
            if line.startswith('//'):
                break

            # entry name and review status
            if line.startswith('ID'):
                split_id_line = line.split()
                up['entry_name'] = split_id_line[1].lower()
                review_status = split_id_line[2].strip(';')
                if review_status == 'Unreviewed':
                    up['source'] = 'TREMBL'
                elif review_status == 'Reviewed':
                    up['source'] = 'SWISSPROT'

            # species
            elif line.startswith('OS') and not os_read:
                species_full = line[2:].strip().strip('.')
                species_split = species_full.split('(')
                up['species_latin_name'] = species_split[0].strip()
                if len(species_split) > 1:
                    up['species_common_name'] = species_split[1].strip().strip(')')
                else:
                    up['species_common_name'] = up['species_latin_name']
                os_read = True

            # accessions
            elif line.startswith('AC'):
                sline = line.split()
                for ac in sline:
                    up['accessions'].append(ac.strip(';'))

            # names
            elif line.startswith('DE'):
                split_de_line = line.split('=')
                if len(split_de_line) > 1:
                    split_segment = split_de_line[1].split('{')
                    up['names'].append(split_segment[0].strip().strip(';'))

            # genes
            elif line.startswith('GN'):
                for p in gene_pattern_list:
                    m = p.search(line)
                    if m:
                        for gene_name in m.groups():
                            up['genes'].append(gene_name)
                m = synonym_tag_pattern.search(line)
                if m:
                    for gene_name in re.findall(r'[A-Za-z0-9.)(_-]+', m.group(1)):
                        if gene_name not in up['genes']:
                            up['genes'].append(gene_name)

            # # structures
            # elif line.startswith('DR') and 'PDB;' in line:
            #     split_gn_line = line.split(';')
            #     up['structures'].append([split_gn_line[1].lstrip(), split_gn_line[3].lstrip().split(" A")[0]])


            # NCBI IDs
            elif line.startswith('DR'):
                line_tagless = line[5:]
                split_dr_line = line_tagless.split(';')
                if split_dr_line[0].strip() == 'GeneID':
                    eid = split_dr_line[1].strip()
                    if (eid.isdecimal()):
                        up['entrez_geneids'].append(eid)
                    else:
                        if logger is not None: logger.info('Encountered non-numeric entrez gene id :' + eid + ' for protein ' + up['entry_name'] + ', skipping')

            # sequence
            elif line.startswith('SQ'):
                read_sequence = True
                up['sequence'] = ''
            elif read_sequence == True:
                up['sequence'] += line.strip().replace(' ', '')

        # close the Uniprot file
        uf.close()

        if entrez_lookup is not None:
            ## Handle edge cases where gene name is not provided but entrez gene id is provided, by looking up the gene symbol based on the species and entrez gene id in the lookup table
            if len(up['entrez_geneids']) > 0 and len(up['genes']) == 0:
                try:
                    reverse_lookup = {v: k for k, v in entrez_lookup["by_species_name"][up['species_latin_name']].items()}
                    try:
                        up['genes'] = [reverse_lookup[up['entrez_geneids'][0]]]
                    except KeyError:
                        if logger is not None: logger.warning('Could not find entrez gene id ' + up['entrez_geneids'][0] + ' in the lookup table for species ' + up['species_latin_name'])
                except KeyError:
                    if logger is not None: logger.warning('Could not find species ' + up['species_latin_name'] + ' in the lookup table')

            # filter genes based on entrez lookup (to remove non-official gene symbols)
            clean_genes = [gene for gene in up['genes'] if gene in entrez_lookup["by_species_name"].get(up['species_latin_name'], {})]
            # If we still have genes left after filtering, update the genes list to the filtered list. If not, keep the original list.
            if len(clean_genes) > 0:
                up['genes'] = clean_genes

        if excel_sequences is not None:
            try:
                up['sequence'] = excel_sequences[up['entry_name']]['Sequence']
            except KeyError:
                pass

    except:
        return False

    # close the local file if appropriate
    if local_file:
        local_file.close()

    return up

def build_entrezgeneid_lookup_dict(entrez_lookup_file, logger, keep_only_lowest_id=True):

        '''
        This function builds a lookup dictionary for entrez gene ids based on the provided file.
        The dictionary is structured to allow lookup by species name and gene
        symbol, as well as by taxon id and gene symbol.
        If keep_only_lowest_id is True, only the lowest (earliest) entrez gene id
        will be kept for each gene symbol and species/taxon id combination.
        Parameters:
        - entrez_lookup_file: A path to a plain-text file containing the mapping of gene symbols, taxon ids,
          entrez gene ids, and species names. The file is expected to have a header line and be tab-delimited with
          the following columns: gene_symbol, taxon_id, entrez_gene_id, species_name.
        - logger: A logging object for logging information and warnings.
        - keep_only_lowest_id: A boolean flag indicating whether to keep only the lowest (earliest) entrez gene id. Default is True.
        Returns:
        - entrez_lookup_dict: A dictionary containing the lookup information for entrez gene ids.
        '''

        entrez_lookup_dict = dict()
        entrez_lookup_dict["by_species_name"] = dict()
        entrez_lookup_dict["by_taxon_id"] = dict()

        with open(entrez_lookup_file, 'r') as f:
            for line in f:
                split_line = line.strip().split('\t')
                if len(split_line) == 4:
                    if split_line[0] != 'gene_symbol': #skip header line
                        gene_symbol, taxon_id, entrez_gene_id, species_name = split_line

                        #Lookup by species
                        try:
                            by_species_ref = entrez_lookup_dict["by_species_name"][species_name]
                        except KeyError:
                            by_species_ref = dict()
                            entrez_lookup_dict["by_species_name"][species_name] = by_species_ref

                        try:
                            gene_array_ref = by_species_ref[gene_symbol]
                        except KeyError:
                            gene_array_ref = []
                            by_species_ref[gene_symbol] = gene_array_ref

                        gene_array_ref.append(entrez_gene_id)

                        #Lookup by taxon id
                        try:
                            by_taxon_ref = entrez_lookup_dict["by_taxon_id"][taxon_id]
                        except KeyError:
                            by_taxon_ref = dict()
                            entrez_lookup_dict["by_taxon_id"][taxon_id] = by_taxon_ref

                        try:
                            gene_array_ref = by_taxon_ref[gene_symbol]
                        except KeyError:
                            gene_array_ref = []
                            by_taxon_ref[gene_symbol] = gene_array_ref

                        gene_array_ref.append(entrez_gene_id)
                else:
                    logger.error('Unexpected format in entrez gene id lookup file for line: ' + line)

        if keep_only_lowest_id:
            for species in entrez_lookup_dict["by_species_name"]:
                for gene_symbol in entrez_lookup_dict["by_species_name"][species]:
                    entrez_lookup_dict["by_species_name"][species][gene_symbol] = min(entrez_lookup_dict["by_species_name"][species][gene_symbol])

            for taxon_id in entrez_lookup_dict["by_taxon_id"]:
                for gene_symbol in entrez_lookup_dict["by_taxon_id"][taxon_id]:
                    entrez_lookup_dict["by_taxon_id"][taxon_id][gene_symbol] = min(entrez_lookup_dict["by_taxon_id"][taxon_id][gene_symbol])

        return entrez_lookup_dict


def select_entrez_id(current_gene_index, uniprot_dict, entrez_lookup):

        '''
        Select an entrez id from either the set parsed from the uniprot file or,
        if entrez gene id is not provided by uniprot file, from a lookup dictionary
        using the species and gene symbol.
        Paramters:
            - current_gene_index: The index/position of the gene within the list of genes present in the uniprot file
            - uniprot_dict: The dictionary containing the parsed information from the uniprot file.
            - entrez_lookup: A lookup dictionary containing entrez gene ids, indexed by species name and gene symbol.
        Returns:
            - entrez_geneid_use: The selected entrez gene id, or None if no suitable id is available.
        '''

        entrez_geneid_use = None

        if 'entrez_geneids' in uniprot_dict and len(uniprot_dict['entrez_geneids']) > 0:
            if len(uniprot_dict['genes']) == len(uniprot_dict['entrez_geneids']):
                entrez_geneid_use = uniprot_dict['entrez_geneids'][current_gene_index] #take index matched id
            else:
                if len(uniprot_dict['entrez_geneids']) > 0:
                    entrez_geneid_use = sorted(uniprot_dict['entrez_geneids'])[0] #take the lowest (earliest) id
        elif "genes" in uniprot_dict: #gene name present but no entrez gene id in uniprot file
            entrez_list = []
            for gene in uniprot_dict["genes"]:
                #lookup entrez gene id using the gene symbol and species name in the lookup file
                if uniprot_dict['species_latin_name'] in entrez_lookup["by_species_name"]:
                    if gene in entrez_lookup["by_species_name"][uniprot_dict['species_latin_name']]:
                        entrez_list.append(entrez_lookup["by_species_name"][uniprot_dict['species_latin_name']][gene])
            entrez_geneid_use = entrez_list[0] if len(entrez_list) > 0 else None #Prioritise the first entry as this hopefully corresponds to the official symbol match

        return entrez_geneid_use