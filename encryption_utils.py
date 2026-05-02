import base64
import os
from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Random import get_random_bytes

ITERATIONS = 200000
KEY_SIZE = 32
SALT_SIZE = 16
TOKEN_VERSION = "v2"
DEFAULT_SALT_HEX = "3FF2EC019C627B945225DEBAD71A01B6985FE84C95A70EB132882F88C0A59A55"


class EncryptionError(Exception):
    pass


def _derive_key(passphrase: str, salt_hex: str) -> bytes:
    salt = bytes.fromhex(salt_hex)
    return PBKDF2(passphrase, salt, dkLen=KEY_SIZE, count=ITERATIONS)


def _b64_encode(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


def _b64_decode(data: str) -> bytes:
    return base64.b64decode(data.encode("utf-8"))


def encrypt_text(plain_text: str, passphrase: str, salt_hex: str | None = None) -> str:
    salt = get_random_bytes(SALT_SIZE)
    key = _derive_key(passphrase, salt.hex())
    nonce = get_random_bytes(12)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plain_text.encode("utf-8"))
    return f"{TOKEN_VERSION}:{_b64_encode(salt)}:{_b64_encode(nonce)}:{_b64_encode(tag)}:{_b64_encode(ciphertext)}"


def decrypt_text(token: str, passphrase: str, salt_hex: str = DEFAULT_SALT_HEX) -> str:
    try:
        # New format: v2:<salt_b64>:<nonce_b64>:<tag_b64>:<ciphertext_b64>
        if token.startswith(f"{TOKEN_VERSION}:"):
            _, salt_b64, nonce_b64, tag_b64, ciphertext_b64 = token.split(":", 4)
            salt = _b64_decode(salt_b64)
            nonce = _b64_decode(nonce_b64)
            tag = _b64_decode(tag_b64)
            ciphertext = _b64_decode(ciphertext_b64)
            key = _derive_key(passphrase, salt.hex())
        else:
            # Legacy format: base64(nonce + tag + ciphertext), fixed salt
            raw = _b64_decode(token)
            nonce = raw[:12]
            tag = raw[12:28]
            ciphertext = raw[28:]
            key = _derive_key(passphrase, salt_hex)

        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        plain = cipher.decrypt_and_verify(ciphertext, tag)
        return plain.decode("utf-8")
    except Exception as exc:
        raise EncryptionError("Failed to decrypt value") from exc


def get_env_passphrase() -> str:
    value = os.getenv("ENC_PASSPHRASE", "").strip()
    if not value:
        raise EncryptionError("ENC_PASSPHRASE is missing in environment")
    return value
