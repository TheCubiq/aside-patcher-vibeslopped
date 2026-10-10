"""Check shared process guards independently of filesystem mutations."""

import unittest
from unittest.mock import patch

from aside_patcher.common.exceptions import Refusal
from aside_patcher.common.utils import require_app_stopped


class TestUtils(unittest.TestCase):
    """Check shared safety utilities."""

    def test_running_process_refuses(self) -> None:
        """Refuse modification while the process check reports a running browser."""
        with patch("aside_patcher.common.utils.aside_is_running", return_value=True):
            with self.assertRaisesRegex(Refusal, "Aside is running"):
                require_app_stopped()


if __name__ == "__main__":
    unittest.main()
