"""Click CLI for meta-ads-cli.

Loads .env from the current working directory (walks up) so commands work from
any project root that has its own Meta credentials.

Usage:
  meta validate --config <yaml>
  meta create   --config <yaml> [--dry-run] [--yes]
  meta status   <campaign_id>
  meta activate <campaign_id>
  meta pause    <object_id>            # campaign OR ad set
  meta delete   <campaign_id> --yes
  meta insights <object_id> [--date-preset last_7d] [--breakdowns ...]
  meta scale    <adset_id> --to <cents>  OR  --percent +20
  meta ab-test  --name ... --start-time ... --end-time ... --cell L:cid:pct --cell L:cid:pct

Required env vars (auto-loaded from a .env in the CWD or any parent):
  META_ACCESS_TOKEN       system user token, scopes:
                          ads_management, ads_read, business_management,
                          pages_read_engagement, pages_manage_ads
  META_AD_ACCOUNT_ID      numeric only, no act_ prefix
  META_PAGE_ID            Facebook Page the ads run from
  META_API_VERSION        optional, default v21.0
  META_PIXEL_ID           optional, used as default for promoted_object.pixel_id
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click
from dotenv import load_dotenv

# Load .env from CWD upward so the CLI picks up project-local credentials.
load_dotenv()

from . import __version__
from .client import MetaAdsAPI
from .config import load_config, validate_config
from .models import (
    ConfigError,
    MetaAPIError,
    VALID_AB_TEST_TYPES,
    VALID_DATE_PRESETS,
)


# Orchestration ---------------------------------------------------------------

def _resolve_promoted_object(api: MetaAdsAPI,
                             promoted_cfg: dict[str, Any] | None) -> dict[str, Any] | None:
    """Default `pixel_id` to META_PIXEL_ID env var if the YAML omits it but sets a custom_event_type."""
    if not promoted_cfg:
        return None
    out = dict(promoted_cfg)
    if out.get("custom_event_type") and not out.get("pixel_id") and not out.get("application_id"):
        if not api.pixel_id:
            raise ConfigError(
                "ad_set.promoted_object.custom_event_type set but no pixel_id provided "
                "and META_PIXEL_ID is not in .env"
            )
        out["pixel_id"] = api.pixel_id
    return out


def create_full_campaign(api: MetaAdsAPI, config: dict[str, Any]) -> dict[str, Any]:
    campaign_cfg = config["campaign"]
    ad_set_cfg = config["ad_set"]
    ads_cfg = config["ads"]
    status = campaign_cfg.get("status", "PAUSED")

    result: dict[str, Any] = {
        "campaign_id": None, "ad_set_id": None, "creatives": [], "ads": []
    }

    # Step 1: Upload media (images / videos / thumbnails / carousel cards)
    click.echo(click.style("\n[1/4] Upload media", fg="blue", bold=True))
    media: dict[str, dict[str, Any]] = {}     # name -> {kind, image_hash | video_id | cards}
    for ad in ads_cfg:
        name = ad["name"]
        if ad.get("video"):
            video_path = Path(ad["video"])
            thumb_path = Path(ad["thumbnail"])
            click.echo(f"  video:     {video_path.name}")
            video_id = api.upload_video(video_path)
            click.echo(f"  thumbnail: {thumb_path.name}")
            image_hash = api.upload_image(thumb_path)
            click.echo("  waiting for video to finish processing...")
            api.wait_for_video_ready(video_id)
            media[name] = {"kind": "video", "video_id": video_id, "image_hash": image_hash}
        elif ad.get("cards"):
            click.echo(f"  carousel:  {ad['name']} ({len(ad['cards'])} cards)")
            cards_with_hashes = []
            for j, card in enumerate(ad["cards"]):
                card_img = Path(card["image"])
                click.echo(f"    card[{j}]: {card_img.name}")
                cards_with_hashes.append({
                    "image_hash": api.upload_image(card_img),
                    "link": card["link"],
                    "headline": card.get("headline", ""),
                    "description": card.get("description", ""),
                })
            media[name] = {"kind": "carousel", "cards": cards_with_hashes}
        else:
            image_path = Path(ad["image"])
            click.echo(f"  image:     {image_path.name}")
            image_hash = api.upload_image(image_path)
            media[name] = {"kind": "image", "image_hash": image_hash}
    click.echo(click.style("  done", fg="green"))

    # Step 2: Campaign
    click.echo(click.style("\n[2/4] Create campaign", fg="blue", bold=True))
    click.echo(f"  Name: {campaign_cfg['name']}")
    campaign_has_cbo = bool(campaign_cfg.get("daily_budget") or campaign_cfg.get("lifetime_budget"))
    if campaign_has_cbo:
        if campaign_cfg.get("daily_budget"):
            click.echo(f"  CBO daily budget: ${int(campaign_cfg['daily_budget']) / 100:.2f}/day")
        else:
            click.echo(f"  CBO lifetime budget: ${int(campaign_cfg['lifetime_budget']) / 100:.2f}")
    result["campaign_id"] = api.create_campaign(
        name=campaign_cfg["name"],
        objective=campaign_cfg.get("objective", "OUTCOME_TRAFFIC"),
        status=status,
        special_ad_categories=campaign_cfg.get("special_ad_categories"),
        daily_budget=campaign_cfg.get("daily_budget"),
        lifetime_budget=campaign_cfg.get("lifetime_budget"),
        bid_strategy=campaign_cfg.get("bid_strategy") if campaign_has_cbo else None,
    )
    click.echo(click.style(f"  campaign_id={result['campaign_id']}", fg="green"))

    # Step 3: Ad set
    click.echo(click.style("\n[3/4] Create ad set", fg="blue", bold=True))
    click.echo(f"  Name: {ad_set_cfg['name']}")
    if ad_set_cfg.get("daily_budget"):
        click.echo(f"  Budget: ${int(ad_set_cfg['daily_budget']) / 100:.2f}/day")
    else:
        click.echo("  Budget: (inherits from campaign CBO)")
    promoted_object = _resolve_promoted_object(api, ad_set_cfg.get("promoted_object"))
    if promoted_object:
        click.echo(f"  Promoted object: {promoted_object}")
    if ad_set_cfg.get("start_time"):
        click.echo(f"  Start: {ad_set_cfg['start_time']}")
    if ad_set_cfg.get("end_time"):
        click.echo(f"  End:   {ad_set_cfg['end_time']}")
    result["ad_set_id"] = api.create_ad_set(
        name=ad_set_cfg["name"],
        campaign_id=result["campaign_id"],
        daily_budget=ad_set_cfg.get("daily_budget"),
        targeting=ad_set_cfg.get("targeting", {}),
        optimization_goal=ad_set_cfg.get("optimization_goal", "LINK_CLICKS"),
        billing_event=ad_set_cfg.get("billing_event", "IMPRESSIONS"),
        bid_strategy=ad_set_cfg.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP"),
        status=status,
        promoted_object=promoted_object,
        start_time=ad_set_cfg.get("start_time"),
        end_time=ad_set_cfg.get("end_time"),
    )
    click.echo(click.style(f"  ad_set_id={result['ad_set_id']}", fg="green"))

    # Step 4: Creatives + ads
    click.echo(click.style("\n[4/4] Create ads", fg="blue", bold=True))
    for ad in ads_cfg:
        click.echo(f"  {ad['name']}")
        m = media[ad["name"]]
        if m["kind"] == "video":
            creative_id = api.create_video_creative(
                name=f"{ad['name']} - Creative",
                video_id=m["video_id"],
                image_hash=m["image_hash"],
                link=ad["link"],
                message=ad["primary_text"].strip(),
                headline=ad.get("headline", ""),
                description=ad.get("description", ""),
                cta=ad.get("cta", "LEARN_MORE"),
            )
        elif m["kind"] == "carousel":
            creative_id = api.create_carousel_creative(
                name=f"{ad['name']} - Creative",
                cards=m["cards"],
                link=ad["link"],
                message=ad["primary_text"].strip(),
                cta=ad.get("cta", "LEARN_MORE"),
            )
        else:
            creative_id = api.create_link_creative(
                name=f"{ad['name']} - Creative",
                image_hash=m["image_hash"],
                link=ad["link"],
                message=ad["primary_text"].strip(),
                headline=ad.get("headline", ""),
                description=ad.get("description", ""),
                cta=ad.get("cta", "LEARN_MORE"),
            )
        result["creatives"].append(creative_id)
        ad_id = api.create_ad(
            name=ad["name"], adset_id=result["ad_set_id"],
            creative_id=creative_id, status=status,
        )
        result["ads"].append(ad_id)
        click.echo(click.style(f"    creative={creative_id} ad={ad_id}", fg="green"))

    return result


# CLI -------------------------------------------------------------------------

@click.group()
@click.version_option(version=__version__, prog_name="meta")
def cli() -> None:
    """Custom Meta Ads CLI (Facebook + Instagram). See `meta COMMAND --help`."""


@cli.command()
@click.option("--config", "config_path", required=True, type=click.Path(exists=True),
              help="Path to campaign YAML.")
def validate(config_path: str) -> None:
    """Validate a campaign YAML (offline, no API calls, no creds required)."""
    try:
        cfg = validate_config(load_config(config_path))
    except ConfigError as e:
        click.echo(click.style(f"Validation failed:\n{e}", fg="red"))
        sys.exit(1)
    click.echo(click.style("Config is valid.", fg="green"))
    click.echo(f"  Campaign: {cfg['campaign']['name']}")
    if cfg['campaign'].get('daily_budget'):
        click.echo(f"  Budget:   ${int(cfg['campaign']['daily_budget']) / 100:.2f}/day (CBO)")
    elif cfg['campaign'].get('lifetime_budget'):
        click.echo(f"  Budget:   ${int(cfg['campaign']['lifetime_budget']) / 100:.2f} lifetime (CBO)")
    else:
        click.echo(f"  Budget:   ${int(cfg['ad_set']['daily_budget']) / 100:.2f}/day (ABO)")
    n_video = sum(1 for a in cfg['ads'] if a.get('video'))
    n_carousel = sum(1 for a in cfg['ads'] if a.get('cards'))
    n_image = len(cfg['ads']) - n_video - n_carousel
    click.echo(f"  Ads:      {len(cfg['ads'])} ({n_image} image, {n_video} video, {n_carousel} carousel)")
    if cfg['ad_set'].get('promoted_object'):
        click.echo(f"  Promoted: {cfg['ad_set']['promoted_object']}")
    if cfg['ad_set'].get('start_time') or cfg['ad_set'].get('end_time'):
        click.echo(f"  Window:   {cfg['ad_set'].get('start_time', '-')} -> "
                   f"{cfg['ad_set'].get('end_time', '-')}")


@cli.command()
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Don't hit the API; print what would happen.")
@click.option("--yes", "-y", is_flag=True, help="Skip 'will create real ads' confirmation.")
def create(config_path: str, dry_run: bool, yes: bool) -> None:
    """Create campaign + ad set + ads from a YAML (PAUSED by default)."""
    try:
        config = validate_config(load_config(config_path))
    except ConfigError as e:
        click.echo(click.style(f"Config error:\n{e}", fg="red"))
        sys.exit(1)

    click.echo(click.style("=" * 50, fg="blue"))
    click.echo(click.style("meta create", fg="blue", bold=True))
    click.echo(click.style("=" * 50, fg="blue"))
    click.echo(f"Campaign: {config['campaign']['name']}")
    if config['campaign'].get('daily_budget'):
        click.echo(f"Budget:   ${int(config['campaign']['daily_budget']) / 100:.2f}/day (CBO)")
    elif config['campaign'].get('lifetime_budget'):
        click.echo(f"Budget:   ${int(config['campaign']['lifetime_budget']) / 100:.2f} lifetime (CBO)")
    else:
        click.echo(f"Budget:   ${int(config['ad_set']['daily_budget']) / 100:.2f}/day (ABO)")
    click.echo(f"Ads:      {len(config['ads'])}")
    click.echo(f"Status:   {config['campaign'].get('status', 'PAUSED')}")
    click.echo(f"Mode:     {'DRY RUN' if dry_run else 'LIVE'}")

    if not dry_run and not yes:
        if not click.confirm(click.style("Create real campaigns?", fg="yellow")):
            click.echo("Aborted.")
            sys.exit(0)

    api = MetaAdsAPI.from_env(dry_run=dry_run)
    try:
        result = create_full_campaign(api, config)
    except (MetaAPIError, ConfigError) as e:
        click.echo(click.style(f"\nError: {e}", fg="red"))
        sys.exit(1)

    click.echo(click.style("\n" + "=" * 50, fg="green"))
    click.echo(click.style("Done", fg="green", bold=True))
    click.echo(click.style("=" * 50, fg="green"))
    click.echo(f"  Campaign: {result['campaign_id']}")
    click.echo(f"  Ad Set:   {result['ad_set_id']}")
    click.echo(f"  Ads:      {len(result['ads'])}")
    if not dry_run:
        click.echo(
            f"\n  Ads Manager:\n"
            f"  https://adsmanager.facebook.com/adsmanager/manage/campaigns"
            f"?act={api.ad_account_id}&selected_campaign_ids={result['campaign_id']}"
        )


@cli.command()
@click.argument("campaign_id")
def status(campaign_id: str) -> None:
    """Show campaign + its ad sets + ads."""
    api = MetaAdsAPI.from_env()
    try:
        c = api.get(campaign_id, "name,status,objective")
        click.echo(click.style(f"\nCampaign: {c['name']}", bold=True))
        click.echo(f"  id:     {c['id']}")
        click.echo(f"  status: {c['status']}")
        click.echo(f"  obj:    {c.get('objective', 'N/A')}")

        ad_sets = api.list_child(campaign_id, "adsets",
                                 "name,status,daily_budget,start_time,end_time")
        if ad_sets:
            click.echo(click.style("\n  Ad Sets:", bold=True))
            for a in ad_sets:
                budget = int(a.get("daily_budget", 0)) / 100
                window = ""
                if a.get("start_time") or a.get("end_time"):
                    window = f"  [{a.get('start_time', '-')} -> {a.get('end_time', '-')}]"
                click.echo(f"    {a['id']:>20}  {a['status']:<8}  ${budget:>6.2f}/day  {a['name']}{window}")

        ads = api.list_child(campaign_id, "ads", "name,status,effective_status")
        if ads:
            click.echo(click.style("\n  Ads:", bold=True))
            for a in ads:
                eff = a.get("effective_status", a["status"])
                click.echo(f"    {a['id']:>20}  {eff:<22}  {a['name']}")
    except MetaAPIError as e:
        click.echo(click.style(f"API Error: {e}", fg="red"))
        sys.exit(1)


@cli.command()
@click.argument("campaign_id")
@click.option("--yes", "-y", is_flag=True)
def activate(campaign_id: str, yes: bool) -> None:
    """Activate a campaign (starts spend)."""
    if not yes and not click.confirm(click.style("This will start spending budget. Continue?", fg="yellow")):
        click.echo("Aborted.")
        return
    api = MetaAdsAPI.from_env()
    try:
        api.update_status(campaign_id, "ACTIVE")
        click.echo(click.style(f"{campaign_id} -> ACTIVE", fg="green"))
    except MetaAPIError as e:
        click.echo(click.style(f"API Error: {e}", fg="red"))
        sys.exit(1)


@cli.command()
@click.argument("object_id")
def pause(object_id: str) -> None:
    """Pause a campaign OR an ad set (object_id determines which)."""
    api = MetaAdsAPI.from_env()
    try:
        api.update_status(object_id, "PAUSED")
        click.echo(click.style(f"{object_id} -> PAUSED", fg="green"))
    except MetaAPIError as e:
        click.echo(click.style(f"API Error: {e}", fg="red"))
        sys.exit(1)


@cli.command()
@click.argument("campaign_id")
@click.option("--yes", "-y", is_flag=True)
def delete(campaign_id: str, yes: bool) -> None:
    """Soft-delete a campaign (status ->DELETED). Irreversible."""
    if not yes and not click.confirm(click.style("Permanently delete this campaign?", fg="red")):
        click.echo("Aborted.")
        return
    api = MetaAdsAPI.from_env()
    try:
        api.update_status(campaign_id, "DELETED")
        click.echo(click.style(f"{campaign_id} -> DELETED", fg="green"))
    except MetaAPIError as e:
        click.echo(click.style(f"API Error: {e}", fg="red"))
        sys.exit(1)


@cli.command()
@click.argument("object_id")
@click.option("--date-preset", default="last_7d",
              type=click.Choice(VALID_DATE_PRESETS, case_sensitive=False))
@click.option("--breakdowns", default="", help="Comma-separated, e.g. publisher_platform,placement")
@click.option("--fields", default="",
              help="Comma-separated override; default: impressions,clicks,ctr,cpm,cpc,spend,reach,frequency,actions")
def insights(object_id: str, date_preset: str, breakdowns: str, fields: str) -> None:
    """Pull insights for a campaign / ad set / ad."""
    api = MetaAdsAPI.from_env()
    try:
        data = api.insights(
            object_id,
            date_preset=date_preset,
            fields=[f.strip() for f in fields.split(",") if f.strip()] or None,
            breakdowns=[b.strip() for b in breakdowns.split(",") if b.strip()] or None,
        )
    except MetaAPIError as e:
        click.echo(click.style(f"API Error: {e}", fg="red"))
        sys.exit(1)
    click.echo(json.dumps(data, indent=2))


@cli.command()
@click.argument("adset_id")
@click.option("--to", "to_cents", type=int, help="Set daily budget to this many cents.")
@click.option("--percent", type=int, help="Adjust by this percent (e.g. 20 for +20%%).")
def scale(adset_id: str, to_cents: int | None, percent: int | None) -> None:
    """Change an ad set's daily budget. Use --to <cents> OR --percent <int>."""
    if (to_cents is None) == (percent is None):
        click.echo(click.style("Provide exactly one of --to <cents> or --percent <int>.", fg="red"))
        sys.exit(1)
    api = MetaAdsAPI.from_env()
    try:
        if to_cents is not None:
            new_budget = to_cents
        else:
            current = int(api.get(adset_id, "daily_budget")["daily_budget"])
            new_budget = int(current * (100 + percent) / 100)
            if percent > 20:
                click.echo(click.style(
                    "Warning: >20% raises can reset learning phase.", fg="yellow"))
        api.update_adset_budget(adset_id, new_budget)
        click.echo(click.style(
            f"{adset_id} -> daily_budget=${new_budget / 100:.2f}/day", fg="green"))
    except MetaAPIError as e:
        click.echo(click.style(f"API Error: {e}", fg="red"))
        sys.exit(1)


@cli.command("ab-test")
@click.option("--name", required=True, help="Study name shown in Experiments tab.")
@click.option("--start-time", required=True, help="ISO-8601 start, e.g. 2026-05-20T00:00:00+10:00")
@click.option("--end-time", required=True, help="ISO-8601 end, e.g. 2026-06-03T23:59:59+10:00")
@click.option("--type", "study_type", default="SPLIT_TEST",
              type=click.Choice(VALID_AB_TEST_TYPES, case_sensitive=False))
@click.option("--cell", "cells", multiple=True, required=True,
              help="Repeat once per cell: 'label:campaign_id:percent'. Min 2, percents must sum to 100.")
def ab_test(name: str, start_time: str, end_time: str, study_type: str,
            cells: tuple[str, ...]) -> None:
    """Create a Meta A/B test (`ad_study`) splitting traffic between existing campaigns.

    Example:
      meta ab-test \\
        --name "Bedtime hook test" \\
        --start-time "2026-05-20T00:00:00+10:00" \\
        --end-time "2026-06-03T23:59:59+10:00" \\
        --cell "Control:120100000001:50" \\
        --cell "Variant:120100000002:50"
    """
    if len(cells) < 2:
        click.echo(click.style("Need at least 2 cells (--cell label:campaign_id:percent).", fg="red"))
        sys.exit(1)
    parsed = []
    total = 0
    for c in cells:
        bits = c.split(":")
        if len(bits) != 3:
            click.echo(click.style(f"Bad --cell '{c}'. Format: label:campaign_id:percent", fg="red"))
            sys.exit(1)
        label, cid, pct_str = bits
        try:
            pct = int(pct_str)
        except ValueError:
            click.echo(click.style(f"Percent must be an integer in --cell '{c}'", fg="red"))
            sys.exit(1)
        parsed.append({"name": label, "treatment_percentage": pct, "campaigns": [cid]})
        total += pct
    if total != 100:
        click.echo(click.style(f"Cell percentages must sum to 100; got {total}.", fg="red"))
        sys.exit(1)

    api = MetaAdsAPI.from_env()
    try:
        study_id = api.create_ad_study(
            name=name, cells=parsed, start_time=start_time, end_time=end_time,
            study_type=study_type,
        )
    except MetaAPIError as e:
        click.echo(click.style(f"API Error: {e}", fg="red"))
        sys.exit(1)
    click.echo(click.style(f"Study created: {study_id}", fg="green"))
    click.echo(f"  Cells: {len(parsed)}")
    for cell in parsed:
        click.echo(f"    {cell['treatment_percentage']:>3}%  {cell['name']}  campaigns={cell['campaigns']}")
    click.echo(f"\n  Experiments: https://business.facebook.com/experiments/{study_id}")


# Entry point referenced by pyproject `meta = "meta.cli:main"`.
main = cli


if __name__ == "__main__":
    main()
