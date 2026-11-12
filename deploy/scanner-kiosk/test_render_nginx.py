import importlib.util
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("render_nginx", HERE / "render-nginx.py")
render_nginx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_nginx)
TEMPLATE = (HERE / "nginx.conf.template").read_text()


class RenderNginxTests(unittest.TestCase):
    def test_without_fallback_the_name_is_resolved_per_request(self):
        site = render_nginx.render(TEMPLATE, "http://mac.tail1.ts.net:8120", "100.100.100.100")
        self.assertIn('set $api_origin "http://mac.tail1.ts.net:8120";', site)
        self.assertIn("resolver 100.100.100.100", site)
        self.assertFalse(site.startswith("upstream"))
        self.assertNotIn("__", site)

    def test_fallback_uses_main_address_first_and_local_ip_as_backup(self):
        site = render_nginx.render(TEMPLATE, "http://100.69.1.2:8120", "100.100.100.100", "http://10.0.0.5:8120")
        self.assertTrue(site.startswith("upstream fabric_mac {"))
        self.assertIn("server 100.69.1.2:8120 max_fails=1 fail_timeout=30s;", site)
        self.assertIn("server 10.0.0.5:8120 backup;", site)
        self.assertIn('set $api_origin "http://fabric_mac";', site)
        self.assertIn("proxy_next_upstream error timeout;", site)
        self.assertNotIn("non_idempotent", site)

    def test_name_is_resolved_once_at_install(self):
        original = render_nginx.socket.getaddrinfo
        render_nginx.socket.getaddrinfo = lambda *a, **k: [(None, None, None, "", ("100.69.9.9", 8120))]
        try:
            site = render_nginx.render(TEMPLATE, "http://mac.tail1.ts.net:8120", "100.100.100.100", "http://10.0.0.5:8120")
        finally:
            render_nginx.socket.getaddrinfo = original
        self.assertIn("server 100.69.9.9:8120 max_fails=1 fail_timeout=30s;  # mac.tail1.ts.net", site)

    def test_same_address_twice_has_no_backup(self):
        site = render_nginx.render(TEMPLATE, "http://10.0.0.5:8120", "1.1.1.1", "http://10.0.0.5:8120")
        self.assertNotIn("backup", site)

    def test_fallback_must_be_a_plain_ipv4_origin(self):
        for bad in ("http://mac.local:8120", "https://10.0.0.5:8120", "http://10.0.0.5:8120/api", "http://[::1]:8120"):
            with self.assertRaises(ValueError, msg=bad):
                render_nginx.fallback_address(bad)


if __name__ == "__main__":
    unittest.main()
