# meta-ads-cli

A custom command-line interface for the **Meta Marketing Graph API v21** (Facebook + Instagram ads).

Built because the upstream `meta-ads-cli` on PyPI is stuck at `0.1.0`, missing the now-required `targeting_automation.advantage_audience` field (mandatory since 2024), and has no path to insights, scaling, ad-set ops, video ads, carousels, conversion campaigns, or A/B tests.

## Features

- **Validate** campaign YAML offline before hitting the API.
- **Create** campaigns end-to-end (campaign + ad set + creative + ad), PAUSED by default.
- **Media uploads**: images, videos (with chunked upload for files > 100 MB), thumbnails.
- **Ad formats**: image, video, and 2-10 card carousels.
- **Targeting**: countries, age, gender, interests, behaviors, custom audiences, exclusions, placements, Advantage+ audience.
- **CBO and ABO budgets**, conversion campaigns via `promoted_object` + pixel.
- **Activate / pause / delete / status** for campaigns and ad sets.
- **Insights** with date presets and breakdowns.
- **Scale** ad-set budgets with `--to <cents>` or `--percent`.
- **A/B tests** (`ad_study`) across existing campaigns.
- **Dry-run** mode prints every call without hitting the API.

## Install

```bash
pip install -e /path/to/meta
# or, once published:
# pip install meta-ads-cli
```

This installs the `meta` console command.

## Configure

The CLI walks up from the current working directory to find a `.env` file. Required vars:

```
META_ACCESS_TOKEN=...        # system user token, scopes: ads_management, ads_read,
                             # business_management, pages_read_engagement, pages_manage_ads
META_AD_ACCOUNT_ID=...       # numeric only, no act_ prefix
META_PAGE_ID=...             # Facebook Page the ads run from
META_API_VERSION=v21.0       # optional, default v21.0
META_PIXEL_ID=...            # optional, used as default for promoted_object.pixel_id
```

## Commands

```bash
meta validate --config campaign.yaml
meta create   --config campaign.yaml [--dry-run] [--yes]
meta status   <campaign_id>
meta activate <campaign_id>
meta pause    <object_id>            # campaign OR ad set
meta delete   <campaign_id> --yes
meta insights <object_id> [--date-preset last_7d] [--breakdowns publisher_platform,placement]
meta scale    <adset_id> --to <cents>
meta scale    <adset_id> --percent 20
meta ab-test  --name "..." --start-time ISO --end-time ISO \
              --cell "Control:cid:50" --cell "Variant:cid:50"
```

Or as a module: `python -m meta <command> ...`

## Minimal campaign YAML

```yaml
campaign:
  name: "Demo Campaign"
  objective: OUTCOME_TRAFFIC
  status: PAUSED

ad_set:
  name: "Demo Ad Set"
  daily_budget: 1000          # cents -- $10/day
  optimization_goal: LINK_CLICKS
  targeting:
    countries: [US]
    age_min: 25
    age_max: 55

ads:
  - name: "Demo Ad"
    image: ./creative.jpg
    primary_text: "Body copy here."
    headline: "Headline"
    description: "Optional description"
    link: https://example.com/landing
    cta: LEARN_MORE
```

Image / video / thumbnail paths are resolved relative to the YAML file.

## Safety

- Every `create` defaults to `status: PAUSED`. Run `meta activate` separately to start spend.
- `delete` requires `--yes` (it's irreversible).
- `scale --percent >20` prints a warning (resets Meta's learning phase).
- `--dry-run` prints every Graph call without hitting the API.

## License

MIT. See [LICENSE](LICENSE).
