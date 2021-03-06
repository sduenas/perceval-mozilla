# -*- coding: utf-8 -*-
#
# Copyright (C) 2015-2017 Bitergia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, 51 Franklin Street, Fifth Floor, Boston, MA 02110-1335, USA.
#
# Authors:
#     Valerio Cosentino <valcos@bitergia.com>
#

import json
import logging
import time

import requests

from grimoirelab.toolkit.datetime import (datetime_utcnow,
                                          datetime_to_utc,
                                          str_to_datetime)
from grimoirelab.toolkit.uris import urijoin

from ...backend import (Backend,
                        BackendCommand,
                        BackendCommandArgumentParser,
                        metadata)
from ...utils import DEFAULT_DATETIME

CRATES_URL = "https://crates.io/"
CRATES_API_URL = 'https://crates.io/api/v1/'

CRATES_CATEGORY = 'crates'
SUMMARY_CATEGORY = 'summary'

SLEEP_TIME = 300

logger = logging.getLogger(__name__)


class Crates(Backend):
    """Crates.io backend for Perceval.

    This class allows the fetch the packages stored in Crates.io

    :param sleep_time: sleep time in case of connection lost
    :param tag: label used to mark the data
    :param cache: use issues already retrieved in cache
    """
    version = '0.1.2'

    def __init__(self, sleep_time=SLEEP_TIME, tag=None, cache=None):
        origin = CRATES_URL

        super().__init__(origin, tag=tag, cache=cache)
        self.client = CratesClient(sleep_time=sleep_time)

    @metadata
    def fetch(self, from_date=DEFAULT_DATETIME, category=CRATES_CATEGORY):
        """Fetch package data.

        The method retrieves packages and summary from Crates.io.

        :param from_date: obtain packages updated since this date
        :param category: select the category to fetch (crates or summary)

        :returns: a summary and crate items
        """

        if category == CRATES_CATEGORY:
            return self.__fetch_crates(from_date)
        else:
            return self.__fetch_summary()

    @classmethod
    def has_caching(cls):
        """Returns whether it supports caching items on the fetch process.

        :returns: this backend supports items cache
        """
        return False

    @classmethod
    def has_resuming(cls):
        """Returns whether it supports to resume the fetch process.

        :returns: this backend supports items resuming
        """
        return False

    @staticmethod
    def metadata_id(item):
        """Extracts the identifier from an item depending on its type."""

        if Crates.metadata_category(item) == CRATES_CATEGORY:
            return str(item['id'])
        else:
            ts = item['fetched_on']
            ts = str_to_datetime(ts)
            return str(ts.timestamp())

    @staticmethod
    def metadata_updated_on(item):
        """Extracts the update time from an item.

        The timestamp used is extracted from 'date_last_updated' field.
        This date is converted to UNIX timestamp format. As Launchpad
        dates are in UTC in ISO 8601 (e.g., '2008-03-26T01:43:15.603905+00:00')
        the conversion is straightforward.

        :param item: item generated by the backend

        :returns: a UNIX timestamp
        """
        if Crates.metadata_category(item) == CRATES_CATEGORY:
            ts = item['updated_at']
        else:
            ts = item['fetched_on']

        ts = str_to_datetime(ts)

        return ts.timestamp()

    @staticmethod
    def metadata_category(item):
        """Extracts the category from an item.

        This backend generates two types of item: 'summary' and 'crate'.
        """
        if 'num_downloads' in item:
            return SUMMARY_CATEGORY
        else:
            return CRATES_CATEGORY

    def __fetch_summary(self):
        """Fetch summary"""

        raw_summary = self.client.summary()
        summary = json.loads(raw_summary)
        summary['fetched_on'] = str(datetime_utcnow())

        yield summary

    def __fetch_crates(self, from_date):
        """Fetch crates"""

        from_date = datetime_to_utc(from_date)

        crates_groups = self.client.crates()

        for raw_crates in crates_groups:
            crates = json.loads(raw_crates)

            for crate_container in crates['crates']:

                if str_to_datetime(crate_container['updated_at']) < from_date:
                    continue

                crate_id = crate_container['id']

                crate = self.__fetch_crate_data(crate_id)
                crate['owner_team_data'] = self.__fetch_crate_owner_team(crate_id)
                crate['owner_user_data'] = self.__fetch_crate_owner_user(crate_id)
                crate['version_downloads_data'] = self.__fetch_crate_version_downloads(crate_id)
                crate['versions_data'] = self.__fetch_crate_versions(crate_id)

                yield crate

    def __fetch_crate_owner_team(self, crate_id):
        """Get crate team owner"""

        raw_owner_team = self.client.crate_attribute(crate_id, 'owner_team')

        owner_team = json.loads(raw_owner_team)

        return owner_team

    def __fetch_crate_owner_user(self, crate_id):
        """Get crate user owners"""

        raw_owner_user = self.client.crate_attribute(crate_id, 'owner_user')

        owner_user = json.loads(raw_owner_user)

        return owner_user

    def __fetch_crate_versions(self, crate_id):
        """Get crate versions data"""

        raw_versions = self.client.crate_attribute(crate_id, "versions")

        version_downloads = json.loads(raw_versions)

        return version_downloads

    def __fetch_crate_version_downloads(self, crate_id):
        """Get crate version downloads"""

        raw_version_downloads = self.client.crate_attribute(crate_id, "downloads")

        version_downloads = json.loads(raw_version_downloads)

        return version_downloads

    def __fetch_crate_data(self, crate_id):
        """Get crate data"""

        raw_crate = self.client.crate(crate_id)

        crate = json.loads(raw_crate)
        return crate['crate']


class CratesClient:
    """Client for retrieving information from Crates API"""

    MAX_RETRIES = 5

    def __init__(self, sleep_time=SLEEP_TIME):
        self.sleep_time = sleep_time

    def summary(self):
        """Get Crates.io summary"""

        path = urijoin(CRATES_API_URL, SUMMARY_CATEGORY)
        raw_content = self.__send_request(path, headers=self.__set_headers())

        return raw_content

    def crates(self, from_page=1):
        """Get crates in alphabetical order"""

        path = urijoin(CRATES_API_URL, CRATES_CATEGORY)
        raw_crates = self.__fetch_items(path, from_page)

        return raw_crates

    def crate(self, crate_id):
        """Get a crate by its ID"""

        path = urijoin(CRATES_API_URL, CRATES_CATEGORY, crate_id)
        raw_crate = self.__send_request(path, headers=self.__set_headers())

        return raw_crate

    def crate_attribute(self, crate_id, attribute):
        """Get crate attribute"""

        path = urijoin(CRATES_API_URL, CRATES_CATEGORY, crate_id, attribute)
        raw_attribute_data = self.__send_request(path, headers=self.__set_headers())

        return raw_attribute_data

    def __get_url_package(self):
        """Build URL package"""

        url = urijoin(CRATES_URL)

        return url

    def __set_headers(self):
        """Set header for request"""

        headers = {'Content-type': 'application/json'}

        return headers

    def __send_request(self, url, params=None, headers=None):
        """Send request"""

        retries = 0

        while retries < self.MAX_RETRIES:
            try:
                r = requests.get(url,
                                 params=params,
                                 headers=headers)
                break
            except requests.exceptions.ConnectionError:
                logger.warning("Connection was lost, the backend will sleep for " +
                               str(self.sleep_time) + "s before starting again")
                time.sleep(self.sleep_time * retries)
                retries += 1

        r.raise_for_status()

        return r.text

    def __build_payload(self, page=None):
        """Build payload"""

        payload = {'sort': 'alphabetical'}

        if page:
            payload['page'] = str(page)

        return payload

    def __fetch_items(self, path, page=1):
        """Return the items from Crates.io API using pagination"""

        fetch_data = True
        parsed_crates = 0
        total_crates = 0

        while fetch_data:
            logger.debug("Fetching page: %i", page)

            try:
                payload = self.__build_payload(page=page)
                raw_content = self.__send_request(path, payload, self.__set_headers())
                content = json.loads(raw_content)

                parsed_crates += len(content['crates'])

                if not total_crates:
                    total_crates = content['meta']['total']

            except requests.exceptions.HTTPError as e:
                logger.error("HTTP exception raised - %s", e.response.text)
                raise e

            yield raw_content
            page += 1

            if parsed_crates >= total_crates:
                fetch_data = False


class CratesCommand(BackendCommand):
    """Class to run Crates.io backend from the command line."""

    BACKEND = Crates

    @staticmethod
    def setup_cmd_parser():
        """Returns the Launchpad argument parser."""

        parser = BackendCommandArgumentParser(from_date=True,
                                              cache=False,
                                              token_auth=True)

        # Optional arguments
        group = parser.parser.add_argument_group('Crates.io arguments')
        group.add_argument('--sleep-time', dest='sleep_time',
                           help="Sleep time in case of connection lost")
        group.add_argument('--category', default=CRATES_CATEGORY,
                           choices=(CRATES_CATEGORY, SUMMARY_CATEGORY),
                           help="category of items to fecth")

        return parser
