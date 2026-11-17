import unittest

from wordwrap import wrap


class WrapTest(unittest.TestCase):
    def test_short_text_is_one_line(self) -> None:
        self.assertEqual(wrap("hello world", 20), ["hello world"])

    def test_empty_text(self) -> None:
        self.assertEqual(wrap("", 10), [])


if __name__ == "__main__":
    unittest.main()
