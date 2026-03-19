.PHONY: build server wasm wasm-clean clean run

WASM_CRATE = crates/wormhole-wasm
WASM_OUT = static/wasm

build: server wasm

server:
	cargo build --release -p wormhole-page-server

wasm:
	cd $(WASM_CRATE) && wasm-pack build --target web --release
	mkdir -p $(WASM_OUT)
	cp $(WASM_CRATE)/pkg/wormhole_wasm_bg.wasm $(WASM_OUT)/
	cp $(WASM_CRATE)/pkg/wormhole_wasm.js $(WASM_OUT)/

run: build
	./target/release/wormhole-page-server --static-dir static/

clean:
	cargo clean
	rm -rf $(WASM_OUT)
