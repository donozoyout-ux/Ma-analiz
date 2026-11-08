from datetime import date


class EmptyMockProvider:
    """Credential-free provider adapter that returns no invented events."""
    name = "mock"

    def get_matches(self, day: date) -> list[dict]:
        return []

    def get_match(self, match_id: str) -> dict | None:
        return None

    def get_odds(self, match_id: str) -> list[dict]:
        return []

    def get_results(self, day: date) -> list[dict]:
        return []

    def get_leagues(self) -> list[dict]:
        return []
