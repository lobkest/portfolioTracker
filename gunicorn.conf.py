import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from accesslog import maak_logger_class

# Zonder IP, URL (met portfolio-code), query, referer en user-agent: de Render-logs mogen geen herleidbare gegevens bevatten.
# De logger_class filtert ruis en logt een gemaskeerd pad; dit formaat is alleen de terugval van gunicorn zelf.
access_log_format = "%(m)s %(s)s %(B)s %(L)s"
logger_class = maak_logger_class()
