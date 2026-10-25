"""Private persistent deduplication for concrete own-account instructions.

Caller holds own-transfer.lock across lookup, bank execution and commit.
Nothing here grants authority: source-message identity only prevents repeats.
"""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from .own_transfer import money


def valid_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r'telegram:[0-9]{1,20}:[0-9]{1,20}', value))


def identity(amount, source, target):
    payload = [format(money(amount), ".2f"), source, target]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True).encode()).hexdigest()


def path(request_id):
    root = Path(os.environ.get('TBANK_OWN_REQUESTS', '~/.local/share/tbank-mcp/own-requests')).expanduser()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root / (hashlib.sha256(request_id.encode()).hexdigest() + '.json')


def lookup(request_id, fingerprint):
    p = path(request_id)
    if not p.exists():
        return None
    with p.open() as f:
        row = json.load(f)
    if row.get('fingerprint') != fingerprint:
        return 'Этот request_id уже связан с другими параметрами. Перевод не отправлен.'
    if row.get('state') == 'finished':
        return 'Повтор поручения: нового списания нет. Предыдущий результат:\n' + row['result']
    return 'Поручение уже начинало выполняться; исход требует проверки payment_attempts и операций. Повтор не отправлен.'


def record(request_id, fingerprint, state, result=None):
    p = path(request_id)
    fd, tmp = tempfile.mkstemp(prefix='.request-', dir=p.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(dict(fingerprint=fingerprint, state=state, result=result), f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        directory = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
