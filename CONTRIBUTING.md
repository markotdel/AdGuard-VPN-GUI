# Contributing

Thanks for considering a contribution!

## Ground rules
- Keep the UI consistent with the Windows AdGuard VPN style where possible.
- Do **not** add or distribute proprietary AdGuard assets without permission.
- Keep changes small and focused.

## Development
1. Install system deps (Ubuntu/XFCE):
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1
```

2. Create venv:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

3. Run:
```bash
adguardvpn-gui
```

## Pull requests
- Describe what you changed and why.
- Add screenshots if UI changed.
- Make sure `install.sh` still works for a clean user install.
