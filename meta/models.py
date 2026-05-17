"""Validation constants and error types for the Meta Ads CLI."""
from __future__ import annotations


VALID_OBJECTIVES = [
    "OUTCOME_TRAFFIC",
    "OUTCOME_AWARENESS",
    "OUTCOME_ENGAGEMENT",
    "OUTCOME_LEADS",
    "OUTCOME_SALES",
    "OUTCOME_APP_PROMOTION",
]

VALID_OPTIMIZATION_GOALS = [
    "LINK_CLICKS",
    "IMPRESSIONS",
    "REACH",
    "LANDING_PAGE_VIEWS",
    "APP_INSTALLS",
    "OFFSITE_CONVERSIONS",
    "LEAD_GENERATION",
    "THRUPLAY",
    "VALUE",
    "CONVERSATIONS",
]

VALID_CTAS = [
    "LEARN_MORE",
    "SIGN_UP",
    "DOWNLOAD",
    "SHOP_NOW",
    "BOOK_NOW",
    "GET_OFFER",
    "SUBSCRIBE",
    "CONTACT_US",
    "APPLY_NOW",
    "WATCH_MORE",
    "INSTALL_MOBILE_APP",
    "USE_APP",
    "MESSAGE_PAGE",
    "WHATSAPP_MESSAGE",
]

VALID_STATUSES = ["PAUSED", "ACTIVE"]

VALID_DATE_PRESETS = [
    "today",
    "yesterday",
    "last_3d",
    "last_7d",
    "last_14d",
    "last_28d",
    "last_30d",
    "this_month",
    "last_month",
    "this_quarter",
    "last_quarter",
    "maximum",
]

VALID_CUSTOM_EVENT_TYPES = [
    # Standard Pixel events. Note: Meta's API enum names DIFFER from the
    # `fbq('track', ...)` Pixel names. Pixel = "InitiateCheckout", API enum =
    # "INITIATED_CHECKOUT" (past tense). Don't conflate them.
    "ACHIEVEMENT_UNLOCKED",
    "ADD_PAYMENT_INFO",
    "ADD_TO_CART",
    "ADD_TO_WISHLIST",
    "AD_IMPRESSION",
    "COMPLETE_REGISTRATION",
    "CONTACT",
    "CONTENT_VIEW",
    "CUSTOMIZE_PRODUCT",
    "D2_RETENTION",
    "D7_RETENTION",
    "DONATE",
    "FIND_LOCATION",
    "INITIATED_CHECKOUT",
    "LEAD",
    "LEVEL_ACHIEVED",
    "LISTING_INTERACTION",
    "MESSAGING_CONVERSATION_STARTED_7D",
    "PURCHASE",
    "RATE",
    "SCHEDULE",
    "SEARCH",
    "SERVICE_BOOKING_REQUEST",
    "SPENT_CREDITS",
    "START_TRIAL",
    "SUBMIT_APPLICATION",
    "SUBSCRIBE",
    "TUTORIAL_COMPLETION",
    "OTHER",
]

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
VIDEO_POLL_INTERVAL_S = 5
VIDEO_POLL_TIMEOUT_S = 300

# Files larger than this use Meta's chunked upload protocol instead of multipart.
# Multipart upload starts becoming unreliable above ~100MB on slow connections.
CHUNKED_UPLOAD_THRESHOLD_BYTES = 100 * 1024 * 1024

CAROUSEL_MIN_CARDS = 2
CAROUSEL_MAX_CARDS = 10

VALID_AB_TEST_TYPES = ["LIFT", "SPLIT_TEST"]


class ConfigError(Exception):
    """Raised when a campaign YAML config fails validation."""


class MetaAPIError(Exception):
    """Raised when the Meta Graph API returns a non-200 response."""

    def __init__(self, status: int, message: str, *, code: int | None = None,
                 subcode: int | None = None, user_title: str | None = None,
                 user_msg: str | None = None, trace_id: str | None = None):
        self.status = status
        self.code = code
        self.subcode = subcode
        self.user_title = user_title
        self.user_msg = user_msg
        self.trace_id = trace_id
        parts = [message]
        if subcode:
            parts.append(f"(subcode {subcode})")
        if user_title:
            parts.append(f"\n  Meta says: {user_title}")
        if user_msg and user_msg != user_title:
            parts.append(f"\n  Detail:    {user_msg}")
        super().__init__(" ".join(parts))
