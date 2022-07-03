import typing
from types import FrameType
import hashlib
import threading
import os
import signal
import time
from contextlib import contextmanager
from collections.abc import Generator

import asn1crypto

from pkcs11 import KeyType, ObjectClass, Mechanism, lib
from pkcs11._pkcs11 import Session as pkcs11Session
from pkcs11._pkcs11 import Token as pkcs11Token
from pkcs11.util.rsa import encode_rsa_public_key

import error

#export PKCS11_MODULE="/usr/lib/softhsm/libsofthsm2.so"
#export PKCS11_TOKEN = 'my_test_token_1'
#export PKCS11_PIN = '1234'


# NOTE:
# FIXME better text here
# Better to keep a pkcs11 session reference all the time
# and then when it fails to open a new session for performance

LOCK = threading.Lock()

@contextmanager
def _time_limit(seconds: int) -> Generator[None, None, None]:
    """Context manager to call PKCS11 functions with a time limit
    
    Parameters:
    seconds (int): Time limit in seconds

    Returns:
    None
    
    """
    def signal_handler(signum: int, frame: typing.Optional[FrameType]) -> None:
        raise error.PKCS11TimeoutException()
    
    signal.signal(signal.SIGALRM, signal_handler)
    signal.alarm(seconds)

    try:
        yield
    finally:
        signal.alarm(0)
        
class Session():
    """Persistent PKCS11 session wrapper."""

    session: pkcs11Session = None
    token: pkcs11Token = None

    @classmethod
    def _healthy_session(cls) -> None:
        """Check if our persistent PKCS11 session is healthy.
        If not then open a new session.
        
        Parameters:

        Returns:
        None
        
        """

        try:
            with _time_limit(2):
                if cls.session.token.label != os.environ['PKCS11_TOKEN']:
                    cls.session.close()
                    pkcs11_lib = lib(os.environ['PKCS11_MODULE'])
                    cls.token = pkcs11_lib.get_token(token_label=os.environ['PKCS11_TOKEN'])
                    cls.session = cls.token.open(rw=True, user_pin=os.environ['PKCS11_PIN'])
        except:
            pkcs11_lib = lib(os.environ['PKCS11_MODULE'])
            cls.token = pkcs11_lib.get_token(token_label=os.environ['PKCS11_TOKEN'])
            cls.session = cls.token.open(rw=True, user_pin=os.environ['PKCS11_PIN'])
            
    @classmethod
    def sign(cls, data: bytes, key_label: str) -> bytes:
        """Sign the data: bytes using the private key with the label in the PKCS11 device.
        Returns the signed data: bytes for the x509 extension and
        'Authority Key Identifier' valid for this keypair.
        
        Parameters:
        data (bytes): Bytes to be signed.
        key_label (str): Keypair label.

        Returns:
        typing.Tuple[asn1crypto.keys.PublicKeyInfo, bytes]
        
        """

        with LOCK:
            # Ensure we get a healthy pkcs11 session
            cls._healthy_session()

            try:
                with _time_limit(3):
                    key_pub = cls.session.get_key(key_type=KeyType.RSA,
                                              object_class=ObjectClass.PUBLIC_KEY,
                                              label=key_label)

                    # Get private key to sign the data with
                    key_priv = cls.session.get_key(key_type=KeyType.RSA,
                                               object_class=ObjectClass.PRIVATE_KEY,
                                               label=key_label)

                    # Sign the data
                    signature = key_priv.sign(data, mechanism=Mechanism.SHA256_RSA_PKCS)
                    assert(isinstance(signature, bytes))
                    return signature
            # FIXME
            except Exception as e:
                raise e


    @classmethod
    def create_keypair(cls, key_size: int, key_label: str) -> typing.Tuple[asn1crypto.keys.PublicKeyInfo, bytes]:
        """Create a RSA keypair in the PKCS11 device with this label.
        Returns the data for the x509 'Subject Public Key Info'
        and x509 extension 'Subject Key Identifier' valid for this keypair.
        
        Parameters:
        key_size (int): Size of the key
        key_label (str): Keypair label.

        Returns:
        typing.Tuple[asn1crypto.keys.PublicKeyInfo, bytes]
        
        """

        with LOCK:
            # Ensure we get a healthy pkcs11 session
            cls._healthy_session()
            
            try:
                with _time_limit(3):
                    # Generate the rsa keypair
                    key_pub, key_priv = cls.session.generate_keypair(KeyType.RSA,
                                                             key_size,
                                                             store=True,
                                                             label=key_label)

                    #import time
                    #time.sleep(4)
                    
                    # Create the asn1crypto.keys.PublicKeyInfo object
                    rsa_pub = asn1crypto.keys.RSAPublicKey.load(encode_rsa_public_key(key_pub))
                    
                    pki = asn1crypto.keys.PublicKeyInfo()
                    pka = asn1crypto.keys.PublicKeyAlgorithm()
                    pka["algorithm"] = asn1crypto.keys.PublicKeyAlgorithmId("rsa")
                    pki["algorithm"] = pka
                    pki["public_key"] = rsa_pub

                    return pki, hashlib.sha1(encode_rsa_public_key(key_pub)).digest()
            # FIXME
            except Exception as e:
                raise e

    @classmethod
    def key_identifier(cls, key_label: str) -> bytes:
        """Returns the bytes for x509 extension 'Authority Key Identifier' valid for this keypair.
        
        Parameters:
        key_label (str): Keypair label.

        Returns:
        bytes
        
        """

        with LOCK:
            # Ensure we get a healthy pkcs11 session
            cls._healthy_session()
            
            try:
                with _time_limit(3):

                    key_pub = cls.session.get_key(key_type=KeyType.RSA,
                                              object_class=ObjectClass.PUBLIC_KEY,
                                              label=key_label)
                    
                    return hashlib.sha1(encode_rsa_public_key(key_pub)).digest()
            # FIXME
            except Exception as e:
                raise e

