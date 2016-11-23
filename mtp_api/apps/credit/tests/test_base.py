from datetime import datetime, time

from django.db import models
from django.utils import timezone
from oauth2_provider.models import AccessToken
from rest_framework import status
from rest_framework.test import APITestCase

from core.tests.utils import make_test_users
from mtp_auth.constants import CASHBOOK_OAUTH_CLIENT_ID
from mtp_auth.tests.utils import AuthTestCaseMixin
from payment.tests.utils import generate_payments, latest_payment_date
from prison.models import Prison
from credit.models import Credit
from credit.constants import CREDIT_STATUS, CREDIT_RESOLUTION
from prison.tests.utils import load_random_prisoner_locations
from transaction.tests.utils import generate_transactions, latest_transaction_date


class BaseCreditViewTestCase(AuthTestCaseMixin, APITestCase):
    fixtures = [
        'initial_groups.json',
        'initial_types.json',
        'test_prisons.json'
    ]
    STATUS_FILTERS = {
        None: lambda c: True,
        CREDIT_STATUS.LOCKED: lambda c: c.owner and c.resolution == CREDIT_RESOLUTION.PENDING,
        CREDIT_STATUS.AVAILABLE: lambda c: (
            c.prison and not c.owner and c.resolution == CREDIT_RESOLUTION.PENDING and
            not c.blocked
        ),
        CREDIT_STATUS.CREDITED: lambda c: c.credited
    }
    transaction_batch = 100

    def setUp(self):
        super().setUp()
        (
            self.prison_clerks, self.prisoner_location_admins,
            self.bank_admins, self.refund_bank_admins,
            self.send_money_users, self.security_staff
        ) = make_test_users(clerks_per_prison=2)

        self.latest_transaction_date = latest_transaction_date()
        self.latest_payment_date = latest_payment_date()
        load_random_prisoner_locations()
        transaction_credits = [t.credit for t in generate_transactions(
            transaction_batch=self.transaction_batch, days_of_history=5
        ) if t.credit]
        payment_credits = [t.credit for t in generate_payments(
            payment_batch=self.transaction_batch, days_of_history=5
        ) if t.credit]
        self.credits = transaction_credits + payment_credits
        self.prisons = Prison.objects.all()

    def _get_queryset(self, user=None, prisons=[]):
        qs = Credit.objects.filter(prison__in=prisons)
        if user and (user.applicationusermapping_set.first().application.client_id ==
                     CASHBOOK_OAUTH_CLIENT_ID):
            return qs.filter(
                received_at__lt=timezone.make_aware(
                    datetime.combine(timezone.now(), time.min)
                )
            )
        return qs

    def _get_locked_credits_qs(self, prisons, user=None):
        params = {
            'resolution': CREDIT_RESOLUTION.PENDING,
            'prison__in': prisons
        }
        if user:
            params['owner'] = user
        else:
            params['owner__isnull'] = False

        return self._get_queryset(user, prisons).filter(**params)

    def _get_available_credits_qs(self, prisons, user=None):
        return self._get_queryset(user, prisons).filter(
            blocked=False, owner__isnull=True, resolution=CREDIT_RESOLUTION.PENDING
        )

    def _get_credited_credits_qs(self, prisons, user=None):
        return self._get_queryset(user, prisons).filter(
            owner=user, resolution=CREDIT_RESOLUTION.CREDITED, prison__in=prisons
        )

    def _get_latest_date(self):
        return Credit.objects.all().aggregate(models.Max('received_at'))['received_at__max']


class CreditRejectsRequestsWithoutPermissionTestMixin:

    """
    Mixin for permission checks on the endpoint.

    It expects `_get_url(user, prison)`, `_get_unauthorised_application_users()`
    and `_get_authorised_user()` instance methods defined.
    """
    ENDPOINT_VERB = 'get'

    def _get_url(self, *args, **kwargs):
        raise NotImplementedError()

    def _get_unauthorised_application_users(self):
        raise NotImplementedError()

    def _get_unauthorised_user(self):
        user = self._get_authorised_user()
        user.groups.first().permissions.all().delete()
        return user

    def _get_authorised_user(self):
        raise NotImplementedError()

    def test_fails_without_application_permissions(self):
        """
        Tests that if the user logs in via a different application,
        they won't be able to access the API.
        """
        # constructing list of unauthorised users+application
        unauthorised_users = self._get_unauthorised_application_users()
        users_data = [
            (user, self.get_http_authorization_for_user(user))
            for user in unauthorised_users
        ]

        # + valid user logged in using a different oauth application
        authorised_user = self._get_authorised_user()

        invalid_client_id = AccessToken.objects.filter(
            user=unauthorised_users[0]
        ).first().application.client_id

        users_data.append(
            (
                authorised_user,
                self.get_http_authorization_for_user(authorised_user, invalid_client_id)
            )
        )

        url = self._get_url()
        for user, http_auth_header in users_data:
            verb_callable = getattr(self.client, self.ENDPOINT_VERB)
            response = verb_callable(
                url, format='json',
                HTTP_AUTHORIZATION=http_auth_header
            )

            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_fails_without_action_permissions(self):
        """
        Tests that if the user does not have permissions,
        they won't be able to access the API.
        """
        user = self._get_unauthorised_user()

        url = self._get_url()

        verb_callable = getattr(self.client, self.ENDPOINT_VERB)
        response = verb_callable(
            url, format='json',
            HTTP_AUTHORIZATION=self.get_http_authorization_for_user(user)
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
