"""Throwaway-mail domains refused at signup.

A short built-in list of the common services, plus
``SIGNUP_BLOCKED_EMAIL_DOMAINS`` for more. A subdomain of a listed domain is
refused too (``x.mailinator.com``). It can't be complete — new services
appear daily — so it only raises the cost of junk signups; the CAPTCHA and
the verification link do the real work.
"""
from django.conf import settings

BUILT_IN = frozenset("""
10minutemail.com 10minutemail.net 20minutemail.com 33mail.com anonbox.net anonymbox.com
burnermail.io byom.de crazymailing.com deadaddress.com discard.email discardmail.com
dispostable.com dropmail.me emailondeck.com emailtemporanea.net emltmp.com fakeinbox.com
fakemail.net fakemailgenerator.com getairmail.com getnada.com guerrillamail.biz guerrillamail.com
guerrillamail.de guerrillamail.info guerrillamail.net guerrillamail.org guerrillamailblock.com
harakirimail.com incognitomail.org inboxbear.com inboxkitten.com jetable.org mail-temp.com
mail.tm maildrop.cc mailcatch.com maildim.com mailinator.com mailinator.net mailinator2.com
mailnesia.com mailpoof.com mailsac.com mintemail.com moakt.com mohmal.com mvrht.net mytemp.email
mytrashmail.com nada.email noclickemail.com nwytg.net one-time.email owlymail.com pokemail.net
quickinbox.com sharklasers.com spam4.me spambog.com spambox.us spamgourmet.com spamex.com
superrito.com tempail.com tempinbox.com tempm.com tempmail.com tempmail.dev tempmail.net
tempmail.plus tempmailo.com temp-mail.io temp-mail.org tempr.email throwawaymail.com
tmail.ws tmailor.com tmpmail.net tmpmail.org trashmail.com trashmail.de trashmail.io
trashmail.me trashmail.net wegwerfmail.de wegwerfmail.net yopmail.com yopmail.fr yopmail.net
mailnull.com emailfake.com fakermail.com luxusmail.org minuteinbox.com
""".split())


def is_disposable(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower().strip().rstrip(".")
    blocked = BUILT_IN | {d.lower().strip() for d in settings.SIGNUP_BLOCKED_EMAIL_DOMAINS if d.strip()}
    parts = domain.split(".")
    return any(".".join(parts[i:]) in blocked for i in range(len(parts) - 1))
