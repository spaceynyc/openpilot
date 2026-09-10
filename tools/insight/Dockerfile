# Pinned Linux/aarch64 toolchain; build inputs and runtime live on the external volume.
FROM ghcr.io/commaai/openpilot-base-aarch64@sha256:c4f206250ec2cbee493589b836e517a3de91281d8d9e4b01f370b5b600a0d143
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    android-sdk-libsparse-utils e2fsprogs rsync openssh-client cmake \
    gcc-aarch64-linux-gnu g++-aarch64-linux-gnu \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://github.com/astral-sh/uv/releases/download/0.8.15/uv-aarch64-unknown-linux-gnu.tar.gz -o /tmp/uv.tar.gz \
    && tar -xzf /tmp/uv.tar.gz -C /usr/local/bin --strip-components=1 \
    && rm /tmp/uv.tar.gz
ARG CAPNP_VERSION=1.0.2
RUN curl -fsSL "https://github.com/capnproto/capnproto/archive/refs/tags/v${CAPNP_VERSION}.tar.gz" -o /tmp/capnp.tar.gz \
    && tar -xzf /tmp/capnp.tar.gz -C /tmp \
    && cmake -S /tmp/capnproto-${CAPNP_VERSION}/c++ -B /tmp/capnp-build -DBUILD_TESTING=OFF -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/capnp-build -j4 \
    && cmake --install /tmp/capnp-build && ldconfig \
    && rm -rf /tmp/capnp.tar.gz /tmp/capnproto-${CAPNP_VERSION} /tmp/capnp-build
WORKDIR /validation/runtime
