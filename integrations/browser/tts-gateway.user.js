// ==UserScript==
// @name         TTS Gateway: Speak Selection
// @namespace    https://github.com/DMGiulioRomano/TTS-Gateway
// @version      0.1.0
// @description  Read the selected text aloud through a local tts-gateway. Alt+S speaks (replacing current speech), Alt+Shift+S queues, Alt+X stops.
// @author       TTS Gateway contributors
// @license      MIT
// @match        *://*/*
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// @connect      127.0.0.1
// @connect      localhost
// ==/UserScript==

// GM_xmlhttpRequest (rather than fetch) is required: HTTPS pages cannot
// fetch a plain-HTTP localhost service directly (mixed content), but the
// userscript manager's privileged request API can.

(function () {
  "use strict";

  const GATEWAY = "http://127.0.0.1:5111"; // change if your gateway uses another port

  function post(path, body, onOk) {
    GM_xmlhttpRequest({
      method: "POST",
      url: GATEWAY + path,
      headers: { "Content-Type": "application/json" },
      data: JSON.stringify(body || {}),
      timeout: 5000,
      onload: (response) => {
        if (response.status >= 400) {
          let detail = "HTTP " + response.status;
          try {
            detail = JSON.parse(response.responseText).detail || detail;
          } catch (ignored) {
            // non-JSON error body; keep the status text
          }
          toast("tts-gateway: " + detail);
        } else if (onOk) {
          onOk(response);
        }
      },
      onerror: () => toast("tts-gateway unreachable. Start it with: tts-gateway serve"),
      ontimeout: () => toast("tts-gateway timed out"),
    });
  }

  function selectedText() {
    const selection = window.getSelection ? String(window.getSelection()) : "";
    return selection.trim();
  }

  function speakSelection(interrupt) {
    const text = selectedText();
    if (!text) {
      toast("Select some text first");
      return;
    }
    post("/v1/speak", { text: text, interrupt: interrupt }, () =>
      toast(interrupt ? "Speaking…" : "Queued")
    );
  }

  function stopSpeaking() {
    post("/v1/stop", {}, () => toast("Stopped"));
  }

  // --- tiny toast ----------------------------------------------------------

  let toastElement = null;
  let toastTimer = null;

  function toast(message) {
    if (!toastElement) {
      toastElement = document.createElement("div");
      toastElement.style.cssText = [
        "position:fixed",
        "right:16px",
        "bottom:16px",
        "z-index:2147483647",
        "max-width:320px",
        "padding:10px 14px",
        "border-radius:8px",
        "background:rgba(20,20,20,.92)",
        "color:#fff",
        "font:13px/1.4 system-ui,sans-serif",
        "box-shadow:0 4px 14px rgba(0,0,0,.35)",
        "pointer-events:none",
        "transition:opacity .25s",
      ].join(";");
      document.documentElement.appendChild(toastElement);
    }
    toastElement.textContent = message;
    toastElement.style.opacity = "1";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toastElement.style.opacity = "0";
    }, 2200);
  }

  // --- shortcuts and menu --------------------------------------------------

  function isEditingContext(target) {
    if (!target) return false;
    const tag = (target.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || target.isContentEditable;
  }

  window.addEventListener(
    "keydown",
    (event) => {
      if (!event.altKey || event.ctrlKey || event.metaKey) return;
      if (isEditingContext(event.target)) return;
      if (event.code === "KeyS") {
        event.preventDefault();
        speakSelection(!event.shiftKey); // Alt+S replaces, Alt+Shift+S queues
      } else if (event.code === "KeyX") {
        event.preventDefault();
        stopSpeaking();
      }
    },
    true
  );

  if (typeof GM_registerMenuCommand === "function") {
    GM_registerMenuCommand("Speak selection (Alt+S)", () => speakSelection(true));
    GM_registerMenuCommand("Queue selection (Alt+Shift+S)", () => speakSelection(false));
    GM_registerMenuCommand("Stop speaking (Alt+X)", stopSpeaking);
  }
})();
