import unittest

from api.next_data import extract_build_id


class NextDataTests(unittest.TestCase):
    def test_extracts_build_id_regardless_of_script_attribute_order(self):
        html = """
        <html><body>
          <script nonce="abc" type="application/json" id="__NEXT_DATA__">
            {"buildId":"build-123","page":"/"}
          </script>
        </body></html>
        """

        self.assertEqual(extract_build_id(html), "build-123")

    def test_reports_invalid_next_data(self):
        with self.assertRaisesRegex(RuntimeError, "Invalid __NEXT_DATA__"):
            extract_build_id('<script id="__NEXT_DATA__">not-json</script>')
