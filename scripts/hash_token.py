"""Print only a SHA256 digest for a randomly generated high-entropy client key."""
import getpass
import hashlib

if __name__ == "__main__":
    token = getpass.getpass("Existing random Bearer token (not echoed): ")
    if not 24 <= len(token) <= 4096 or not token.isascii() or not token.isprintable():
        raise SystemExit("Token must be 24–4096 printable ASCII characters")
    print("SHA256 hex (put in the server Secret, NOT the plaintext token):")
    print(hashlib.sha256(token.encode()).hexdigest())
