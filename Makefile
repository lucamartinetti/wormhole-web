.PHONY: build server wasm sri wasm-clean clean run

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
	@WASM_JS_HASH=$$(openssl dgst -sha384 -binary static/wasm/wormhole_wasm.js | openssl base64 -A) && \
	WASM_BG_HASH=$$(openssl dgst -sha384 -binary static/wasm/wormhole_wasm_bg.wasm | openssl base64 -A) && \
	sed -i "s|WASM_JS_SRI_HASH|sha384-$$WASM_JS_HASH|g" static/index.html && \
	sed -i "s|WASM_BG_SRI_HASH|sha384-$$WASM_BG_HASH|g" static/index.html && \
	echo "  wormhole_wasm.js:     sha384-$$WASM_JS_HASH" && \
	echo "  wormhole_wasm_bg.wasm: sha384-$$WASM_BG_HASH"

run: build
	./target/release/wormhole-page-server --static-dir static/

clean:
	cargo clean
	rm -rf $(WASM_OUT)
