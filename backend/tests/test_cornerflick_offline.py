import hashlib
import unittest
from unittest.mock import patch
from app.providers.cornerflick_offline import archive_plan, match_synthetic, verify_bytes

F = {'id': 'f1', 'season': '2024-2025', 'league': 'de.bl1', 'date': '2024-08-24', 'home_team': 'Alpha', 'away_team': 'Beta'}
O = {'match_id': 'f1', 'source_match_id': 's1', 'season': '2024-2025', 'league': 'de.bl1', 'date': '2024-08-24', 'home_team': 'Alpha', 'away_team': 'Beta', 'statistics': {'corners': {'home': 3, 'away': 0}, 'yellow_cards': {'home': 0, 'away': 2}}}

class OfflineTests(unittest.TestCase):
    def test_missing_and_zero(self):
        stats = match_synthetic([F], [O], '2024-2025')[0]['statistics']
        self.assertIsNone(stats['red_cards']['home'])
        self.assertEqual(stats['yellow_cards']['home'], 0)
    def test_drift(self):
        with self.assertRaisesRegex(ValueError, 'SCHEMA_DRIFT'):
            match_synthetic([F], [{**O, 'statistics': {'unknown': 1}}], '2024-2025')
    def test_identity(self):
        with self.assertRaisesRegex(ValueError, 'IDENTITY_MISMATCH'):
            match_synthetic([F], [{**O, 'match_id': 'other'}], '2024-2025')
    def test_duplicate(self):
        with self.assertRaisesRegex(ValueError, 'DUPLICATE'):
            match_synthetic([F], [O, O], '2024-2025')
    def test_permission(self):
        with self.assertRaisesRegex(PermissionError, 'BLOCKED_PERMISSION_EVIDENCE'):
            archive_plan({'review_status': 'APPROVED'})
    def test_sha(self):
        verify_bytes(b'x', hashlib.sha256(b'x').hexdigest())
        with self.assertRaisesRegex(ValueError, 'SHA256_MISMATCH'):
            verify_bytes(b'x', '0' * 64)
    def test_cutoff(self):
        with self.assertRaisesRegex(ValueError, 'INVALID_IDENTITY_OR_DATE'):
            match_synthetic([{**F, 'season': '2026-2027', 'date': '2026-11-29'}], [], '2026-2027')
    def test_no_network_or_secrets(self):
        with patch('socket.socket', side_effect=AssertionError('network')), patch('os.getenv', side_effect=AssertionError('secret')):
            match_synthetic([F], [O], '2024-2025')

if __name__ == '__main__':
    unittest.main()
