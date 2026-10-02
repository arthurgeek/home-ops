# syntax=docker/dockerfile:labs

ARG PYTHON_VERSION="3.13"
ARG GO_VERSION="1.25"

# 1. Build go2rtc binary from mpapierski:unifi-talkback (PR #2340)
FROM --platform=$BUILDPLATFORM golang:${GO_VERSION}-alpine AS build
ARG TARGETOS
ARG TARGETARCH

ENV GOOS=${TARGETOS}
ENV GOARCH=${TARGETARCH}

WORKDIR /build

RUN apk add --no-cache git

# Clone mpapierski/go2rtc branch unifi-talkback
RUN git clone --depth 1 --branch unifi-talkback https://github.com/mpapierski/go2rtc.git .

RUN --mount=type=cache,target=/root/.cache/go-build CGO_ENABLED=0 go build -ldflags "-s -w" -trimpath

# 2. Final runtime image
FROM python:${PYTHON_VERSION}-alpine AS base

# Install ffmpeg, tini, and common media utilities
RUN apk add --no-cache tini ffmpeg ffplay bash curl jq alsa-plugins-pulse font-droid

# Hardware Acceleration for Intel CPU
ARG TARGETARCH
RUN if [ "${TARGETARCH}" = "amd64" ]; then apk add --no-cache libva-intel-driver intel-media-driver; fi

COPY --from=build /build/go2rtc /usr/local/bin/

EXPOSE 1984 8554 8555 8555/udp
ENTRYPOINT ["/sbin/tini", "--"]
VOLUME /config
WORKDIR /config

CMD ["go2rtc", "-config", "/config/go2rtc.yaml"]
