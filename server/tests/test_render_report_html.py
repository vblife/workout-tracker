import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import render_report_html as r


class RenderReportHtmlTest(unittest.TestCase):
    def test_injects_before_first_script_and_escapes(self):
        tpl = "<html><body><p>x</p><script>app()</script></body></html>"
        data = {"note": "</script><!-- 练 & <b>", "n": 3}
        out = r.render(data, tpl)
        inject_end = out.index("<script>app()")
        head = out[:inject_end]
        self.assertTrue(head.startswith("<html><body><p>x</p><script>window.openai={toolOutput:"))
        payload = head[len("<html><body><p>x</p><script>window.openai={toolOutput:"):-len("};</script>")]
        self.assertNotIn("<", payload)
        self.assertNotIn(">", payload)
        self.assertIn("练", payload)
        self.assertEqual(json.loads(payload), data)
        self.assertEqual(out.count("</script>"), 2)

    def test_real_template_renders(self):
        with open(r.DEFAULT_TEMPLATE, encoding="utf-8") as f:
            out = r.render({"period": "week"}, f.read())
        self.assertIn("window.openai={toolOutput:", out)

    def test_template_without_script_raises(self):
        with self.assertRaises(ValueError):
            r.render({}, "<html></html>")


if __name__ == "__main__":
    unittest.main()
