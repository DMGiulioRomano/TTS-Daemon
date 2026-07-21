// ==UserScript==
// @name         TTS Daemon: Speak Selection + Auto-read
// @namespace    https://github.com/DMGiulioRomano/TTS-Daemon
// @version      0.2.0
// @description  Read text aloud through a local tts-daemon. Alt+S speaks the selection (replacing current speech), Alt+Shift+S queues, Alt+X stops. Alt+A toggles auto-reading of new chat replies on the current site.
// @author       TTS Daemon contributors
// @license      MIT
// @match        *://*/*
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// @grant        GM_setValue
// @grant        GM_getValue
// @connect      127.0.0.1
// @connect      localhost
// ==/UserScript==

// GM_xmlhttpRequest (rather than fetch) is required: HTTPS pages cannot
// fetch a plain-HTTP localhost service directly (mixed content), but the
// userscript manager's privileged request API can.

(function () {
  "use strict";

  const GATEWAY = "http://127.0.0.1:5111"; // change if your gateway uses another port
  const TOKEN = ""; // set to your server.auth_token when the gateway requires one

  // How long an assistant message must stop changing before we read it.
  // Chat UIs stream text token by token; this debounce waits for the reply
  // to settle so we speak it once, whole, instead of in fragments.
  const SETTLE_MS = 1200;

  // Best-effort default selectors for the assistant's message containers on
  // common chat sites. These target the *assistant* turns only (never your
  // own messages). Sites change their markup often — if auto-read reads the
  // wrong thing or nothing, override the selector from the userscript menu
  // ("Set assistant selector for this site"); your value is remembered.
  const DEFAULT_SELECTORS = {
    "chatgpt.com": '[data-message-author-role="assistant"]',
    "chat.openai.com": '[data-message-author-role="assistant"]',
    "claude.ai": ".font-claude-message",
    "gemini.google.com": ".model-response-text",
  };

  const origin = location.hostname;
  const keyEnabled = "autoread:enabled:" + origin;
  const keySelector = "autoread:selector:" + origin;

  function post(path, body, onOk) {
    const headers = { "Content-Type": "application/json" };
    if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
    GM_xmlhttpRequest({
      method: "POST",
      url: GATEWAY + path,
      headers: headers,
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
          toast("tts-daemon: " + detail);
        } else if (onOk) {
          onOk(response);
        }
      },
      onerror: () => toast("tts-daemon unreachable. Start it with: tts-daemon serve"),
      ontimeout: () => toast("tts-daemon timed out"),
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

  // --- auto-read of new chat replies ---------------------------------------

  let observer = null;
  const spoken = new WeakSet(); // assistant elements already read (or baselined)
  const timers = new WeakMap(); // element -> settle timer

  function currentSelector() {
    return GM_getValue(keySelector, "") || DEFAULT_SELECTORS[origin] || "";
  }

  // Visible text of an assistant message, skipping code blocks (reading code
  // aloud is noise) and collapsing whitespace.
  function readableText(element) {
    const clone = element.cloneNode(true);
    clone.querySelectorAll("pre, code").forEach((node) => node.remove());
    return (clone.textContent || "").replace(/\s+/g, " ").trim();
  }

  function scheduleSpeak(element) {
    if (spoken.has(element)) return;
    clearTimeout(timers.get(element));
    timers.set(
      element,
      setTimeout(() => {
        if (spoken.has(element) || !element.isConnected) return;
        const text = readableText(element);
        if (!text) return;
        spoken.add(element);
        // Queue (interrupt:false) so successive replies read in order.
        post("/v1/speak", { text: text, interrupt: false });
      }, SETTLE_MS)
    );
  }

  function matches(node) {
    const selector = currentSelector();
    if (!selector || !(node instanceof Element)) return [];
    const found = [];
    if (node.matches(selector)) found.push(node);
    node.querySelectorAll(selector).forEach((el) => found.push(el));
    return found;
  }

  function baselineExisting() {
    // Mark everything already on the page as "spoken" so turning auto-read
    // on (or reloading) never dumps the whole prior conversation at you.
    const selector = currentSelector();
    if (!selector) return;
    document.querySelectorAll(selector).forEach((el) => spoken.add(el));
  }

  function startAutoRead() {
    if (observer) return;
    baselineExisting();
    observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        // New nodes: an assistant turn was inserted.
        mutation.addedNodes.forEach((node) => {
          matches(node).forEach(scheduleSpeak);
        });
        // Text streaming into an existing (or ancestor) assistant turn.
        const host =
          mutation.target instanceof Element
            ? mutation.target.closest(currentSelector() || "*")
            : null;
        if (host && !spoken.has(host)) scheduleSpeak(host);
      }
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    showIndicator(true);
  }

  function stopAutoRead() {
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    showIndicator(false);
  }

  function setAutoRead(enabled) {
    GM_setValue(keyEnabled, enabled);
    if (enabled) {
      if (!currentSelector()) {
        promptForSelector(() => {
          if (currentSelector()) {
            startAutoRead();
            toast("Auto-read ON for " + origin);
          } else {
            GM_setValue(keyEnabled, false);
          }
        });
        return;
      }
      startAutoRead();
      toast("Auto-read ON for " + origin);
    } else {
      stopAutoRead();
      toast("Auto-read OFF");
    }
  }

  function toggleAutoRead() {
    setAutoRead(!observer);
  }

  function promptForSelector(after) {
    const current = GM_getValue(keySelector, "") || DEFAULT_SELECTORS[origin] || "";
    const value = window.prompt(
      "CSS selector for this site's assistant messages\n(" +
        origin +
        ").\nExamples:\n  ChatGPT  [data-message-author-role=\"assistant\"]\n  Claude   .font-claude-message\nLeave empty to clear.",
      current
    );
    if (value !== null) {
      GM_setValue(keySelector, value.trim());
      toast(value.trim() ? "Selector saved" : "Selector cleared");
    }
    if (after) after();
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

  // Small always-visible dot while auto-read is active; click to toggle off.
  let indicator = null;

  function showIndicator(active) {
    if (active && !indicator) {
      indicator = document.createElement("div");
      indicator.textContent = "🔊 auto";
      indicator.title = "tts-daemon auto-read is on (click or Alt+A to stop)";
      indicator.style.cssText = [
        "position:fixed",
        "left:16px",
        "bottom:16px",
        "z-index:2147483647",
        "padding:6px 10px",
        "border-radius:999px",
        "background:rgba(99,102,241,.95)",
        "color:#fff",
        "font:12px/1 system-ui,sans-serif",
        "cursor:pointer",
        "box-shadow:0 3px 10px rgba(0,0,0,.3)",
      ].join(";");
      indicator.addEventListener("click", () => setAutoRead(false));
      document.documentElement.appendChild(indicator);
    } else if (!active && indicator) {
      indicator.remove();
      indicator = null;
    }
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
      if (event.code === "KeyA") {
        // Alt+A works even while typing: toggling auto-read is not text entry.
        event.preventDefault();
        toggleAutoRead();
        return;
      }
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
    GM_registerMenuCommand("Toggle auto-read new replies (Alt+A)", toggleAutoRead);
    GM_registerMenuCommand("Set assistant selector for this site", () => promptForSelector());
  }

  // Restore the per-site auto-read preference across reloads. Baselining in
  // startAutoRead() keeps the existing conversation silent; only replies that
  // arrive after the page settles are read.
  if (GM_getValue(keyEnabled, false) && currentSelector()) {
    if (document.body) {
      startAutoRead();
    } else {
      window.addEventListener("DOMContentLoaded", startAutoRead, { once: true });
    }
  }
})();
