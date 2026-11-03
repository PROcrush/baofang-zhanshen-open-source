"""Opt-in, host-scoped macOS Keychain storage; no CLI arguments or file fallback."""
import ctypes as C
import sys
from contextlib import contextmanager


class KeychainError(ValueError):
    pass


class KeychainStore:
    SERVICE = 'local.baofangzhanshen.bailian-api'

    def __init__(self, service=SERVICE):
        self.service = service
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        if sys.platform != 'darwin':
            raise KeychainError('此系统暂不支持安全记住 Key；可仅在本次运行使用')
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.sec = C.CDLL('/System/Library/Frameworks/Security.framework/Security')
        signatures = {
            'CFStringCreateWithCString': ([C.c_void_p, C.c_char_p, C.c_uint32], C.c_void_p),
            'CFDataCreate': ([C.c_void_p, C.c_void_p, C.c_long], C.c_void_p),
            'CFDictionaryCreate': ([C.c_void_p, C.POINTER(C.c_void_p), C.POINTER(C.c_void_p), C.c_long, C.c_void_p, C.c_void_p], C.c_void_p),
            'CFDataGetLength': ([C.c_void_p], C.c_long),
            'CFDataGetBytePtr': ([C.c_void_p], C.c_void_p),
            'CFRelease': ([C.c_void_p], None),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.cf, name)
            fn.argtypes, fn.restype = args, result
        for name, args in {
            'SecItemCopyMatching': [C.c_void_p, C.POINTER(C.c_void_p)],
            'SecItemAdd': [C.c_void_p, C.POINTER(C.c_void_p)],
            'SecItemUpdate': [C.c_void_p, C.c_void_p],
            'SecItemDelete': [C.c_void_p],
        }.items():
            fn = getattr(self.sec, name)
            fn.argtypes, fn.restype = args, C.c_int32
        self._loaded = True

    def _constant(self, name):
        return C.c_void_p.in_dll(self.sec, name).value

    @contextmanager
    def _dictionary(self, fields):
        self._load()
        owned, keys, values = [], [], []
        try:
            for name, kind, value in fields:
                keys.append(self._constant(name))
                if kind == 'constant':
                    ref = self._constant(value)
                elif kind == 'bool':
                    ref = C.c_void_p.in_dll(self.cf, 'kCFBooleanTrue').value
                elif kind == 'data':
                    ref = self.cf.CFDataCreate(None, value, len(value))
                    owned.append(ref)
                else:
                    ref = self.cf.CFStringCreateWithCString(None, value.encode('utf-8'), 0x08000100)
                    owned.append(ref)
                values.append(ref)
            array = C.c_void_p * len(keys)
            # Owned values remain alive for the dictionary lifetime.
            dictionary = self.cf.CFDictionaryCreate(None, array(*keys), array(*values), len(keys), None, None)
            if not dictionary:
                raise KeychainError('系统钥匙串暂不可用')
            owned.append(dictionary)
            yield dictionary
        finally:
            for ref in reversed(owned):
                if ref:
                    self.cf.CFRelease(ref)

    def _query(self, account):
        return [('kSecClass', 'constant', 'kSecClassGenericPassword'),
                ('kSecAttrService', 'string', self.service),
                ('kSecAttrAccount', 'string', account),
                # Fail closed if locked/authorization is required; no modal UI
                # during startup, and never fall back to plaintext storage.
                ('kSecUseAuthenticationUI', 'constant', 'kSecUseAuthenticationUIFail')]

    @staticmethod
    def _check(status):
        if status:
            raise KeychainError(f'系统钥匙串未完成操作（{status}）；请解锁登录钥匙串后重试')

    def read(self, account):
        result = C.c_void_p()
        with self._dictionary(self._query(account) + [('kSecReturnData', 'bool', True)]) as query:
            status = self.sec.SecItemCopyMatching(query, C.byref(result))
        if status == -25300:
            return ''
        self._check(status)
        try:
            return C.string_at(self.cf.CFDataGetBytePtr(result), self.cf.CFDataGetLength(result)).decode('utf-8')
        finally:
            if result:
                self.cf.CFRelease(result)

    def write(self, account, secret):
        data = [('kSecValueData', 'data', secret.encode('utf-8'))]
        with self._dictionary(self._query(account)) as query, self._dictionary(data) as value:
            status = self.sec.SecItemUpdate(query, value)
        if status == -25300:
            with self._dictionary(self._query(account) + data) as item:
                status = self.sec.SecItemAdd(item, None)
        self._check(status)

    def delete(self, account):
        with self._dictionary(self._query(account)) as query:
            status = self.sec.SecItemDelete(query)
        if status != -25300:
            self._check(status)
