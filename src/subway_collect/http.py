"""HTTP retries, durable request accounting and secret-free response archives."""
from __future__ import annotations

import json
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .config import now
from .storage import identity

LOG = logging.getLogger('subway')


class SourceError(Exception):
    def __init__(self, code):
        self.code = str(code)
        super().__init__(self.code)


def decode(raw):
    text = raw.decode('utf-8-sig')
    if text.lstrip().startswith('<'):
        tree = ET.fromstring(text)
        code = tree.findtext('.//returnReasonCode') or tree.findtext('.//resultCode')
        raise SourceError(code or 'unexpected_xml')
    return json.loads(text)


def redact(text, keys):
    for secret in keys.values():
        if secret:
            for variant in {secret, unquote(secret), quote(secret, safe='')}:
                text = text.replace(variant, '[REDACTED]')
    return text


class Client:
    def __init__(self, config, store, allowed, clock=now, opener=urlopen, sleep=time.sleep):
        self.config, self.store, self.allowed = config, store, allowed
        self.clock, self.opener, self.sleep = clock, opener, sleep
        self.keys = {}

    def fetch(self, provider, dataset, target, url, parser, headers=None, attempts=None):
        fingerprint = identity(self.keys)
        block_key = f'blocked:{provider}:{fingerprint}:{self.clock():%Y-%m-%d}'
        auth_block_key = block_key + ':' + dataset
        if self.store.get(block_key) or self.store.get(auth_block_key):
            return None
        attempts = attempts or self.config.values['max_attempts']
        for attempt in range(attempts):
            current = self.clock()
            if not self.allowed(current):
                return None
            request_id = self.store.begin_request(provider, dataset, target, current,
                                                  self.config.values['daily_budgets'][provider])
            if request_id is None:
                exhausted_key = f'local_budget_exhausted:{provider}:{current:%Y-%m-%d}'
                if not self.store.get(exhausted_key):
                    self.store.gap(dataset, target, current, 'local_daily_budget_exhausted')
                    self.store.put(exhausted_key, True)
                    LOG.warning('%s local daily budget exhausted; resumes next KST date', provider)
                return None
            status, raw_path = None, None
            try:
                request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'SeoulSubwayCollector/1.0', **(headers or {})})
                with self.opener(request, timeout=self.config.values['request_timeout_seconds']) as response:
                    status, raw = response.status, response.read()
                safe_text = redact(raw.decode('utf-8-sig', errors='replace'), self.keys)
                received_at = self.clock()
                raw_path = self.store.raw(dataset, received_at, {'target': target, 'requested_at': current.isoformat(), 'collected_at': received_at.isoformat(), 'http_status': status, 'body': safe_text})
                doc = decode(safe_text.encode('utf-8'))
                result = parser(doc)
                row_count = len(result[0]) if isinstance(result, tuple) else len(result.get('contents', []))
                self.store.finish(request_id, 'success', status, rows=row_count, raw=raw_path)
                return result
            except HTTPError as exc:
                status = exc.code
                # Preserve redacted error bodies for diagnosis; never log their URL/text.
                try:
                    safe_text = redact(exc.read().decode('utf-8-sig', errors='replace'), self.keys)
                    received_at = self.clock()
                    raw_path = self.store.raw(dataset, received_at, {
                        'target': target, 'requested_at': current.isoformat(),
                        'collected_at': received_at.isoformat(), 'http_status': status,
                        'body': safe_text})
                except OSError:
                    pass
                exc.close()
                code = f'http_{status}'
            except SourceError as exc:
                code = exc.code
            except (URLError, TimeoutError, OSError):
                code = 'network_error'
            except (ValueError, KeyError, TypeError, ET.ParseError):
                code = 'invalid_response'
            self.store.finish(request_id, 'failed', status, raw=raw_path, error=code)
            LOG.warning('%s %s failed: %s', dataset, target, code)
            if code in {'http_401', 'http_403', 'http_429', '20', '22', '29', '30', '31', '32', 'INFO-100', 'ERROR-100', 'ERROR-337'}:
                self.store.put(block_key if code in {'http_429', '22', 'ERROR-337'} else auth_block_key, code)
                break
            if code.startswith('http_4') or code in {'10', '12'}:
                break
            if attempt + 1 < attempts:
                self.sleep(2 ** attempt)
        self.store.gap(dataset, target, self.clock(), 'request_failed')
        return None
