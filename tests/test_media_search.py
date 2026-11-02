import unittest
from unittest.mock import patch

import requests

import media


class MediaSearchTests(unittest.TestCase):
    def test_build_search_queries_expands_multiword_topics(self):
        queries = media._build_search_queries(["Künstliche Intelligenz im Mittelstand"])

        self.assertEqual(
            queries,
            [
                "Künstliche Intelligenz im Mittelstand",
                "Künstliche Intelligenz",
                "Künstliche",
                "Intelligenz",
                "Mittelstand",
            ],
        )

    def test_search_batch_keeps_commons_results_when_openverse_times_out(self):
        result = {
            "id": "commons-1",
            "url": "https://example.org/image.jpg",
            "title": "Mittelstand",
        }

        def commons_search(query, filetype="bitmap"):
            return [result] if query == "Mittelstand" else []

        with (
            patch("media.search_openverse", side_effect=requests.Timeout("timed out")),
            patch("media.search_commons", side_effect=commons_search),
        ):
            results = media.search_batch(["Künstliche Intelligenz im Mittelstand"])

        self.assertEqual(results, [result])

    def test_search_batch_raises_when_all_providers_fail(self):
        with (
            patch("media.search_openverse", side_effect=requests.Timeout("timed out")),
            patch("media.search_commons", side_effect=requests.Timeout("timed out")),
        ):
            with self.assertRaises(media.MediaSearchError):
                media.search_batch(["nature"])


if __name__ == "__main__":
    unittest.main()
