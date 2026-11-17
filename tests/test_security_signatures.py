import unittest

try:
    from core.utils.security import Security
except ImportError:  # cryptography missing in a minimal environment
    Security = None


@unittest.skipIf(Security is None, 'cryptography is not installed')
class SecuritySignatureTests(unittest.TestCase):
    """Signing and verifying read buffers directly; every buffer type must keep its exact bytes."""

    @classmethod
    def setUpClass(cls):
        cls.security = Security()
        cls.security.generate_key_pair()

    def test_round_trip_for_every_buffer_type(self):
        payload = bytes(range(256)) * 4096  # 1 MB, like a DLL
        signature = self.security.sign(payload)
        for data in (payload, bytearray(payload), memoryview(payload)):
            with self.subTest(type=type(data).__name__):
                self.assertTrue(self.security.verify(signature, data))

    def test_buffers_are_used_without_copying(self):
        payload = bytearray(b'dll bytes')
        self.assertIs(self.security.to_bytearray(payload, 'utf-8'), payload)

    def test_any_changed_byte_fails_verification(self):
        payload = bytes(range(256)) * 64
        signature = self.security.sign(payload)
        for data in (payload[:-1], payload + b'\0', b'\1' + payload[1:]):
            with self.subTest(size=len(data)):
                self.assertFalse(self.security.verify(signature, data))
                self.assertFalse(self.security.verify(signature, memoryview(data)))

    def test_text_is_signed_as_encoded_bytes(self):
        signature = self.security.sign('version 1.0')
        self.assertTrue(self.security.verify(signature, b'version 1.0'))
        self.assertFalse(self.security.verify(signature, 'version 1.1'))


if __name__ == '__main__':
    unittest.main()
