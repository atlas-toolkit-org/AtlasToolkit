"""The pure-Python Ed25519 that checks preset pack signatures (src/atlas_toolkit/core/ed25519.py): RFC 8032's test
vectors, and the rejections a verifier must make."""
import _isolate  # noqa: F401
import unittest

from atlas_toolkit.core import ed25519 as E

# RFC 8032, section 7.1: TEST 1 (empty message), TEST 2 (one byte), TEST 3 (two bytes)
VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


class Ed25519(unittest.TestCase):
    def test_rfc_8032_vectors(self):
        for secret, public, message, signature in VECTORS:
            secret, public, message, signature = map(bytes.fromhex, (secret, public, message, signature))
            self.assertEqual(E.public_key(secret), public)
            self.assertEqual(E.sign(secret, message), signature)
            self.assertTrue(E.verify(public, message, signature))

    def test_rejections(self):
        secret = E.new_secret()
        public = E.public_key(secret)
        sig = E.sign(secret, b"pack.json bytes")
        self.assertTrue(E.verify(public, b"pack.json bytes", sig))
        self.assertFalse(E.verify(public, b"pack.json bytez", sig))             # another message
        self.assertFalse(E.verify(E.public_key(E.new_secret()), b"pack.json bytes", sig))   # another key
        bad = bytearray(sig)
        bad[5] ^= 1
        self.assertFalse(E.verify(public, b"pack.json bytes", bytes(bad)))
        s = int.from_bytes(sig[32:], "little") + E.L                            # same point, non-canonical s
        self.assertFalse(E.verify(public, b"pack.json bytes", sig[:32] + int.to_bytes(s, 32, "little")))
        self.assertFalse(E.verify(public[:31], b"x", sig))
        self.assertFalse(E.verify(public, b"x", sig[:63]))
        self.assertFalse(E.verify(b"\xff" * 32, b"x", sig))                     # not a point


if __name__ == "__main__":
    unittest.main()
