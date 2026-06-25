# Runtime hook — point OpenSSL at the bundled certifi CA bundle.
#
# In a PyInstaller bundle the embedded Python has no access to the OS trust
# store on macOS/Linux, so HTTPS certificate verification fails with
# CERTIFICATE_VERIFY_FAILED (e.g. when demucs downloads model weights or
# yt-dlp fetches from YouTube). Windows works without this because ssl reads
# the system cert store directly.
#
# OpenSSL reads SSL_CERT_FILE / SSL_CERT_DIR when a default-verify context is
# created, so setting them before any HTTPS request makes urllib, torch.hub
# and yt-dlp all verify against certifi's bundle.

import os

try:
    import certifi

    _ca = certifi.where()
    if _ca and os.path.exists(_ca):
        os.environ.setdefault("SSL_CERT_FILE", _ca)
        os.environ.setdefault("SSL_CERT_DIR", os.path.dirname(_ca))
        os.environ.setdefault("REQUESTS_CA_BUNDLE", _ca)
except Exception:
    # Never block startup over cert wiring; fall back to default behaviour.
    pass
