# IZLY – QR Code Generator

A Python command-line tool that authenticates with the [Izly](https://www.izly.fr/) web portal
and downloads your payment QR codes as a PNG image.

---

## Requirements

- Python ≥ 3.10
- `beautifulsoup4` ≥ 4.11.1
- `Pillow` ≥ 9.3.0
- `requests` ≥ 2.28.0

Install all dependencies with:

```bash
pip install -r requirements.txt
```

---

## Usage

```
python main.py [-h] [-q {1,2,3}] [-u USERNAME] [-p PASSWORD] [-o OUTPUT] [-s SIZE]
```

### Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `-h`, `--help` | – | Show help message and exit |
| `-q`, `--codes` | `1` | Number of QR codes to generate (1–3) |
| `-u`, `--username` | *(prompted)* | Your Izly username |
| `-p`, `--password` | *(prompted)* | Your Izly password |
| `-o`, `--output` | `qrcode.png` | Output image file (`.png`, `.jpg`, `.jpeg`, `.gif`) |
| `-s`, `--size` | `300` | Size of each QR code in pixels |

### Examples

Generate one QR code and save it to `qrcode.png`:

```bash
python main.py -u myusername -p mypassword
```

Generate 3 QR codes, each 400 × 400 px, saved to `codes.png`:

```bash
python main.py -q 3 -u myusername -p mypassword -o codes.png -s 400
```

If `-u` or `-p` are omitted the script prompts for them interactively (the
password prompt hides typing).
