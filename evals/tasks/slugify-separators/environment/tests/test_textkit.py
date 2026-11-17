import unittest

from textkit import slugify


class SlugifyTest(unittest.TestCase):
    def test_two_words(self) -> None:
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_accents_are_dropped(self) -> None:
        self.assertEqual(slugify("Creme Brulee"), "creme-brulee")
        self.assertEqual(slugify("Crème brûlée"), "creme-brulee")


if __name__ == "__main__":
    unittest.main()
