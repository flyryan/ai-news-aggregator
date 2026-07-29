"""Security guard: the admin panel must verify Access JWTs, not trust headers.

Context / why this exists
-------------------------
An earlier admin service (unmerged branch `admin-service`, commit 42cfad0)
authenticated like this:

    principal = request.headers.get("cf-access-authenticated-user-email") or \
                request.headers.get("cf-access-jwt-assertion")
    if principal:
        return await call_next(request)

That is a truthiness test on an attacker-controlled header. `curl -H
'cf-access-authenticated-user-email: x'` was a full admin session, the literal
string "x" became the audit-log identity, a correctly-signed token from any
OTHER Access application passed, and expiry was never checked.

The blast radius is not abstract: this service holds a GitHub PAT and can
dispatch pipeline runs that cost roughly $8-11 each, and it triggers container
rebuilds on the origin host.

This guard locks in real verification: RS256 signature against the team JWKS,
plus aud, iss, exp, and an email allowlist.

  python3 -m unittest tests.admin_auth_test -v
"""

import datetime
import os
import unittest
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from admin_service.auth import AccessVerifier, NotAuthorized, TokenInvalid
from admin_service.config import AdminSettings

TEAM = "testteam.cloudflareaccess.com"
AUD = "test-aud-tag"
ISS = f"https://{TEAM}"
ALLOWED = "operator@example.com"


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


class _StubJWKClient:
    """Stands in for PyJWKClient so tests never touch the network."""

    def __init__(self, public_pem):
        self._public_pem = public_pem

    def get_signing_key_from_jwt(self, token):  # noqa: ARG002 - signature parity
        class _Key:
            def __init__(self, pem):
                self.key = pem

        return _Key(self._public_pem)


class AccessVerifierTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_pem, cls.public_pem = _keypair()
        cls.evil_private_pem, _ = _keypair()
        cls.settings = AdminSettings.from_env({
            "ADMIN_CF_TEAM_DOMAIN": TEAM,
            "ADMIN_CF_AUD": AUD,
            "ADMIN_ALLOWED_EMAILS": ALLOWED,
        })

    def _verifier(self, settings=None):
        return AccessVerifier(
            settings or self.settings, jwks_client=_StubJWKClient(self.public_pem)
        )

    def _claims(self, **overrides):
        now = datetime.datetime.now(datetime.timezone.utc)
        claims = {
            "aud": [AUD],           # Cloudflare sends aud as an ARRAY
            "iss": ISS,
            "sub": "user-uuid-1",
            "email": ALLOWED,
            "iat": now,
            "nbf": now,
            "exp": now + datetime.timedelta(minutes=5),
        }
        claims.update(overrides)
        return claims

    def _token(self, claims=None, key=None, algorithm="RS256"):
        return jwt.encode(
            claims if claims is not None else self._claims(),
            key if key is not None else self.private_pem,
            algorithm=algorithm,
        )

    # --- the cases that must succeed ----------------------------------------

    def test_accepts_a_valid_token(self):
        principal = self._verifier().verify(self._token())
        self.assertEqual(ALLOWED, principal.email)
        self.assertEqual("user-uuid-1", principal.subject)
        self.assertEqual("user", principal.kind)

    def test_accepts_a_service_token(self):
        settings = AdminSettings.from_env({
            "ADMIN_CF_TEAM_DOMAIN": TEAM,
            "ADMIN_CF_AUD": AUD,
            "ADMIN_ALLOWED_EMAILS": ALLOWED,
            "ADMIN_ALLOWED_SERVICE_TOKENS": "ci-runner.access",
        })
        claims = self._claims(common_name="ci-runner.access", sub="")
        claims.pop("email")
        principal = self._verifier(settings).verify(self._token(claims))
        self.assertEqual("service_token", principal.kind)
        self.assertEqual("ci-runner.access", principal.email)

    def test_email_matching_is_case_insensitive(self):
        token = self._token(self._claims(email=ALLOWED.upper()))
        self.assertEqual(ALLOWED, self._verifier().verify(token).email)

    # --- everything below must be rejected ----------------------------------

    def test_rejects_a_forged_signature(self):
        token = self._token(key=self.evil_private_pem)
        with self.assertRaises(TokenInvalid):
            self._verifier().verify(token)

    def test_rejects_alg_none(self):
        token = jwt.encode(self._claims(), None, algorithm="none")
        with self.assertRaises(TokenInvalid):
            self._verifier().verify(token)

    def test_rejects_a_token_for_another_access_application(self):
        # A real, correctly-signed token from a different app. This is what the
        # presence check could not distinguish.
        with self.assertRaises(TokenInvalid):
            self._verifier().verify(self._token(self._claims(aud=["someone-elses-app"])))

    def test_rejects_a_token_from_another_team(self):
        with self.assertRaises(TokenInvalid):
            self._verifier().verify(
                self._token(self._claims(iss="https://evil.cloudflareaccess.com"))
            )

    def test_rejects_an_expired_token(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        claims = self._claims(
            iat=now - datetime.timedelta(hours=2),
            nbf=now - datetime.timedelta(hours=2),
            exp=now - datetime.timedelta(hours=1),
        )
        with self.assertRaises(TokenInvalid):
            self._verifier().verify(self._token(claims))

    def test_rejects_a_token_with_no_expiry(self):
        claims = self._claims()
        claims.pop("exp")
        with self.assertRaises(TokenInvalid):
            self._verifier().verify(self._token(claims))

    def test_rejects_garbage_and_empty_input(self):
        for value in ("", "not.a.jwt", "x", "a.b.c"):
            with self.subTest(value=value), self.assertRaises(TokenInvalid):
                self._verifier().verify(value)

    def test_rejects_a_valid_token_for_an_unlisted_email(self):
        # Correctly signed and scoped, but not an operator. Authentication and
        # authorization are separate failures.
        token = self._token(self._claims(email="stranger@example.com"))
        with self.assertRaises(NotAuthorized):
            self._verifier().verify(token)

    def test_rejects_an_unlisted_service_token(self):
        claims = self._claims(common_name="rogue.access", sub="")
        claims.pop("email")
        with self.assertRaises(NotAuthorized):
            self._verifier().verify(self._token(claims))


class DevBypassTest(unittest.TestCase):
    """The dev bypass must be opt-in, and must never be on by default."""

    def test_bypass_is_off_unless_explicitly_enabled(self):
        self.assertNotEqual(
            "1", os.environ.get("ADMIN_DEV"),
            "ADMIN_DEV=1 is set in this environment; tests would pass against a "
            "bypassed verifier and prove nothing",
        )

    def test_provisioning_never_writes_it_into_the_host_env_file(self):
        setup = Path(__file__).resolve().parents[1] / "deploy" / "setup_admin_service.sh"
        if setup.is_file():
            self.assertNotIn(
                "ADMIN_DEV", setup.read_text(),
                "the provisioning script must never write ADMIN_DEV into the host "
                "env file -- that would ship an auth bypass to production",
            )


if __name__ == "__main__":
    unittest.main()
