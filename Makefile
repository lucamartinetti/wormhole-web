.PHONY: build server wasm sri wasm-clean clean run test

WASM_CRATE = crates/wormhole-wasm
WASM_OUT = static/wasm

build: server wasm sri

server:
	cargo build --release -p wormhole-page-server

wasm:
	cd $(WASM_CRATE) && wasm-pack build --target web --release
	mkdir -p $(WASM_OUT)
	cp $(WASM_CRATE)/pkg/wormhole_wasm_bg.wasm $(WASM_OUT)/
	cp $(WASM_CRATE)/pkg/wormhole_wasm.js $(WASM_OUT)/

sri: wasm
	@echo "Generating SRI hashes..."
	@STYLE_HASH=$$(openssl dgst -sha384 -binary static/style.css | openssl base64 -A) && \
	sed -i 's|href="/static/style.css"|href="/static/style.css" integrity="sha384-'"$$STYLE_HASH"'" crossorigin="anonymous"|' static/index.html && \
	echo "  style.css: sha384-$$STYLE_HASH"

run: build
	./target/release/wormhole-page-server --static-dir static/

test:
	npx playwright test

clean:
	cargo clean
	rm -rf $(WASM_OUT)
