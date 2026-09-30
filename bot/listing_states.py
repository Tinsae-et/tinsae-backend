"""The /myproducts listing states.

Pure: no Django, no Telegram. Shared by the selector (which annotates
`listing_rank`) and the keyboards (which pick the per-state buttons). Lower leads.
"""

LISTING_DUE = 0
LISTING_FRESH = 1
LISTING_PAUSED = 2
LISTING_SOLD = 3
