from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("alumni/profiles", views.AlumniProfileViewSet, basename="alumni-profile")
router.register("alumni/employments", views.EmploymentViewSet, basename="alumni-employment")
router.register("alumni/higher-studies", views.HigherStudyViewSet, basename="alumni-higher-study")
router.register("alumni/achievements", views.AchievementViewSet, basename="alumni-achievement")
router.register("alumni/events", views.AlumniEventViewSet, basename="alumni-event")
router.register("alumni/mentorships", views.MentorshipViewSet, basename="alumni-mentorship")
router.register("alumni/campaigns", views.CampaignViewSet, basename="alumni-campaign")
router.register("alumni/donations", views.DonationViewSet, basename="alumni-donation")

urlpatterns = router.urls
