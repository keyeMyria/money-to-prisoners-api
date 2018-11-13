from django.conf.urls import url, include

from rest_framework_nested import routers

from . import views

router = routers.SimpleRouter()
router.register(r'senders', views.SenderProfileView)
sender_router = routers.NestedSimpleRouter(router, r'senders', lookup='sender')
sender_router.register(
    r'credits',
    views.SenderProfileCreditsView,
    base_name='sender-credits'
)

router.register(r'recipients', views.RecipientProfileView)
recipient_router = routers.NestedSimpleRouter(router, r'recipients', lookup='recipient')
recipient_router.register(
    r'disbursements',
    views.RecipientProfileDisbursementsView,
    base_name='recipient-disbursements'
)

router.register(r'prisoners', views.PrisonerProfileView)
prisoner_router = routers.NestedSimpleRouter(router, r'prisoners', lookup='prisoner')
prisoner_router.register(
    r'credits',
    views.PrisonerProfileCreditsView,
    base_name='prisoner-credits'
)
prisoner_router.register(
    r'disbursements',
    views.PrisonerProfileDisbursementsView,
    base_name='prisoner-disbursements'
)

router.register(r'searches', views.SavedSearchView)

urlpatterns = [
    url(r'^', include(router.urls)),
    url(r'^', include(sender_router.urls)),
    url(r'^', include(recipient_router.urls)),
    url(r'^', include(prisoner_router.urls)),
]
