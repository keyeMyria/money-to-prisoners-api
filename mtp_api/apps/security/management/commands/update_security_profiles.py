import logging

from django.db.transaction import atomic
from django.core.management import BaseCommand, CommandError

from credit.models import Credit
from disbursement.constants import DISBURSEMENT_METHOD
from disbursement.models import Disbursement
from prison.models import PrisonerLocation
from security.models import (
    SenderProfile, BankTransferSenderDetails, DebitCardSenderDetails,
    CardholderName, SenderEmail, PrisonerProfile, ProvidedPrisonerName,
    BankAccount, RecipientProfile, BankTransferRecipientDetails
)

logger = logging.getLogger('mtp')


class Command(BaseCommand):
    anonymous_sender = None
    cheque_recipient = None

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
            self.handle_totals(batch_size=batch_size)
        else:
            self.handle_update(batch_size=batch_size, recreate=options['recreate'])

    def handle_update(self, batch_size, recreate):
        if recreate:
            self.delete_profiles()

        try:
            self.handle_credit_update(batch_size, recreate)
            self.handle_disbursement_update(batch_size, recreate)
        finally:
            self.stdout.write('Updating prisoner profiles for current locations')
            PrisonerProfile.objects.update_current_prisons()

    def handle_credit_update(self, batch_size, recreate):
        new_credits = Credit.objects.filter(sender_profile__isnull=True).order_by('pk')
        new_credits_count = new_credits.count()
        if not new_credits_count:
            self.stdout.write(self.style.SUCCESS('No new credits'))
            return
        else:
            self.stdout.write('Updating profiles for (at least) %d new credits' % new_credits_count)

        try:
            self.anonymous_sender = SenderProfile.objects.get(
                bank_transfer_details__isnull=True,
                debit_card_details__isnull=True
            )
        except SenderProfile.DoesNotExist:
            if self.verbose:
                self.stdout.write('Creating anonymous sender')
            self.anonymous_sender = SenderProfile.objects.create()
        except SenderProfile.MultipleObjectsReturned:
            raise CommandError('Multiple sender profiles exist with no linked debit card or bank transfer details')

        processed_count = 0
        while True:
            count = self.process_credit_batch(new_credits[0:batch_size])
            if count == 0:
                break
            processed_count += count
            if self.verbose:
                self.stdout.write('Processed %d credits' % processed_count)

        self.stdout.write(self.style.SUCCESS('Done'))

    def handle_disbursement_update(self, batch_size, recreate):
        new_disbursements = Disbursement.objects.filter(
            recipient_profile__isnull=True
        ).order_by('pk')
        new_disbursements_count = new_disbursements.count()
        if not new_disbursements_count:
            self.stdout.write(self.style.SUCCESS('No new disbursements'))
            return
        else:
            self.stdout.write('Updating profiles for (at least) %d new disbursements' % new_disbursements_count)

        try:
            self.cheque_recipient = RecipientProfile.objects.get(
                bank_transfer_details__isnull=True
            )
        except RecipientProfile.DoesNotExist:
            if self.verbose:
                self.stdout.write('Creating anonymous recipient')
            self.cheque_recipient = RecipientProfile.objects.create()
        except RecipientProfile.MultipleObjectsReturned:
            raise CommandError('Multiple recipient profiles exist with no linked bank transfer details')

        processed_count = 0
        while True:
            count = self.process_disbursement_batch(new_disbursements[0:batch_size])
            if count == 0:
                break
            processed_count += count
            if self.verbose:
                self.stdout.write('Processed %d disbursements' % processed_count)

        self.stdout.write(self.style.SUCCESS('Done'))

    @atomic()
    def process_credit_batch(self, new_credits):
        for credit in new_credits:
            self.create_or_update_profiles_for_credit(credit)
        return len(new_credits)

    @atomic()
    def process_disbursement_batch(self, new_disbursements):
        for disbursement in new_disbursements:
            self.create_or_update_profiles_for_disbursement(disbursement)
        return len(new_disbursements)

    def create_or_update_profiles_for_credit(self, credit):
        sender_profile = self.create_or_update_sender_profile(credit)
        credit.sender_profile = sender_profile
        if credit.prison:
            sender_profile.prisons.add(credit.prison)
            prisoner_profile = self.create_or_update_prisoner_profile_for_credit(
                credit, sender_profile
            )
            credit.prisoner_profile = prisoner_profile
        credit.save()

    def create_or_update_profiles_for_disbursement(self, disbursement):
        recipient_profile = self.create_or_update_recipient_profile(disbursement)
        disbursement.recipient_profile = recipient_profile
        recipient_profile.prisons.add(disbursement.prison)
        prisoner_profile = self.create_or_update_prisoner_profile_for_disbursement(
            disbursement, recipient_profile
        )
        disbursement.prisoner_profile = prisoner_profile
        disbursement.save()

    def create_or_update_sender_profile(self, credit):
        if hasattr(credit, 'transaction'):
            sender_profile = self.create_or_update_bank_transfer(credit)
        elif hasattr(credit, 'payment'):
            sender_profile = self.create_or_update_debit_card(credit)
        else:
            logger.error('Credit %s does not have a payment nor transaction' % credit.pk)
            sender_profile = self.anonymous_sender

        sender_profile.credit_count += 1
        sender_profile.credit_total += credit.amount
        sender_profile.save()
        return sender_profile

    def create_or_update_bank_transfer(self, credit):
        try:
            sender_profile = SenderProfile.objects.get(
                bank_transfer_details__sender_name=credit.sender_name,
                bank_transfer_details__sender_bank_account__sort_code=credit.sender_sort_code,
                bank_transfer_details__sender_bank_account__account_number=credit.sender_account_number,
                bank_transfer_details__sender_bank_account__roll_number=credit.sender_roll_number
            )
        except SenderProfile.DoesNotExist:
            if self.verbose:
                self.stdout.write('Creating bank transfer profile for %s' % credit.sender_name)
            bank_account, _ = BankAccount.objects.get_or_create(
                sort_code=credit.sender_sort_code,
                account_number=credit.sender_account_number,
                roll_number=credit.sender_roll_number
            )
            sender_profile = SenderProfile()
            sender_profile.save()
            sender_profile.bank_transfer_details.add(
                BankTransferSenderDetails(
                    sender_name=credit.sender_name,
                    sender_bank_account=bank_account
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

    def create_or_update_prisoner_profile_for_credit(self, credit, sender):
        prisoner_profile, _ = PrisonerProfile.objects.update_or_create(
            prisoner_number=credit.prisoner_number,
            defaults=dict(
                prisoner_name=credit.prisoner_name,
                single_offender_id=credit.single_offender_id,
                prisoner_dob=credit.prisoner_dob,
            )
        )
        provided_name = ''
        if hasattr(credit, 'payment'):
            provided_name = credit.payment.recipient_name or provided_name
        if provided_name:
            try:
                prisoner_profile.provided_names.get(
                    name=provided_name
                )
            except ProvidedPrisonerName.DoesNotExist:
                prisoner_profile.provided_names.add(
                    ProvidedPrisonerName(name=provided_name),
                    bulk=False
                )

        prisoner_profile.credit_count += 1
        prisoner_profile.credit_total += credit.amount
        prisoner_profile.prisons.add(credit.prison)
        prisoner_profile.senders.add(sender)
        prisoner_profile.save()
        return prisoner_profile

    def create_or_update_prisoner_profile_for_disbursement(self, disbursement, recipient):
        prisoner_info = dict(prisoner_name=disbursement.prisoner_name)
        try:
            prisoner_info.update(
                prisoner_dob=PrisonerLocation.objects.get(
                    prisoner_number=disbursement.prisoner_number, active=True
                ).prisoner_dob
            )
        except PrisonerLocation.DoesNotExist:
            pass

        prisoner_profile, _ = PrisonerProfile.objects.get_or_create(
            prisoner_number=disbursement.prisoner_number,
            defaults=prisoner_info
        )

        prisoner_profile.disbursement_count += 1
        prisoner_profile.disbursement_total += disbursement.amount
        prisoner_profile.prisons.add(disbursement.prison)
        prisoner_profile.recipients.add(recipient)
        prisoner_profile.save()
        return prisoner_profile

    def create_or_update_recipient_profile(self, disbursement):
        if disbursement.method == DISBURSEMENT_METHOD.CHEQUE:
            return self.cheque_recipient

        try:
            recipient_profile = RecipientProfile.objects.get(
                bank_transfer_details__recipient_bank_account__sort_code=disbursement.sort_code,
                bank_transfer_details__recipient_bank_account__account_number=disbursement.account_number,
                bank_transfer_details__recipient_bank_account__roll_number=disbursement.roll_number or ''
            )
        except RecipientProfile.DoesNotExist:
            if self.verbose:
                self.stdout.write(
                    'Creating bank transfer profile for %s' %
                    disbursement.recipient_name
                )
            bank_account, _ = BankAccount.objects.get_or_create(
                sort_code=disbursement.sort_code,
                account_number=disbursement.account_number,
                roll_number=disbursement.roll_number or ''
            )
            recipient_profile = RecipientProfile()
            recipient_profile.save()
            recipient_profile.bank_transfer_details.add(
                BankTransferRecipientDetails(
                    recipient_bank_account=bank_account
                ),
                bulk=False
            )

        recipient_profile.disbursement_count += 1
        recipient_profile.disbursement_total += disbursement.amount
        recipient_profile.save()
        return recipient_profile

    def handle_totals(self, batch_size):
        profiles = (
            (SenderProfile, 'sender'),
            (PrisonerProfile, 'prisoner'),
        )
        for model, name in profiles:
            queryset = model.objects.order_by('pk').all()
            count = queryset.count()
            if not count:
                self.stdout.write(self.style.SUCCESS('No %s profiles to update' % name))
                return
            else:
                self.stdout.write('Updating %d %s profile totals' % (count, name))

            processed_count = 0
            for offset in range(0, count, batch_size):
                batch = slice(offset, min(offset + batch_size, count))
                processed_count += self.process_totals_batch(queryset[batch])
                if self.verbose:
                    self.stdout.write('Processed %d %s profiles' % (processed_count, name))

        self.stdout.write(self.style.SUCCESS('Done'))

    @atomic()
    def process_totals_batch(self, profile_batch):
        for profile in profile_batch:
            profile.update_totals()
        return len(profile_batch)

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
