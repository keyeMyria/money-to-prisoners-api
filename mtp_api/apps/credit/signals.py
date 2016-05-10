from django.dispatch import Signal

credit_created = Signal(providing_args=['credit', 'by_user'])
credit_locked = Signal(providing_args=['credit', 'by_user'])
credit_unlocked = Signal(providing_args=['credit', 'by_user'])
credit_credited = Signal(providing_args=['credit', 'by_user'])
credit_refunded = Signal(providing_args=['credit', 'by_user'])
credit_reconciled = Signal(providing_args=['credit', 'by_user'])

credit_prisons_need_updating = Signal()