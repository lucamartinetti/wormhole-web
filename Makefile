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
	@WASM_JS_HASH=$$(openssl dgst -sha384 -binary static/wasm/wormhole_wasm.js | openssl base64 -A) && \
	WASM_BG_HASH=$$(openssl dgst -sha384 -binary static/wasm/wormhole_wasm_bg.wasm | openssl base64 -A) && \
	sed -i "s|WASM_JS_SRI_HASH|sha384-$$WASM_JS_HASH|g" static/index.html && \
	sed -i "s|WASM_BG_SRI_HASH|sha384-$$WASM_BG_HASH|g" static/index.html && \
	echo "  wormhole_wasm.js:     sha384-$$WASM_JS_HASH" && \
	echo "  wormhole_wasm_bg.wasm: sha384-$$WASM_BG_HASH"
	@QR_JS_HASH=$$(openssl dgst -sha384 -binary static/qr.js | openssl base64 -A) && \
	WASM_CLIENT_JS_HASH=$$(openssl dgst -sha384 -binary static/wasm-client.js | openssl base64 -A) && \
	APP_JS_HASH=$$(openssl dgst -sha384 -binary static/app.js | openssl base64 -A) && \
	sed -i 's|src="/static/qr.js"|src="/static/qr.js" integrity="sha384-'"$$QR_JS_HASH"'" crossorigin="anonymous"|' static/index.html && \
	sed -i 's|src="/static/wasm-client.js"|src="/static/wasm-client.js" integrity="sha384-'"$$WASM_CLIENT_JS_HASH"'" crossorigin="anonymous"|' static/index.html && \
	sed -i 's|src="/static/app.js"|src="/static/app.js" integrity="sha384-'"$$APP_JS_HASH"'" crossorigin="anonymous"|' static/index.html && \
	echo "  qr.js:            sha384-$$QR_JS_HASH" && \
	echo "  wasm-client.js:   sha384-$$WASM_CLIENT_JS_HASH" && \
	echo "  app.js:           sha384-$$APP_JS_HASH"
	@BUILD_HASH=$$(cat static/wasm/wormhole_wasm_bg.wasm static/wasm/wormhole_wasm.js static/app.js static/style.css | openssl dgst -sha256 -binary | openssl base64 -A | head -c 8) && \
	sed -i "s|BUILD_HASH|$$BUILD_HASH|g" static/sw.js && \
	echo "  sw.js cache:      wormhole-$$BUILD_HASH"

run: build
	./target/release/wormhole-page-server --static-dir static/

test:
	npx playwright test

clean:
	cargo clean
	rm -rf $(WASM_OUT)
