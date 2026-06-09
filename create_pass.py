#!/usr/bin/env python3
"""
create_pass.py — Génère un pass Apple Wallet (.pkpass) depuis un QR code Izly PNG.

Fonctionnement :
  1. Décode l'image QR pour extraire la donnée brute (via pyzbar).
  2. Construit la structure d'un pass de type storeCard.
  3. Signe le pass si les certificats Apple sont fournis.
  4. Emballe le tout dans un fichier .pkpass (ZIP).

Usage minimal (pass non signé — test uniquement) :
    python create_pass.py --qr izly_qr.png

Usage avec signature (requis pour l'installation sur iPhone) :
    python create_pass.py --qr izly_qr.png \\
        --cert pass_cert.pem \\
        --key  pass_key.pem  \\
        --wwdr AppleWWDRCA.pem \\
        --team-id  XXXXXXXXXX \\
        --pass-type-id pass.fr.izly.paiement

Obtenir les certificats Apple :
    - Crée un Pass Type ID sur developer.apple.com/account/resources
    - Exporte le certificat en .p12, puis convertis en PEM :
        openssl pkcs12 -in cert.p12 -clcerts -nokeys -out pass_cert.pem
        openssl pkcs12 -in cert.p12 -nocerts -nodes  -out pass_key.pem
    - Télécharge AppleWWDRCAG4.cer sur https://www.apple.com/certificateauthority/
        openssl x509 -inform DER -in AppleWWDRCAG4.cer -out AppleWWDRCA.pem

Prérequis Python :
    pip install Pillow pyzbar cryptography
    # Windows : installe aussi la DLL zbar → https://github.com/NaturalHistoryMuseum/pyzbar#windows
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# ── Import optionnel : décodage QR ────────────────────────────────────────────
try:
    from pyzbar.pyzbar import decode as _zbar_decode
    from PIL import Image as _PilImg
    _QR_DECODE_OK = True
except ImportError:
    _QR_DECODE_OK = False

# ── Import optionnel : signature PKCS7 ────────────────────────────────────────
try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import pkcs7 as _pkcs7
    _SIGN_OK = True
except ImportError:
    _SIGN_OK = False

# ── Import optionnel : génération d'icônes ────────────────────────────────────
try:
    from PIL import Image, ImageDraw
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

# Couleurs Izly
_BLUE  = (0, 93, 171)
_WHITE = (255, 255, 255)


# ── Décodage QR ───────────────────────────────────────────────────────────────

def decode_qr(png_path: str) -> str | None:
    """
    Décode l'image QR et retourne la donnée brute (chaîne de caractères).
    Retourne None si pyzbar n'est pas disponible ou si le décodage échoue.
    """
    if not _QR_DECODE_OK:
        return None
    img = _PilImg.open(png_path)
    results = _zbar_decode(img)
    if not results:
        return None
    return results[0].data.decode("utf-8", errors="replace")


# ── Génération des images du pass ─────────────────────────────────────────────

def _make_icon_png(size: int) -> bytes:
    """Génère une icône PNG carrée aux couleurs Izly (bleue, texte 'IZ')."""
    if _PIL_OK:
        img = Image.new("RGB", (size, size), _BLUE)
        draw = ImageDraw.Draw(img)
        text = "IZ"
        # textbbox disponible depuis Pillow 8
        bbox = draw.textbbox((0, 0), text)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((size - tw) // 2, (size - th) // 2), text, fill=_WHITE)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    # Fallback : PNG 1×1 bleu encodé en base64 (aucune dépendance)
    import base64
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQ"
        "AABjkB6QAAAABJRU5ErkJggg=="
    )


# ── Construction du pass.json ─────────────────────────────────────────────────

def _build_pass_json(
    qr_data: str | None,
    validity_date: str | None,
    team_id: str,
    pass_type_id: str,
) -> bytes:
    serial = f"izly-{int(datetime.now(timezone.utc).timestamp())}"

    pass_dict: dict = {
        "formatVersion": 1,
        "passTypeIdentifier": pass_type_id,
        "serialNumber": serial,
        "teamIdentifier": team_id,
        "organizationName": "Izly",
        "description": "QR code de paiement Izly",
        "logoText": "Izly",
        "foregroundColor": "rgb(255, 255, 255)",
        "backgroundColor": "rgb(0, 93, 171)",
        "labelColor": "rgb(173, 210, 255)",
        "storeCard": {
            "primaryFields": [],
            "secondaryFields": [],
            "auxiliaryFields": [],
            "backFields": [
                {
                    "key": "info",
                    "label": "À savoir",
                    "value": (
                        "Le QR code Izly expire en ~10 minutes. "
                        "Génère-en un nouveau avec fetch_qr.py avant chaque paiement, "
                        "puis recrée le pass."
                    ),
                }
            ],
        },
    }

    if validity_date:
        pass_dict["storeCard"]["secondaryFields"].append(
            {"key": "validity", "label": "Valide jusqu'à", "value": validity_date}
        )

    if qr_data:
        barcode: dict = {
            "message": qr_data,
            "format": "PKBarcodeFormatQR",
            "messageEncoding": "iso-8859-1",
        }
        # "barcode" (iOS ≤ 8 legacy) + "barcodes" (iOS ≥ 9)
        pass_dict["barcode"]  = barcode
        pass_dict["barcodes"] = [barcode]

    return json.dumps(pass_dict, ensure_ascii=False, indent=2).encode("utf-8")


# ── Signature PKCS7 ───────────────────────────────────────────────────────────

def _sign(manifest_bytes: bytes, cert: str, key: str, wwdr: str) -> bytes:
    """
    Retourne la signature PKCS7 détachée (DER) du manifest.json.
    Nécessite la bibliothèque 'cryptography' >= 37.0.
    """
    if not _SIGN_OK:
        sys.exit(
            "Bibliothèque 'cryptography' manquante.\n"
            "Installe-la avec : pip install cryptography"
        )

    with open(cert, "rb") as f:
        cert_obj = x509.load_pem_x509_certificate(f.read())
    with open(key, "rb") as f:
        key_obj = serialization.load_pem_private_key(f.read(), password=None)
    with open(wwdr, "rb") as f:
        wwdr_obj = x509.load_pem_x509_certificate(f.read())

    builder = (
        _pkcs7.PKCS7SignatureBuilder()
        .set_data(manifest_bytes)
        .add_signer(cert_obj, key_obj, hashes.SHA256())
        .add_certificate(wwdr_obj)
    )
    return builder.sign(
        serialization.Encoding.DER,
        [_pkcs7.PKCS7Options.DetachedSignature],
    )


# ── Assemblage du .pkpass ─────────────────────────────────────────────────────

def build_pkpass(
    qr_png: str,
    output: str,
    team_id: str,
    pass_type_id: str,
    validity_date: str | None,
    cert: str | None,
    key: str | None,
    wwdr: str | None,
) -> None:
    # 1. Décode le QR
    qr_data = decode_qr(qr_png)
    if qr_data:
        preview = qr_data[:80] + ("…" if len(qr_data) > 80 else "")
        print(f"[OK] Données QR décodées : {preview}")
    else:
        print(
            "[!]  pyzbar non disponible — le pass utilisera le QR en image (strip)\n"
            "     sans code-barres natif Apple Wallet. Installe pyzbar pour le mode complet."
        )

    # 2. Assemble les fichiers
    qr_bytes = Path(qr_png).read_bytes()
    files: dict[str, bytes] = {
        "pass.json":    _build_pass_json(qr_data, validity_date, team_id, pass_type_id),
        # strip : image affichée en fond de la carte (format banner)
        "strip.png":    qr_bytes,
        "strip@2x.png": qr_bytes,
        "icon.png":     _make_icon_png(29),
        "icon@2x.png":  _make_icon_png(58),
        "logo.png":     _make_icon_png(160),
        "logo@2x.png":  _make_icon_png(320),
    }

    # 3. manifest.json (hash SHA-1 de chaque fichier)
    manifest = {name: hashlib.sha1(data).hexdigest() for name, data in files.items()}
    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    files["manifest.json"] = manifest_bytes

    # 4. Signature (optionnelle)
    signed = False
    if cert and key and wwdr:
        files["signature"] = _sign(manifest_bytes, cert, key, wwdr)
        signed = True
    else:
        print(
            "[!]  Aucun certificat fourni — pass NON signé.\n"
            "     Ce fichier .pkpass ne peut PAS être installé sur un iPhone.\n"
            "     Fournis --cert, --key et --wwdr pour un pass installable."
        )

    # 5. Zip → .pkpass
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)

    status = "signé — prêt pour iPhone" if signed else "NON signé — test uniquement"
    print(f"[OK] Pass créé : {output}  [{status}]")

    if signed:
        print()
        print("  → AirDrop le fichier .pkpass sur ton iPhone, ou ouvre-le dans Safari.")


# ── Point d'entrée ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crée un pass Apple Wallet (.pkpass) depuis un QR code Izly."
    )
    parser.add_argument("--qr", required=True, metavar="FILE",
                        help="Image PNG du QR code (ex. izly_qr.png)")
    parser.add_argument("--output", default="izly.pkpass", metavar="FILE",
                        help="Fichier .pkpass de sortie (défaut : izly.pkpass)")
    parser.add_argument("--validity-date", default=None, metavar="TEXTE",
                        help="Texte de date d'expiration affiché sur le pass")
    parser.add_argument("--team-id", default="XXXXXXXXXX", metavar="ID",
                        help="Apple Team ID à 10 caractères")
    parser.add_argument("--pass-type-id", default="pass.fr.izly.paiement", metavar="ID",
                        help="Pass Type Identifier enregistré sur developer.apple.com")
    # Certificats (facultatifs — requis uniquement pour un pass installable)
    parser.add_argument("--cert", default=None, metavar="FILE",
                        help="Certificat Pass Type ID en PEM")
    parser.add_argument("--key",  default=None, metavar="FILE",
                        help="Clé privée correspondante en PEM")
    parser.add_argument("--wwdr", default=None, metavar="FILE",
                        help="Certificat Apple WWDR intermédiaire en PEM")
    args = parser.parse_args()

    if not Path(args.qr).exists():
        sys.exit(f"Fichier QR introuvable : {args.qr!r}\nLance d'abord : python fetch_qr.py")

    build_pkpass(
        qr_png=args.qr,
        output=args.output,
        team_id=args.team_id,
        pass_type_id=args.pass_type_id,
        validity_date=args.validity_date,
        cert=args.cert,
        key=args.key,
        wwdr=args.wwdr,
    )


if __name__ == "__main__":
    main()
