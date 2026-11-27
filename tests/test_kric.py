import unittest
from src.subway_collect.kric import response
from src.subway_collect.http import SourceError, redact


class KricTests(unittest.TestCase):
    def test_success_and_empty_are_distinct_from_failure(self):
        self.assertEqual(response({'header': {'resultCode': '03', 'resultCnt': 0}}), ([], 0))
        rows = [{'trnNo': '1001', 'arvTm': '241000'}]
        self.assertEqual(response({'header': {'resultCode': '00', 'resultCnt': 1}, 'body': rows}), (rows, 1))
        with self.assertRaises(SourceError):
            response({'header': {'resultCode': '99'}})

    def test_incomplete_response_is_not_marked_complete(self):
        with self.assertRaises(ValueError):
            response({'header': {'resultCode': '00', 'resultCnt': 5}, 'body': []})

    def test_bcrypt_style_key_is_redacted_in_plain_and_encoded_forms(self):
        from urllib.parse import quote
        key = '$2a$10$example/secret'
        self.assertEqual(redact(key + ' ' + quote(key, safe=''), {'KRIC_API_KEY': key}), '[REDACTED] [REDACTED]')
