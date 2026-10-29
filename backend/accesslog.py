"""Gunicorn-accesslog zonder ruis, IP-adres, querystring en portfolio-code (logger_class in gunicorn.conf.py)."""
from backend.debug_utils import maskeer_pad

ACCESSLOG_NEGEER_PADEN = frozenset({"/favicon.ico", "/robots.txt", "/sitemap.xml"})
ACCESSLOG_NEGEER_VOORVOEGSELS = ("/static/",)
STATUS_NIET_MELDEN = 304
STATUS_ALTIJD_LOGGEN_VANAF = 400


def accesslog_regel(methode, pad, status, duur_seconden):
    """De logregel, of None als dit verzoek niet gelogd hoeft te worden. Fouten (4xx/5xx) altijd, ook bij static."""
    if status < STATUS_ALTIJD_LOGGEN_VANAF:
        if status == STATUS_NIET_MELDEN or pad in ACCESSLOG_NEGEER_PADEN or pad.startswith(ACCESSLOG_NEGEER_VOORVOEGSELS):
            return None
    return f"{methode} {maskeer_pad(pad)} {status} {duur_seconden:.3f}s"


def maak_logger_class():
    # Pas hier importeren: gunicorn.glogging bestaat niet op Windows (alleen lokaal relevant voor tests).
    from gunicorn.glogging import Logger

    class FilterLogger(Logger):
        def access(self, resp, req, environ, request_time):
            cfg = self.cfg
            if not (cfg.accesslog or cfg.logconfig or cfg.logconfig_dict
                    or (cfg.syslog and not cfg.disable_redirect_access_to_syslog)):
                return
            try:
                duur = request_time.total_seconds() if hasattr(request_time, "total_seconds") else float(request_time)
                regel = accesslog_regel(environ.get("REQUEST_METHOD", "-"), environ.get("PATH_INFO", "-"),
                                        int(resp.status_code), duur)
                if regel:
                    self.access_log.info("%s", regel)
            except Exception:
                self.error("accesslog-regel niet te maken")

    return FilterLogger
