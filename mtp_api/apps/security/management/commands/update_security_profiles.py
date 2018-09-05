import logging

from django.db.models import Count, Sum, Subquery, OuterRef, Q
from django.db.models.functions import Coalesce
from django.db.transaction import atomic
from django.core.management import BaseCommand, CommandError

from credit.models import Credit
from security.constants import TIME_PERIOD, get_start_date_for_time_period
from security.models import (
    SenderProfile, BankTransferSenderDetails, DebitCardSenderDetails,
    CardholderName, SenderEmail, PrisonerProfile, PrisonerRecipientName,
    SenderTotals, PrisonerTotals
)

logger = logging.getLogger('mtp')


class Command(BaseCommand):
    anonymous_sender = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.verbose = False

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument('--batch-size', type=int, default=200,
                            help='Number of credits to process in one atomic transaction')
        parser.add_argument('--totals', action='store_true', help='Recalculates the credit counts and totals only')
        parser.add_argument('--recreate', action='store_true', help='Deletes existing sender and prisoner profiles')

    def handle(self, **options):
        if options['totals'] and options['recreate']:
            raise CommandError('Cannot recalculate totals when deleting all profiles')

        self.verbose = options['verbosity'] > 1

        batch_size = options['batch_size']
        if batch_size < 1:
            raise CommandError('Batch size must be at least 1')

        if options['totals']:
            self.handle_totals()
        else:
            self.handle_update(batch_size=batch_size, recreate=options['recreate'])

    def handle_update(self, batch_size, recreate):
        if recreate:
            self.delete_profiles()

        new_credits = Credit.objects.filter(sender_profile__isnull=True).order_by('pk')
        new_credits_count = new_credits.count()
        if not new_credits_count:
            self.stdout.write(self.style.SUCCESS('No new credits'))
            return
        else:
            self.stdout.write('Updating profiles for (at least) %d new credits' % new_credits_count)

        try:
            self.anonymous_sender = SenderProfile.objects.get(bank_transfer_details__isnull=True,
                                                              debit_card_details__isnull=True)
        except SenderProfile.DoesNotExist:
            if self.verbose:
                self.stdout.write('Creating anonymous sender')
            self.anonymous_sender = SenderProfile.objects.create()
        except SenderProfile.MultipleObjectsReturned:
            raise CommandError('Multiple sender profiles exist with no linked debit card or bank transfer details')

        try:
            processed_count = 0
            while True:
                count = self.process_batch(new_credits[0:batch_size])
                if count == 0:
                    break
                processed_count += count
                if self.verbose:
                    self.stdout.write('Processed %d credits' % processed_count)
        finally:
            self.stdout.write('Updating prisoner profiles for current locations')
            PrisonerProfile.objects.update_current_prisons()
            self.handle_totals()

        self.stdout.write(self.style.SUCCESS('Done'))

    @atomic()
    def process_batch(self, new_credits):
        for credit in new_credits:
            self.create_or_update_profiles(credit)
        return len(new_credits)

    def create_or_update_profiles(self, credit):
        sender_profile = self.create_or_update_sender_profile(credit)
        credit.sender_profile = sender_profile
        if credit.prison:
            sender_profile.prisons.add(credit.prison)
            prisoner_profile = self.create_or_update_prisoner_profile(credit, sender_profile)
            credit.prisoner_profile = prisoner_profile
        credit.save()

    def create_or_update_sender_profile(self, credit):
        if hasattr(credit, 'transaction'):
            sender_profile = self.create_or_update_bank_transfer(credit)
        elif hasattr(credit, 'payment'):
            sender_profile = self.create_or_update_debit_card(credit)
        else:
            logger.error('Credit %s does not have a payment nor transaction' % credit.pk)
            sender_profile = self.anonymous_sender

        sender_profile.save()
        return sender_profile

    def create_or_update_bank_transfer(self, credit):
        try:
            sender_profile = SenderProfile.objects.get(
                bank_transfer_details__sender_name=credit.sender_name,
                bank_transfer_details__sender_sort_code=credit.sender_sort_code,
                bank_transfer_details__sender_account_number=credit.sender_account_number,
                bank_transfer_details__sender_roll_number=credit.sender_roll_number
            )
        except SenderProfile.DoesNotExist:
            if self.verbose:
                self.stdout.write('Creating bank transfer profile for %s' % credit.sender_name)
            sender_profile = SenderProfile()
            sender_profile.save()
            sender_profile.bank_transfer_details.add(
                BankTransferSenderDetails(
                    sender_name=credit.sender_name,
                    sender_sort_code=credit.sender_sort_code,
                    sender_account_number=credit.sender_account_number,
                    sender_roll_number=credit.sender_roll_number
                ),
                bulk=False
            )
        return sender_profile

    def create_or_update_debit_card(self, credit):
        sender_name = credit.sender_name or ''
        sender_email = credit.payment.email or ''
        billing_address = credit.payment.billing_address
        normalised_postcode = billing_address.normalised_postcode if billing_address else None
        try:
            sender_details = DebitCardSenderDetails.objects.get(
                card_number_last_digits=credit.card_number_last_digits,
                card_expiry_date=credit.card_expiry_date,
                postcode=normalised_postcode
            )
            sender_profile = sender_details.sender
            if sender_name:
                try:
                    sender_details.cardholder_names.get(
                        name=sender_name
                    )
                except CardholderName.DoesNotExist:
                    sender_details.cardholder_names.add(
                        CardholderName(name=sender_name),
                        bulk=False
                    )
            if sender_email:
                try:
                    sender_details.sender_emails.get(
                        email=sender_email
                    )
                except SenderEmail.DoesNotExist:
                    sender_details.sender_emails.add(
                        SenderEmail(email=sender_email),
                        bulk=False
                    )
        except DebitCardSenderDetails.DoesNotExist:
            if self.verbose:
                self.stdout.write('Creating debit card profile for ****%s, %s' % (credit.card_number_last_digits,
                                                                                  sender_name))
            sender_profile = SenderProfile()
            sender_profile.save()
            sender_profile.debit_card_details.add(
                DebitCardSenderDetails(
                    card_number_last_digits=credit.card_number_last_digits,
                    card_expiry_date=credit.card_expiry_date,
                    postcode=normalised_postcode
                ),
                bulk=False
            )
            sender_details = sender_profile.debit_card_details.first()
            if sender_name:
                sender_details.cardholder_names.add(
                    CardholderName(name=sender_name), bulk=False
                )
            if sender_email:
                sender_details.sender_emails.add(
                    SenderEmail(email=sender_email), bulk=False
                )

        if billing_address:
            billing_address.debit_card_sender_details = sender_details
            billing_address.save()

        return sender_profile

    def create_or_update_prisoner_profile(self, credit, sender):
        recipient_name = ''
        if hasattr(credit, 'payment'):
            recipient_name = credit.payment.recipient_name or recipient_name
        try:
            prisoner_profile = PrisonerProfile.objects.get(
                prisoner_number=credit.prisoner_number,
                prisoner_dob=credit.prisoner_dob
            )
            if recipient_name:
                try:
                    prisoner_profile.recipient_names.get(
                        name=recipient_name
                    )
                except PrisonerRecipientName.DoesNotExist:
                    prisoner_profile.recipient_names.add(
                        PrisonerRecipientName(name=recipient_name),
                        bulk=False
                    )
        except PrisonerProfile.DoesNotExist:
            if self.verbose:
                self.stdout.write('Creating prisoner profile for %s' % credit.prisoner_number)
            prisoner_profile = PrisonerProfile(
                single_offender_id=credit.single_offender_id,
                prisoner_name=credit.prisoner_name,
                prisoner_number=credit.prisoner_number,
                prisoner_dob=credit.prisoner_dob
            )
            prisoner_profile.save()
            if recipient_name:
                prisoner_profile.recipient_names.add(
                    PrisonerRecipientName(name=recipient_name), bulk=False
                )

        prisoner_profile.prisons.add(credit.prison)
        prisoner_profile.senders.add(sender)
        prisoner_profile.save()
        return prisoner_profile

    def handle_totals(self):
        self.update_sender_profile_counts()
        self.update_prisoner_profile_counts()
        self.stdout.write(self.style.SUCCESS('Done'))

    def update_sender_profile_counts(self):
        self.update_credit_counts()
        self.update_credit_totals()
        self.update_prisoner_counts()
        self.update_prison_counts()

    def update_prisoner_profile_counts(self):
        self.update_credit_counts()
        self.update_credit_totals()
        self.update_sender_counts()

    def update_credit_counts(self):
        for profile, totals in [
            (SenderProfile, SenderTotals), (PrisonerProfile, PrisonerTotals)
        ]:
            for time_period in TIME_PERIOD.values:
                start = get_start_date_for_time_period(time_period)
                related_field = profile.credits.rel.remote_field.name
                totals.objects.filter(time_period=time_period).update(
                    credit_count=Coalesce(Subquery(
                        profile.objects.filter(
                            id=OuterRef('%s_id' % related_field),
                        ).annotate(
                            credit_count=Count(
                                'credits',
                                filter=Q(
                                    credits__received_at__gte=start,
                                ),
                                distinct=True
                            )
                        ).values('credit_count')[:1]
                    ), 0)
                )

    def update_credit_totals(self):
        for profile, totals in [
            (SenderProfile, SenderTotals), (PrisonerProfile, PrisonerTotals)
        ]:
            for time_period in TIME_PERIOD.values:
                start = get_start_date_for_time_period(time_period)
                related_field = profile.credits.rel.remote_field.name
                totals.objects.filter(time_period=time_period).update(
                    credit_total=Coalesce(Subquery(
                        profile.objects.filter(
                            id=OuterRef('%s_id' % related_field),
                        ).annotate(
                            credit_total=Sum(
                                'credits__amount',
                                filter=Q(
                                    credits__received_at__gte=start,
                                )
                            )
                        ).values('credit_total')[:1]
                    ), 0)
                )

    def update_prisoner_counts(self):
        for time_period in TIME_PERIOD.values:
            start = get_start_date_for_time_period(time_period)
            SenderTotals.objects.filter(time_period=time_period).update(
                prisoner_count=Coalesce(Subquery(
                    SenderProfile.objects.filter(
                        id=OuterRef('sender_profile_id'),
                    ).annotate(
                        prisoner_count=Count(
                            'credits__prisoner_profile',
                            filter=Q(
                                credits__received_at__gte=start,
                            ),
                            distinct=True
                        )
                    ).values('prisoner_count')[:1]
                ), 0)
            )

    def update_prison_counts(self):
        for time_period in TIME_PERIOD.values:
            start = get_start_date_for_time_period(time_period)
            SenderTotals.objects.filter(time_period=time_period).update(
                prison_count=Coalesce(Subquery(
                    SenderProfile.objects.filter(
                        id=OuterRef('sender_profile_id'),
                    ).annotate(
                        prison_count=Count(
                            'credits__prison',
                            filter=Q(
                                credits__received_at__gte=start,
                            ),
                            distinct=True
                        )
                    ).values('prison_count')[:1]
                ), 0)
            )

    def update_sender_counts(self):
        for time_period in TIME_PERIOD.values:
            start = get_start_date_for_time_period(time_period)
            PrisonerTotals.objects.filter(time_period=time_period).update(
                sender_count=Coalesce(Subquery(
                    PrisonerProfile.objects.filter(
                        id=OuterRef('prisoner_profile_id'),
                    ).annotate(
                        sender_count=Count(
                            'credits__sender_profile',
                            filter=Q(
                                credits__received_at__gte=start,
                            ),
                            distinct=True
                        )
                    ).values('sender_count')[:1]
                ), 0)
            )

    @atomic()
    def delete_profiles(self):
        from django.apps import apps
        from django.core.management.color import no_style
        from django.db import connection

        PrisonerProfile.objects.all().delete()
        SenderProfile.objects.all().delete()

        security_app = apps.app_configs['security']
        with connection.cursor() as cursor:
            for reset_sql in connection.ops.sequence_reset_sql(no_style(), security_app.get_models()):
                cursor.execute(reset_sql)
