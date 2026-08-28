import unittest
from unittest.mock import patch

from app.mcp import runtime


class FakeSession:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, _type, _value, _traceback):
        self.exited = True


class McpRuntimeSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_session_can_be_used_as_an_async_context_manager(self) -> None:
        fake = FakeSession()

        with patch.object(runtime, "async_session", return_value=fake):
            async with runtime.open_session() as session:
                self.assertIs(session, fake)
                self.assertTrue(fake.entered)

        self.assertTrue(fake.exited)


if __name__ == "__main__":
    unittest.main()
