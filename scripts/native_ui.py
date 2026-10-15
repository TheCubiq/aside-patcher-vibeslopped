"""Drive Chromium Views through its native UI DevTools protocol, not screen input."""

import json
import hashlib
import time
from urllib.parse import urlsplit


class ProtocolError(RuntimeError):
    """A protocol request failed or a selector was ambiguous."""


class StaleNodeError(ProtocolError):
    """A native widget disappeared between observation and dispatch."""


class Connection:
    """Keep one bounded, ordered protocol session and retain an audit transcript."""

    def __init__(self, url: str, timeout: float = 10, open_timeout: float | None = None):
        from websockets.sync.client import connect  # pylint: disable=import-outside-toplevel
        from websockets.exceptions import WebSocketException  # pylint: disable=import-outside-toplevel

        address = urlsplit(url)
        if address.scheme != "ws" or address.hostname != "127.0.0.1":
            raise ValueError("Automation accepts loopback WebSocket endpoints only")
        self.timeout = timeout
        self.transport_errors = (WebSocketException,)
        try:
            self.socket = connect(
                url, open_timeout=timeout if open_timeout is None else open_timeout, proxy=None, max_size=32 * 1024 * 1024
            )
        except WebSocketException as error:
            raise ProtocolError(f"Native/CDP connection failed: {error}") from error
        self.sequence = 0
        self.transcript = []

    def call(self, method: str, **parameters) -> dict:
        """Ignore asynchronous events while awaiting this request's exact response."""
        self.sequence += 1
        request = {"id": self.sequence, "method": method, "params": parameters}
        self.transcript.append({"request": request, "time": time.monotonic()})
        self._transport("send", json.dumps(request))
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            response = json.loads(self._transport("recv", timeout=max(0.001, deadline - time.monotonic())))
            encoded = json.dumps(response).encode("utf-8")
            self.transcript.append(
                {"response": response}
                if len(encoded) < 32768
                else {"response_id": response.get("id"), "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}
            )
            if response.get("id") != self.sequence:
                continue
            if "error" in response:
                error = response["error"]
                if error.get("code") == -32000 and (
                    error.get("message", "").startswith("Node with id=") or error.get("message") == "Element not found on node id"
                ):
                    raise StaleNodeError(f"{method}: {error}")
                raise ProtocolError(f"{method}: {response['error']}")
            return response.get("result", {})
        raise TimeoutError(method)

    def _transport(self, method: str, *arguments, **keywords):
        """Surface library disconnects through the same reportable protocol error path."""
        try:
            return getattr(self.socket, method)(*arguments, **keywords)
        except getattr(self, "transport_errors", ()) as error:
            raise ProtocolError(f"Native/CDP transport {method} failed: {error}") from error

    def close(self) -> None:
        """Release the session without closing the browser."""
        self.socket.close()


def walk(node: dict):
    """Yield a native view and its descendants in tree order."""
    yield node
    for child in node.get("children", []):
        yield from walk(child)


def attributes(node: dict) -> dict:
    """Decode the protocol's alternating attribute/value array."""
    values = node.get("attributes", [])
    return dict(zip(values[::2], values[1::2]))


def text_matches(properties: dict, text: str) -> bool:
    """Match native labels while allowing the tooltip's separate shortcut suffix."""
    if properties.get("Text") == text:
        return True
    return any(
        isinstance(value, str) and text in (value, value.split("\x1f", 1)[0])
        for value in (properties.get("TooltipText"), properties.get("Title"))
    )


class NativeUI:
    """Resolve fresh native nodes by class and metadata before every interaction."""

    def __init__(self, connection: Connection):
        self.connection = connection

    def tree(self) -> dict:
        """Fetch a complete tree; node IDs belong only to this connection."""
        return self.connection.call("DOM.getDocument")["root"]

    def properties(self, node: dict) -> dict:
        """Read native Views metadata, including labels, visibility and geometry."""
        result = self.connection.call("CSS.getMatchedStylesForNode", nodeId=node["nodeId"])
        properties = {}
        for rule in result.get("matchedCSSRules", []):
            for prop in rule["rule"]["style"].get("cssProperties", []):
                properties.setdefault(prop["name"], prop["value"])
        return properties

    def matches(self, class_name: str, text: str | None = None) -> list[dict]:
        """Find drawn matches; a descendant label can identify its owner."""
        deadline = time.monotonic() + self.connection.timeout
        while True:
            try:
                return self._read_matches(class_name, text)
            except StaleNodeError:
                # Navigation/theme changes may rebuild the tree during inspection.
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)

    def _read_matches(self, class_name: str, text: str | None) -> list[dict]:
        """Read one coherent selector snapshot, discarding stale snapshots whole."""
        matches = []
        for node in walk(self.tree()):
            if attributes(node).get("class") != class_name:
                continue
            props = self.properties(node)
            if props.get("IsDrawn") != "true":
                continue
            if (
                text is None
                or text_matches(props, text)
                or any(text_matches(self.properties(child), text) for child in walk(node) if child is not node)
            ):
                matches.append(node)
        return matches

    def find(self, class_name: str, text: str | None = None, index: int | None = None, expected_count: int | None = None) -> dict:
        """Refuse missing and ambiguous selectors instead of choosing the first."""
        matches = self.matches(class_name, text)
        if index is not None:
            if expected_count != len(matches) or not 0 <= index < len(matches):
                raise ProtocolError("Indexed native selector differs from its reviewed control count")
            return matches[index]
        if len(matches) != 1:
            raise ProtocolError(f"Expected one drawn {class_name} with text {text!r}; found {len(matches)}")
        return matches[0]

    def mouse(self, node: dict, event_type: str, button: str = "none", x: int | None = None, y: int | None = None) -> None:
        """Dispatch directly to a native node using its current local bounds."""
        props = self.properties(node)
        width, height = int(props["Width"]), int(props["Height"])
        if width <= 0 or height <= 0:
            raise ProtocolError("Cannot interact with an empty native view")
        event = {
            "type": event_type,
            "button": button,
            "wheelDirection": "none",
            "x": width // 2 if x is None else x,
            "y": height // 2 if y is None else y,
        }
        self.connection.call("DOM.dispatchMouseEvent", nodeId=node["nodeId"], event=event)

    def click(self, class_name: str, text: str | None = None, button: str = "left") -> None:
        """Press and release a uniquely identified native control."""
        self.click_selector({"class_name": class_name, "text": text}, button)

    def click_selector(self, selector: dict, button: str = "left") -> None:
        """Resolve either a unique label or an explicitly counted indexed control."""
        deadline = time.monotonic() + self.connection.timeout
        while True:
            try:
                node = self.find(**selector)
                self.click_node(node, button)
                return
            except StaleNodeError:
                # A rejected stale-node dispatch never reached the press callback.
                # Release-time destruction is already handled by click_node.
                if time.monotonic() >= deadline:
                    raise

    def click_node(self, node: dict, button: str = "left") -> None:
        """Release unless the press callback already destroyed its own widget."""
        self.mouse(node, "mouseEntered")
        self.mouse(node, "mouseMoved")
        self.mouse(node, "mousePressed", button)
        try:
            self.mouse(node, "mouseReleased", button)
        except StaleNodeError:
            # Callers still must assert the action's observable postcondition.
            pass

    def hover(self, class_name: str, text: str | None = None) -> None:
        """Send native enter and move events so real hover callbacks execute."""
        node = self.find(class_name, text)
        self.mouse(node, "mouseEntered")
        self.mouse(node, "mouseMoved")

    def wait(self, predicate, timeout: float = 10):
        """Poll an observable postcondition with a deadline instead of fixed sleeps."""
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            try:
                result = predicate()
                if result:
                    return result
            except ProtocolError as error:
                last_error = error
            time.sleep(0.05)
        raise TimeoutError(f"Native UI postcondition failed: {last_error}")
