from functools import reduce
from itertools import chain

from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _
from model_utils.models import TimeStampedModel

from core.models import ScheduledCommand
from prison.models import Prison
from .constants import TIME_PERIOD
from .managers import PrisonProfileManager
from .signals import prisoner_profile_current_prisons_need_updating


class SenderProfile(TimeStampedModel):
    prisons = models.ManyToManyField(Prison, related_name='senders')

    class Meta:
        ordering = ('created',)
        permissions = (
            ('view_senderprofile', 'Can view sender profile'),
        )

    def __str__(self):
        return 'Sender %s' % self.id

    @property
    def credit_filters(self):
        try:
            return reduce(
                models.Q.__or__,
                chain(
                    (
                        models.Q(transaction__sender_name=d.sender_name,
                                 transaction__sender_sort_code=d.sender_sort_code,
                                 transaction__sender_account_number=d.sender_account_number,
                                 transaction__sender_roll_number=d.sender_roll_number)
                        for d in self.bank_transfer_details.all()
                    ),
                    (
                        models.Q(payment__card_number_last_digits=d.card_number_last_digits,
                                 payment__card_expiry_date=d.card_expiry_date)
                        for d in self.debit_card_details.all()
                    )
                )
            )
        except TypeError:
            return models.Q(pk=None)

    def get_sender_names(self):
        yield from (details.sender_name for details in self.bank_transfer_details.all())
        for details in self.debit_card_details.all():
            yield from (cardholder.name for cardholder in details.cardholder_names.all())

    def get_sorted_sender_names(self):
        return sorted(set(filter(lambda name: (name or '').strip() or _('(Unknown)'), self.get_sender_names())))


class SenderTotals(models.Model):
    credit_count = models.IntegerField(default=0)
    credit_total = models.IntegerField(default=0)
    prison_count = models.IntegerField(default=0)
    prisoner_count = models.IntegerField(default=0)

    time_period = models.CharField(max_length=50, choices=TIME_PERIOD)
    sender_profile = models.ForeignKey(
        SenderProfile, on_delete=models.CASCADE, related_name='totals'
    )


class BankTransferSenderDetails(TimeStampedModel):
    sender_name = models.CharField(max_length=250, blank=True)
    sender_sort_code = models.CharField(max_length=50, blank=True)
    sender_account_number = models.CharField(max_length=50, blank=True)
    sender_roll_number = models.CharField(max_length=50, blank=True)
    sender = models.ForeignKey(
        SenderProfile, on_delete=models.CASCADE, related_name='bank_transfer_details'
    )

    class Meta:
        ordering = ('created',)
        verbose_name_plural = 'bank transfer sender details'
        unique_together = (
            ('sender_name', 'sender_sort_code', 'sender_account_number', 'sender_roll_number'),
        )

    def __str__(self):
        return self.sender_name


class DebitCardSenderDetails(TimeStampedModel):
    card_number_last_digits = models.CharField(max_length=4, blank=True, null=True, db_index=True)
    card_expiry_date = models.CharField(max_length=5, blank=True, null=True)
    postcode = models.CharField(max_length=250, blank=True, null=True, db_index=True)
    sender = models.ForeignKey(
        SenderProfile, on_delete=models.CASCADE, related_name='debit_card_details'
    )

    class Meta:
        ordering = ('created',)
        verbose_name_plural = 'debit card sender details'
        unique_together = (
            ('card_number_last_digits', 'card_expiry_date', 'postcode',),
        )

    def __str__(self):
        return '%s %s' % (self.card_number_last_digits, self.card_expiry_date)


class CardholderName(models.Model):
    name = models.CharField(max_length=250)
    debit_card_sender_details = models.ForeignKey(
        DebitCardSenderDetails, on_delete=models.CASCADE,
        related_name='cardholder_names', related_query_name='cardholder_name'
    )

    class Meta:
        ordering = ('pk',)

    def __str__(self):
        return self.name


class SenderEmail(models.Model):
    email = models.CharField(max_length=250)
    debit_card_sender_details = models.ForeignKey(
        DebitCardSenderDetails, on_delete=models.CASCADE,
        related_name='sender_emails', related_query_name='sender_email'
    )

    class Meta:
        ordering = ('pk',)

    def __str__(self):
        return self.email


class PrisonerProfile(TimeStampedModel):
    prisoner_name = models.CharField(max_length=250)
    prisoner_number = models.CharField(max_length=250, db_index=True)
    single_offender_id = models.UUIDField(blank=True, null=True)
    prisoner_dob = models.DateField()
    current_prison = models.ForeignKey(
        Prison, on_delete=models.SET_NULL, null=True, related_name='current_prisoners'
    )

    prisons = models.ManyToManyField(Prison, related_name='historic_prisoners')
    senders = models.ManyToManyField(SenderProfile, related_name='prisoners')

    objects = PrisonProfileManager()

    class Meta:
        ordering = ('created',)
        permissions = (
            ('view_prisonerprofile', 'Can view prisoner profile'),
        )
        unique_together = (
            ('prisoner_number', 'prisoner_dob',),
        )

    def __str__(self):
        return self.prisoner_number

    @property
    def credit_filters(self):
        return models.Q(prisoner_name=self.prisoner_name, prisoner_dob=self.prisoner_dob)


class PrisonerTotals(models.Model):
    credit_count = models.IntegerField(default=0)
    credit_total = models.IntegerField(default=0)
    sender_count = models.IntegerField(default=0)

    time_period = models.CharField(max_length=50, choices=TIME_PERIOD)
    prisoner_profile = models.ForeignKey(
        PrisonerProfile, on_delete=models.CASCADE, related_name='totals'
    )


class PrisonerRecipientName(models.Model):
    name = models.CharField(max_length=250)
    prisoner = models.ForeignKey(
        PrisonerProfile, on_delete=models.CASCADE,
        related_name='recipient_names', related_query_name='recipient_name',
    )

    class Meta:
        ordering = ('pk',)

    def __str__(self):
        return self.name


class SavedSearch(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    description = models.CharField(max_length=255)
    endpoint = models.CharField(max_length=255)
    last_result_count = models.IntegerField(default=0)
    site_url = models.CharField(max_length=1000, null=True, blank=True)

    class Meta:
        ordering = ('created',)

    def __str__(self):
        return '{user}: {title}'.format(user=self.user.username, title=self.description)


class SearchFilter(models.Model):
    field = models.CharField(max_length=255)
    value = models.CharField(max_length=255)
    saved_search = models.ForeignKey(
        SavedSearch, on_delete=models.CASCADE, related_name='filters'
    )

    def __str__(self):
        return '{field}={value}'.format(field=self.field, value=self.value)


@receiver(prisoner_profile_current_prisons_need_updating)
def update_current_prisons(*args, **kwargs):
    job = ScheduledCommand(
        name='update_current_prisons',
        arg_string='',
        cron_entry='*/10 * * * *',
        delete_after_next=True
    )
    job.save()


@receiver(post_save, sender=SenderProfile, dispatch_uid='post_save_create_sender_totals')
def post_save_create_sender_totals(sender, instance, created, *args, **kwargs):
    if created:
        sender_profiles = []
        for time_period in TIME_PERIOD:
            sender_profiles.append(SenderTotals(
                sender_profile=instance, time_period=time_period
            ))
        SenderProfile.objects.bulk_create(sender_profiles)


@receiver(post_save, sender=PrisonerProfile, dispatch_uid='post_save_create_prisoner_totals')
def post_save_create_prisoner_totals(sender, instance, created, *args, **kwargs):
    if created:
        prisoner_profiles = []
        for time_period in TIME_PERIOD:
            prisoner_profiles.append(PrisonerTotals(
                prisoner_profile=instance, time_period=time_period
            ))
        PrisonerProfile.objects.bulk_create(prisoner_profiles)
