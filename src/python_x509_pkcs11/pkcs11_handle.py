"""Module which handles a PKCS11 session and its exposed methods

# Better to keep a pkcs11 session reference all the time
# and then when it fails to open a new session for performance
# See PKCS11Session()._healthy_session()

The classes PKCS11Session and RemotePKCS11 exposes the functions:
- import_keypair()
- create_keypair()
- key_labels()
- sign()
- verify()
- delete_keypair()
- public_key_data()
- import_certificate()
- export_certificate()
- delete_certificate()
- get_session()
"""

import base64
import logging
import os
import time
from asyncio import get_event_loop, sleep
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from email import header
from hashlib import sha256, sha384, sha512
from socket import timeout
from threading import Lock, Thread
from typing import Any, AsyncIterator, Dict, Optional, Tuple, Type, Union

logger = logging.getLogger(__name__)

import aiohttp
from asn1crypto import pem as asn1_pem
from asn1crypto import x509 as asn1_x509
from asn1crypto.algos import SignedDigestAlgorithmId
from asn1crypto.keys import PublicKeyAlgorithm, PublicKeyAlgorithmId, PublicKeyInfo, RSAPublicKey
from pkcs11 import Attribute, Key, KeyType, Mechanism, ObjectClass, Session, Token, lib
from pkcs11.exceptions import GeneralError, MultipleObjectsReturned, NoSuchKey, SignatureInvalid
from pkcs11.util.ec import (
    decode_ec_private_key,
    decode_ec_public_key,
    encode_ec_public_key,
    encode_named_curve_parameters,
)
from pkcs11.util.rsa import decode_rsa_private_key, decode_rsa_public_key, encode_rsa_public_key
from pkcs11.util.x509 import decode_x509_certificate

from .crypto import (
    convert_asn1_ec_signature,
    convert_rs_ec_signature,
    decode_eddsa_private_key,
    decode_eddsa_public_key,
    encode_eddsa_public_key,
)
from .error import PKCS11UnknownErrorException
from .lib import DEBUG, DEFAULT_KEY_TYPE, KEY_TYPE_VALUES, KEYTYPES, get_keytypes_enum

TIMEOUT = 10  # Seconds
pool = ThreadPoolExecutor()


@asynccontextmanager
async def async_lock(lock: Lock) -> AsyncIterator[None]:
    """Used as a simple async lock"""

    loop = get_event_loop()
    await loop.run_in_executor(pool, lock.acquire)

    try:
        yield  # the lock is held
    finally:
        lock.release()


class PKCS11Session:
    """Persistent PKCS11 session wrapper."""

    # We want a single instance of this class.
    # Because pkcs11 allows one connection to the pkcs11 device.
    _self = None

    _session_status: int = 9
    _token: Token
    _lib: lib

    def __new__(cls, *args: Any, **kwargs: Any) -> "PKCS11Session":
        if cls._self is None:
            cls._self = super(PKCS11Session, cls).__new__(cls, *args, **kwargs)
        return cls._self

    def __init__(
        self,
        base_url: Optional[str] = None,
        http_data: Optional[Dict[str, str]] = None,
        http_headers: Optional[Dict[str, str]] = None,
        pkcs11_module: Optional[str] = None,
        pkcs11_token: Optional[str] = None,
        pkcs11_pin: Optional[str] = None,
        pkcs11_recreate_session: Optional[bool] = False,
    ) -> None:
        self.lock = Lock()

        self.base_url = base_url
        self.http_data = http_data
        self.http_headers = http_headers
        self.support_recreate_session = False

        if "PKCS11_BASE_URL" in os.environ:
            self.base_url = os.environ["PKCS11_BASE_URL"]
            return

        # TODO: Update the recreate session code in a cleaner way.

        if "PKCS11_TOKEN_SUPPORT_RECREATE_SESSION" in os.environ:
            if (
                os.environ["PKCS11_TOKEN_SUPPORT_RECREATE_SESSION"] == "true"
                or os.environ["PKCS11_TOKEN_SUPPORT_RECREATE_SESSION"] == "TRUE"
            ):
                self.support_recreate_session = True

        # From argument
        if pkcs11_recreate_session:
            self.support_recreate_session = True

        try:
            # First let us get all 3 PKCS11 variables
            if not pkcs11_module:
                pkcs11_module = os.environ["PKCS11_MODULE"]
            if not pkcs11_token:
                pkcs11_token = os.environ["PKCS11_TOKEN"]
            if not pkcs11_pin:
                pkcs11_pin = os.environ["PKCS11_PIN"]

            self._lib = lib(pkcs11_module)
            self._lib.reinitialize()

            # Open the PKCS11 session
            self._token = self._lib.get_token(token_label=pkcs11_token)

            # user_pin need to be a string, not bytes
            self.session = self._token.open(rw=True, user_pin=pkcs11_pin)
            logger.debug("Created new pkcs11 session")

            # Test get a public key from the PKCS11 device
            _ = self.session.get_key(
                key_type=KeyType.RSA,
                object_class=ObjectClass.PUBLIC_KEY,
                label="test_pkcs11_device_do_not_use",
            )

        except NoSuchKey:
            try:
                _, _ = self.session.generate_keypair(
                    KeyType.RSA, 512, label="test_pkcs11_device_do_not_use", store=True
                )
            except GeneralError:
                pass

        except GeneralError as exc:
            logger.error("Failed to open PKCS11 session")
            logger.error(exc)

    def _get_pub_key(self, key_label: str, key_type: KEYTYPES = DEFAULT_KEY_TYPE) -> Key:
        return self.session.get_key(
            key_type=KEY_TYPE_VALUES[key_type],
            object_class=ObjectClass.PUBLIC_KEY,
            label=key_label,
        )

    def _public_key_data(self, key_pub: Key, key_type: KEYTYPES) -> Tuple[str, bytes]:
        if key_type in [KEYTYPES.RSA2048, KEYTYPES.RSA4096]:
            # Create the PublicKeyInfo object
            rsa_pub = RSAPublicKey.load(encode_rsa_public_key(key_pub))

            pki = PublicKeyInfo()
            pka = PublicKeyAlgorithm()
            pka["algorithm"] = PublicKeyAlgorithmId("rsa")
            pki["algorithm"] = pka
            pki["public_key"] = rsa_pub

        elif key_type in [KEYTYPES.ED25519, KEYTYPES.ED448]:
            pki = PublicKeyInfo.load(encode_eddsa_public_key(key_pub))

        elif key_type in [KEYTYPES.SECP256r1, KEYTYPES.SECP384r1, KEYTYPES.SECP521r1]:
            pki = PublicKeyInfo.load(encode_ec_public_key(key_pub))

        key_pub_pem: bytes = asn1_pem.armor("PUBLIC KEY", pki.dump())
        return key_pub_pem.decode("utf-8"), pki.sha1

    def _open_session(self, force: Optional[bool] = None, simulate_pkcs11_timeout: Optional[bool] = None) -> None:
        if simulate_pkcs11_timeout:
            time.sleep(TIMEOUT + 1)

        if "PKCS11_MODULE" not in os.environ:
            logger.error("ERROR: PKCS11_MODULE was not an env variable")
        if "PKCS11_TOKEN" not in os.environ:
            logger.error("ERROR: PKCS11_TOKEN was not an env variable")
        if "PKCS11_PIN" not in os.environ:
            logger.error("ERROR: PKCS11_PIN was not an env variable")

        self._session_status = 9
        try:
            # if force or self.session is None:
            if force or self.session is None:
                # Reload the PKCS11 lib
                self._lib = lib(os.environ["PKCS11_MODULE"])
                self._lib.reinitialize()

                # Open the PKCS11 session
                self._token = self._lib.get_token(token_label=os.environ["PKCS11_TOKEN"])
                # user_pin need to be a string, not bytes
                self.session = self._token.open(rw=True, user_pin=os.environ["PKCS11_PIN"])
                logger.debug("created new pkcs11 session")

            # Test get a public key from the PKCS11 device
            _ = self.session.get_key(
                key_type=KeyType.RSA,
                object_class=ObjectClass.PUBLIC_KEY,
                label="test_pkcs11_device_do_not_use",
            )
            self._session_status = 0

        except NoSuchKey:
            try:
                _, _ = self.session.generate_keypair(
                    KeyType.RSA, 512, label="test_pkcs11_device_do_not_use", store=True
                )
                self._session_status = 0
            except GeneralError:
                pass

        except GeneralError as exc:
            logger.error("Failed to open PKCS11 session")
            logger.error(exc)

    async def healthy_session(self, simulate_pkcs11_timeout: Optional[bool] = None) -> None:
        """Run the PKCS11 test command in a thread to easy handle PKCS11 timeouts."""

        if not self.support_recreate_session:
            return
        thread = Thread(target=self._open_session, args=(False, simulate_pkcs11_timeout))
        thread.start()
        await sleep(0)
        thread.join(timeout=TIMEOUT)

        if thread.is_alive() or self._session_status != 0:
            thread2 = Thread(target=self._open_session, args=(True, simulate_pkcs11_timeout))
            thread2.start()
            # yield to other coroutines while we wait for thread2 to join
            await sleep(0)
            thread2.join(timeout=TIMEOUT)

            if thread2.is_alive() or self._session_status != 0:
                raise PKCS11UnknownErrorException("ERROR: Could not get a healthy PKCS11 connection in time")

    async def get_session(self) -> Session:
        """Return the PKCS11 session."""

        async with async_lock(self.lock):
            # Ensure we get a healthy pkcs11 session
            await self.healthy_session()
            return self.session

    async def import_certificate(self, cert_pem: str, cert_label: str) -> None:
        """Import a certificate into the PKCS11 device with this label.

        Parameters:
        cert_pem (str): Certificate in PEM form.
        cert_label (str): Certificate label in the PKCS11 device.

        Returns:
        None
        """

        if self.base_url is not None:
            http_request_data: Dict[str, str] = {}

            if self.http_data is not None:
                http_request_data.update(self.http_data)

            http_request_data["cert_pem"] = cert_pem
            http_request_data["cert_label"] = cert_label

            async with aiohttp.ClientSession(headers=self.http_headers) as session:
                async with session.post(
                    url=f"{self.base_url}/import_certificate",
                    json=http_request_data,
                    headers=self.http_headers,
                    timeout=10,
                ) as response:
                    response.raise_for_status()
                    # json_body = await response.json()
                    return

        async with async_lock(self.lock):
            # Ensure we get a healthy pkcs11 session
            await self.healthy_session()

            for cert in self.session.get_objects(
                {
                    Attribute.CLASS: ObjectClass.CERTIFICATE,
                    Attribute.LABEL: cert_label,
                }
            ):
                raise ValueError("Certificate with that label already exists in the PKCS11 device")

            data = cert_pem.encode("utf-8")
            if asn1_pem.detect(data):
                _, _, data = asn1_pem.unarmor(data)

            cert = decode_x509_certificate(data)
            cert[Attribute.TOKEN] = True
            cert[Attribute.LABEL] = cert_label
            self.session.create_object(cert)

    async def export_certificate(self, cert_label: str) -> str:
        """Export a certificate from the PKCS11 device with this label.
        Returns the PEM encoded cert.

        Parameters:
        cert_label (str): Certificate label in the PKCS11 device.

        Returns:
        str
        """

        if self.base_url is not None:
            http_request_data: Dict[str, str] = {}

            if self.http_data is not None:
                http_request_data.update(self.http_data)

            http_request_data["cert_label"] = cert_label

            async with aiohttp.ClientSession(headers=self.http_headers) as session:
                async with session.post(
                    url=f"{self.base_url}/export_certificate",
                    json=http_request_data,
                    headers=self.http_headers,
                    timeout=10,
                ) as response:
                    response.raise_for_status()
                    json_body = await response.json()
                    ret = json_body["certificate"]  # handle errors
                    if isinstance(ret, str):
                        return ret
                    raise ValueError("Problem with cert")

        async with async_lock(self.lock):
            # Ensure we get a healthy pkcs11 session
            await self.healthy_session()

            for cert in self.session.get_objects(
                {
                    Attribute.CLASS: ObjectClass.CERTIFICATE,
                    Attribute.LABEL: cert_label,
                }
            ):
                der_bytes = cert[Attribute.VALUE]

                # Load a certificate object from the DER-encoded value
                cert_asn1 = asn1_x509.Certificate.load(der_bytes)

                # Write out a PEM encoded value
                ret_data: bytes = asn1_pem.armor("CERTIFICATE", cert_asn1.dump())
                return ret_data.decode("utf-8")

            raise ValueError("No such certificate in the PKCS11 device")

    async def delete_certificate(self, cert_label: str) -> None:
        """Delete a certificate from the PKCS11 device with this label.

        Parameters:
        cert_label (str): Certificate label in the PKCS11 device.
        """

        if self.base_url is not None:
            http_request_data: Dict[str, str] = {}

            if self.http_data is not None:
                http_request_data.update(self.http_data)

            http_request_data["cert_label"] = cert_label

            async with aiohttp.ClientSession(headers=self.http_headers) as session:
                async with session.post(
                    url=f"{self.base_url}/delete_certificate",
                    json=http_request_data,
                    headers=self.http_headers,
                    timeout=10,
                ) as response:
                    response.raise_for_status()
                    return
                    # json_body = await response.json()
                    # return json_body["cert_label"] # handle errors

        async with async_lock(self.lock):
            # Ensure we get a healthy pkcs11 session
            await self.healthy_session()

            for cert in self.session.get_objects(
                {
                    Attribute.CLASS: ObjectClass.CERTIFICATE,
                    Attribute.LABEL: cert_label,
                }
            ):
                cert.destroy()

    async def import_keypair(
        self, public_key: bytes, private_key: bytes, key_label: str, key_type: Union[str, KEYTYPES]
    ) -> None:
        """Import a DER encoded keypair into the PKCS11 device with this label.
        If the label already exists in the PKCS11 device then raise pkcs11.MultipleObjectsReturned.

        Generating public_key and private_key can be done with:
        openssl genpkey -algorithm ed25519 -out private.pem
        openssl pkey -in private.pem -outform DER -out private.key
        openssl pkey -in private.pem -pubout -out public.pem
        openssl pkey -in private.pem -pubout -outform DER -out public.key

        Parameters:
        public_key (bytes): Public RSA key in DER form.
        private_key (bytes): Private RSA key in DER form.
        key_label (str): Keypair label.
        key_type Union[str, KEYTYPES]: Key type in string or enum.

        Returns:
        None
        """

        if isinstance(key_type, str):
            key_type = get_keytypes_enum(key_type)

        if self.base_url is not None:
            http_request_data: Dict[str, str] = {}

            if self.http_data is not None:
                http_request_data.update(self.http_data)

            http_request_data["public_key_b64"] = base64.b64encode(public_key).decode("utf-8")
            http_request_data["private_key_b64"] = base64.b64encode(private_key).decode("utf-8")
            http_request_data["key_label"] = key_label
            http_request_data["key_type"] = key_type.value

            async with aiohttp.ClientSession(headers=self.http_headers) as session:
                async with session.post(
                    url=f"{self.base_url}/import_keypair", json=http_request_data, headers=self.http_headers, timeout=10
                ) as response:
                    response.raise_for_status()
                    return
                    # json_body = await response.json()

        async with async_lock(self.lock):
            # Ensure we get a healthy pkcs11 session
            await self.healthy_session()

            try:
                key_pub = self._get_pub_key(key_label, key_type)
                raise MultipleObjectsReturned
            except NoSuchKey:
                pass

            if key_type in [KEYTYPES.RSA2048, KEYTYPES.RSA4096]:
                key_pub = decode_rsa_public_key(public_key)
                key_priv = decode_rsa_private_key(private_key)

            elif key_type in [KEYTYPES.ED25519, KEYTYPES.ED448]:
                key_pub = decode_eddsa_public_key(public_key)
                key_priv = decode_eddsa_private_key(private_key)

            elif key_type in [KEYTYPES.SECP256r1, KEYTYPES.SECP384r1, KEYTYPES.SECP521r1]:
                key_pub = decode_ec_public_key(public_key)
                key_priv = decode_ec_private_key(private_key)

            key_pub[Attribute.TOKEN] = True
            key_pub[Attribute.LABEL] = key_label
            key_priv[Attribute.TOKEN] = True
            key_priv[Attribute.LABEL] = key_label

            self.session.create_object(key_pub)
            self.session.create_object(key_priv)

    async def create_keypair(
        self, key_label: str, key_type: Union[str, KEYTYPES] = DEFAULT_KEY_TYPE
    ) -> Tuple[str, bytes]:
        """Create an RSA keypair in the PKCS11 device with this label.
        If the label already exists in the PKCS11 device then raise pkcs11.MultipleObjectsReturned.
        Returns the data for the x509 'Subject Public Key Info'
        and x509 extension 'Subject Key Identifier' valid for this keypair.

        ed25519 is default key_type.

        Parameters:
        key_label (str): Keypair label.
        key_type str: Key type, defaults to "ed25519".


        Returns:
        Tuple[str, bytes]
        """

        if isinstance(key_type, str):
            key_type = get_keytypes_enum(key_type)

        if self.base_url is not None:
            http_request_data: Dict[str, str] = {}

            if self.http_data is not None:
                http_request_data.update(self.http_data)

            http_request_data["key_label"] = key_label
            http_request_data["key_type"] = key_type.value

            async with aiohttp.ClientSession(headers=self.http_headers) as session:
                async with session.post(
                    url=f"{self.base_url}/create_keypair", json=http_request_data, headers=self.http_headers, timeout=10
                ) as response:
                    response.raise_for_status()
                    json_body = await response.json()
                    spi = json_body["subjectPublicKeyInfo"]  # handle errors
                    ski = json_body["subjectKeyIdentifier_b64"]

                    if isinstance(spi, str) and isinstance(ski, str):  # handle errors
                        return spi, base64.b64decode(ski)
                    raise ValueError("Problem with create keypair")

        async with async_lock(self.lock):
            # Ensure we get a healthy pkcs11 session
            await self.healthy_session()

            # Try to get the key, if not exist then create it
            try:
                key_pub = self._get_pub_key(key_label, key_type)
                raise MultipleObjectsReturned
            except NoSuchKey:
                # Generate the rsa keypair
                if key_type in [KEYTYPES.RSA2048, KEYTYPES.RSA4096]:
                    key_pub, _ = self.session.generate_keypair(
                        KeyType.RSA, int(key_type.value.split("_")[1]), store=True, label=key_label
                    )

                elif key_type in [KEYTYPES.ED25519, KEYTYPES.ED448]:
                    parameters = self.session.create_domain_parameters(
                        KeyType.EC_EDWARDS,
                        {
                            Attribute.EC_PARAMS: encode_named_curve_parameters(
                                SignedDigestAlgorithmId(key_type.value).dotted
                            ),
                        },
                        local=True,
                    )
                    key_pub, _ = parameters.generate_keypair(
                        mechanism=Mechanism.EC_EDWARDS_KEY_PAIR_GEN, store=True, label=key_label
                    )

                elif key_type in [KEYTYPES.SECP256r1, KEYTYPES.SECP384r1, KEYTYPES.SECP521r1]:
                    parameters = self.session.create_domain_parameters(
                        KeyType.EC,
                        {Attribute.EC_PARAMS: encode_named_curve_parameters(key_type.value)},
                        local=True,
                    )
                    key_pub, _ = parameters.generate_keypair(
                        store=True,
                        label=key_label,
                    )

            return self._public_key_data(key_pub, key_type)

    async def key_labels(self) -> Dict[str, str]:
        """Return a dict of key labels as keys and key type as values in the PKCS11 device.

        Returns:
        Dict[str, str]
        """

        if self.base_url is not None:
            async with aiohttp.ClientSession(headers=self.http_headers) as session:
                async with session.post(
                    url=f"{self.base_url}/key_labels", json=self.http_data, headers=self.http_headers, timeout=10
                ) as response:
                    response.raise_for_status()
                    json_body = await response.json()
                    ret = json_body["key_labels"]  # handle errors

                    if isinstance(ret, dict):  # handle only [str, str]
                        return ret
                    raise ValueError("Problem with key labels")

        async with async_lock(self.lock):
            # Ensure we get a healthy pkcs11 session
            await self.healthy_session()

            key_labels: Dict[str, str] = {}

            # For rsa
            for obj in self.session.get_objects(
                {Attribute.CLASS: ObjectClass.PUBLIC_KEY, Attribute.KEY_TYPE: KEY_TYPE_VALUES[KEYTYPES.RSA2048]}
            ):
                if obj.key_length == 2048:
                    key_labels[obj.label] = "rsa_2048"
                elif obj.key_length == 4096:
                    key_labels[obj.label] = "rsa_4096"
                else:
                    key_labels[obj.label] = "rsa_512"

            # For ed25519
            for obj in self.session.get_objects(
                {
                    Attribute.CLASS: ObjectClass.PUBLIC_KEY,
                    Attribute.KEY_TYPE: KEY_TYPE_VALUES[KEYTYPES.ED25519],
                    Attribute.EC_PARAMS: encode_named_curve_parameters("1.3.101.112"),
                }
            ):
                key_labels[obj.label] = "ed25519"

            # For ed448
            for obj in self.session.get_objects(
                {
                    Attribute.CLASS: ObjectClass.PUBLIC_KEY,
                    Attribute.KEY_TYPE: KEY_TYPE_VALUES[KEYTYPES.ED448],
                    Attribute.EC_PARAMS: encode_named_curve_parameters("1.3.101.113"),
                }
            ):
                key_labels[obj.label] = "ed448"

            # for secp256r1, secp384r1, secp521r1
            for curve in [KEYTYPES.SECP256r1, KEYTYPES.SECP384r1, KEYTYPES.SECP521r1]:
                for obj in self.session.get_objects(
                    {
                        Attribute.CLASS: ObjectClass.PUBLIC_KEY,
                        Attribute.KEY_TYPE: KEY_TYPE_VALUES[curve],
                        Attribute.EC_PARAMS: encode_named_curve_parameters(curve.value),
                    }
                ):
                    key_labels[obj.label] = curve.value

            return key_labels

    async def _sign(  # pylint: disable-msg=too-many-arguments
        self, key_label: str, data: bytes, verify_signature: Optional[bool], mechanism: Mechanism, key_type: KEYTYPES
    ) -> bytes:

        async with async_lock(self.lock):
            # Ensure we get a healthy pkcs11 session
            await self.healthy_session()

            # Get private key to sign the data with
            key_priv = self.session.get_key(
                key_type=KEY_TYPE_VALUES[key_type],
                object_class=ObjectClass.PRIVATE_KEY,
                label=key_label,
            )
            if verify_signature:
                key_pub = self._get_pub_key(key_label, key_type)

            # Sign the data
            signature = key_priv.sign(data, mechanism=mechanism)

            if not isinstance(signature, bytes):
                raise SignatureInvalid

            if verify_signature:
                if not key_pub.verify(data, signature, mechanism=mechanism):
                    raise SignatureInvalid

            return signature

    async def sign(
        self,
        key_label: str,
        data: bytes,
        verify_signature: Optional[bool] = None,
        key_type: Union[str, KEYTYPES] = DEFAULT_KEY_TYPE,
    ) -> bytes:
        """Sign the data: bytes using the private key
        with the label in the PKCS11 device.

        Returns the signed data: bytes for the x509 extension and
        'Authority Key Identifier' valid for this keypair.

        Parameters:
        key_label (str): Keypair label.
        data (bytes): Bytes to be signed.
        verify_signature (Union[bool, None] = None):
        If we should verify the signature. PKCS11 operations can be expensive, default None (False)
        key_type Union[str, KEYTYPES]: Key type, defaults to "ed25519".

        Returns:
        bytes
        """

        if isinstance(key_type, str):
            key_type = get_keytypes_enum(key_type)

        if self.base_url is not None:
            http_request_data: Dict[str, Union[str, bool]] = {}

            if self.http_data is not None:
                http_request_data.update(self.http_data)

            http_request_data["data_b64"] = base64.b64encode(data).decode("utf-8")
            http_request_data["key_label"] = key_label
            http_request_data["key_type"] = key_type.value

            if verify_signature is None or not verify_signature:
                http_request_data["verify_signature"] = False
            else:
                http_request_data["verify_signature"] = True

            async with aiohttp.ClientSession(headers=self.http_headers) as session:
                async with session.post(
                    url=f"{self.base_url}/sign", json=http_request_data, headers=self.http_headers, timeout=10
                ) as response:
                    response.raise_for_status()
                    json_body = await response.json()
                    ret = json_body["signature_b64"]  # handle errors

                    if isinstance(ret, str):
                        return base64.b64decode(ret)
                    raise ValueError("Problem with signature")

        if key_type in [KEYTYPES.ED25519, KEYTYPES.ED448]:
            mech = Mechanism.EDDSA

        elif key_type in [KEYTYPES.SECP256r1, KEYTYPES.SECP384r1, KEYTYPES.SECP521r1]:
            mech = Mechanism.ECDSA

            # Set hash alg
            if key_type == KEYTYPES.SECP256r1:
                hash_obj = sha256()
            elif key_type == KEYTYPES.SECP384r1:
                hash_obj = sha384()
            else:
                hash_obj = sha512()

            hash_obj.update(data)
            data = hash_obj.digest()

        else:
            if key_type == KEYTYPES.RSA2048:
                mech = Mechanism.SHA256_RSA_PKCS
            else:
                mech = Mechanism.SHA512_RSA_PKCS

        signature = await self._sign(key_label, data, verify_signature, mech, key_type)

        # PKCS11 specific stuff for EC curves, sig is in R&S format, convert it to openssl format
        if key_type in [KEYTYPES.SECP256r1, KEYTYPES.SECP384r1, KEYTYPES.SECP521r1]:
            signature = convert_rs_ec_signature(signature, key_type.value)

        return signature

    async def verify(  # pylint: disable-msg=too-many-arguments
        self, key_label: str, data: bytes, signature: bytes, key_type: Union[str, KEYTYPES] = DEFAULT_KEY_TYPE
    ) -> bool:
        """Verify a signature with its data using the private key
        with the label in the PKCS11 device.

        Returns True if the signature is valid.

        Parameters:
        key_label (str): Keypair label.
        data (bytes): Bytes to be signed.
        signature (bytes): The signature.
        key_type str: Key type, defaults to "ed25519".

        Returns:
        bool
        """

        if isinstance(key_type, str):
            key_type = get_keytypes_enum(key_type)

        if self.base_url is not None:
            http_request_data: Dict[str, str] = {}

            if self.http_data is not None:
                http_request_data.update(self.http_data)

            http_request_data["data_b64"] = base64.b64encode(data).decode("utf-8")
            http_request_data["signature_b64"] = base64.b64encode(signature).decode("utf-8")
            http_request_data["key_label"] = key_label
            http_request_data["key_type"] = key_type.value

            async with aiohttp.ClientSession(headers=self.http_headers) as session:
                async with session.post(
                    url=f"{self.base_url}/verify", json=http_request_data, headers=self.http_headers, timeout=10
                ) as response:
                    response.raise_for_status()
                    json_body = await response.json()
                    ret = json_body["verified"]  # handle errors

                    if isinstance(ret, bool):
                        return ret
                    raise ValueError("Problem with verify")

        async with async_lock(self.lock):
            # Ensure we get a healthy pkcs11 session
            await self.healthy_session()

            # Get public key to sign the data with
            key_pub = self._get_pub_key(key_label, key_type)

            if key_type in [KEYTYPES.ED25519, KEYTYPES.ED448]:
                mech = Mechanism.EDDSA

            elif key_type in [KEYTYPES.SECP256r1, KEYTYPES.SECP384r1, KEYTYPES.SECP521r1]:
                mech = Mechanism.ECDSA

                # Set hash alg
                if key_type == KEYTYPES.SECP256r1:
                    hash_obj = sha256()
                elif key_type == KEYTYPES.SECP384r1:
                    hash_obj = sha384()
                else:
                    hash_obj = sha512()

                hash_obj.update(data)
                data = hash_obj.digest()

                try:
                    signature = convert_asn1_ec_signature(signature, key_type.value)
                except (IndexError, ValueError):
                    # Signature was not in ASN1 format, signature verification will probably fail.
                    pass

            else:  # rsa
                if key_type == KEYTYPES.RSA2048:
                    mech = Mechanism.SHA256_RSA_PKCS
                else:
                    mech = Mechanism.SHA512_RSA_PKCS

            if key_pub.verify(data, signature, mechanism=mech):
                return True
            return False

    async def delete_keypair(self, key_label: str, key_type: Union[str, KEYTYPES] = DEFAULT_KEY_TYPE) -> None:
        """Delete the keypair from the PKCS11 device.

        Parameters:
        key_label (str): Keypair label.
        key_type (Union[str, KEYTYPES] = None): Key type.

        Returns:
        None
        """

        if isinstance(key_type, str):
            key_type = get_keytypes_enum(key_type)

        if self.base_url is not None:
            http_request_data: Dict[str, str] = {}

            if self.http_data is not None:
                http_request_data.update(self.http_data)

            http_request_data["key_type"] = key_type.value
            http_request_data["key_label"] = key_label

            async with aiohttp.ClientSession(headers=self.http_headers) as session:
                async with session.post(
                    url=f"{self.base_url}/delete_keypair", json=http_request_data, headers=self.http_headers, timeout=10
                ) as response:
                    response.raise_for_status()
                    json_body = await response.json()
                    return
                    # ret = json_body["verified"]  # handle errors

        async with async_lock(self.lock):
            # Ensure we get a healthy pkcs11 session
            await self.healthy_session()

            try:
                self.session.get_key(
                    key_type=KEY_TYPE_VALUES[key_type],
                    object_class=ObjectClass.PUBLIC_KEY,
                    label=key_label,
                ).destroy()
            finally:
                self.session.get_key(
                    key_type=KEY_TYPE_VALUES[key_type],
                    object_class=ObjectClass.PRIVATE_KEY,
                    label=key_label,
                ).destroy()

    async def public_key_data(self, key_label: str, key_type: KEYTYPES = DEFAULT_KEY_TYPE) -> Tuple[str, bytes]:
        """Returns the public key in PEM form
        and 'Key Identifier' valid for this keypair.

        Parameters:
        key_label (str): Keypair label.
        key_type (Union[str, KEYTYPES]: Key type, default value is KEYTYPES.ED25519.

        Returns:
        Tuple[str, bytes]
        """

        if isinstance(key_type, str):
            key_type = get_keytypes_enum(key_type)

        if self.base_url is not None:
            http_request_data: Dict[str, str] = {}

            if self.http_data is not None:
                http_request_data.update(self.http_data)

            http_request_data["key_label"] = key_label
            http_request_data["key_type"] = key_type.value

            async with aiohttp.ClientSession(headers=self.http_headers) as session:
                async with session.post(
                    url=f"{self.base_url}/public_key_data",
                    json=http_request_data,
                    headers=self.http_headers,
                    timeout=10,
                ) as response:
                    response.raise_for_status()
                    json_body = await response.json()

                    if json_body["status"] == "error" and json_body["detail"] == "NoSuchKey":
                        raise NoSuchKey()

                    spi = json_body["subjectPublicKeyInfo"]  # handle errors
                    ski = json_body["subjectKeyIdentifier_b64"]

                    if isinstance(spi, str) and isinstance(ski, str):
                        return spi, base64.b64decode(ski)
                    raise ValueError("Problem with create keypair")

        async with async_lock(self.lock):
            # Ensure we get a healthy pkcs11 session
            await self.healthy_session()

            key_pub = self._get_pub_key(key_label, key_type)
            return self._public_key_data(key_pub, key_type)
