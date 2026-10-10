# Loopback TLS fixtures

These public test certificates and the **test-only private key** have no production use.
The certificate authority is never installed in the Windows certificate store.
`tools/verify_downloads.py` trusts it only inside downloader subprocesses through `SSL_CERT_FILE`.

The leaf certificate names `huggingface.co` and `github.com` so the unmodified downloader CLI can
use its validated manifest URLs. The harness CONNECT proxy routes these names exclusively to
its loopback TLS server; it never connects to those public sites. Both certificates are valid
from 2020-01-01 through 2050-01-01. The CA private key was discarded after signing the leaf.
