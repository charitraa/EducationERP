from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("careers/vacancies", views.VacancyViewSet, basename="careers-vacancy")
router.register("careers/candidacies", views.CandidacyViewSet, basename="careers-candidacy")
router.register("careers/interviews", views.InterviewViewSet, basename="careers-interview")
router.register("careers/offers", views.JobOfferViewSet, basename="careers-offer")
router.register("careers/postings", views.JobPostingViewSet, basename="careers-posting")

PUBLIC = "public/organizations/<slug:code>/careers/"
urlpatterns = router.urls + [
    path(PUBLIC + "vacancies/", views.PublicVacanciesView.as_view(), name="public-vacancies"),
    path(PUBLIC + "vacancies/<int:pk>/", views.PublicVacancyView.as_view(), name="public-vacancy"),
    path(PUBLIC + "vacancies/<int:pk>/apply/", views.PublicApplyView.as_view(), name="public-vacancy-apply"),
    path(PUBLIC + "offer/", views.PublicOfferView.as_view(), name="public-offer"),
    path(PUBLIC + "offer/respond/", views.PublicOfferRespondView.as_view(), name="public-offer-respond"),
]
