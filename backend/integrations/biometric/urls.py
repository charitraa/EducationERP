"""ZKTeco push endpoints. The devices use these fixed paths, some firmware
with an ``.aspx`` suffix."""
from django.urls import path

from . import zkteco

urlpatterns = [
    path("cdata", zkteco.cdata),
    path("cdata.aspx", zkteco.cdata),
    path("getrequest", zkteco.getrequest),
    path("getrequest.aspx", zkteco.getrequest),
    path("devicecmd", zkteco.devicecmd),
    path("devicecmd.aspx", zkteco.devicecmd),
]
