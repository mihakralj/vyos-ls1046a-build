# Shared secret-redaction policy for the MCP config surfaces (the
# vyos-config:// resource and the `show configuration` tool path).
#
# Policy: mask genuine SECRETS (private keys, password hashes, API keys,
# pre-shared secrets, RADIUS keys, ...) but keep PUBLIC material visible
# (SSH/host public keys, X.509 certificates, CRLs). Public keys and certs
# are not confidential -- masking them protects nothing and only breaks
# legitimate read-only audit tasks ("is user X's key still the expected one?",
# "which cert is bound to HTTPS?").
#
# Rather than maintain a parallel secret list (which would drift from VyOS),
# we reuse vyos.utils.strip_config's own computed secret-path list and SUBTRACT
# the known-public entries from it. That way any NEW secret upstream adds is
# still masked automatically; we only ever remove known-public paths. If the
# strip_config internals ever change shape, we fail CLOSED -- fall back to the
# full upstream strip_config_tree() (over-redact rather than leak).

import copy
import logging

import vyos.utils.strip_config as _sc

LOG = logging.getLogger('http_api.mcp.redaction')

# Public-material secret-path signatures to keep VISIBLE (drop from redaction).
# Each tuple is base_path + secret_path (or a standalone path) as strip_config
# stores it.
_PUBLIC_SIGNATURES = {
    ('pki', 'ca', 'certificate'),
    ('pki', 'ca', 'crl'),
    ('pki', 'certificate', 'certificate'),
    ('pki', 'key-pair', 'public', 'key'),
    ('pki', 'openssh', 'public', 'key'),
}


def _signature(sp):
    if 'base_path' in sp:
        return tuple(sp['base_path']) + tuple(sp.get('secret_path', []))
    return tuple(sp.get('path', []))


def _is_public(sp):
    if _signature(sp) in _PUBLIC_SIGNATURES:
        return True
    # `system login user <u> authentication public-keys <id> key` is generated
    # dynamically by strip_config with base_path ending .../authentication/
    # public-keys and secret_path == ['key']. That is an SSH PUBLIC key -> keep.
    if ('base_path' in sp and sp.get('secret_path') == ['key']
            and tuple(sp['base_path'][-2:]) == ('authentication', 'public-keys')):
        return True
    return False


def _redact(_value):
    return '<REDACTED>'


def strip_mcp_secrets(config_tree):
    """Redact secrets in-place on a ConfigTree, keeping public keys/certs.

    Password-class secrets use the neutral ``<REDACTED>`` marker. Private-key
    material keeps VyOS' more specific ``<KEY DATA REDACTED>`` marker.
    Fails closed: on any internal mismatch, applies the full upstream
    strip_config_tree() so a broken filter over-redacts rather than leaks.
    """
    try:
        prepare = getattr(_sc, '__prepare_secret_paths')
        base_paths = getattr(_sc, '__secret_paths')
        strip_private = getattr(_sc, '__strip_private')
        password_redactor = getattr(_sc, '__anonymize_password')
    except AttributeError as e:
        LOG.warning('strip_config internals unavailable (%s); '
                    'falling back to full redaction', e)
        _sc.strip_config_tree(config_tree)
        return config_tree

    try:
        full = prepare(config_tree, copy.deepcopy(base_paths))
        secret_only = [sp for sp in full if not _is_public(sp)]
        for sp in secret_only:
            if sp.get('func') is password_redactor:
                sp['func'] = _redact
        strip_private(config_tree, secret_only)
        return config_tree
    except Exception as e:
        LOG.warning('secret-only redaction failed (%s); '
                    'falling back to full redaction', e)
        # config_tree may be partially modified; a full pass is idempotent for
        # already-redacted nodes and closes anything the partial pass missed.
        _sc.strip_config_tree(config_tree)
        return config_tree
