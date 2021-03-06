from django.db.transaction import atomic
from rest_framework import serializers

from credit.constants import CREDIT_RESOLUTION
from credit.models import Credit
from .models import Batch, BillingAddress, Payment
from .constants import PAYMENT_STATUS
from .exceptions import InvalidStateForUpdateException


class BatchSerializer(serializers.ModelSerializer):
    payment_amount = serializers.IntegerField()

    class Meta:
        model = Batch
        fields = '__all__'


class BillingAddressSerializer(serializers.ModelSerializer):

    class Meta:
        model = BillingAddress
        fields = '__all__'


class PaymentSerializer(serializers.ModelSerializer):
    prisoner_dob = serializers.DateField()
    prisoner_number = serializers.CharField()
    received_at = serializers.DateTimeField(required=False)
    billing_address = BillingAddressSerializer(required=False)

    class Meta:
        model = Payment
        read_only = ('uuid', 'prisoner_dob', 'prisoner_number')
        fields = (
            'uuid',
            'status',
            'processor_id',
            'amount',
            'service_charge',
            'recipient_name',
            'email',
            'prisoner_dob',
            'prisoner_number',
            'received_at',
            'cardholder_name',
            'card_number_last_digits',
            'card_expiry_date',
            'card_brand',
            'ip_address',
            'billing_address',
            'modified',
        )

    @atomic
    def create(self, validated_data):
        new_credit = Credit(
            amount=validated_data['amount'],
            prisoner_number=validated_data.pop('prisoner_number'),
            prisoner_dob=validated_data.pop('prisoner_dob'),
            resolution=CREDIT_RESOLUTION.INITIAL
        )
        new_credit.save()
        validated_data['credit'] = new_credit
        return super().create(validated_data)

    @atomic
    def update(self, instance, validated_data):
        if instance.status != PAYMENT_STATUS.PENDING:
            raise InvalidStateForUpdateException(
                'Payment cannot be updated in status "%s"'
                % instance.status
            )
        billing_address = validated_data.pop('billing_address', None)
        if billing_address:
            if instance.billing_address:
                BillingAddress.objects.filter(
                    pk=instance.billing_address.pk
                ).update(**billing_address)
            else:
                new_address = BillingAddress.objects.create(**billing_address)
                validated_data['billing_address'] = new_address

        received_at = validated_data.pop('received_at', None)
        if received_at:
            instance.credit.received_at = received_at
            instance.credit.save()

        return super().update(instance, validated_data)
