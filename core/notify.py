"""Cross-app notification entry point.

Placeholder until TIN-114 lands the real service. Callers already go through
this function, so TIN-114 only has to replace the body -- the call sites in
`inquiries.services` stay as they are.
"""

import logging

logger = logging.getLogger(__name__)


def notify(recipient, kind, **payload):
    """Tell `recipient` (a User or a Shop) that `kind` happened.

    No-op for now: nothing is delivered until TIN-114 decides the channels.
    """
    logger.debug("notify(%r, %s) skipped: no delivery wired yet", recipient, kind)
