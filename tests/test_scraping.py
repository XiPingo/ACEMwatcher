import unittest

import acem


class XPathNoticeTests(unittest.TestCase):
    def test_extracts_links_from_each_selected_section(self):
        document = """
        <html><body>
          <div></div><div></div><div></div><div></div><div>
            <div><div><div>
              <div><a href="/doctor/1">Doctor admission notice</a></div>
              <div><a href="/master/1">Master admission notice</a></div>
            </div></div></div>
          </div>
        </body></html>
        """

        doctoral = acem._xpath_notices(
            document,
            "https://yzb.sjtu.edu.cn/",
            "/html/body/div[5]/div[1]/div/div/div[1]",
            "Doctor admissions",
        )
        master = acem._xpath_notices(
            document,
            "https://yzb.sjtu.edu.cn/",
            "/html/body/div[5]/div[1]/div/div/div[2]",
            "Master admissions",
        )

        self.assertEqual(doctoral, [acem.Notice(
            "Doctor admission notice",
            "https://yzb.sjtu.edu.cn/doctor/1",
            source="Doctor admissions",
        )])
        self.assertEqual(master, [acem.Notice(
            "Master admission notice",
            "https://yzb.sjtu.edu.cn/master/1",
            source="Master admissions",
        )])

    def test_missing_section_fails_instead_of_silently_clearing_snapshot(self):
        with self.assertRaisesRegex(RuntimeError, "Could not find"):
            acem._xpath_notices(
                "<html><body></body></html>",
                "https://www.gs.sjtu.edu.cn/",
                "/html/body/div[5]/div[1]/div/div/div[1]",
                "Announcements",
            )

    def test_config_uses_the_requested_homepages_and_paths(self):
        self.assertEqual(
            [(url, xpath) for url, _, xpath in acem.SITES[1:]],
            [
                ("https://www.gs.sjtu.edu.cn/", "/html/body/div[5]/div[1]/div/div/div[1]"),
                ("https://yzb.sjtu.edu.cn/", "/html/body/div[5]/div[1]/div/div/div[1]"),
                ("https://yzb.sjtu.edu.cn/", "/html/body/div[5]/div[1]/div/div/div[2]"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
