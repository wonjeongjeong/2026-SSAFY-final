import subprocess
import unittest
from unittest.mock import patch

import remote_snapshot as remote


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        remote._expires=0
        remote._value=None

    @patch('remote_snapshot.subprocess.run')
    def test_success_is_cached_without_copying_credentials(self,run):
        run.return_value=subprocess.CompletedProcess([],0,'{"source":"ec2","lines":[]}', '')
        self.assertTrue(remote.snapshot()['available'])
        remote.snapshot()
        self.assertEqual(run.call_count,1)
        args=run.call_args.args[0]
        self.assertIn('BatchMode=yes',args)
        self.assertIn('subway-collector',args)
        self.assertNotIn('StrictHostKeyChecking=no',args)

    @patch('remote_snapshot.subprocess.run')
    def test_failure_does_not_expose_remote_stderr(self,run):
        run.return_value=subprocess.CompletedProcess([],255,'','sensitive connection detail')
        result=remote.snapshot()
        self.assertFalse(result['available'])
        self.assertNotIn('sensitive',str(result))

    @patch('remote_snapshot.subprocess.run',side_effect=subprocess.TimeoutExpired('ssh',20))
    def test_connection_timeout_has_explicit_unavailable_state(self,run):
        self.assertFalse(remote.snapshot()['available'])
