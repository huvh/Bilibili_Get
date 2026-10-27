"""@brief 验证扩展包只含公开源码，不包含项目状态或用户凭据。"""
import json
import unittest
import zipfile

from cookie_extension import extension_info, extension_package, PACKAGE_FILES


class CookieExtensionTests(unittest.TestCase):
    def test_package_whitelist_and_permissions(self):
        with extension_package() as package, zipfile.ZipFile(package) as archive:
            self.assertEqual(set(archive.namelist()),
                             {"bili-cookie-extension/" + name for name in PACKAGE_FILES})
            manifest = json.loads(archive.read("bili-cookie-extension/manifest.json"))
            self.assertEqual(manifest["manifest_version"], 3)
            self.assertEqual(manifest["permissions"], ["cookies"])
            self.assertEqual(set(manifest["host_permissions"]),
                             {"https://*.bilibili.com/*", "http://127.0.0.1/*"})
            self.assertNotIn("externally_connectable", manifest)
            self.assertEqual(extension_info()["version"], manifest["version"])


if __name__ == "__main__":
    unittest.main()
