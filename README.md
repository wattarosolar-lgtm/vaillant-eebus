# Vaillant EEBUS für Home Assistant

Home-Assistant-App, die eine Vaillant-Wärmepumpe ausschließlich lokal über EEBUS anbindet.
Anleitung für Nutzer: [vaillant-eebus/DOCS.md](vaillant-eebus/DOCS.md).

## Installation

In Home Assistant unter *Einstellungen → Apps → App-Store → ⋮ → Repositories* diese URL hinzufügen:

```
https://github.com/wattarosolar-lgtm/vaillant-eebus
```

## Aufbau

| Pfad | Inhalt |
|---|---|
| `repository.yaml` | App-Repository für den HA-App-Store |
| `vaillant-eebus/` | Die App: `config.yaml`, `Dockerfile`, s6-Dienste (`rootfs/`), Doku |
| `bridge/` | Git-Submodul: Fork der EEBUS-Bridge ([volschin/eebus-ha-bridge](https://github.com/volschin/eebus-ha-bridge)), fester Commit |
| `backend/` | Python-Backend (`src/vaillant_eebus`), Lock-Dateien, Skript für die gRPC-Stubs |
| `tests/` | Backend-Tests und Fake-Bridge |
| `.github/workflows/` | CI und Image-Build |

Im Container laufen zwei s6-Dienste: `eebus-bridge` (Go, SHIP/SPINE, Port 4712, gRPC auf `127.0.0.1:50051`)
und `backend` (Python), das nur über gRPC mit der Bridge spricht.

## Entwicklung

Voraussetzungen: Git, Go 1.27, Python 3.13. Kein Test spricht mit echter Hardware.

```bash
git clone --recurse-submodules https://github.com/wattarosolar-lgtm/vaillant-eebus.git
cd vaillant-eebus
python3.13 -m venv .venv
.venv/bin/pip install --require-hashes -r backend/requirements-dev.lock   # Windows: .venv\Scripts\...
.venv/bin/python backend/scripts/generate_proto.py --typed                # Stubs nach backend/gen/
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest
```

Die gRPC-Stubs werden aus `bridge/eebus-bridge/proto` erzeugt und nie von Hand geändert oder eingecheckt.

**Fake-Bridge** (gRPC-Server mit denselben Protos und Beispieldaten), um das Backend ohne Wärmepumpe zu starten:

```bash
PYTHONPATH=backend/gen .venv/bin/python -m tests.fake_bridge --port 50051
PYTHONPATH=backend/src:backend/gen .venv/bin/python -m vaillant_eebus
```

**Lock-Dateien** neu erzeugen (mit [uv](https://docs.astral.sh/uv/)), nachdem eine `requirements*.in` geändert wurde:

```bash
cd backend
for f in requirements requirements-build requirements-dev; do
  uv pip compile $f.in --universal --python-version 3.13 --generate-hashes --no-header -o $f.lock
done
```

**Bridge:** `cd bridge/eebus-bridge && go vet ./... && go test -race ./...`
(`-race` braucht CGO und läuft deshalb in der CI unter Linux).

## Release

1. Version in `vaillant-eebus/config.yaml` und `CHANGELOG.md` erhöhen.
2. Tag `v<version>` pushen. Die CI baut das Image für amd64 und aarch64 und veröffentlicht es als
   `ghcr.io/wattarosolar-lgtm/vaillant-eebus:<version>`.
3. Nur beim ersten Release: das Paket `vaillant-eebus` auf GitHub unter *Packages → Package settings*
   auf **Public** stellen, sonst kann Home Assistant das Image nicht laden.
