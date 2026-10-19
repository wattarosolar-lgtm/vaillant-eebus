# Vaillant EEBUS app image.
#
# The build context is the repository root, not this folder: the image needs
# the bridge/ submodule and backend/. GitHub Actions builds it with
#   docker buildx build --platform linux/amd64,linux/arm64 -f vaillant-eebus/Dockerfile .
# The Supervisor only pulls the published image (config.yaml `image:`).

ARG BASE_IMAGE=ghcr.io/home-assistant/base-python:3.13-alpine3.24-2026.10.0

# 1. EEBUS bridge (Go) from the pinned commit of the bridge fork in bridge/.
#    Runs natively on the build host and cross-compiles: CGO_ENABLED=0 gives a
#    static binary for every target, as in bridge/eebus-bridge/Dockerfile.
# hadolint ignore=DL3029
FROM --platform=$BUILDPLATFORM golang:1.27-alpine AS bridge
WORKDIR /src
COPY bridge/eebus-bridge/go.mod bridge/eebus-bridge/go.sum ./
RUN go mod download
COPY bridge/eebus-bridge/ ./
ARG BRIDGE_VERSION=dev
ARG TARGETOS
ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} go build -trimpath -buildvcs=false \
      -ldflags "-s -w -X github.com/volschin/eebus-bridge/internal/grpc.BuildVersion=${BRIDGE_VERSION}" \
      -o /out/eebus-bridge ./cmd/eebus-bridge

# 2. Python gRPC stubs from the bridge's proto files. The output is plain
#    Python, so this stage also runs natively on the build host.
# hadolint ignore=DL3029
FROM --platform=$BUILDPLATFORM ${BASE_IMAGE} AS proto
WORKDIR /build
COPY backend/requirements-build.lock backend/
RUN python3 -m venv /opt/tools \
 && /opt/tools/bin/pip install --no-cache-dir --require-hashes --only-binary=:all: -r backend/requirements-build.lock
COPY bridge/eebus-bridge/proto/ bridge/eebus-bridge/proto/
COPY backend/scripts/ backend/scripts/
RUN /opt/tools/bin/python backend/scripts/generate_proto.py

# 3. Runtime: Home Assistant base image (s6-overlay, bashio) with Python 3.13.
FROM ${BASE_IMAGE}
COPY backend/requirements.lock /tmp/requirements.lock
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.lock \
 && rm /tmp/requirements.lock

COPY --from=bridge /out/eebus-bridge /usr/local/bin/eebus-bridge
COPY bridge/LICENSE /usr/share/licenses/eebus-bridge/LICENSE
COPY --from=proto /build/backend/gen/eebus/ /opt/backend/eebus/
COPY backend/src/vaillant_eebus/ /opt/backend/vaillant_eebus/
COPY vaillant-eebus/rootfs/ /
RUN chmod a+x /etc/s6-overlay/s6-rc.d/*/run /etc/s6-overlay/s6-rc.d/*/finish

ENV PYTHONPATH=/opt/backend \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

ARG BUILD_VERSION=dev
LABEL io.hass.type="addon" \
      io.hass.version="${BUILD_VERSION}" \
      org.opencontainers.image.title="Vaillant EEBUS" \
      org.opencontainers.image.source="https://github.com/wattarosolar-lgtm/vaillant-eebus" \
      org.opencontainers.image.version="${BUILD_VERSION}"
