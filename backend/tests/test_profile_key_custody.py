"""Custody encryption and restore failure probes, with runtime-generated keys."""
import base64
import json
import os
from datetime import UTC, datetime

import pytest
from cryptography.exceptions import InvalidTag

from scripts import profile_key_custody as custody
from scripts.profile_key_custody import KEY_NAMES, open_keys, seal_keys, validate_keys


def synthetic():
    return {name: base64.b64encode(os.urandom(32)).decode() for name in KEY_NAMES}


def test_roundtrip_randomized_and_no_plaintext_in_file():
    keys = synthetic()
    password = base64.b64encode(os.urandom(24)).decode()
    stamp = datetime.now(UTC).isoformat()
    first = seal_keys(keys, password, stamp)
    second = seal_keys(keys, password, stamp)
    assert first != second
    assert open_keys(json.loads(json.dumps(first)), password) == {"keys": keys, "created_at": stamp}
    encoded = json.dumps(first)
    assert all(value not in encoded for value in keys.values())
    assert password not in encoded


def test_wrong_password_and_tampering_are_rejected():
    password = base64.b64encode(os.urandom(24)).decode()
    envelope = seal_keys(synthetic(), password, datetime.now(UTC).isoformat())
    with pytest.raises(InvalidTag):
        open_keys(envelope, password + "wrong")
    raw = bytearray(base64.b64decode(envelope["ciphertext"]))
    raw[-1] ^= 1
    envelope["ciphertext"] = base64.b64encode(raw).decode()
    with pytest.raises(InvalidTag):
        open_keys(envelope, password)


def test_shared_or_invalid_keys_and_unknown_format_are_rejected():
    keys = synthetic()
    keys[KEY_NAMES[1]] = keys[KEY_NAMES[0]]
    with pytest.raises(ValueError):
        validate_keys(keys)
    with pytest.raises(ValueError):
        validate_keys({})
    with pytest.raises(ValueError):
        open_keys({"format": "unknown"}, "password")


def test_saved_file_verified_and_receipt_contains_no_key_values(tmp_path, monkeypatch):
    keys = synthetic()
    password = base64.b64encode(os.urandom(24)).decode()
    envelope = seal_keys(keys, password, datetime.now(UTC).isoformat())
    output, receipt = tmp_path / "keys.ysgkeys", tmp_path / "receipt.json"

    def remote(mode, supplied_password, saved_envelope, *_):
        assert supplied_password == password
        if mode == "create":
            return envelope
        assert saved_envelope == json.loads(output.read_text())
        assert open_keys(saved_envelope, password)["keys"] == keys
        return {"backup_matches_runtime": True, "recovered_aes_and_hmac_roundtrip": True}

    monkeypatch.setattr(custody, "remote_call", remote)
    custody.create_backup(password, output, receipt, "unused", "unused")
    for path in (output, receipt):
        text = path.read_text()
        assert password not in text and all(value not in text for value in keys.values())
    original = output.read_bytes()
    with pytest.raises(ValueError):
        custody.create_backup(password, output, receipt, "unused", "unused")
    assert output.read_bytes() == original


def test_failed_verification_keeps_encrypted_copy_without_success_receipt(tmp_path, monkeypatch):
    password = base64.b64encode(os.urandom(24)).decode()
    envelope = seal_keys(synthetic(), password, datetime.now(UTC).isoformat())
    output, receipt = tmp_path / "keys.ysgkeys", tmp_path / "receipt.json"

    def remote(mode, *_):
        if mode == "create":
            return envelope
        raise RuntimeError("verification unavailable")

    monkeypatch.setattr(custody, "remote_call", remote)
    with pytest.raises(RuntimeError):
        custody.create_backup(password, output, receipt, "unused", "unused")
    assert output.exists() and not receipt.exists()


def test_ssh_uses_stdin_and_fixed_host_without_password_argument(tmp_path, monkeypatch):
    identity = tmp_path / "identity-placeholder"
    identity.touch()
    password = base64.b64encode(os.urandom(24)).decode()
    captured = {}

    def run(command, **kwargs):
        captured.update({"command": command, **kwargs})
        return type("Result", (), {"returncode": 0, "stdout": b"{}"})()

    monkeypatch.setattr(custody.subprocess, "run", run)
    assert custody.remote_call("verify", password, {}, str(identity), "root@8.159.145.112") == {}
    assert password not in repr(captured["command"])
    assert "input" in captured and not captured.get("shell", False)
    with pytest.raises(ValueError):
        custody.remote_call("verify", password, {}, str(identity), "-oProxyCommand=unexpected")
