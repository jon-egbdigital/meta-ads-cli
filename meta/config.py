"""Campaign YAML config loader + validator."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import (
    CAROUSEL_MAX_CARDS,
    CAROUSEL_MIN_CARDS,
    ConfigError,
    VALID_CTAS,
    VALID_CUSTOM_EVENT_TYPES,
    VALID_OBJECTIVES,
    VALID_OPTIMIZATION_GOALS,
    VALID_STATUSES,
)


def load_config(path: str | Path) -> dict[str, Any]:
    """Load a campaign YAML config. Resolves `ads[].image`, `ads[].video`,
    and `ads[].thumbnail` paths relative to the YAML file."""
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not config:
        raise ConfigError("Config file is empty")

    config_dir = config_path.parent
    for ad in config.get("ads", []):
        for field in ("image", "video", "thumbnail"):
            if field in ad and not Path(ad[field]).is_absolute():
                ad[field] = str((config_dir / ad[field]).resolve())
        for card in ad.get("cards", []) or []:
            if "image" in card and not Path(card["image"]).is_absolute():
                card["image"] = str((config_dir / card["image"]).resolve())

    return config


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate a campaign config. Raises ConfigError listing every problem found."""
    errors: list[str] = []

    campaign = config.get("campaign")
    if not campaign:
        errors.append("Missing 'campaign' section")
    else:
        if not campaign.get("name"):
            errors.append("campaign.name is required")
        objective = campaign.get("objective", "OUTCOME_TRAFFIC")
        if objective not in VALID_OBJECTIVES:
            errors.append(f"campaign.objective '{objective}' invalid. Options: {', '.join(VALID_OBJECTIVES)}")
        status = campaign.get("status", "PAUSED")
        if status not in VALID_STATUSES:
            errors.append("campaign.status must be PAUSED or ACTIVE")
        if campaign.get("daily_budget") and campaign.get("lifetime_budget"):
            errors.append("campaign: set daily_budget OR lifetime_budget for CBO, not both")

    campaign_has_cbo = bool(campaign and (campaign.get("daily_budget") or campaign.get("lifetime_budget")))

    ad_set = config.get("ad_set")
    if not ad_set:
        errors.append("Missing 'ad_set' section")
    else:
        if not ad_set.get("name"):
            errors.append("ad_set.name is required")
        if ad_set.get("daily_budget") and campaign_has_cbo:
            errors.append("ad_set.daily_budget cannot be set when campaign has CBO budget")
        if not ad_set.get("daily_budget") and not campaign_has_cbo:
            errors.append(
                "ad_set.daily_budget is required (in cents -- 1000 = $10/day), "
                "OR set campaign.daily_budget for CBO"
            )
        opt_goal = ad_set.get("optimization_goal", "LINK_CLICKS")
        if opt_goal not in VALID_OPTIMIZATION_GOALS:
            errors.append(f"ad_set.optimization_goal '{opt_goal}' invalid. Options: {', '.join(VALID_OPTIMIZATION_GOALS)}")
        targeting = ad_set.get("targeting", {})
        if not targeting.get("countries"):
            errors.append("ad_set.targeting.countries is required (e.g. ['US', 'AU'])")

        promoted = ad_set.get("promoted_object")
        if promoted:
            cet = promoted.get("custom_event_type")
            if cet and cet not in VALID_CUSTOM_EVENT_TYPES:
                errors.append(
                    f"ad_set.promoted_object.custom_event_type '{cet}' invalid. "
                    f"Options: {', '.join(VALID_CUSTOM_EVENT_TYPES)}"
                )
            has_pixel = bool(promoted.get("pixel_id"))
            has_app = bool(promoted.get("application_id"))
            if not (has_pixel or has_app):
                errors.append(
                    "ad_set.promoted_object requires either pixel_id (web conversions) "
                    "or application_id (app campaigns)"
                )
            if has_pixel and not promoted.get("custom_event_type"):
                errors.append("ad_set.promoted_object.custom_event_type is required when pixel_id is set")
            if has_app and not promoted.get("object_store_url"):
                errors.append(
                    "ad_set.promoted_object.object_store_url is required when application_id is set "
                    "(e.g. https://apps.apple.com/us/app/.../id123)"
                )

    ads = config.get("ads")
    if not ads:
        errors.append("Missing 'ads' section (need at least one ad)")
    else:
        for i, ad in enumerate(ads):
            prefix = f"ads[{i}]"
            if not ad.get("name"):
                errors.append(f"{prefix}.name is required")
            kinds = sum(1 for k in ("image", "video", "cards") if ad.get(k))
            if kinds == 0:
                errors.append(f"{prefix} requires one of 'image', 'video', or 'cards' (carousel)")
            if kinds > 1:
                errors.append(f"{prefix} has more than one of image/video/cards -- pick exactly one")
            if ad.get("image") and not Path(ad["image"]).exists():
                errors.append(f"{prefix}.image not found: {ad['image']}")
            if ad.get("video"):
                if not Path(ad["video"]).exists():
                    errors.append(f"{prefix}.video not found: {ad['video']}")
                if not ad.get("thumbnail"):
                    errors.append(f"{prefix}.thumbnail is required for video ads (jpg/png frame)")
                elif not Path(ad["thumbnail"]).exists():
                    errors.append(f"{prefix}.thumbnail not found: {ad['thumbnail']}")
            if ad.get("cards") is not None:
                cards = ad["cards"]
                if not isinstance(cards, list):
                    errors.append(f"{prefix}.cards must be a list")
                elif not (CAROUSEL_MIN_CARDS <= len(cards) <= CAROUSEL_MAX_CARDS):
                    errors.append(
                        f"{prefix}.cards must have {CAROUSEL_MIN_CARDS}-{CAROUSEL_MAX_CARDS} entries; "
                        f"got {len(cards)}"
                    )
                else:
                    for j, card in enumerate(cards):
                        cp = f"{prefix}.cards[{j}]"
                        if not card.get("image"):
                            errors.append(f"{cp}.image is required")
                        elif not Path(card["image"]).exists():
                            errors.append(f"{cp}.image not found: {card['image']}")
                        if not card.get("headline"):
                            errors.append(f"{cp}.headline is required")
                        if not card.get("link"):
                            errors.append(f"{cp}.link is required")
            if not ad.get("primary_text"):
                errors.append(f"{prefix}.primary_text is required")
            if not ad.get("headline") and not ad.get("cards"):
                errors.append(f"{prefix}.headline is required (or use 'cards' for carousel)")
            if not ad.get("link"):
                errors.append(f"{prefix}.link is required (shared fallback for carousel)")
            cta = ad.get("cta", "LEARN_MORE")
            if cta not in VALID_CTAS:
                errors.append(f"{prefix}.cta '{cta}' invalid. Options: {', '.join(VALID_CTAS)}")

    if errors:
        raise ConfigError("\n".join(f"  - {e}" for e in errors))

    return config
