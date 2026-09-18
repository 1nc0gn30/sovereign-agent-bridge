"""Cryptographic Message Envelope & Symmetric Ratchet Protocol for Sovereign Agents.

Provides tamper-proof, authenticated, and encrypted message envelopes for agent-to-agent
communication over untrusted relays, channels, and peer networks.
Implements:
1. HKDF Key Derivation (RFC 5869) using HMAC-SHA256.
2. SHA256-CTR Keystream cipher for zero-dependency cross-platform encryption.
3. Envelope packaging with HMAC-SHA256 integrity verification.
4. Monotonic sequence numbering and anti-replay defense.

100% Python Standard Library. Zero external dependencies.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union


# ============================================================================
# RFC 5869 HKDF Key Derivation
# ============================================================================

def hkdf_extract(salt: Optional[bytes], ikm: bytes) -> bytes:
    """HKDF-Extract(salt, IKM) -> PRK (Pseudo-Random Key)."""
    if salt is None or len(salt) == 0:
        salt = bytes([0] * 32)
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """HKDF-Expand(PRK, info, L) -> OKM (Output Keying Material)."""
    n = (length + 31) // 32
    if n > 255:
        raise ValueError("Cannot expand to more than 255 * 32 bytes")
    t = b""
    okm = b""
    for i in range(1, n + 1):
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        okm += t
    return okm[:length]


def derive_keys(shared_secret: str, salt: str = "sovereign-agent-bridge-salt-v1", info: str = "envelope-keys") -> Tuple[bytes, bytes]:
    """Derive (enc_key, mac_key) from a shared secret using HKDF."""
    prk = hkdf_extract(salt.encode("utf-8"), shared_secret.encode("utf-8"))
    key_material = hkdf_expand(prk, info.encode("utf-8"), 64)
    enc_key = key_material[:32]
    mac_key = key_material[32:64]
    return enc_key, mac_key


# ============================================================================
# SHA-256 CTR Keystream Cipher (Zero-Dependency)
# ============================================================================

def sha256_ctr_crypt(data: bytes, key: bytes, nonce: bytes) -> bytes:
    """Encrypt/Decrypt bytes using SHA256 in Counter Mode with key and 16-byte nonce."""
    out = bytearray(len(data))
    block_count = (len(data) + 31) // 32
    for block_idx in range(block_count):
        counter_bytes = struct.pack(">Q", block_idx)
        block_key = hashlib.sha256(key + nonce + counter_bytes).digest()
        start = block_idx * 32
        end = min(len(data), start + 32)
        for i in range(start, end):
            out[i] = data[i] ^ block_key[i - start]
    return bytes(out)


# ============================================================================
# Message Envelope Data Models
# ============================================================================

@dataclass
class MessageEnvelope:
    """An authenticated and optionally encrypted sovereign agent message envelope."""

    envelope_id: str
    sender_id: str
    recipient_id: str
    seq_no: int
    timestamp_ms: int
    key_id: str
    is_encrypted: bool
    payload_b64: str
    nonce_hex: str
    mac_hex: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MessageEnvelope:
        return cls(
            envelope_id=data["envelope_id"],
            sender_id=data["sender_id"],
            recipient_id=data["recipient_id"],
            seq_no=int(data["seq_no"]),
            timestamp_ms=int(data["timestamp_ms"]),
            key_id=data.get("key_id", "default"),
            is_encrypted=bool(data.get("is_encrypted", False)),
            payload_b64=data["payload_b64"],
            nonce_hex=data.get("nonce_hex", ""),
            mac_hex=data["mac_hex"],
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> MessageEnvelope:
        return cls.from_dict(json.loads(json_str))


# ============================================================================
# Envelope Security Manager
# ============================================================================

class EnvelopeSecurityManager:
    """Manages creation, verification, and sequence tracking of secure message envelopes."""

    def __init__(self, agent_id: str, default_secret: Optional[str] = None):
        self.agent_id = agent_id
        self.default_secret = default_secret or "sovereign-agent-default-bridge-secret"
        self._seq_counter = 0
        self._seen_sequences: Dict[str, int] = {}  # sender_id -> highest_seen_seq

    def seal_envelope(
        self,
        recipient_id: str,
        payload: Union[str, Dict[str, Any], bytes],
        shared_secret: Optional[str] = None,
        encrypt: bool = True,
        key_id: str = "psk-v1",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MessageEnvelope:
        """Package and sign/encrypt a message for target recipient."""
        self._seq_counter += 1
        seq_no = self._seq_counter
        timestamp_ms = int(time.time() * 1000)
        envelope_id = f"env-{secrets.token_hex(8)}"

        secret = shared_secret or self.default_secret
        enc_key, mac_key = derive_keys(secret, info=f"envelope-{key_id}")

        # Serialize payload
        if isinstance(payload, dict):
            raw_data = json.dumps(payload, sort_keys=True).encode("utf-8")
        elif isinstance(payload, str):
            raw_data = payload.encode("utf-8")
        else:
            raw_data = payload

        nonce_hex = ""
        if encrypt:
            nonce = secrets.token_bytes(16)
            nonce_hex = nonce.hex()
            ciphertext = sha256_ctr_crypt(raw_data, enc_key, nonce)
            payload_b64 = base64.b64encode(ciphertext).decode("ascii")
        else:
            payload_b64 = base64.b64encode(raw_data).decode("ascii")

        # Compute HMAC over Envelope Header + Payload
        sig_body = f"{envelope_id}|{self.agent_id}|{recipient_id}|{seq_no}|{timestamp_ms}|{key_id}|{int(encrypt)}|{payload_b64}|{nonce_hex}"
        mac_hex = hmac.new(mac_key, sig_body.encode("utf-8"), hashlib.sha256).hexdigest()

        return MessageEnvelope(
            envelope_id=envelope_id,
            sender_id=self.agent_id,
            recipient_id=recipient_id,
            seq_no=seq_no,
            timestamp_ms=timestamp_ms,
            key_id=key_id,
            is_encrypted=encrypt,
            payload_b64=payload_b64,
            nonce_hex=nonce_hex,
            mac_hex=mac_hex,
            metadata=metadata or {},
        )

    def open_envelope(
        self,
        envelope: Union[MessageEnvelope, Dict[str, Any], str],
        shared_secret: Optional[str] = None,
        max_age_ms: int = 300_000,  # 5 minutes replay window
    ) -> Dict[str, Any]:
        """Verify HMAC integrity, check sequence monotonic order, and decrypt payload."""
        if isinstance(envelope, str):
            env = MessageEnvelope.from_json(envelope)
        elif isinstance(envelope, dict):
            env = MessageEnvelope.from_dict(envelope)
        else:
            env = envelope

        # 1. Age verification
        now_ms = int(time.time() * 1000)
        if abs(now_ms - env.timestamp_ms) > max_age_ms:
            raise ValueError(f"Envelope expired or clock skew exceeded: timestamp {env.timestamp_ms} vs now {now_ms}")

        # 2. Sequence Monotonicity & Anti-Replay Check
        last_seq = self._seen_sequences.get(env.sender_id, 0)
        if env.seq_no <= last_seq:
            raise ValueError(f"Replay or out-of-order sequence detected: seq {env.seq_no} <= last seen {last_seq}")

        # 3. Derive keys & Verify HMAC Signature
        secret = shared_secret or self.default_secret
        enc_key, mac_key = derive_keys(secret, info=f"envelope-{env.key_id}")

        sig_body = f"{env.envelope_id}|{env.sender_id}|{env.recipient_id}|{env.seq_no}|{env.timestamp_ms}|{env.key_id}|{int(env.is_encrypted)}|{env.payload_b64}|{env.nonce_hex}"
        expected_mac = hmac.new(mac_key, sig_body.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected_mac, env.mac_hex):
            raise ValueError("Integrity failure: HMAC signature mismatch (tampered message or wrong key)")

        # Record sequence on successful MAC validation
        self._seen_sequences[env.sender_id] = env.seq_no

        # 4. Decrypt or decode payload
        raw_payload = base64.b64decode(env.payload_b64.encode("ascii"))
        if env.is_encrypted:
            nonce = bytes.fromhex(env.nonce_hex)
            decrypted_bytes = sha256_ctr_crypt(raw_payload, enc_key, nonce)
            payload_str = decrypted_bytes.decode("utf-8", errors="replace")
        else:
            payload_str = raw_payload.decode("utf-8", errors="replace")

        try:
            parsed_payload = json.loads(payload_str)
        except Exception:
            parsed_payload = payload_str

        return {
            "envelope_id": env.envelope_id,
            "sender_id": env.sender_id,
            "recipient_id": env.recipient_id,
            "seq_no": env.seq_no,
            "timestamp_ms": env.timestamp_ms,
            "payload": parsed_payload,
            "is_encrypted": env.is_encrypted,
            "verified": True,
            "metadata": env.metadata,
        }
