"""
Test our OCSP
"""
from typing import List
import unittest
import datetime
import os
import asyncio
from secrets import token_bytes

from asn1crypto import x509 as asn1_x509
from asn1crypto import ocsp as asn1_ocsp


from src.python_x509_pkcs11.pkcs11_handle import PKCS11Session
from src.python_x509_pkcs11.ocsp import certificate_ocsp_data, request, response
from src.python_x509_pkcs11.error import DuplicateExtensionException

# Replace the above with this should you use this code
# from python_x509_pkcs11.ca import create

name_dict = {
    "country_name": "SE",
    "state_or_province_name": "Stockholm",
    "locality_name": "Stockholm_TEST",
    "organization_name": "SUNET",
    "organizational_unit_name": "SUNET Infrastructure",
    "common_name": "ca-test-ocsp-14.sunet.se",
    "email_address": "soc@sunet.se",
}

TEST_CERT = """-----BEGIN CERTIFICATE-----
MIIFTjCCBDagAwIBAgIUTSCngZMLWEY0NsmHifr/Pu2bsicwDQYJKoZIhvcNAQEL
BQAwgZwxCzAJBgNVBAYTAlNFMRIwEAYDVQQIDAlTdG9ja2hvbG0xEjAQBgNVBAcM
CVN0b2NraG9sbTEOMAwGA1UECgwFU1VORVQxHTAbBgNVBAsMFFNVTkVUIEluZnJh
c3RydWN0dXJlMRkwFwYDVQQDDBBjYS10ZXN0LnN1bmV0LnNlMRswGQYJKoZIhvcN
AQkBFgxzb2NAc3VuZXQuc2UwHhcNMjIwOTI3MDYzODQwWhcNMjUwOTI2MDY0MDQw
WjCBqzELMAkGA1UEBhMCU0UxEjAQBgNVBAgMCVN0b2NraG9sbTEXMBUGA1UEBwwO
U3RvY2tob2xtX3Rlc3QxDjAMBgNVBAoMBVNVTkVUMR0wGwYDVQQLDBRTVU5FVCBJ
bmZyYXN0cnVjdHVyZTEjMCEGA1UEAwwaY2EtdGVzdC1jcmVhdGUtMjAuc3VuZXQu
c2UxGzAZBgkqhkiG9w0BCQEWDHNvY0BzdW5ldC5zZTCCASIwDQYJKoZIhvcNAQEB
BQADggEPADCCAQoCggEBALZdE70YSvQgHIhWw+LQ47M9lEEeFjC0xKoptV6G586m
yHKS4ti2NclE82sPrFiUye3/FitLT7Pf+eTKZ4rAU+P/LuirL5XYsTgf6Pf6UsKw
9T9DDycO2llMmOHCGa+qPlMzDAJ/9Vffzr/bFz+Cv/n1/TWZhTMzAk4aGWfXvWbq
CHpGhPLuB1TXfmRBOB8cUCfbrfUJ+i0lD8oivrJtAdEEJDLuAQ5sZ7YI5Xw1AFPZ
fYHMY5Nw5PWydUI3OnpLL4rrAGDvHEvwtLro6znd8elHiK3SjgpMyTAgD4F2oZqQ
zBrO/cUksMCkQiwPa0kgfRNu91vq2SpKo47eYdPFo1cCAwEAAaOCAXUwggFxMA4G
A1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MIGgBggrBgEFBQcBAQSBkzCB
kDBlBggrBgEFBQcwAoZZaHR0cDovL2xvY2FsaG9zdDo4MDAwL2NhLzNhOWU1ZTYy
ZjFlN2IzZTIxN2RiMWUzNTNmMjA4MzNmZDI4NzI4ZThhZWMzZTEzOWU3OTRkMDFj
NTE5ZGU5MTcwJwYIKwYBBQUHMAGGG2h0dHA6Ly9sb2NhbGhvc3Q6ODAwMC9vY3Nw
LzBrBgNVHR8EZDBiMGCgXqBchlpodHRwOi8vbG9jYWxob3N0OjgwMDAvY3JsLzNh
OWU1ZTYyZjFlN2IzZTIxN2RiMWUzNTNmMjA4MzNmZDI4NzI4ZThhZWMzZTEzOWU3
OTRkMDFjNTE5ZGU5MTcwHQYDVR0OBBYEFFmrno6DYIVpbwUvhaMPr242LhmYMB8G
A1UdIwQYMBaAFK3QiERXlifO9CLGxzdXye9ppFuLMA0GCSqGSIb3DQEBCwUAA4IB
AQAkh+ijRkxjABqfkw4+fr8ZYAbdaZdXdZ2NgXGeB3DAFPYp6xZIREB+bE4YRd5n
xIsYWZTya1oTTCcMA2oLMO7Jv5KqJgkS5jDKM+SK3QIK68HfCW2ZrhkcGAmYmxOY
4eUkhFY3axEJ501/PqVxBRCj/FJbXsoI72v7lFj6MdESxEtJCj8lz5DdH3OHDgDd
4SQomVowm8nIfuxIuuoSoZR4DluPeWMDUoiKky8ocVxEymtE1tJYdrrL3f0ZcFey
mF+JNgr8wdkW7fMy3HpRk7QOvJ2calp9V2THBZ8T+UPKmCkBxdW511hDzLpIb7rA
lgIDB0Y1AZDNLKuq6QWifdf3
-----END CERTIFICATE-----
"""

requestor_name_dict = {
    "state_or_province_name": "Stockholm",
    "country_name": "FI",
    "organization_name": "SUNET",
    "locality_name": "Stockholm_test",
    "organizational_unit_name": "SUNET Infrastructure",
    "common_name": "ca-test-ocsp-14.sunet.se",
    "email_address": "soc@sunet.se",
}


class TestOCSP(unittest.TestCase):
    """
    Test our OCSP module.
    """

    def _mixed_response(self, ocsp_request: asn1_ocsp.OCSPRequest) -> asn1_ocsp.Responses:
        cert_ids: List[asn1_ocsp.CertId] = []
        responses = asn1_ocsp.Responses()

        for _, curr_req in enumerate(ocsp_request["tbs_request"]["request_list"]):
            cert_ids.append(curr_req["req_cert"])

        for index, cert_id in enumerate(cert_ids):
            curr_response = asn1_ocsp.SingleResponse()
            curr_response["cert_id"] = cert_id

            if index == 0:
                curr_response["cert_status"] = asn1_ocsp.CertStatus("good")
            elif index == 1:
                revoked_info = asn1_ocsp.RevokedInfo()
                revoked_info["revocation_time"] = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                    minutes=20
                )
                revoked_info["revocation_reason"] = asn1_ocsp.CRLReason(5)
                curr_response["cert_status"] = asn1_ocsp.CertStatus({"revoked": revoked_info})
            elif index == 2:
                curr_response["cert_status"] = asn1_ocsp.CertStatus("unknown")

            curr_response["this_update"] = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)
            responses.append(curr_response)
        return responses

    def _good_response(self, ocsp_request: asn1_ocsp.OCSPRequest) -> asn1_ocsp.Responses:
        cert_ids: List[asn1_ocsp.CertId] = []
        responses = asn1_ocsp.Responses()

        for _, curr_req in enumerate(ocsp_request["tbs_request"]["request_list"]):
            cert_ids.append(curr_req["req_cert"])

        for _, cert_id in enumerate(cert_ids):
            curr_response = asn1_ocsp.SingleResponse()
            curr_response["cert_id"] = cert_id
            curr_response["cert_status"] = asn1_ocsp.CertStatus("good")
            curr_response["this_update"] = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)
            responses.append(curr_response)
        return responses

    def _revoked_response(self, ocsp_request: asn1_ocsp.OCSPRequest) -> asn1_ocsp.Responses:
        cert_ids: List[asn1_ocsp.CertId] = []
        responses = asn1_ocsp.Responses()

        for _, curr_req in enumerate(ocsp_request["tbs_request"]["request_list"]):
            cert_ids.append(curr_req["req_cert"])

        for _, cert_id in enumerate(cert_ids):
            curr_response = asn1_ocsp.SingleResponse()
            curr_response["cert_id"] = cert_id

            revoked_info = asn1_ocsp.RevokedInfo()
            revoked_info["revocation_time"] = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                minutes=20
            )
            revoked_info["revocation_reason"] = asn1_ocsp.CRLReason(5)

            curr_response["cert_status"] = asn1_ocsp.CertStatus({"revoked": revoked_info})
            curr_response["this_update"] = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)
            responses.append(curr_response)
        return responses

    def _unknown_response(self, ocsp_request: asn1_ocsp.OCSPRequest) -> asn1_ocsp.Responses:
        cert_ids: List[asn1_ocsp.CertId] = []
        responses = asn1_ocsp.Responses()

        for _, curr_req in enumerate(ocsp_request["tbs_request"]["request_list"]):
            cert_ids.append(curr_req["req_cert"])

        for _, cert_id in enumerate(cert_ids):
            curr_response = asn1_ocsp.SingleResponse()
            curr_response["cert_id"] = cert_id
            curr_response["cert_status"] = asn1_ocsp.CertStatus("unknown")
            curr_response["this_update"] = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)
            responses.append(curr_response)
        return responses

    def test_ocsp_request(self) -> None:
        """
        Create an ocsp request.
        """

        # Test default
        i_name_h, i_key_h, serial, _ = certificate_ocsp_data(TEST_CERT)
        data = asyncio.run(request([(i_name_h, i_key_h, serial)]))
        test_ocsp = asn1_ocsp.OCSPRequest.load(data)
        self.assertTrue(isinstance(test_ocsp, asn1_ocsp.OCSPRequest))
        self.assertTrue(test_ocsp["tbs_request"]["version"].native == "v1")
        self.assertTrue(len(test_ocsp["tbs_request"]["request_list"]) == 1)
        self.assertTrue(test_ocsp["optional_signature"].native is None)

        # Test requestor name
        self.assertTrue(test_ocsp["tbs_request"]["requestor_name"].native is None)
        g_n = asn1_x509.GeneralName(name="directory_name", value=(asn1_ocsp.Name().build(requestor_name_dict)))
        data = asyncio.run(request([(i_name_h, i_key_h, serial)], requestor_name=g_n))
        test_ocsp = asn1_ocsp.OCSPRequest.load(data)
        self.assertTrue(isinstance(test_ocsp, asn1_ocsp.OCSPRequest))
        self.assertTrue(test_ocsp["tbs_request"]["requestor_name"] == g_n)

        # Test no certs in request
        with self.assertRaises(ValueError):
            data = asyncio.run(request([]))

        # Test multiple certs in request
        data = asyncio.run(request([(i_name_h, i_key_h, serial), (i_name_h, i_key_h, serial)]))
        test_ocsp = asn1_ocsp.OCSPRequest.load(data)
        self.assertTrue(isinstance(test_ocsp, asn1_ocsp.OCSPRequest))
        self.assertTrue(len(test_ocsp["tbs_request"]["request_list"]) == 2)

        # Test nonce extension
        nonce_val = token_bytes(32)
        nonce_ext = asn1_ocsp.TBSRequestExtension()
        nonce_ext["extn_id"] = asn1_ocsp.TBSRequestExtensionId("1.3.6.1.5.5.7.48.1.2")
        nonce_ext["extn_value"] = nonce_val
        extra_extensions = asn1_ocsp.TBSRequestExtensions()
        extra_extensions.append(nonce_ext)

        data = asyncio.run(
            request([(i_name_h, i_key_h, serial), (i_name_h, i_key_h, serial)], extra_extensions=extra_extensions)
        )
        test_ocsp = asn1_ocsp.OCSPRequest.load(data)
        self.assertTrue(isinstance(test_ocsp, asn1_ocsp.OCSPRequest))
        self.assertTrue(test_ocsp["tbs_request"]["request_extensions"][0]["extn_value"].native == nonce_val)

    def test_signed_ocsp_request(self) -> None:
        """
        Create a signed_ocsp request.
        """

        new_key_label = hex(int.from_bytes(os.urandom(20), "big") >> 1)
        asyncio.run(PKCS11Session.create_keypair(new_key_label))
        i_name_h, i_key_h, serial, _ = certificate_ocsp_data(TEST_CERT)
        g_n = asn1_x509.GeneralName(name="directory_name", value=(asn1_ocsp.Name().build(requestor_name_dict)))

        # Test signed but no requestor name
        with self.assertRaises(ValueError):
            data = asyncio.run(request([(i_name_h, i_key_h, serial)], key_label=new_key_label))
            test_ocsp = asn1_ocsp.OCSPRequest.load(data)
            self.assertTrue(isinstance(test_ocsp, asn1_ocsp.OCSPRequest))

        data = asyncio.run(request([(i_name_h, i_key_h, serial)], key_label=new_key_label, requestor_name=g_n))
        test_ocsp = asn1_ocsp.OCSPRequest.load(data)
        self.assertTrue(isinstance(test_ocsp, asn1_ocsp.OCSPRequest))

        # Ensure we have a sig
        self.assertTrue(isinstance(test_ocsp["optional_signature"]["signature"].native, bytes))
        self.assertTrue(len(test_ocsp["optional_signature"]["signature"].native) > 32)

        # 0 extra certs
        data = asyncio.run(request([(i_name_h, i_key_h, serial)], key_label=new_key_label, requestor_name=g_n))
        test_ocsp = asn1_ocsp.OCSPRequest.load(data)
        self.assertTrue(isinstance(test_ocsp, asn1_ocsp.OCSPRequest))
        self.assertTrue(len(test_ocsp["optional_signature"]["certs"]) == 0)

        # 2 extra certs
        data = asyncio.run(
            request(
                [(i_name_h, i_key_h, serial)], key_label=new_key_label, requestor_name=g_n, certs=[TEST_CERT, TEST_CERT]
            )
        )
        test_ocsp = asn1_ocsp.OCSPRequest.load(data)
        self.assertTrue(isinstance(test_ocsp, asn1_ocsp.OCSPRequest))
        self.assertTrue(len(test_ocsp["optional_signature"]["certs"]) == 2)

    def test_ocsp_response(self) -> None:
        """
        Create an ocsp response.
        """

        new_key_label = hex(int.from_bytes(os.urandom(20), "big") >> 1)
        asyncio.run(PKCS11Session.create_keypair(new_key_label))

        i_name_h, i_key_h, serial, _ = certificate_ocsp_data(TEST_CERT)
        data = asyncio.run(request([(i_name_h, i_key_h, serial)]))
        test_request = asn1_ocsp.OCSPRequest.load(data)
        self.assertTrue(isinstance(test_request, asn1_ocsp.OCSPRequest))
        data = asyncio.run(response(new_key_label, name_dict, self._good_response(test_request), 0))
        test_response = asn1_ocsp.OCSPResponse.load(data)
        self.assertTrue(isinstance(test_response, asn1_ocsp.OCSPResponse))
        self.assertTrue(test_response["response_bytes"].native is not None)
        self.assertTrue(
            test_response["response_bytes"]["response"].native["tbs_response_data"]["responses"][0]["cert_status"]
            == "good"
        )
        self.assertTrue(
            test_response["response_bytes"]["response"].native["tbs_response_data"]["response_extensions"] is None
            or len(test_response["response_bytes"]["response"].native["tbs_response_data"]["response_extensions"]) == 0
        )

    def test_ocsp_response_cert_status(self) -> None:
        """
        Create an ocsp responses with different cert status
        """

        new_key_label = hex(int.from_bytes(os.urandom(20), "big") >> 1)
        asyncio.run(PKCS11Session.create_keypair(new_key_label))

        i_name_h, i_key_h, serial, _ = certificate_ocsp_data(TEST_CERT)

        # Revoked
        data = asyncio.run(request([(i_name_h, i_key_h, serial)]))
        test_request = asn1_ocsp.OCSPRequest.load(data)
        self.assertTrue(isinstance(test_request, asn1_ocsp.OCSPRequest))
        data = asyncio.run(response(new_key_label, name_dict, self._revoked_response(test_request), 0))
        test_response = asn1_ocsp.OCSPResponse.load(data)
        self.assertTrue(isinstance(test_response, asn1_ocsp.OCSPResponse))
        self.assertTrue(
            test_response["response_bytes"]["response"].native["tbs_response_data"]["responses"][0]["cert_status"][
                "revocation_reason"
            ]
            == "cessation_of_operation"
        )

        # Unknown
        data = asyncio.run(response(new_key_label, name_dict, self._unknown_response(test_request), 0))
        test_response = asn1_ocsp.OCSPResponse.load(data)
        self.assertTrue(isinstance(test_response, asn1_ocsp.OCSPResponse))
        self.assertTrue(
            test_response["response_bytes"]["response"].native["tbs_response_data"]["responses"][0]["cert_status"]
            == "unknown"
        )

        # Mixed
        data = asyncio.run(
            request([(i_name_h, i_key_h, serial), (i_name_h, i_key_h, serial), (i_name_h, i_key_h, serial)])
        )
        test_request = asn1_ocsp.OCSPRequest.load(data)
        self.assertTrue(isinstance(test_request, asn1_ocsp.OCSPRequest))
        data = asyncio.run(response(new_key_label, name_dict, self._mixed_response(test_request), 0))
        test_response = asn1_ocsp.OCSPResponse.load(data)
        self.assertTrue(isinstance(test_response, asn1_ocsp.OCSPResponse))
        self.assertTrue(test_response["response_bytes"].native is not None)
        self.assertTrue(
            test_response["response_bytes"]["response"].native["tbs_response_data"]["responses"][0]["cert_status"]
            == "good"
        )
        self.assertTrue(
            test_response["response_bytes"]["response"].native["tbs_response_data"]["responses"][1]["cert_status"][
                "revocation_reason"
            ]
            == "cessation_of_operation"
        )
        self.assertTrue(
            test_response["response_bytes"]["response"].native["tbs_response_data"]["responses"][2]["cert_status"]
            == "unknown"
        )

    def test_ocsp_response_fail(self) -> None:
        """
        Create an unsuccessful ocsp response.
        """

        new_key_label = hex(int.from_bytes(os.urandom(20), "big") >> 1)
        asyncio.run(PKCS11Session.create_keypair(new_key_label))

        i_name_h, i_key_h, serial, _ = certificate_ocsp_data(TEST_CERT)
        data = asyncio.run(request([(i_name_h, i_key_h, serial)]))
        test_request = asn1_ocsp.OCSPRequest.load(data)

        # Test status codes
        data = asyncio.run(response(new_key_label, name_dict, self._good_response(test_request), 1))
        test_response = asn1_ocsp.OCSPResponse.load(data)
        self.assertTrue(isinstance(test_response, asn1_ocsp.OCSPResponse))
        self.assertTrue(test_response["response_bytes"].native is None)
        data = asyncio.run(response(new_key_label, name_dict, self._good_response(test_request), 2))
        test_response = asn1_ocsp.OCSPResponse.load(data)
        self.assertTrue(test_response["response_bytes"].native is None)
        data = asyncio.run(response(new_key_label, name_dict, self._good_response(test_request), 3))
        test_response = asn1_ocsp.OCSPResponse.load(data)
        self.assertTrue(test_response["response_bytes"].native is None)
        data = asyncio.run(response(new_key_label, name_dict, self._good_response(test_request), 5))
        test_response = asn1_ocsp.OCSPResponse.load(data)
        self.assertTrue(test_response["response_bytes"].native is None)
        data = asyncio.run(response(new_key_label, name_dict, self._good_response(test_request), 6))
        test_response = asn1_ocsp.OCSPResponse.load(data)
        self.assertTrue(test_response["response_bytes"].native is None)

        with self.assertRaises(ValueError):
            data = asyncio.run(response(new_key_label, name_dict, self._good_response(test_request), 4))
            test_response = asn1_ocsp.OCSPResponse.load(data)
        with self.assertRaises(ValueError):
            data = asyncio.run(response(new_key_label, name_dict, self._good_response(test_request), 99))
            test_response = asn1_ocsp.OCSPResponse.load(data)

    def test_ocsp_response_extensions(self) -> None:
        """
        Create an ocsp response with extra extensions.
        """

        new_key_label = hex(int.from_bytes(os.urandom(20), "big") >> 1)
        asyncio.run(PKCS11Session.create_keypair(new_key_label))

        i_name_h, i_key_h, serial, _ = certificate_ocsp_data(TEST_CERT)
        data = asyncio.run(request([(i_name_h, i_key_h, serial)]))
        test_request = asn1_ocsp.OCSPRequest.load(data)

        # Test too big nonce
        nonce_ext = asn1_ocsp.ResponseDataExtension()
        nonce_ext["extn_id"] = asn1_ocsp.ResponseDataExtensionId("1.3.6.1.5.5.7.48.1.2")
        nonce_ext["extn_value"] = token_bytes(33)
        extra_extensions = asn1_ocsp.ResponseDataExtensions()
        extra_extensions.append(nonce_ext)
        with self.assertRaises(ValueError):
            data = asyncio.run(
                response(new_key_label, name_dict, self._revoked_response(test_request), 0, extra_extensions)
            )
        nonce_val = token_bytes(32)
        nonce_ext["extn_value"] = nonce_val
        extra_extensions = asn1_ocsp.ResponseDataExtensions()
        extra_extensions.append(nonce_ext)
        extra_extensions.append(nonce_ext)
        with self.assertRaises(DuplicateExtensionException):
            data = asyncio.run(
                response(new_key_label, name_dict, self._revoked_response(test_request), 0, extra_extensions)
            )

        # Test both ok
        extra_extensions = asn1_ocsp.ResponseDataExtensions()
        extended_revoke_ext = asn1_ocsp.ResponseDataExtension()
        extended_revoke_ext["extn_id"] = asn1_ocsp.ResponseDataExtensionId("1.3.6.1.5.5.7.48.1.9")
        extended_revoke_ext["extn_value"] = None
        extra_extensions.append(nonce_ext)
        extra_extensions.append(extended_revoke_ext)
        data = asyncio.run(
            response(new_key_label, name_dict, self._revoked_response(test_request), 0, extra_extensions)
        )
        test_response = asn1_ocsp.OCSPResponse.load(data)
        self.assertTrue(isinstance(test_response, asn1_ocsp.OCSPResponse))
        self.assertTrue(
            test_response["response_bytes"]["response"].native["tbs_response_data"]["response_extensions"][0][
                "extn_value"
            ]
            == nonce_val
        )
        self.assertTrue(
            test_response["response_bytes"]["response"].native["tbs_response_data"]["response_extensions"][1]["extn_id"]
            == "extended_revoke"
        )

    def test_request_nonce(self) -> None:
        """
        Test request nonce function.
        """

        nonce_ext = TBSRequestExtension()
        nonce_ext["extn_id"] = TBSRequestExtensionId("1.3.6.1.5.5.7.48.1.2")
        nonce_val = token_bytes(32)
        nonce_ext["extn_value"] = nonce_val
        extra_extensions = TBSRequestExtensions()
        extra_extensions.append(nonce_ext)

        request_certs_data = [
            (
                b"R\x94\xca?\xac`\xf7i\x819\x14\x94\xa7\x085H\x84\xb4&\xcc",
                b"\xad\xd0\x88DW\x96'\xce\xf4\"\xc6\xc77W\xc9\xefi\xa4[\x8b",
                440320505043419981128735462508870123525487964711,
            )
        ]
        ocsp_request_bytes = asyncio.run(request(request_certs_data, extra_extensions=extra_extensions))
        nonce = request_nonce(ocsp_request_bytes)
        self.assertTrue(nonce_val == nonce)

        ocsp_request_bytes = asyncio.run(request(request_certs_data))
        nonce = request_nonce(ocsp_request_bytes)
        self.assertTrue(nonce is None)
        
    def test_certificate_ocsp_data(self) -> None:
        """
        Test request certificate_ocsp_data function.
        """

        non_ocsp_cert = """-----BEGIN CERTIFICATE-----
MIIFIjCCBAqgAwIBAgIUQihqqBASG58siv7si/dOKCr4yH8wDQYJKoZIhvcNAQEL
BQAwgZwxCzAJBgNVBAYTAlNFMRIwEAYDVQQIDAlTdG9ja2hvbG0xEjAQBgNVBAcM
CVN0b2NraG9sbTEOMAwGA1UECgwFU1VORVQxHTAbBgNVBAsMFFNVTkVUIEluZnJh
c3RydWN0dXJlMRkwFwYDVQQDDBBjYS10ZXN0LnN1bmV0LnNlMRswGQYJKoZIhvcN
AQkBFgxzb2NAc3VuZXQuc2UwHhcNMjIwOTI4MTExOTU1WhcNMjUwOTI3MTEyMTU1
WjCBqzELMAkGA1UEBhMCU0UxEjAQBgNVBAgMCVN0b2NraG9sbTEXMBUGA1UEBwwO
U3RvY2tob2xtX3Rlc3QxDjAMBgNVBAoMBVNVTkVUMR0wGwYDVQQLDBRTVU5FVCBJ
bmZyYXN0cnVjdHVyZTEjMCEGA1UEAwwaY2EtdGVzdC1jcmVhdGUtMjAuc3VuZXQu
c2UxGzAZBgkqhkiG9w0BCQEWDHNvY0BzdW5ldC5zZTCCASIwDQYJKoZIhvcNAQEB
BQADggEPADCCAQoCggEBAK7oiAE2i2/ggmRfkccHxeeA3OzN+GRZuKV0Gh/f+WE7
+1uq1Wm0wuovnpdDmQpsfXnu6D4zbzy9jysnS+7EcLQcEhSfq6ixBayj0yPjHz/i
sSk1lbFh94o/5TZE+o/gcqgsVTbjTGqIOQ/EfD+E3xMF8ZnNyvJjslu8SMuPbj6B
WRBBTKB7baGLoaOlxJTZ0c97oVGdSH46x782sKooyQInO81gNwWcBUTHBjG216wP
vMVtW9gxplm2dVw/l2nrz6g7Hp6xyY12ESWOdaRT73RdxmnQETe2wLHA0u7qcfmS
c8MUA6qeXcwwHzcoF8onUbV0UVJXhRPoLQ6R45q5C4ECAwEAAaOCAUkwggFFMA4G
A1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MHUGCCsGAQUFBwEBBGkwZzBl
BggrBgEFBQcwAoZZaHR0cDovL2xvY2FsaG9zdDo4MDAwL2NhLzMwMmVkNWE2NTQz
NDliYWYxNWU0MDAzMDhlODlmMmE3MzExODJhODJmMzgxODJjYzgxZWQyMzE3ZTkx
ODYwM2QwawYDVR0fBGQwYjBgoF6gXIZaaHR0cDovL2xvY2FsaG9zdDo4MDAwL2Ny
bC8zMDJlZDVhNjU0MzQ5YmFmMTVlNDAwMzA4ZTg5ZjJhNzMxMTgyYTgyZjM4MTgy
Y2M4MWVkMjMxN2U5MTg2MDNkMB0GA1UdDgQWBBQjhvSAPiHHO9ypvQW/5euSCcsx
dDAfBgNVHSMEGDAWgBRx52znW9b1xo5nW/lL+SukqsuVnzANBgkqhkiG9w0BAQsF
AAOCAQEAK1xFV5bpCulzA+a2g8pWSidaWW4stTZOvUrrpqMDXkicvsRjz7z7VrLG
3/B2ktD6vbq2PbOV92HmRSQeLfeOX9Mt4fYDYgvMNTopPA03WxIUngNOTSq4En97
ImB+yAP/aDnWPEIHFB+OtzKG4keGFEz4MLIwtaRALYfLstq6QWHShueSnX2HpKvU
S5G5p16d5rgraJAzUYzG7tn6jZxFSp2uAiOJDmegf6ss9fN+AOVN2GVEuQCRbICi
qYr0IdrSItJfk89KDm/ZB74C2xn1XUdvsxsM8HoKOotusIdFvpvrj/DCiKoqv7id
cvFnVe0ady+2DhPNGwbUXz1ExrpNcA==
-----END CERTIFICATE-----
"""

        with self.assertRaises(DuplicateExtensionException):
            i_n_h, i_k_h, serial, ocsp_url = certificate_ocsp_data(non_ocsp_cert)

        with self.assertRaises(DuplicateExtensionException):
            i_n_h, i_k_h, serial, ocsp_url = certificate_ocsp_data(non_aki_cert)
        
        
# def certificate_ocsp_data(pem: str) -> Tuple[bytes, bytes, int, str]:
#     """Get OCSP request data from a certificate.                                                                                                                                                                   
#     Returns a tuple of:                                                                                                                                                                                            
#     sha1 hash of issuer name                                                                                                                                                                                       
#     sha1 hash of issuer public key                                                                                                                                                                                 
#     serial number                                                                                                                                                                                                  
#     ocsp url                                                                                                                                                                                                       
                                                                                                                                                                                                                   
#     The certificate MUST have the AKI extension (2.5.29.35)                                                                                                                                                        
#     and the AIA extension with ocsp method (1.3.6.1.5.5.7.1.1).                                                                                                                                                    
      