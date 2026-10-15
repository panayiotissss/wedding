"""
Email validation for signup.

Two layers:
  1. Syntax, via email-validator (no network).
  2. The domain must exist (MX or A record), so typos like "gmai1l.com" are
     caught while the couple is still on the form.

The DNS layer fails OPEN: a domain is only rejected when DNS clearly says it
doesn't exist AND a known-good domain still resolves. If the resolver itself
is broken, every signup would otherwise be blocked.
"""

from email_validator import EmailNotValidError, validate_email

import config

_CANARY = "gmail.com"


def _domain_status(domain: str) -> str:
    """'exists', 'missing' or 'unknown' (DNS unusable)."""
    import dns.exception
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 3.0
    for rtype in ("MX", "A", "AAAA"):
        try:
            resolver.resolve(domain, rtype)
            return "exists"
        except dns.resolver.NXDOMAIN:
            return "missing"
        except dns.resolver.NoAnswer:
            continue  # name exists but has no record of this type, try the next
        except dns.exception.DNSException:
            return "unknown"
    return "missing"


def _domain_exists(domain: str) -> bool:
    status = _domain_status(domain)
    if status != "missing":
        return True
    # Only trust "missing" if DNS can still find a domain we know is real.
    return _domain_status(_CANARY) != "exists" if domain != _CANARY else False


def check_email(raw: str) -> str:
    """Return the normalized address, or raise ValueError with a friendly message."""
    try:
        info = validate_email(raw.strip(), check_deliverability=False)
    except EmailNotValidError:
        raise ValueError("Please enter a valid email address.")

    if config.EMAIL_DNS_CHECK and not _domain_exists(info.domain):
        raise ValueError(
            f"We can't find the email domain \"{info.domain}\". Please check for a typo."
        )
    return info.normalized.lower()
