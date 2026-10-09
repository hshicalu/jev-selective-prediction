import unittest

from jev_selective.config import PROTOCOL_PATH, sha256_file


class ConfigurationPathTests(unittest.TestCase):
    def test_protocol_path_resolves_from_repository_root(self):
        self.assertTrue(PROTOCOL_PATH.is_file())
        self.assertEqual(len(sha256_file(PROTOCOL_PATH)), 64)


if __name__ == "__main__":
    unittest.main()
