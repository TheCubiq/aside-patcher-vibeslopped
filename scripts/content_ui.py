"""Automate internal Chromium WebUI controls through CDP and semantic selectors."""

import json
import time

from scripts.native_ui import Connection, ProtocolError


class WebUI:
    """Own a temporary internal page and await its real navigation and controls."""

    def __init__(self, browser: Connection, port: int, url: str, ready: str):
        self.browser = browser
        self.target = browser.call("Target.createTarget", url=url)["targetId"]
        self.page = Connection(f"ws://127.0.0.1:{port}/devtools/page/{self.target}")
        try:
            deadline = time.monotonic() + 10
            while True:
                try:
                    if self.evaluate(ready) is True:
                        break
                except ProtocolError as error:
                    if "Execution context was destroyed" not in str(error):
                        raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"WebUI controls did not become ready: {url}")
                time.sleep(0.05)
        except (OSError, RuntimeError, TimeoutError):
            self.close()
            raise

    def evaluate(self, expression: str, await_promise: bool = False):
        """Return values only; a JavaScript exception is always a test failure."""
        result = self.page.call("Runtime.evaluate", expression=expression, awaitPromise=await_promise, returnByValue=True)
        if result.get("exceptionDetails"):
            raise ProtocolError(f"WebUI JavaScript failed: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    def close(self) -> None:
        """Release the page without affecting unrelated browser targets."""
        self.page.close()
        self.browser.call("Target.closeTarget", targetId=self.target)

    def __enter__(self):
        """Expose the owned internal page to one scenario operation."""
        return self

    def __exit__(self, *_error):
        """Close only this temporary target when its operation finishes."""
        self.close()


def set_theme(browser: Connection, port: int, value: int) -> None:
    """Select the actual appearance dropdown through CDP and its shadow DOM."""
    if value not in (0, 1, 2):
        raise ValueError("Theme adapter expects system, light or dark enum values")
    query = """function find(root) {
      let matches = [...root.querySelectorAll('select#colorSchemeModeSelect')];
      for (const element of root.querySelectorAll('*')) {
        if (element.shadowRoot) matches.push(...find(element.shadowRoot));
      }
      return matches;
    }"""
    ready = f"(()=>{{{query};return location.protocol === 'chrome:' && find(document).length === 1;}})()"
    with WebUI(browser, port, "chrome://settings/appearance", ready) as page:
        expression = """(()=>{QUERY;
          const matches = find(document);
          if (matches.length !== 1) throw new Error('Ambiguous theme dropdown');
          const select = matches[0];
          if (JSON.stringify([...select.options].map(x=>x.value)) !== '["0","1","2"]') {
            throw new Error('Theme adapter options changed');
          }
          select.value = 'THEME_VALUE';
          select.dispatchEvent(new Event('change', {bubbles:true, composed:true}));
          return Number(select.value);
        })()""".replace("QUERY", query).replace("THEME_VALUE", str(value))
        if page.evaluate(expression) != value:
            raise ValueError("Native theme dropdown did not change")


def bookmark_call(browser: Connection, port: int, method: str, arguments: list):
    """Exercise the native bookmark model API and retain sidebar assertions separately."""
    if method not in ("getTree", "update", "move", "create", "removeTree"):
        raise ValueError("Unsupported bookmark test operation")
    ready = "location.protocol === 'chrome:' && !!globalThis.chrome?.bookmarks"
    with WebUI(browser, port, "chrome://bookmarks/", ready) as page:
        expression = """new Promise(resolve => {
          chrome.bookmarks[METHOD](...ARGUMENTS, value => {
            const error = chrome.runtime.lastError;
            resolve({value, error:error?.message});
          });
        })""".replace("METHOD", json.dumps(method)).replace("ARGUMENTS", json.dumps(arguments))
        result = page.evaluate(expression, await_promise=True)
        if not isinstance(result, dict) or result.get("error"):
            raise ValueError(f"Native bookmark API failed: {result}")
        return result.get("value")


def read_bookmark(page: WebUI, reference: dict) -> dict:
    """Observe a real drop's resulting model without issuing any mutation API."""
    roots = page.evaluate("new Promise(resolve=>chrome.bookmarks.getTree(resolve))", await_promise=True)
    pending, matches = list(roots), []
    while pending:
        node = pending.pop()
        if all(node.get(key) == value for key, value in reference.items()):
            matches.append(node)
        pending.extend(node.get("children", []))
    if len(matches) != 1:
        raise ProtocolError(f"Bookmark reference is not unique: {reference}, matches={len(matches)}")
    return matches[0]
