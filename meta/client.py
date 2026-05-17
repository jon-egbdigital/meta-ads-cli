"""Thin wrapper around the Meta Marketing Graph API."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import click
import requests

from .models import (
    CHUNKED_UPLOAD_THRESHOLD_BYTES,
    MetaAPIError,
    VIDEO_POLL_INTERVAL_S,
    VIDEO_POLL_TIMEOUT_S,
)


class MetaAdsAPI:
    """Thin wrapper around the Meta Marketing Graph API."""

    def __init__(self, access_token: str, ad_account_id: str, page_id: str,
                 api_version: str = "v21.0", dry_run: bool = False,
                 pixel_id: str | None = None):
        self.access_token = access_token
        self.ad_account_id = ad_account_id
        self.act_id = f"act_{ad_account_id}"
        self.page_id = page_id
        self.api_version = api_version
        self.base_url = f"https://graph.facebook.com/{api_version}"
        self.dry_run = dry_run
        self.pixel_id = pixel_id
        self._dry_run_counter = 0

    @classmethod
    def from_env(cls, dry_run: bool = False) -> "MetaAdsAPI":
        missing = [k for k in ("META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_PAGE_ID")
                   if not os.getenv(k)]
        if missing:
            click.echo(click.style("Missing required env vars:", fg="red"))
            for var in missing:
                click.echo(click.style(f"  {var}", fg="red"))
            click.echo(
                "\nSet them in a .env file in your project root, or export in your shell.\n"
                "Required: META_ACCESS_TOKEN, META_AD_ACCOUNT_ID, META_PAGE_ID\n"
                "Optional: META_API_VERSION (default v21.0), META_PIXEL_ID"
            )
            sys.exit(1)
        return cls(
            access_token=os.environ["META_ACCESS_TOKEN"],
            ad_account_id=os.environ["META_AD_ACCOUNT_ID"],
            page_id=os.environ["META_PAGE_ID"],
            api_version=os.getenv("META_API_VERSION", "v21.0"),
            pixel_id=os.getenv("META_PIXEL_ID"),
            dry_run=dry_run,
        )

    def _request(self, method: str, endpoint: str, **kwargs) -> dict[str, Any]:
        url = f"{self.base_url}/{endpoint}"
        params = kwargs.setdefault("params", {})
        params["access_token"] = self.access_token

        if self.dry_run:
            self._dry_run_counter += 1
            fake_id = f"dry_run_{self._dry_run_counter}"
            click.echo(click.style(f"  [DRY RUN] {method} {endpoint}", fg="yellow"))
            shown = {k: v for k, v in params.items() if k != "access_token"}
            if shown:
                preview = json.dumps(shown, indent=2)
                if len(preview) > 600:
                    preview = preview[:600] + "..."
                click.echo(click.style(f"  Params: {preview}", fg="yellow"))
            if "files" in kwargs:
                click.echo(click.style(f"  Files:  {list(kwargs['files'].keys())}", fg="yellow"))
            return {"id": fake_id}

        resp = getattr(requests, method.lower())(url, **kwargs)
        if resp.status_code != 200:
            try:
                err = resp.json().get("error", {})
                raise MetaAPIError(
                    resp.status_code,
                    err.get("message", resp.text),
                    code=err.get("code"),
                    subcode=err.get("error_subcode"),
                    user_title=err.get("error_user_title"),
                    user_msg=err.get("error_user_msg"),
                    trace_id=err.get("fbtrace_id"),
                )
            except (ValueError, KeyError):
                raise MetaAPIError(resp.status_code, resp.text)
        return resp.json()

    # writes: media -----------------------------------------------------------

    def upload_image(self, image_path: Path) -> str:
        with open(image_path, "rb") as f:
            mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
            result = self._request(
                "POST",
                f"{self.act_id}/adimages",
                files={"filename": (image_path.name, f, mime)},
            )
        if self.dry_run:
            return "dry_run_hash"
        for val in result.get("images", {}).values():
            return val["hash"]
        raise MetaAPIError(0, f"Unexpected image upload response: {result}")

    def upload_video(self, video_path: Path) -> str:
        """Upload a video. Uses chunked upload for files >100MB, simple multipart otherwise."""
        if self.dry_run:
            return "dry_run_video_id"
        size = video_path.stat().st_size
        if size > CHUNKED_UPLOAD_THRESHOLD_BYTES:
            return self._upload_video_chunked(video_path, size)
        with open(video_path, "rb") as f:
            result = self._request(
                "POST",
                f"{self.act_id}/advideos",
                files={"source": (video_path.name, f, "video/mp4")},
            )
        video_id = result.get("id")
        if not video_id:
            raise MetaAPIError(0, f"Unexpected video upload response: {result}")
        return video_id

    def _upload_video_chunked(self, video_path: Path, size: int) -> str:
        """Meta's chunked upload protocol: phase=start -> N x phase=transfer -> phase=finish."""
        start = self._request(
            "POST",
            f"{self.act_id}/advideos",
            params={"upload_phase": "start", "file_size": str(size)},
        )
        session_id = start["upload_session_id"]
        video_id = start["video_id"]
        start_offset = int(start["start_offset"])
        end_offset = int(start["end_offset"])

        with open(video_path, "rb") as f:
            while start_offset < end_offset:
                f.seek(start_offset)
                chunk = f.read(end_offset - start_offset)
                xfer = self._request(
                    "POST",
                    f"{self.act_id}/advideos",
                    params={
                        "upload_phase": "transfer",
                        "upload_session_id": session_id,
                        "start_offset": str(start_offset),
                    },
                    files={"video_file_chunk": (video_path.name, chunk, "application/octet-stream")},
                )
                start_offset = int(xfer["start_offset"])
                end_offset = int(xfer["end_offset"])

        self._request(
            "POST",
            f"{self.act_id}/advideos",
            params={"upload_phase": "finish", "upload_session_id": session_id},
        )
        return video_id

    def wait_for_video_ready(self, video_id: str, timeout: int = VIDEO_POLL_TIMEOUT_S) -> None:
        """Block until the video status is `ready`. Raises on `error` or timeout."""
        if self.dry_run:
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self._request("GET", video_id, params={"fields": "status"})
            video_status = (result.get("status") or {}).get("video_status")
            if video_status == "ready":
                return
            if video_status == "error":
                raise MetaAPIError(0, f"Video {video_id} processing failed: {result.get('status')}")
            time.sleep(VIDEO_POLL_INTERVAL_S)
        raise MetaAPIError(0, f"Video {video_id} not ready within {timeout}s")

    # writes: campaign / ad set / creative / ad -------------------------------

    def create_campaign(self, *, name: str, objective: str = "OUTCOME_TRAFFIC",
                        status: str = "PAUSED",
                        special_ad_categories: list[str] | None = None,
                        daily_budget: int | None = None,
                        lifetime_budget: int | None = None,
                        bid_strategy: str | None = None) -> str:
        """Create a campaign. If daily_budget or lifetime_budget is set, this is a CBO
        (Advantage+ Campaign Budget) campaign -- ad sets inside cannot have their own budgets."""
        params: dict[str, Any] = {
            "name": name,
            "objective": objective,
            "status": status,
            "special_ad_categories": json.dumps(special_ad_categories or []),
        }
        if daily_budget:
            params["daily_budget"] = str(daily_budget)
        if lifetime_budget:
            params["lifetime_budget"] = str(lifetime_budget)
        if bid_strategy:
            params["bid_strategy"] = bid_strategy
        return self._request("POST", f"{self.act_id}/campaigns", params=params)["id"]

    def create_ad_set(self, *, name: str, campaign_id: str,
                      daily_budget: int | None,
                      targeting: dict[str, Any], optimization_goal: str = "LINK_CLICKS",
                      billing_event: str = "IMPRESSIONS",
                      bid_strategy: str = "LOWEST_COST_WITHOUT_CAP",
                      status: str = "PAUSED",
                      promoted_object: dict[str, Any] | None = None,
                      start_time: str | None = None,
                      end_time: str | None = None) -> str:
        spec: dict[str, Any] = {
            "age_min": targeting.get("age_min", 18),
            "age_max": targeting.get("age_max", 65),
            "geo_locations": {"countries": targeting.get("countries", ["US"])},
            # 2024+ requirement -- explicit advantage_audience choice.
            "targeting_automation": {
                "advantage_audience": targeting.get("advantage_audience", 0)
            },
        }
        if targeting.get("genders"):
            spec["genders"] = targeting["genders"]
        if targeting.get("interests"):
            spec["flexible_spec"] = [{"interests": targeting["interests"]}]
        if targeting.get("behaviors"):
            # behaviors can be passed as IDs (strings/ints) or as {id, name} dicts
            spec["behaviors"] = [
                {"id": str(b)} if not isinstance(b, dict) else b
                for b in targeting["behaviors"]
            ]
        if targeting.get("custom_audiences"):
            spec["custom_audiences"] = [
                {"id": str(a)} if not isinstance(a, dict) else a
                for a in targeting["custom_audiences"]
            ]
        if targeting.get("excluded_custom_audiences"):
            spec["excluded_custom_audiences"] = [
                {"id": str(a)} if not isinstance(a, dict) else a
                for a in targeting["excluded_custom_audiences"]
            ]

        platforms = targeting.get("platforms", ["facebook", "instagram"])
        spec["publisher_platforms"] = platforms
        if "facebook" in platforms:
            spec["facebook_positions"] = targeting.get("facebook_positions", ["feed"])
        if "instagram" in platforms:
            spec["instagram_positions"] = targeting.get(
                "instagram_positions", ["stream", "story", "reels"]
            )

        params: dict[str, Any] = {
            "name": name,
            "campaign_id": campaign_id,
            "billing_event": billing_event,
            "optimization_goal": optimization_goal,
            "status": status,
            "targeting": json.dumps(spec),
        }
        # Under CBO the campaign owns budget+bid_strategy; ad sets must not duplicate them.
        if daily_budget:
            params["daily_budget"] = str(daily_budget)
            params["bid_strategy"] = bid_strategy
        if promoted_object:
            params["promoted_object"] = json.dumps(promoted_object)
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time

        return self._request("POST", f"{self.act_id}/adsets", params=params)["id"]

    def create_link_creative(self, *, name: str, image_hash: str, link: str,
                             message: str, headline: str, description: str,
                             cta: str = "LEARN_MORE") -> str:
        spec = {
            "page_id": self.page_id,
            "link_data": {
                "image_hash": image_hash,
                "link": link,
                "message": message,
                "name": headline,
                "description": description,
                "call_to_action": {"type": cta, "value": {"link": link}},
            },
        }
        return self._request(
            "POST",
            f"{self.act_id}/adcreatives",
            params={"name": name, "object_story_spec": json.dumps(spec)},
        )["id"]

    def create_video_creative(self, *, name: str, video_id: str, image_hash: str,
                              link: str, message: str, headline: str,
                              description: str, cta: str = "LEARN_MORE") -> str:
        spec = {
            "page_id": self.page_id,
            "video_data": {
                "video_id": video_id,
                "image_hash": image_hash,   # thumbnail
                "title": headline,
                "message": message,
                "link_description": description,
                "call_to_action": {"type": cta, "value": {"link": link}},
            },
        }
        return self._request(
            "POST",
            f"{self.act_id}/adcreatives",
            params={"name": name, "object_story_spec": json.dumps(spec)},
        )["id"]

    def create_carousel_creative(self, *, name: str, cards: list[dict[str, Any]],
                                 link: str, message: str, cta: str = "LEARN_MORE") -> str:
        """Carousel ad. `cards` is a list of {image_hash, link, headline, description}."""
        child_attachments = [
            {
                "image_hash": c["image_hash"],
                "link": c["link"],
                "name": c.get("headline", ""),
                "description": c.get("description", ""),
            }
            for c in cards
        ]
        spec = {
            "page_id": self.page_id,
            "link_data": {
                "link": link,
                "message": message,
                "call_to_action": {"type": cta, "value": {"link": link}},
                "child_attachments": child_attachments,
                # Tell Meta the cards are pre-ordered; otherwise Meta auto-optimizes order.
                "multi_share_optimized": False,
            },
        }
        return self._request(
            "POST",
            f"{self.act_id}/adcreatives",
            params={"name": name, "object_story_spec": json.dumps(spec)},
        )["id"]

    # Backwards-compatible alias: existing callers can keep using create_creative for images.
    create_creative = create_link_creative

    def create_ad_study(self, *, name: str, cells: list[dict[str, Any]],
                        start_time: str, end_time: str,
                        study_type: str = "SPLIT_TEST") -> str:
        """Create an A/B test (`ad_study`). `cells` is a list of {name, treatment_percentage, adsets|campaigns}.

        Example:
            cells = [
                {"name": "Control",  "treatment_percentage": 50, "campaigns": ["120..."]},
                {"name": "Variant",  "treatment_percentage": 50, "campaigns": ["120..."]},
            ]
        """
        return self._request(
            "POST",
            f"{self.act_id}/ad_studies",
            params={
                "name": name,
                "type": study_type,
                "cells": json.dumps(cells),
                "start_time": start_time,
                "end_time": end_time,
            },
        )["id"]

    def create_ad(self, *, name: str, adset_id: str, creative_id: str,
                  status: str = "PAUSED") -> str:
        return self._request(
            "POST",
            f"{self.act_id}/ads",
            params={
                "name": name,
                "adset_id": adset_id,
                "creative": json.dumps({"creative_id": creative_id}),
                "status": status,
            },
        )["id"]

    def update_status(self, object_id: str, status: str) -> dict[str, Any]:
        return self._request("POST", object_id, params={"status": status})

    def update_adset_budget(self, adset_id: str, daily_budget_cents: int) -> dict[str, Any]:
        return self._request(
            "POST", adset_id, params={"daily_budget": str(daily_budget_cents)}
        )

    # reads -------------------------------------------------------------------

    def get(self, object_id: str, fields: str) -> dict[str, Any]:
        return self._request("GET", object_id, params={"fields": fields})

    def list_child(self, parent_id: str, edge: str, fields: str) -> list[dict[str, Any]]:
        return self._request(
            "GET", f"{parent_id}/{edge}", params={"fields": fields}
        ).get("data", [])

    def insights(self, object_id: str, *, date_preset: str = "last_7d",
                 fields: Iterable[str] | None = None,
                 breakdowns: Iterable[str] | None = None) -> list[dict[str, Any]]:
        fields = fields or (
            "impressions", "clicks", "ctr", "cpm", "cpc", "spend",
            "reach", "frequency", "actions",
        )
        params = {"date_preset": date_preset, "fields": ",".join(fields)}
        if breakdowns:
            params["breakdowns"] = ",".join(breakdowns)
        return self._request("GET", f"{object_id}/insights", params=params).get("data", [])
