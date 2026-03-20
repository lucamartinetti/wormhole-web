# --- Build stage: Rust server + WASM ---
FROM rust:1.94-slim AS builder

RUN apt-get update && apt-get install -y pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
RUN cargo install wasm-pack

WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY crates/ crates/

# Build server
RUN cargo build --release -p wormhole-page-server

# Build WASM
RUN cd crates/wormhole-wasm && wasm-pack build --target web --release

# --- Runtime stage ---
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/target/release/wormhole-page-server /usr/local/bin/
COPY static/ /app/static/
COPY --from=builder /build/crates/wormhole-wasm/pkg/wormhole_wasm_bg.wasm /app/static/wasm/
COPY --from=builder /build/crates/wormhole-wasm/pkg/wormhole_wasm.js /app/static/wasm/

# Generate SRI hashes for WASM integrity verification
RUN WASM_JS_HASH=$(openssl dgst -sha384 -binary /app/static/wasm/wormhole_wasm.js | openssl base64 -A) && \
    WASM_BG_HASH=$(openssl dgst -sha384 -binary /app/static/wasm/wormhole_wasm_bg.wasm | openssl base64 -A) && \
    QR_JS_HASH=$(openssl dgst -sha384 -binary /app/static/qr.js | openssl base64 -A) && \
    WASM_CLIENT_JS_HASH=$(openssl dgst -sha384 -binary /app/static/wasm-client.js | openssl base64 -A) && \
    sed -i "s|WASM_JS_SRI_HASH|sha384-${WASM_JS_HASH}|g" /app/static/index.html && \
    sed -i "s|WASM_BG_SRI_HASH|sha384-${WASM_BG_HASH}|g" /app/static/index.html && \
    sed -i "s|QR_JS_SRI_HASH|sha384-${QR_JS_HASH}|g" /app/static/index.html && \
    sed -i "s|WASM_CLIENT_JS_SRI_HASH|sha384-${WASM_CLIENT_JS_HASH}|g" /app/static/index.html

EXPOSE 8080

ENTRYPOINT ["wormhole-page-server"]
CMD ["--port", "8080", "--static-dir", "/app/static"]
