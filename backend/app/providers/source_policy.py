"""Reviewed source decisions; public availability is not an automation licence."""
SOURCES = {
    "goaloo": {
        "kind": "public_web_json_html", "terms_url": "https://www.goaloo.com/disclaimer",
        "feed_terms_url": "https://www.goaloo.com/freemap",
        "automation": "ROBOTS_GATED_READ_ONLY_PROBE", "redistribution": "NOT_VERIFIED",
        "coverage": "Selected league/day; score and technical fields only when actually present",
        "odds": "BLOCKED_BY_ROBOTS_AJAX", "production_permission": "NOT_INFERRED_FROM_FREE_IFRAME",
    },
    "football_data_co_uk": {
        "kind": "csv", "terms_url": "https://football-data.co.uk/data.php",
        "automation": "PROHIBITED_FOR_THIS_PRODUCT", "download_enabled": False,
        "coverage": "Publisher advertises 22 main European divisions plus 16 additional top divisions; field coverage varies",
        "reason": "Private-individual use only; excludes commercial/data-training products using bots/scrapers/AI",
        "first_odds": "PRE_CLOSING_NOT_GUARANTEED_OPENING", "closing": "Only explicit C columns where available",
    },
    "openfootball": {
        "kind": "open_json_txt", "terms_url": "https://github.com/openfootball/football.json/blob/master/LICENSE.md",
        "licence": "CC0-1.0", "automation": "OPEN_DATA_CANDIDATE_NOT_FETCHED",
        "coverage": "England, Germany, Spain, Italy, France and other community-maintained fixtures/results",
        "odds": "NOT_PROVIDED", "technical_statistics": "NOT_PROVIDED", "download_enabled": False,
        "freshness": "Community source updates are not guaranteed daily even when JSON generation runs daily",
    },
    "openligadb": {
        "kind": "free_community_service", "terms_url": "https://www.openligadb.de/nutzungsbedingungen",
        "licence_url": "https://www.openligadb.de/lizenz", "licence": "ODbL-1.0",
        "automation": "OPTIONAL_REVIEWED_CANDIDATE_NOT_FETCHED", "download_enabled": False,
        "coverage": "Community leagues; strongest documented identifiers bl1/bl2/bl3; completeness varies",
        "rate_limit": "30-60 seconds per league during live play; much less often otherwise; cache and last-change poll",
        "requirements": "Attribution; share-alike for redistribution of a modified database; descriptive User-Agent with contact",
        "odds": "NOT_PROVIDED", "reason": "No paid provider dependency; not enabled as an automatic API fallback",
    },
}
