import datetime
import math
import random
import re
from unittest import mock
import urllib.parse

from django.conf import settings
from django.core.urlresolvers import reverse
from django.db import connection
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.dateparse import parse_datetime
from django.utils.dateformat import format as format_date
from django.utils.timezone import localtime
from mtp_common.test_utils import silence_logger
from rest_framework import status

from core import getattr_path
from credit.views import CreditTextSearchFilter
from credit.models import Credit, Log
from credit.constants import CREDIT_STATUS, LOCK_LIMIT, LOG_ACTIONS
from credit.tests.test_base import (
    BaseCreditViewTestCase, CreditRejectsRequestsWithoutPermissionTestMixin
)
from mtp_auth.constants import CASHBOOK_OAUTH_CLIENT_ID
from mtp_auth.models import PrisonUserMapping
from prison.models import Prison
from transaction.tests.utils import generate_transactions


class CashbookCreditRejectsRequestsWithoutPermissionTestMixin(
    CreditRejectsRequestsWithoutPermissionTestMixin
):

    def _get_unauthorised_application_users(self):
        return self.send_money_users

    def _get_authorised_user(self):
        return self.prison_clerks[0]


class CreditListTestCase(
    CashbookCreditRejectsRequestsWithoutPermissionTestMixin,
    BaseCreditViewTestCase
):
    pagination_response_keys = ['page', 'page_count']

    def _get_url(self, **filters):
        url = reverse('credit-list')

        filters['limit'] = 1000
        return '{url}?{filters}'.format(
            url=url, filters=urllib.parse.urlencode(filters, doseq=True)
        )

    def _get_managed_prison_credits(self, logged_in_user=None):
        credits = self.credits
        logged_in_user = logged_in_user or self._get_authorised_user()
        if logged_in_user.has_perm('credit.view_any_credit'):
            return credits
        else:
            if (logged_in_user.applicationusermapping_set.first().application.client_id ==
                    CASHBOOK_OAUTH_CLIENT_ID):
                credits = [c for c in credits if c.received_at < datetime.datetime.combine(
                    timezone.now().date(), datetime.time.min
                ).replace(tzinfo=timezone.utc)]
            managing_prisons = list(PrisonUserMapping.objects.get_prison_set_for_user(logged_in_user))
            return [c for c in credits if c.prison in managing_prisons]

    def _get_invalid_credits(self):
        return [c for c in self.credits if c.prison is None]

    def _test_response_with_filters(self, filters={}):
        logged_in_user = self._get_authorised_user()
        credits = self._get_managed_prison_credits()

        url = self._get_url(**filters)
        response = self.client.get(
            url, format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # check expected result
        def noop_checker(c):
            return True

        status_checker = self.STATUS_FILTERS[filters.get('status', None)]
        if filters.get('prison'):
            def prison_checker(c):
                return c.prison and c.prison.pk in filters['prison'].split(',')
        elif filters.get('prison__isnull'):
            def prison_checker(c):
                return (c.prison is None) == (filters['prison__isnull'] == 'True')
        else:
            prison_checker = noop_checker
        if filters.get('user'):
            def user_checker(c):
                return c.owner and c.owner.pk == filters['user']
        else:
            user_checker = noop_checker
        received_at_checker = self._get_received_at_checker(filters, noop_checker)
        search_checker = self._get_search_checker(filters, noop_checker)
        sender_name_checker = self._get_attribute_checker(
            'sender_name', filters, noop_checker, True
        )
        sender_sort_code_checker = self._get_attribute_checker(
            'sender_sort_code', filters, noop_checker
        )
        sender_account_number_checker = self._get_attribute_checker(
            'sender_account_number', filters, noop_checker
        )
        sender_roll_number_checker = self._get_attribute_checker(
            'sender_roll_number', filters, noop_checker
        )
        prisoner_name_checker = self._get_attribute_checker(
            'prisoner_name', filters, noop_checker, True
        )
        prisoner_number_checker = self._get_attribute_checker(
            'prisoner_number', filters, noop_checker
        )
        prison_region_checker = self._get_sub_attribute_checker(
            'prison_region', 'prison.region', filters, noop_checker
        )
        prison_population_checker = self._get_prison_type_checker(
            'prison_population', 'prison.populations', filters, noop_checker
        )
        prison_category_checker = self._get_prison_type_checker(
            'prison_category', 'prison.categories', filters, noop_checker
        )
        amount_pattern_checker = self._get_amount_pattern_checker(filters, noop_checker)
        valid_checker = self._get_valid_checker(filters, noop_checker)
        pk_checker = self._get_multivalue_attribute_checker('pk', filters, noop_checker)

        expected_ids = [
            c.pk
            for c in credits
            if c.resolution != 'initial' and
            status_checker(c) and
            prison_checker(c) and
            user_checker(c) and
            received_at_checker(c) and
            search_checker(c) and
            sender_name_checker(c) and
            sender_sort_code_checker(c) and
            sender_account_number_checker(c) and
            sender_roll_number_checker(c) and
            prisoner_name_checker(c) and
            prisoner_number_checker(c) and
            prison_region_checker(c) and
            prison_population_checker(c) and
            prison_category_checker(c) and
            amount_pattern_checker(c) and
            valid_checker(c) and
            pk_checker(c)
        ]

        self.assertEqual(response.data['count'], len(expected_ids))
        self.assertListEqual(
            sorted([c['id'] for c in response.data['results']]),
            sorted(expected_ids)
        )

        # ensure date-based pagination hasn't occurred
        for key in self.pagination_response_keys:
            self.assertNotIn(key, response.data)

        return response

    def _get_attribute_checker(self, attribute, filters, noop_checker, text_search=False):
        if filters.get(attribute):
            if text_search:
                return lambda c: filters[attribute].lower() in (getattr(c, attribute) or '').lower()
            return lambda c: getattr(c, attribute) == filters[attribute]
        return noop_checker

    def _get_multivalue_attribute_checker(self, attribute, filters, noop_checker, text_search=False):
        if filters.get(attribute):
            return lambda c: getattr(c, attribute) in filters[attribute]
        return noop_checker

    def _get_sub_attribute_checker(self, filter_name, attribute_path, filters, noop_checker, text_search=False):
        if filters.get(filter_name):
            if text_search:
                return lambda c: filters[filter_name].lower() in getattr_path(c, attribute_path, '').lower()
            return lambda c: getattr_path(c, attribute_path) == filters[filter_name]
        return noop_checker

    def _get_prison_type_checker(self, filter_name, list_attribute_path, filters, noop_checker):
        if filters.get(filter_name):
            filter_list = filters[filter_name] if isinstance(filters[filter_name], list) else [filters[filter_name]]
            return lambda c: any(
                value in [t.name for t in getattr_path(c, list_attribute_path).all()]
                for value in filter_list
            )
        return noop_checker

    def _get_received_at_checker(self, filters, noop_checker):
        def parse_date(date):
            parsed_date = parse_datetime(date)
            if parsed_date is not None:
                return parsed_date
            for date_format in settings.DATETIME_INPUT_FORMATS:
                try:
                    return datetime.datetime.strptime(date, date_format)
                except (ValueError, TypeError):
                    continue
            raise ValueError('Cannot parse date %s' % date)

        received_at__gte, received_at__lt = filters.get('received_at__gte'), filters.get('received_at__lt')
        received_at__gte = parse_date(received_at__gte) if received_at__gte else None
        received_at__lt = parse_date(received_at__lt) if received_at__lt else None

        if received_at__gte and received_at__gte.tzinfo is None:
            received_at__gte = timezone.make_aware(received_at__gte)
        if received_at__lt and received_at__lt.tzinfo is None:
            received_at__lt = timezone.make_aware(received_at__lt)

        if received_at__gte and received_at__lt:
            return lambda c: received_at__gte <= c.received_at < received_at__lt
        elif received_at__gte:
            return lambda c: received_at__gte <= c.received_at
        elif received_at__lt:
            return lambda c: c.received_at < received_at__lt
        return noop_checker

    def _get_search_checker(self, filters, noop_checker):
        if filters.get('search'):
            search_phrase = filters['search'].lower()
            search_fields = ['prisoner_name', 'prisoner_number', 'sender_name']

            return lambda c: any(
                search_phrase in (getattr(c, field) or '').lower()
                for field in search_fields
            ) or (search_phrase in '£%0.2f' % (c.amount / 100))
        return noop_checker

    def _get_amount_pattern_checker(self, filters, noop_checker):
        checkers = [noop_checker]
        if 'exclude_amount__endswith' in filters:
            excluded_ends = filters['exclude_amount__endswith']
            if type(excluded_ends) is not list:
                excluded_ends = [excluded_ends]
            for ending in excluded_ends:
                checkers.append(
                    lambda c: not str(c.amount).endswith(ending)
                )
        if 'exclude_amount__regex' in filters:
            checkers.append(lambda c: not re.match(
                filters['exclude_amount__regex'], str(c.amount))
            )
        if 'amount__endswith' in filters:
            endings = filters['amount__endswith']
            if type(endings) is not list:
                endings = [endings]
            for ending in endings:
                checkers.append(
                    lambda c: str(c.amount).endswith(ending)
                )
        if 'amount__regex' in filters:
            checkers.append(lambda c: re.match(
                filters['amount__regex'], str(c.amount))
            )
        if 'amount__lte' in filters:
            checkers.append(lambda c: c.amount <= int(filters['amount__lte']))
        if 'amount__gte' in filters:
            checkers.append(lambda c: c.amount >= int(filters['amount__gte']))
        if 'amount' in filters:
            checkers.append(lambda c: c.amount == int(filters['amount']))
        return lambda c: all([checker(c) for checker in checkers])

    def _get_valid_checker(self, filters, noop_checker):
        if 'valid' in filters:
            def valid_checker(c):
                return (
                    self.STATUS_FILTERS[CREDIT_STATUS.AVAILABLE](c) or
                    self.STATUS_FILTERS[CREDIT_STATUS.LOCKED](c) or
                    self.STATUS_FILTERS[CREDIT_STATUS.CREDITED](c)
                )
            if filters['valid'] in ('true', 'True', 1, True):
                return valid_checker
            else:
                return lambda c: not valid_checker(c)
        return noop_checker


class CreditListWithDefaultsTestCase(CreditListTestCase):

    def test_returns_all_credits(self):
        """
        Returns all credits attached to all the prisons that
        the logged-in user can manage.
        """
        self._test_response_with_filters(filters={})

    def test_filter_by_sender_name(self):
        search = ''
        while not search:
            credit = random.choice(self.credits)
            if credit.sender_name:
                search = credit.sender_name[:-4].strip()
        self._test_response_with_filters(filters={
            'sender_name': search
        })

    def test_filter_by_prisoner_name(self):
        search = ''
        while not search:
            credit = random.choice(self.credits)
            if credit.prisoner_name:
                search = credit.prisoner_name[:-4].strip()
        self._test_response_with_filters(filters={
            'prisoner_name': search
        })

    def test_filter_by_prison_region(self):
        search = ''
        while not search:
            credit = random.choice(self.credits)
            if credit.prison:
                search = credit.prison.region
        self._test_response_with_filters(filters={
            'prison_region': search
        })

    def test_filter_by_prison_population(self):
        search = ''
        while not search:
            credit = random.choice(self.credits)
            if credit.prison:
                search = credit.prison.populations.first().name
        self._test_response_with_filters(filters={
            'prison_population': search
        })

    def test_filter_by_prison_category(self):
        search = ''
        while not search:
            credit = random.choice(self.credits)
            if credit.prison:
                search = credit.prison.categories.first().name
        self._test_response_with_filters(filters={
            'prison_category': search
        })

    def test_filter_by_multiple_prison_categories(self):
        search = []
        while len(search) < 2:
            credit = random.choice(self.credits)
            if credit.prison and credit.prison.categories.first().name not in search:
                search.append(credit.prison.categories.first().name)
        self._test_response_with_filters(filters={
            'prison_category': search
        })

    def test_filter_by_pks(self):
        search = []
        while len(search) < 2:
            credit = random.choice(self.credits)
            if credit.pk not in search:
                search.append(credit.pk)
        self._test_response_with_filters(filters={
            'pk': search
        })


class CreditListWithDefaultPrisonAndUserTestCase(CreditListTestCase):

    def test_filter_by_status_available(self):
        """
        Returns available credits attached to all the prisons
        that the logged-in user can manage.
        """
        self._test_response_with_filters(filters={
            'status': CREDIT_STATUS.AVAILABLE
        })

    def test_filter_by_status_locked(self):
        """
        Returns locked credits attached to all the prisons
        that the logged-in user can manage.
        """
        self._test_response_with_filters(filters={
            'status': CREDIT_STATUS.LOCKED
        })

    def test_filter_by_status_credited(self):
        """
        Returns credited credits attached to all the prisons
        that the logged-in user can manage.
        """
        self._test_response_with_filters(filters={
            'status': CREDIT_STATUS.CREDITED
        })


class CreditListWithDefaultUserTestCase(CreditListTestCase):

    def test_filter_by_status_available_and_prison(self):
        """
        Returns available credits attached to the passed-in prison.
        """
        self._test_response_with_filters(filters={
            'prison': self.prisons[0].pk
        })

    def test_filter_by_status_locked_and_prison(self):
        """
        Returns locked credits attached to the passed-in prison.
        """
        self._test_response_with_filters(filters={
            'status': CREDIT_STATUS.LOCKED,
            'prison': self.prisons[0].pk
        })

    def test_filter_by_status_credited_prison(self):
        """
        Returns crdited credits attached to the passed-in prison.
        """
        self._test_response_with_filters(filters={
            'status': CREDIT_STATUS.CREDITED,
            'prison': self.prisons[0].pk
        })


class CreditListWithDefaultPrisonTestCase(CreditListTestCase):

    def test_filter_by_status_available_and_user(self):
        """
        Returns available credits attached to all the prisons
        that the passed-in user can manage.
        """
        self._test_response_with_filters(filters={
            'user': self.prison_clerks[1].pk
        })

    def test_filter_by_status_locked_and_user(self):
        """
        Returns credits locked by the passed-in user.
        """
        self._test_response_with_filters(filters={
            'status': CREDIT_STATUS.LOCKED,
            'user': self.prison_clerks[1].pk
        })

    def test_filter_by_status_credited_and_user(self):
        """
        Returns credits credited by the passed-in user.
        """
        self._test_response_with_filters(filters={
            'status': CREDIT_STATUS.CREDITED,
            'user': self.prison_clerks[1].pk
        })


class CreditListWithoutDefaultsTestCase(CreditListTestCase):

    def test_filter_by_status_available_and_prison_and_user(self):
        """
        Returns available credits attached to the passed-in prison.
        """
        self._test_response_with_filters(filters={
            'prison': self.prisons[0].pk,
            'user': self.prison_clerks[1].pk
        })

    def test_filter_by_status_locked_and_prison_and_user(self):
        """
        Returns credits locked by the passed-in user and
        attached to the passed-in prison.
        """
        self._test_response_with_filters(filters={
            'status': CREDIT_STATUS.LOCKED,
            'prison': self.prisons[0].pk,
            'user': self.prison_clerks[1].pk
        })

    def test_filter_by_status_credited_and_prison_and_user(self):
        """
        Returns credits credited by the passed-in user and
        attached to the passed-in prison.
        """
        self._test_response_with_filters(filters={
            'status': CREDIT_STATUS.CREDITED,
            'prison': self.prisons[0].pk,
            'user': self.prison_clerks[1].pk
        })


class CreditListWithDefaultStatusAndUserTestCase(CreditListTestCase):

    def test_filter_by_prison(self):
        """
        Returns all credits attached to the passed-in prison.
        """
        self._test_response_with_filters(filters={
            'prison': self.prisons[0].pk
        })

    def test_filter_by_multiple_prisons(self):
        """
        Returns all credits attached to the passed-in prisons.
        """
        self._test_response_with_filters(filters={
            'prison[]': [p.pk for p in self.prisons]
        })


class CreditListWithDefaultStatusTestCase(CreditListTestCase):

    def test_filter_by_prison_and_user(self):
        """
        Returns all credits attached to the passed-in prison.
        """
        self._test_response_with_filters(filters={
            'prison': self.prisons[0].pk,
            'user': self.prison_clerks[1].pk
        })


class CreditListWithDefaultStatusAndPrisonTestCase(CreditListTestCase):

    def test_filter_by_user(self):
        """
        Returns all credits managed by the passed-in user
        """
        self._test_response_with_filters(filters={
            'user': self.prison_clerks[1].pk
        })


class CreditListWithValidFilterTestCase(CreditListTestCase):

    def test_filter_by_invalidity(self):
        self._test_response_with_filters(filters={
            'valid': 'true'
        })

    def test_filter_by_validity(self):
        self._test_response_with_filters(filters={
            'valid': 'false'
        })


class CreditListWithReceivedAtFilterTestCase(CreditListTestCase):
    def _format_date(self, date):
        return format_date(date, 'Y-m-d')

    def test_filter_received_at_yesterday(self):
        """
        Returns all credits received yesterday
        """
        yesterday = self._get_latest_date()
        self._test_response_with_filters(filters={
            'received_at__gte': self._format_date(yesterday),
            'received_at__lt': self._format_date(yesterday + datetime.timedelta(days=1)),
        })

    def test_filter_received_since_five_days_ago(self):
        """
        Returns all credits received since 5 days ago
        """
        five_days_ago = self._get_latest_date() - datetime.timedelta(days=5)
        self._test_response_with_filters(filters={
            'received_at__gte': self._format_date(five_days_ago),
        })

    def test_filter_received_until_five_days_ago(self):
        """
        Returns all credits received until 5 days ago
        """
        five_days_ago = self._get_latest_date() - datetime.timedelta(days=4)
        self._test_response_with_filters(filters={
            'received_at__lt': self._format_date(five_days_ago),
        })

    def test_filter_received_at_datetimes(self):
        """
        Returns all credits received between two time
        """
        start_date = datetime.datetime.combine(
            self._get_latest_date() - datetime.timedelta(days=2),
            datetime.time(10, 0, tzinfo=timezone.utc)
        )
        end_date = datetime.datetime.combine(
            self._get_latest_date(),
            datetime.time(22, 0, tzinfo=timezone.utc)
        )

        self._test_response_with_filters(filters={
            'received_at__gte': start_date.isoformat(),
            'received_at__lt': end_date.isoformat(),
        })


class CreditListWithSearchTestCase(CreditListTestCase):
    def test_filter_search_for_prisoner_number(self):
        """
        Search for a prisoner number
        """
        search_phrase = ''
        while not search_phrase:
            credit = random.choice(self.credits)
            if credit.prisoner_number:
                search_phrase = credit.prisoner_number
        self._test_response_with_filters(filters={
            'search': search_phrase
        })

    def test_filter_search_for_prisoner_name(self):
        """
        Search for a prisoner first name
        """
        search_phrase = ''
        while not search_phrase:
            credit = random.choice(self.credits)
            if credit.prisoner_name:
                search_phrase = credit.prisoner_name.split()[0]
        self._test_response_with_filters(filters={
            'search': search_phrase
        })

    def test_filter_search_for_sender_name(self):
        """
        Search for a partial sender name
        """
        search_phrase = ''
        while not search_phrase:
            credit = random.choice(self.credits)
            if credit.sender_name:
                search_phrase = credit.sender_name[:2].strip()
        self._test_response_with_filters(filters={
            'search': search_phrase
        })

    def test_filter_search_for_amount(self):
        """
        Search for a payment amount
        """
        credit = random.choice(self.credits)
        search_phrase = '£%0.2f' % (credit.amount / 100)
        self._test_response_with_filters(filters={
            'search': search_phrase
        })

    def test_filter_search_for_amount_prefix(self):
        search_phrase = '£5'
        self._test_response_with_filters(filters={
            'search': search_phrase
        })

    def test_empty_search(self):
        """
        Empty search causes no errors
        """
        self._test_response_with_filters(filters={
            'search': ''
        })

    def test_search_with_no_results(self):
        """
        Search for a value that cannot exist in generated credits
        """
        response = self._test_response_with_filters(filters={
            'search': get_random_string(
                length=20,  # too long for generated sender names
                allowed_chars='§±@£$#{}[];:<>',  # includes characters not used in generation
            )
        })
        self.assertFalse(response.data['results'])


class CreditListInvalidValuesTestCase(CreditListTestCase):

    def test_invalid_status_filter(self):
        logged_in_user = self.prison_clerks[0]
        url = self._get_url(status='invalid')
        response = self.client.get(
            url, format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)

    def test_invalid_user_filter(self):
        logged_in_user = self.prison_clerks[0]
        url = self._get_url(user='invalid')
        response = self.client.get(
            url, format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)

    def test_invalid_prison_filter(self):
        logged_in_user = self.prison_clerks[0]
        url = self._get_url(prison='invalid')
        response = self.client.get(
            url, format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)

    def test_prison_not_managed_by_loggedin_user(self):
        logged_in_user = self.prison_clerks[0]
        managing_prison_ids = PrisonUserMapping.objects.get_prison_set_for_user(logged_in_user)\
            .values_list('pk', flat=True)
        non_managing_prisons = Prison.objects.exclude(pk__in=managing_prison_ids)

        self.assertTrue(len(non_managing_prisons) > 0)

        url = self._get_url(prison=non_managing_prisons[0].pk)
        response = self.client.get(
            url, format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)

    def test_prison_not_managed_by_passed_in_user(self):
        # logged-in user managing all the prisons
        logged_in_user = self.prison_clerks[0]
        logged_in_user.prisonusermapping.prisons.add(*self.prisons)

        # passed-in user managing only prison #1
        passed_in_user = self.prison_clerks[1]
        passed_in_user.prisonusermapping.prisons.clear()
        Credit.objects.filter(
            owner=passed_in_user, prison__isnull=False
        ).update(prison=self.prisons[1])
        passed_in_user.prisonusermapping.prisons.add(self.prisons[1])

        # filtering by prison #0, passed-in user doesn't manage that one so it should
        # return an empty list
        url = self._get_url(
            user=passed_in_user.pk,
            prison=self.prisons[0].pk
        )
        response = self.client.get(
            url, format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)

    def test_logged_in_user_not_managing_prison(self):
        # logged-in user managing only prison #1
        logged_in_user = self.prison_clerks[0]
        logged_in_user.prisonusermapping.prisons.clear()
        logged_in_user.prisonusermapping.prisons.add(self.prisons[1])

        # passed-in user managing all the prisons
        passed_in_user = self.prison_clerks[1]
        passed_in_user.prisonusermapping.prisons.add(*self.prisons)

        # filtering by prison #0, logged-in user doesn't manage that one so it should
        # return an empty list
        url = self._get_url(
            user=passed_in_user.pk,
            prison=self.prisons[0].pk
        )
        response = self.client.get(
            url, format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)


class CreditListOrderingTestCase(CreditListTestCase):
    transaction_batch = 200

    @classmethod
    def setUpClass(cls):
        with connection.cursor() as cursor:
            # forces C collation to order names without ignoring spaces on RDS
            cursor.execute(
                '''
                ALTER TABLE %(table_name)s
                ALTER COLUMN prisoner_name
                SET DATA TYPE CHARACTER VARYING(%(prisoner_name_len)d) COLLATE "C";
                ''' % {
                    'table_name': Credit._meta.db_table,
                    'prisoner_name_len': Credit._meta.get_field('prisoner_name').max_length,
                }
            )
        super().setUpClass()

    def setUp(self):
        super().setUp()
        credit_1 = Credit.objects.first()
        credit_2 = Credit.objects.exclude(prisoner_name=credit_1.prisoner_name).first()
        Credit.objects.filter(prisoner_name=credit_1.prisoner_name).update(prisoner_name='LEON PETERS')
        Credit.objects.filter(prisoner_name=credit_2.prisoner_name).update(prisoner_name='LEONARD CARTER')
        self.logged_in_user = self._get_authorised_user()

    @classmethod
    def add_test_methods(cls):
        for ordering_field in ['received_at', 'amount', 'prisoner_number', 'prisoner_name']:
            cls.add_test_method(ordering_field)

    @classmethod
    def add_test_method(cls, ordering):
        def test_method(self):
            response = self._test_ordering(ordering)
            response_reversed = self._test_ordering('-' + ordering)
            self.assertEqual(response.data['count'], response_reversed.data['count'])

            search = ''
            while not search:
                credit = random.choice(self.credits)
                if credit.prison in self.logged_in_user.prisonusermapping.prisons.all() and credit.prisoner_name:
                    search = credit.prisoner_name[:-4].strip()
            self._test_ordering(ordering, prisoner_name=search.lower())

        setattr(cls, 'test_ordering_by_' + ordering, test_method)

    def _test_ordering(self, ordering, **filters):
        url = self._get_url(ordering=ordering, **filters)
        response = self.client.get(
            url, format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(self.logged_in_user)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = response.data['results']
        if len(results) < 2:
            print('Cannot test ordering on a list of fewer than 2 results')
            return
        if ordering.startswith('-'):
            ordering = ordering[1:]
            results = reversed(results)
        last_item = None
        for item in results:
            if last_item is not None:
                self.assertGreaterEqual(item[ordering], last_item[ordering])
            last_item = item
        return response


CreditListOrderingTestCase.add_test_methods()


class LockedCreditListTestCase(CreditListTestCase):
    def _get_url(self, **filters):
        url = reverse('credit-locked')

        filters['limit'] = 1000
        return '{url}?{filters}'.format(
            url=url, filters=urllib.parse.urlencode(filters)
        )

    def test_locked_credits_returns_same_ones_as_filtered_list(self):
        logged_in_user = self.prison_clerks[0]
        logged_in_user.prisonusermapping.prisons.add(*self.prisons)
        # managing_prisons = list(PrisonUserMapping.objects.get_prison_set_for_user(logged_in_user))

        url_list = super()._get_url(status=CREDIT_STATUS.LOCKED)
        response_list = self.client.get(
            url_list, format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )
        self.assertTrue(response_list.data['count'])  # ensure some credits exist!

        url_locked = self._get_url()
        response_locked = self.client.get(
            url_locked, format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )

        self.assertEqual(response_locked.status_code, status.HTTP_200_OK)
        self.assertEqual(response_locked.data['count'], response_list.data['count'])
        self.assertListEqual(
            list(sorted(credit['id'] for credit in response_locked.data['results'])),
            list(sorted(credit['id'] for credit in response_list.data['results'])),
        )
        self.assertTrue(all(credit['locked'] for credit in response_locked.data['results']))
        self.assertTrue(all('locked_at' in credit for credit in response_locked.data['results']))


class DateBasedPaginationTestCase(CreditListTestCase):
    def _get_response(self, filters):
        logged_in_user = self._get_authorised_user()
        url = self._get_url(**filters)
        return self.client.get(
            url, format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )

    def _test_invalid_response(self, filters):
        response = self._get_response(filters)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_invalid_ordering(self):
        self._test_invalid_response({'page_by_date_field': 'received_at',
                                     'ordering': 'prisoner_number'})

    def test_invalid_pagination(self):
        received_at = self._get_random_credit_date()
        received_at__gte = received_at.strftime('%Y-%m-%d')
        received_at__lt = (received_at - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        self._test_invalid_response({'page_by_date_field': 'prisoner_name',
                                     'received_at__gte': received_at__gte,
                                     'received_at__lt': received_at__lt})

    def _get_random_credit(self):
        return random.choice(self._get_managed_prison_credits())

    def _get_date_of_credit(self, credit):
        return localtime(credit.received_at).date()

    def _get_random_credit_date(self):
        return self._get_date_of_credit(self._get_random_credit())

    def _get_date_count(self, credits):
        credit_dates = set(map(self._get_date_of_credit, credits))
        return len(credit_dates)

    def _get_page_count(self, credits, page_size=settings.REQUEST_PAGE_DAYS):
        return int(math.ceil(self._get_date_count(credits) / page_size))

    def _get_all_pages_of_credits(self, credits, page_size=settings.REQUEST_PAGE_DAYS):
        all_pages = []
        current_page = []
        dates_collected = 0
        last_date = None
        for credit in credits:
            date = self._get_date_of_credit(credit)
            if date != last_date:
                dates_collected += 1
                last_date = date
            if dates_collected > page_size:
                dates_collected = 1
                last_date = date
                all_pages.append(current_page)
                current_page = []
            current_page.append(credit)
        if current_page:
            all_pages.append(current_page)
        return all_pages

    def _get_page_of_credits(self, credits, page=1, page_size=settings.REQUEST_PAGE_DAYS):
        all_pages = self._get_all_pages_of_credits(credits, page_size=page_size)
        return all_pages[page - 1]

    def _get_page_of_credit_ids(self, credits, page=1, page_size=settings.REQUEST_PAGE_DAYS):
        page = self._get_page_of_credits(credits, page=page, page_size=page_size)
        return sorted(credit.id for credit in page)

    def _test_paginated_response(self, filters, credit_ids, count, page, page_count):
        response = self._get_response(filters)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response_ids = sorted(credit['id'] for credit in response.data['results'])
        self.assertListEqual(response_ids, credit_ids)

        self.assertEqual(response.data['count'], count)
        self.assertEqual(response.data['page'], page)
        self.assertEqual(response.data['page_count'], page_count)

    def _get_credits(self, credit_filter=None, ordering='-received_at'):
        credits = self._get_managed_prison_credits()
        credits = filter(credit_filter, credits)

        def credit_sort(credit):
            return credit.received_at

        if ordering == 'received_at':
            credits = sorted(credits, key=credit_sort)
        elif ordering == '-received_at':
            credits = sorted(credits, key=credit_sort, reverse=True)
        else:
            raise NotImplementedError

        return credits

    def test_pagination_without_filters(self):
        credits = self._get_credits()

        expected = {
            'count': len(credits),
            'page': 1,
            'page_count': self._get_page_count(credits),
            'credit_ids': self._get_page_of_credit_ids(credits),
        }
        self._test_paginated_response(filters={'page_by_date_field': 'received_at',
                                               'ordering': '-received_at'},
                                      **expected)

    def test_pagination_with_search(self):
        search_term = ''
        while not search_term:
            random_credit = self._get_random_credit()
            search_term = random_credit.prisoner_name or \
                random_credit.prisoner_number
        search_term = search_term.lower().split()[0]

        search_fields = CreditTextSearchFilter.fields

        def credit_filter(credit):
            return any(
                search_term in str(getattr(credit, search_field, '') or '').lower()
                for search_field in search_fields
            )

        credits = self._get_credits(credit_filter)

        expected = {
            'count': len(credits),
            'page': 1,
            'page_count': self._get_page_count(credits),
            'credit_ids': self._get_page_of_credit_ids(credits),
        }
        self._test_paginated_response(filters={'page_by_date_field': 'received_at',
                                               'ordering': '-received_at',
                                               'search': search_term},
                                      **expected)

    def test_pagination_with_single_date(self):
        received_at = self._get_random_credit_date()

        def credit_filter(credit):
            return self._get_date_of_credit(credit) == received_at

        credits = self._get_credits(credit_filter)

        expected = {
            'count': len(credits),
            'page': 1,
            'page_count': self._get_page_count(credits),
            'credit_ids': self._get_page_of_credit_ids(credits),
        }
        received_at__gte = received_at.strftime('%Y-%m-%d')
        received_at__lt = (received_at + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        self._test_paginated_response(filters={'page_by_date_field': 'received_at',
                                               'ordering': '-received_at',
                                               'received_at__gte': received_at__gte,
                                               'received_at__lt': received_at__lt},
                                      **expected)

    def test_pagination_with_date_range(self):
        received_at__gte, received_at__lt = self._get_random_credit_date(), self._get_random_credit_date()
        while received_at__lt == received_at__gte:
            received_at__gte, received_at__lt = self._get_random_credit_date(), self._get_random_credit_date()
        if received_at__gte > received_at__lt:
            received_at__gte, received_at__lt = received_at__lt, received_at__gte

        def credit_filter(credit):
            return received_at__gte <= self._get_date_of_credit(credit) < received_at__lt

        credits = self._get_credits(credit_filter)

        expected = {
            'count': len(credits),
            'page': 1,
            'page_count': self._get_page_count(credits),
            'credit_ids': self._get_page_of_credit_ids(credits),
        }
        received_at__gte = received_at__gte.strftime('%Y-%m-%d')
        received_at__lt = received_at__lt.strftime('%Y-%m-%d')
        self._test_paginated_response(filters={'page_by_date_field': 'received_at',
                                               'ordering': '-received_at',
                                               'received_at__gte': received_at__gte,
                                               'received_at__lt': received_at__lt},
                                      **expected)

    def test_pagination_beyond_page_1(self):
        tries = 6
        page_count = 0
        credits = []
        for _ in range(tries):
            credits = self._get_credits()
            page_count = self._get_page_count(credits)
            if page_count > 1:
                break
            self.credits = [t.credit for t in generate_transactions(
                transaction_batch=150
            ) if t.credit]
        self.assertGreater(page_count, 1,
                           'Could not generate enough pages for test in %d tries' % tries)

        response = self._get_response(filters={'page_by_date_field': 'received_at',
                                               'ordering': '-received_at',
                                               'page': 1})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected = {
            'count': len(credits),
            'page': 2,
            'page_count': page_count,
            'credit_ids': self._get_page_of_credit_ids(credits, page=2),
        }
        self._test_paginated_response(filters={'page_by_date_field': 'received_at',
                                               'ordering': '-received_at',
                                               'page': 2},
                                      **expected)


class SecurityCreditListTestCase(CreditListTestCase):

    def _get_authorised_user(self):
        return self.security_staff[0]

    def _test_response_with_filters(self, filters={}):
        response = super()._test_response_with_filters(filters)
        for response_credit in response.data['results']:
            db_credit = Credit.objects.get(pk=response_credit['id'])
            self.assertEqual(
                db_credit.sender_sort_code,
                response_credit.get('sender_sort_code')
            )
            self.assertEqual(
                db_credit.sender_account_number,
                response_credit.get('sender_account_number')
            )
            self.assertEqual(
                db_credit.sender_roll_number,
                response_credit.get('sender_roll_number')
            )


class TransactionSenderDetailsCreditListTestCase(SecurityCreditListTestCase):

    def test_sort_code_filter(self):
        random_sort_code = (
            Credit.objects.filter(transaction__sender_sort_code__isnull=False)
            .exclude(transaction__sender_sort_code='')
            .order_by('?').first().sender_sort_code
        )
        self._test_response_with_filters(filters={
            'sender_sort_code': random_sort_code
        })

    def test_account_number_filter(self):
        random_account_number = (
            Credit.objects.filter(transaction__sender_account_number__isnull=False)
            .exclude(transaction__sender_account_number='')
            .order_by('?').first().sender_account_number
        )
        self._test_response_with_filters(filters={
            'sender_account_number': random_account_number
        })

    def test_roll_number_filter(self):
        random_roll_number = (
            Credit.objects.filter(transaction__sender_roll_number__isnull=False)
            .exclude(transaction__sender_roll_number='')
            .order_by('?').first().sender_roll_number
        )
        self._test_response_with_filters(filters={
            'sender_roll_number': random_roll_number
        })


class CreditListWithBlankStringFiltersTestCase(SecurityCreditListTestCase):
    def assertAllResponsesHaveBlankField(self, filters, blank_fields, expected_filter):  # noqa
        expected_results = list(filter(expected_filter, self._get_managed_prison_credits()))

        url = self._get_url(**filters)
        response = self.client.get(
            url, format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(self._get_authorised_user())
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = []
        for result in response.data.get('results', []):
            results.append(result['id'])
            for blank_field in blank_fields:
                self.assertIn(result[blank_field], ['', None])

        self.assertListEqual(
            sorted(results),
            sorted(expected_result.id for expected_result in expected_results)
        )

    def test_blank_sender_name(self):
        self.assertAllResponsesHaveBlankField(
            {
                'sender_name__isblank': 'True'
            },
            ['sender_name'],
            lambda credit: getattr_path(credit, 'transaction.sender_name', None) == ''
        )

    def test_blank_sender_sort_code(self):
        self.assertAllResponsesHaveBlankField(
            {
                'sender_sort_code__isblank': 'True'
            },
            ['sender_sort_code'],
            lambda credit: getattr_path(credit, 'transaction.sender_sort_code', None) == ''
        )

    def test_blank_sender_account_number(self):
        self.assertAllResponsesHaveBlankField(
            {
                'sender_account_number__isblank': 'True'
            },
            ['sender_account_number'],
            lambda credit: getattr_path(credit, 'transaction.sender_account_number', None) == ''
        )

    def test_blank_sender_roll_number(self):
        self.assertAllResponsesHaveBlankField(
            {
                'sender_roll_number__isblank': 'True'
            },
            ['sender_roll_number'],
            lambda credit: getattr_path(credit, 'transaction.sender_roll_number', None) == ''
        )


class PrisonerNumberCreditListTestCase(SecurityCreditListTestCase):

    def test_prisoner_number_filter(self):
        random_prisoner_number = (
            Credit.objects.filter(prisoner_number__isnull=False)
            .exclude(prisoner_number='')
            .order_by('?').first().prisoner_number
        )
        self._test_response_with_filters(filters={
            'prisoner_number': random_prisoner_number
        })


class AmountPatternCreditListTestCase(SecurityCreditListTestCase):

    def test_exclude_amount_pattern_filter_endswith_multiple(self):
        self._test_response_with_filters(filters={
            'exclude_amount__endswith': ['000', '500'],
        })

    def test_exclude_amount_pattern_filter_regex(self):
        self._test_response_with_filters(filters={
            'exclude_amount__regex': '^.*000$',
        })

    def test_amount_pattern_filter_endswith(self):
        self._test_response_with_filters(filters={
            'amount__endswith': '000',
        })

    def test_amount_pattern_filter_endswith_multiple(self):
        self._test_response_with_filters(filters={
            'amount__endswith': ['000', '500'],
        })

    def test_amount_pattern_filter_regex(self):
        self._test_response_with_filters(filters={
            'amount__regex': '^.*000$',
        })

    def test_amount_pattern_filter_less_than_regex(self):
        self._test_response_with_filters(filters={
            'amount__lte': 5000,
            'amount__regex': '^.*00$',
        })

    def test_amount_pattern_filter_range(self):
        self._test_response_with_filters(filters={
            'amount__gte': 5000,
            'amount__lte': 10000,
        })

    def test_amount_pattern_filter_exact(self):
        random_amount = random.choice(self.credits).amount
        self._test_response_with_filters(filters={
            'amount': random_amount,
        })


class NoPrisonCreditListTestCase(SecurityCreditListTestCase):

    def test_no_prison_filter(self):
        self._test_response_with_filters(filters={
            'prison__isnull': 'True'
        })


class LockCreditTestCase(
    CashbookCreditRejectsRequestsWithoutPermissionTestMixin,
    BaseCreditViewTestCase
):
    ENDPOINT_VERB = 'post'
    transaction_batch = 500

    def _get_url(self):
        return reverse('credit-lock')

    def setUp(self):
        super().setUp()

        self.logged_in_user = self.prison_clerks[0]
        self.logged_in_user.prisonusermapping.prisons.add(*self.prisons)

    def _test_lock(self, already_locked_count, available_count=LOCK_LIMIT):
        locked_qs = self._get_locked_credits_qs(self.prisons, self.logged_in_user)
        available_qs = self._get_available_credits_qs(self.prisons, self.logged_in_user)

        # set nr of credits locked by logged-in user to 'already_locked'
        locked = locked_qs.values_list('pk', flat=True)
        Credit.objects.filter(
            pk__in=[-1]+list(locked[:locked.count() - already_locked_count])
        ).delete()

        self.assertEqual(locked_qs.count(), already_locked_count)

        # set nr of credits available to 'available'
        available = available_qs.values_list('pk', flat=True)
        Credit.objects.filter(
            pk__in=[-1]+list(available[:available.count() - available_count])
        ).delete()

        self.assertEqual(available_qs.count(), available_count)

        expected_locked = min(
            already_locked_count + available_qs.count(),
            LOCK_LIMIT
        )

        # make lock request
        url = self._get_url()
        response = self.client.post(
            url, format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(self.logged_in_user)
        )
        self.assertEqual(response.status_code, status.HTTP_303_SEE_OTHER)

        # check that expected_locked got locked
        self.assertEqual(locked_qs.count(), expected_locked)

        return locked_qs

    def test_lock_with_none_locked_already(self):
        locked_credits = self._test_lock(already_locked_count=0)

        # check logs
        self.assertEqual(
            Log.objects.filter(
                user=self.logged_in_user,
                action=LOG_ACTIONS.LOCKED,
                credit__id__in=locked_credits.values_list('id', flat=True)
            ).count(),
            locked_credits.count()
        )

    def test_lock_with_max_locked_already(self):
        self._test_lock(already_locked_count=LOCK_LIMIT)

    def test_lock_with_some_locked_already(self):
        self._test_lock(already_locked_count=(LOCK_LIMIT/2))

    def test_lock_with_some_locked_already_but_none_available(self):
        self._test_lock(already_locked_count=(LOCK_LIMIT/2), available_count=0)


class UnlockCreditTestCase(
    CashbookCreditRejectsRequestsWithoutPermissionTestMixin,
    BaseCreditViewTestCase
):
    ENDPOINT_VERB = 'post'

    def _get_url(self):
        return reverse('credit-unlock')

    def test_can_unlock_somebody_else_s_credits(self):
        logged_in_user = self.prison_clerks[0]
        logged_in_user.prisonusermapping.prisons.add(*self.prisons)
        locked_qs = self._get_locked_credits_qs(self.prisons, logged_in_user)

        to_unlock = list(locked_qs.values_list('id', flat=True))
        response = self.client.post(
            self._get_url(),
            {'credit_ids': to_unlock},
            format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )

        self.assertEqual(response.status_code, status.HTTP_303_SEE_OTHER)
        self.assertEqual(
            urllib.parse.urlsplit(response['Location']).path,
            reverse('credit-list')
        )

        self.assertEqual(locked_qs.count(), 0)

        # check logs
        self.assertEqual(
            Log.objects.filter(
                user=logged_in_user,
                action=LOG_ACTIONS.UNLOCKED,
                credit__id__in=to_unlock
            ).count(),
            len(to_unlock)
        )

    def test_cannot_unlock_somebody_else_s_credits_in_different_prison(self):
        # logged-in user managing prison #0
        logged_in_user = self.prison_clerks[0]
        logged_in_user.prisonusermapping.prisons.clear()
        logged_in_user.prisonusermapping.prisons.add(self.prisons[0])

        # other user managing prison #1
        other_user = self.prison_clerks[1]
        other_user.prisonusermapping.prisons.add(self.prisons[1])

        locked_qs = self._get_locked_credits_qs(self.prisons, other_user)
        locked_qs.update(prison=self.prisons[1])
        to_unlock = locked_qs.values_list('id', flat=True)

        with silence_logger():
            response = self.client.post(
                self._get_url(),
                {'credit_ids': to_unlock},
                format='json',
                HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
            )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        errors = response.data['errors']
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['msg'], 'Some credits could not be unlocked.')
        self.assertEqual(errors[0]['ids'], sorted(to_unlock))

    def test_cannot_unlock_credited_credits(self):
        logged_in_user = self.prison_clerks[0]
        managing_prisons = list(PrisonUserMapping.objects.get_prison_set_for_user(logged_in_user))

        locked_qs = self._get_locked_credits_qs(managing_prisons, user=logged_in_user)
        credited_qs = self._get_credited_credits_qs(managing_prisons, user=logged_in_user)

        locked_ids = list(locked_qs.values_list('id', flat=True))
        credited_ids = list(credited_qs.values_list('id', flat=True)[:1])

        with silence_logger():
            response = self.client.post(
                self._get_url(),
                {'credit_ids': locked_ids + credited_ids},
                format='json',
                HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
            )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        errors = response.data['errors']
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['msg'], 'Some credits could not be unlocked.')
        self.assertEqual(errors[0]['ids'], sorted(credited_ids))

    @mock.patch('credit.managers.credit_prisons_need_updating')
    def test_unlock_sends_credit_prisons_need_updating_signal(
        self, mocked_credit_prisons_need_updating
    ):
        logged_in_user = self.prison_clerks[0]
        logged_in_user.prisonusermapping.prisons.add(*self.prisons)
        locked_qs = self._get_locked_credits_qs(self.prisons, logged_in_user)

        response = self.client.post(
            self._get_url(),
            {'credit_ids': list(locked_qs.values_list('id', flat=True))},
            format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )

        self.assertEqual(response.status_code, status.HTTP_303_SEE_OTHER)

        mocked_credit_prisons_need_updating.send.assert_called_with(sender=Credit)


class MarkCreditsCreditedTestCase(
    CashbookCreditRejectsRequestsWithoutPermissionTestMixin,
    BaseCreditViewTestCase
):
    ENDPOINT_VERB = 'patch'

    def _get_url(self, **filters):
        return reverse('credit-list')

    def test_mark_credits_credited(self):
        logged_in_user = self.prison_clerks[0]
        managing_prisons = list(PrisonUserMapping.objects.get_prison_set_for_user(logged_in_user))

        locked_qs = self._get_locked_credits_qs(managing_prisons, logged_in_user)
        credited_qs = self._get_credited_credits_qs(managing_prisons, logged_in_user)

        self.assertTrue(locked_qs.count() > 0)

        to_credit = list(locked_qs.values_list('id', flat=True))

        data = [
            {'id': t_id, 'credited': True} for t_id in to_credit
        ]
        response = self.client.patch(
            self._get_url(), data=data,
            format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        # check db
        self.assertEqual(
            credited_qs.filter(id__in=to_credit).count(), len(to_credit)
        )
        # check logs
        self.assertEqual(
            Log.objects.filter(
                user=logged_in_user,
                action=LOG_ACTIONS.CREDITED,
                credit__id__in=to_credit
            ).count(),
            len(to_credit)
        )

    def test_cannot_mark_somebody_else_s_credits_credited(self):
        logged_in_user = self.prison_clerks[0]
        other_user = self.prison_clerks[1]

        locked_qs = self._get_locked_credits_qs(self.prisons, logged_in_user)
        credited_qs = self._get_credited_credits_qs(self.prisons, logged_in_user)
        locked_by_other_user_qs = self._get_locked_credits_qs(self.prisons, other_user)

        credited = credited_qs.count()

        locked_by_other_user_ids = list(locked_by_other_user_qs.values_list('id', flat=True))
        data = [
            {'id': t_id, 'credited': True}
            for t_id in locked_qs.values_list('id', flat=True)
        ] + [
            {'id': t_id, 'credited': True}
            for t_id in locked_by_other_user_ids
        ]

        with silence_logger():
            response = self.client.patch(
                self._get_url(), data=data,
                format='json',
                HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
            )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        errors = response.data['errors']
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['msg'], 'Some credits could not be marked credited.')
        self.assertEqual(errors[0]['ids'], sorted(locked_by_other_user_ids))

        # nothing changed in db
        self.assertEqual(credited_qs.count(), credited)

    def test_cannot_mark_non_locked_credits_credited(self):
        logged_in_user = self.prison_clerks[0]
        managing_prisons = list(PrisonUserMapping.objects.get_prison_set_for_user(logged_in_user))

        locked_qs = self._get_locked_credits_qs(managing_prisons, logged_in_user)
        credited_qs = self._get_credited_credits_qs(self.prisons, logged_in_user)
        available_qs = self._get_available_credits_qs(managing_prisons, logged_in_user)

        credited = credited_qs.count()

        available_ids = available_qs.values_list('id', flat=True)
        data = [
            {'id': t_id, 'credited': True}
            for t_id in locked_qs.values_list('id', flat=True)
        ] + [
            {'id': t_id, 'credited': True}
            for t_id in available_ids
        ]

        with silence_logger():
            response = self.client.patch(
                self._get_url(), data=data,
                format='json',
                HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
            )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        errors = response.data['errors']
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['msg'], 'Some credits could not be marked credited.')
        self.assertEqual(errors[0]['ids'], sorted(available_ids))

        # nothing changed in db
        self.assertEqual(credited_qs.count(), credited)

    def test_invalid_format(self):
        logged_in_user = self.prison_clerks[0]

        response = self.client.patch(
            self._get_url(), data={},
            format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_ids(self):
        logged_in_user = self.prison_clerks[0]

        response = self.client.patch(
            self._get_url(), data=[
                {'credited': True}
            ],
            format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_misspelt_credit(self):
        logged_in_user = self.prison_clerks[0]
        managing_prisons = list(PrisonUserMapping.objects.get_prison_set_for_user(logged_in_user))

        locked_qs = self._get_locked_credits_qs(managing_prisons, logged_in_user)

        data = [
            {'id': t_id, 'credted': True}
            for t_id in locked_qs.values_list('id', flat=True)
        ]

        response = self.client.patch(
            self._get_url(), data=data,
            format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CreditCreditsTestCase(
    CashbookCreditRejectsRequestsWithoutPermissionTestMixin,
    BaseCreditViewTestCase
):
    ENDPOINT_VERB = 'post'

    def _get_url(self, **filters):
        return reverse('credit-credit')

    def test_credit_credits(self):
        logged_in_user = self.prison_clerks[0]
        managing_prisons = list(PrisonUserMapping.objects.get_prison_set_for_user(logged_in_user))

        available_qs = self._get_available_credits_qs(managing_prisons, logged_in_user)
        credited_qs = self._get_credited_credits_qs(managing_prisons, logged_in_user)

        self.assertTrue(available_qs.count() > 0)

        to_credit = list(available_qs.values_list('id', flat=True))

        data = {
            'credit_ids': to_credit
        }
        response = self.client.post(
            self._get_url(), data=data,
            format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        # check db
        self.assertEqual(
            credited_qs.filter(id__in=to_credit).count(), len(to_credit)
        )
        # check logs
        self.assertEqual(
            Log.objects.filter(
                user=logged_in_user,
                action=LOG_ACTIONS.CREDITED,
                credit__id__in=to_credit
            ).count(),
            len(to_credit)
        )

    def test_missing_ids(self):
        logged_in_user = self.prison_clerks[0]

        response = self.client.post(
            self._get_url(), data={
                'credit_ids': []
            },
            format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_invalid_format(self):
        logged_in_user = self.prison_clerks[0]

        response = self.client.post(
            self._get_url(), data={},
            format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ReviewCreditTestCase(
    CashbookCreditRejectsRequestsWithoutPermissionTestMixin,
    BaseCreditViewTestCase
):
    ENDPOINT_VERB = 'post'

    def _get_authorised_user(self):
        return self.security_staff[0]

    def _get_url(self):
        return reverse('credit-review')

    def test_can_mark_credits_reviewed(self):
        logged_in_user = self._get_authorised_user()
        reviewed = Credit.objects.available()[:10].values_list('id', flat=True)

        response = self.client.post(
            self._get_url(),
            {'credit_ids': reviewed},
            format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(logged_in_user)
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        self.assertEqual(
            set(reviewed),
            set(Credit.objects.filter(reviewed=True).values_list('id', flat=True))
        )

        reviewed_logs = Log.objects.filter(
            user=logged_in_user,
            action=LOG_ACTIONS.REVIEWED,
        )
        self.assertEqual(len(reviewed_logs), len(reviewed))
        for log in reviewed_logs:
            self.assertTrue(log.credit.id in reviewed)
