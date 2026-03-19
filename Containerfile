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

EXPOSE 8080

ENTRYPOINT ["wormhole-page-server"]
CMD ["--port", "8080", "--static-dir", "/app/static"]
