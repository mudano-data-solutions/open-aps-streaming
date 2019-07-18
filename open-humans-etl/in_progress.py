

"""

    1. Extract records from OH

        Options:
            - download_all function
            - download_specific, based on ingestion ETL table

    2. Extract compressed .gz files

    3. For each file:

        a. Slice .json file based on index in ETL table

        b. If new records after slice, upload to db

        c. Update/add row in ETL table showing the most recent completed slice

"""

from models import Treatment, Entry, Profile, DeviceStatus, DeviceStatusMetric
from helpers import get_openaps_con, get_master_token
from utils.database import Database, Psycopg2Error
from utils.stream_ingester import StreamIngester
from datetime import datetime, timedelta
from json.decoder import JSONDecodeError
from oh_wrapper import OHWrapper, OHError
from utils.logger import Logger
import traceback
import json
import sys
import os


FILES_DIRECTORY = '/temp_files'
LAST_RUN = datetime.now() - timedelta(weeks=1)

ENTITY_MAPPER = {
    'treatment': {'object': Treatment, 'table': 'treatments'},
    'entries': {'object': Entry, 'table': 'entries'},
    'profile': {'object': Profile, 'table': 'profile'},
    'device': {'object': DeviceStatus, 'table': 'device_status'},
    'status_metrics': {'object': DeviceStatusMetric,'table': 'device_status_metrics'}
}



def add_user_to_etl_log(user_id):

    db.execute_query(""" INSERT INTO openaps.oh_etl_log
                         (openhumans_id, treatments_last_index, entries_last_index, profile_last_index, device_last_index)
                         VALUES
                         (%(openhumans_id)s, 0,0,0,0)
                         LIMIT 1
                         """, {'openhumans_id': user_id})


def update_user_index(user_id, entity, index):

    db.execute_query(""" UPDATE openaps.oh_etl_log
                         SET %(entity)s_last_index = %(new_index)s
                         WHERE openhumans_id = %(openhumans_id)s
                         ;""",
                         {'entity': entity, 'new_index': index, 'openhumans_id': user_id})


def get_user(openhumans_id):

    db_user = db.execute_query(""" SELECT * FROM openaps.oh_etl_log WHERE openhumans_id = %(openhumans_id)s """, {'openhumans_id': openhumans_id})

    if not db_user:

        add_user_to_etl_log(openhumans_id)
        db_user = db.execute_query(""" SELECT * FROM openaps.oh_etl_log WHERE openhumans_id = %(openhumans_id)s """, {'openhumans_id': openhumans_id})

    return db_user


def get_user_filepaths(user_id):

    openhumans_files = []

    for subdirs, dirs, files in os.walk(FILES_DIRECTORY + user_id):

        for file in files:

            if '.json' in file and file not in openhumans_files:

                openhumans_files.append(f'{subdirs}/{file}')

    return openhumans_files



def process_file_load(user_id, file, entity, slice_index):

    with open(file) as infile:

        lines = [{**json.loads(json_line), **{'user_id': user_id}} for json_line in infile[slice_index:]]

    ingest(lines, ENTITY_MAPPER[entity])
    update_user_index(user_id, entity, slice_index + len(lines))

    if entity == 'device':

        status_metrics = [{**device['openaps'], **{'device_status_id': device['id']}} for device in lines if 'openaps' in device]
        ingest(status_metrics, ENTITY_MAPPER['status_metrics'])
        update_user_index(user_id, entity, slice_index + len(lines))


def ingest(lod, lod_params):

    temp_list = []
    for item in lod:
        with lod_params['object'](item) as t:
            temp_list.append(vars(t))

    if temp_list:

        ingester.add_target(
            target_data=temp_list,
            output_schema='openaps',
            table_name=lod_params['table'],
            date_format='YYYY-MM-DD HH24:MI:SS'
        )


def main(output_directory):

    oh.get_all_records(output_directory)
    oh.extract_directory_files(output_directory)

    user_folders = [x for x in next(os.walk(FILES_DIRECTORY))[1]]
    for folder_id in user_folders:

        user_id = str(folder_id)

        user = get_user(user_id)
        user_files = get_user_filepaths(user_id)

        for file in user_files:

            for entity in ENTITY_MAPPER.keys():

                if entity in file:

                    last_index = user[entity + '_last_index']

                    try:
                        process_file_load(user_id, file, entity, last_index)

                    except (JSONDecodeError, TypeError):
                        logger.error(f'Incorrect json format found for user with ID {user_id} and file with name {file}. Traceback was: {traceback.format_exc()}')

                    except IndexError:
                        logger.error(f'Index out of sync for user with ID {user_id} and file with name {file}. Traceback was: {traceback.format_exc()}')

                    except Psycopg2Error:
                        logger.error(f'Insert error while working with ID {user_id} and entity device metrics: {traceback.format_exc()}')


if __name__ == '__main__':

    logger = Logger()

    try:
        db = Database(get_openaps_con())
        ingester = StreamIngester(get_openaps_con())

    except Psycopg2Error:
        logger.error(f'Error while establishing connection with DB: {traceback.format_exc()}')
        sys.exit(1)

    try:
        oh = OHWrapper(get_master_token())

    except OHError:
        logger.error(f'Error while authenticating with OpenHumans: {traceback.format_exc()}')

    try:
        main(FILES_DIRECTORY)

    except Psycopg2Error:
        logger.error(f'Error occurred while working with DB: {traceback.format_exc()}')

    except Exception:
        logger.error(f'Error occurred during ingestion: {traceback.format_exc()}')

    finally:
        sys.exit(1)
