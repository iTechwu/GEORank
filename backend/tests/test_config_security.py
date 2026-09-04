import unittest

from app.core.config import Settings


class ConfigSecurityTests(unittest.TestCase):
    def test_production_rejects_development_secrets_and_origin(self):
        config = Settings(_env_file=None, DEBUG=False)

        with self.assertRaisesRegex(RuntimeError, "生产环境安全配置无效"):
            config.validate_production_security()

    def test_production_accepts_independent_secrets_and_https_origin(self):
        config = Settings(
            _env_file=None,
            DEBUG=False,
            SECRET_KEY="s" * 40,
            JWT_SECRET="j" * 40,
            SETTINGS_ENCRYPTION_KEY="e" * 40,
            PUBLIC_BASE_URL="https://app.georank.com",
            SSO_AUTH_REQUIRED=True,
            SSO_CLIENT_SECRET="sso-secret",
            SSO_REDIRECT_URI="https://georank.dofe.ai/auth/oidc/callback",
        )

        config.validate_production_security()

    def test_production_rejects_local_user_source(self):
        config = Settings(
            _env_file=None,
            DEBUG=False,
            SECRET_KEY="s" * 40,
            JWT_SECRET="j" * 40,
            SETTINGS_ENCRYPTION_KEY="e" * 40,
            PUBLIC_BASE_URL="https://app.georank.com",
            SSO_AUTH_REQUIRED=False,
            SSO_CLIENT_SECRET="sso-secret",
            SSO_REDIRECT_URI="https://georank.dofe.ai/auth/oidc/callback",
        )

        with self.assertRaisesRegex(RuntimeError, "用户源只能使用 sso.ixicai.cn"):
            config.validate_production_security()

    def test_debug_mode_keeps_local_development_bootable(self):
        config = Settings(_env_file=None, DEBUG=True)

        config.validate_production_security()


if __name__ == "__main__":
    unittest.main()
