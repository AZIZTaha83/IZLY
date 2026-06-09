#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Izly QR Code Generator

Authenticates with the Izly web portal and downloads QR codes as a single image.

Usage:
    python main.py [-h] [-q {1,2,3}] [-u USERNAME] [-p PASSWORD]
                   [-o OUTPUT] [-s SIZE]
"""

import sys
import re
import argparse
import base64
from io import BytesIO
from getpass import getpass

import requests
from bs4 import BeautifulSoup
from PIL import Image

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://mon-espace.izly.fr"
LOGIN_URL = f"{BASE_URL}/Home/Logon"
QRCODE_URL = f"{BASE_URL}/Home/CreateQrCodeImg"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step(label: str):
    """Decorator factory: prints a status line around a function call."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            print(f"{label} ...", end=" ", flush=True)
            try:
                result = func(*args, **kwargs)
                print("\033[92m[OK]\033[0m")
                return result
            except (PermissionError, requests.RequestException) as exc:
                print("\033[31m[ERROR]\033[0m")
                print(f"  {exc}", file=sys.stderr)
                sys.exit(1)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Core steps
# ---------------------------------------------------------------------------


@_step("Fetching CSRF token")
def get_csrf() -> tuple[dict, str]:
    """Return (cookies, csrf_token) from the Izly login page."""
    response = requests.get(LOGIN_URL, timeout=20)
    if response.status_code != 200:
        raise PermissionError(f"Cannot reach login page (HTTP {response.status_code})")

    soup = BeautifulSoup(response.text, "html.parser")
    token_input = soup.find("input", {"name": "__RequestVerificationToken"})
    if token_input is None:
        raise PermissionError("CSRF token not found on login page")

    return response.cookies, token_input["value"]


@_step("Logging in")
def login(cookies: dict, csrf: str, username: str, password: str) -> dict:
    """
    Post credentials to Izly and return an updated cookie jar that
    includes the .ASPXAUTH session cookie.
    """
    response = requests.post(
        LOGIN_URL,
        data={
            "__RequestVerificationToken": csrf,
            "UserName": username,
            "Password": password,
        },
        cookies=cookies,
        allow_redirects=False,
        timeout=20,
    )

    if response.status_code != 302:
        raise PermissionError("Login failed – unexpected response code")

    if ".ASPXAUTH" not in response.cookies:
        raise PermissionError("Login failed – invalid credentials")

    cookies[".ASPXAUTH"] = response.cookies[".ASPXAUTH"]
    return cookies


@_step("Generating QR codes")
def fetch_qrcodes(cookies: dict, count: int) -> list[dict]:
    """
    Ask Izly to generate *count* QR codes and return the JSON list.

    Each item in the list has at least a ``Src`` field containing a
    data-URI (``data:image/png;base64,...``).
    """
    response = requests.post(
        QRCODE_URL,
        cookies=cookies,
        data={"nbrOfQrCode": str(count)},
        allow_redirects=True,
        timeout=20,
    )

    if response.status_code != 200:
        raise requests.RequestException(
            f"QR code request failed (HTTP {response.status_code})"
        )

    return response.json()


@_step("Saving image")
def save_qrcodes(qrcode_list: list[dict], output: str, size: int) -> None:
    """
    Decode all QR codes from their base64 data-URIs, lay them out
    side-by-side and write the result to *output*.
    """
    margin = size // 8
    cell = size + margin * 2
    canvas = Image.new("RGB", (len(qrcode_list) * cell, cell), (255, 255, 255))

    for idx, item in enumerate(qrcode_list):
        match = re.search(r"base64,(.+)", item["Src"])
        if match is None:
            raise ValueError(f"Unexpected QR code data format for item {idx}")

        qr_img = Image.open(BytesIO(base64.b64decode(match.group(1)))).resize(
            (size, size)
        )
        canvas.paste(qr_img, (margin + idx * cell, margin))

    canvas.save(output)
    print(f"  → saved to {output}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Generate Izly QR codes from your account credentials.",
    )
    parser.add_argument(
        "-q", "--codes",
        metavar="N",
        type=int,
        default=1,
        choices=range(1, 4),
        help="Number of QR codes to generate (1-3, default: 1)",
    )
    parser.add_argument(
        "-u", "--username",
        type=str,
        default=None,
        help="Izly username (prompted if omitted)",
    )
    parser.add_argument(
        "-p", "--password",
        type=str,
        default=None,
        help="Izly password (prompted if omitted)",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default="qrcode.png",
        help="Output image file path (default: qrcode.png)",
    )
    parser.add_argument(
        "-s", "--size",
        type=int,
        default=300,
        help="Width/height of each QR code in pixels (default: 300)",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not re.search(r"\.(png|jpg|jpeg|gif)$", args.output, re.IGNORECASE):
        parser.error("Output file must have a .png, .jpg, .jpeg or .gif extension")

    if args.username is None:
        args.username = input("Izly username: ")
    if args.password is None:
        args.password = getpass("Izly password: ")

    cookies, csrf = get_csrf()
    session = login(cookies, csrf, args.username, args.password)
    qrcodes = fetch_qrcodes(session, args.codes)
    save_qrcodes(qrcodes, args.output, args.size)


if __name__ == "__main__":
    main()
