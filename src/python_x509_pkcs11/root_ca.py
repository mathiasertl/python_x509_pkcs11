"""
Module which create a root CA

Exposes the functions:
- create()
"""

import asn1crypto
from asn1crypto import x509 as asn1_x509
from asn1crypto import csr as asn1_csr
from asn1crypto import pem as asn1_pem
from asn1crypto.algos import (
    SignedDigestAlgorithm,
    SignedDigestAlgorithmId
)

from .pkcs11_handle import PKCS11Session
from .csr import sign_csr


def _set_tbs_version(tbs: asn1_csr.CertificationRequestInfo
                     ) -> asn1_csr.CertificationRequestInfo:
    tbs["version"] = 0
    return tbs


def _set_tbs_subject(tbs: asn1_csr.CertificationRequestInfo,
                     subject_name: dict[str, str]
                     ) -> asn1_csr.CertificationRequestInfo:
    tbs["subject"] = asn1_csr.Name().build(subject_name)
    return tbs


def _set_tbs_subject_pk_info(tbs: asn1_csr.CertificationRequestInfo,
                             pk_info: asn1crypto.keys.PublicKeyInfo
                             ) -> asn1_csr.CertificationRequestInfo:
    tbs["subject_pk_info"] = pk_info
    return tbs


def _set_tbs_basic_constraints(tbs: asn1_csr.CertificationRequestInfo
                               ) -> asn1_csr.CertificationRequestInfo:
    b_c = asn1_x509.BasicConstraints()
    b_c["ca"] = True

    ext = asn1_x509.Extension()
    ext["extn_id"] = asn1_x509.ExtensionId("2.5.29.19")
    ext["critical"] = True
    ext["extn_value"] = b_c

    exts = asn1_csr.Extensions()
    exts.append(ext)

    ses = asn1_csr.SetOfExtensions()
    ses.append(exts)

    cria = asn1_csr.CRIAttribute()
    cria["type"] = asn1_csr.CSRAttributeType("1.2.840.113549.1.9.14")
    cria["values"] = ses

    if len(tbs["attributes"]) == 0:
        crias = asn1_csr.CRIAttributes()
        crias.append(cria)
        tbs["attributes"] = crias
    else:
        tbs["attributes"].append(cria)
    return tbs


def _set_tbs_key_usage(tbs: asn1_csr.CertificationRequestInfo
                       ) -> asn1_csr.CertificationRequestInfo:
    # https://github.com/wbond/asn1crypto/blob/master/asn1crypto/x509.py#L438
    # Bit 0, 5 ,6, from left to right
    k_u = asn1_x509.KeyUsage(('100001100',))
    ext = asn1_x509.Extension()
    ext["extn_id"] = asn1_x509.ExtensionId("2.5.29.15")
    ext["critical"] = True
    ext["extn_value"] = k_u

    exts = asn1_csr.Extensions()
    exts.append(ext)

    ses = asn1_csr.SetOfExtensions()
    ses.append(exts)

    cria = asn1_csr.CRIAttribute()
    cria["type"] = asn1_csr.CSRAttributeType("1.2.840.113549.1.9.14")
    cria["values"] = ses

    if len(tbs["attributes"]) == 0:
        crias = asn1_csr.CRIAttributes()
        crias.append(cria)
        tbs["attributes"] = crias
    else:
        tbs["attributes"].append(cria)
    return tbs


def _create_tbs(subject_name: dict[str, str],
                pk_info: asn1crypto.keys.PublicKeyInfo
                ) -> asn1_csr.CertificationRequestInfo:
    tbs = asn1_csr.CertificationRequestInfo()

    tbs = _set_tbs_version(tbs)
    tbs = _set_tbs_subject(tbs, subject_name)
    tbs = _set_tbs_subject_pk_info(tbs, pk_info)
    tbs = _set_tbs_basic_constraints(tbs)
    tbs = _set_tbs_key_usage(tbs)
    return tbs


def create(key_label: str,
           key_size: int,
           subject_name: dict[str, str],
           ) -> str:
    """
    Create and selfsign a CSR with
    the key_label in the PKCS11 device.

    Parameters:
    key_label (str): Keypair label.
    key_size (int): Key size, 2048 and 4096 works best.
    subject_name (dict[str, str]): Dict with the new root CA x509 Names.

    Returns:
    str

    """
    pk_info, _ = PKCS11Session().create_keypair_if_not_exists(
        key_label, key_size)

    tbs = _create_tbs(subject_name, pk_info)

    signed_csr = asn1_csr.CertificationRequest()
    signed_csr["certification_request_info"] = tbs

    sda = SignedDigestAlgorithm()
    sda["algorithm"] = SignedDigestAlgorithmId("sha256_rsa")

    signed_csr["signature_algorithm"] = sda
    signed_csr["signature"] = PKCS11Session().sign(key_label, tbs.dump())

    pem_enc = asn1_pem.armor('CERTIFICATE REQUEST', signed_csr.dump())

    # Needed for mypy strict
    assert isinstance(pem_enc, bytes)

    return sign_csr(key_label, subject_name, pem_enc.decode('utf-8'))