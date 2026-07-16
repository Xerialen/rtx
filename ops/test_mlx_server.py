import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

import mlx_server


class MlxServerContractTests(unittest.TestCase):
    def test_server_contract_is_loopback_only_ktx_4on4(self) -> None:
        config = mlx_server.build_server_config("smoke-1", 28600, 1)

        executable = [line for line in config.splitlines() if line and not line.startswith("//")]
        self.assertEqual(executable[0], "setmaster")
        for required in (
            'hostname "mlx:28600"',
            "set k_defmode 4on4",
            "set k_fb_enabled 1",
            "set k_fb_skill 20",
            "set k_membercount 4",
            "set k_noframechecks 1",
            "set sv_public 0",
            "sv_mapcheck 0",
            "sv_demodir demos_p28600",
            "timelimit 1",
        ):
            self.assertIn(required, executable)

        argv = mlx_server.server_argv(
            Path("/opt/mlx/serverdir"), Path("/opt/mlx/serverdir/mvdsv"), 28600, "mlx_28600.cfg"
        )
        self.assertEqual(argv[1:5], ["-ip", "127.0.0.1", "-port", "28600"])
        self.assertEqual(argv[-4:], ["+exec", "mlx_28600.cfg", "+map", "dm3"])

    def test_client_contract_uses_a_real_plus_set_triplet(self) -> None:
        argv = mlx_server.client_argv(
            Path("/opt/mlx/rtx-client"),
            Path("/opt/mlx/serverdir"),
            28600,
            29600,
            team="mlx",
            bhop=False,
        )

        self.assertEqual(argv[argv.index("--bots") + 1], "4")
        self.assertEqual(argv[argv.index("--team") + 1], "mlx")
        self.assertEqual(argv[-3:], ["+set", "rtx_bot_bhop", "0"])
        self.assertNotIn("--no-auto-ready", argv)

    def test_status_parser_handles_oob_prefix_and_high_bit_names(self) -> None:
        packet = (
            b"\xff\xff\xff\xffn\\hostname\\mlx:28600\\map\\dm3\\clients\\2\n"
            b'1 40 12 25 "m\xecx" "mlx" 4 4\n'
            b'2 55 10 18 "frog" "frog" 13 13\n\x00'
        )

        status = mlx_server.parse_status_packet(packet)

        self.assertEqual(status["hostname"], "mlx:28600")
        self.assertEqual(status["map"], "dm3")
        self.assertEqual(status["clients"], "2")
        self.assertEqual(len(status["players"]), 2)
        self.assertIn("m", status["players"][0])

    def test_match_server_port_policy_is_enforced(self) -> None:
        for port in (28600, 28650, 28700):
            self.assertEqual(mlx_server.validate_match_port(port), port)
        for port in (27500, 28599, 28701, 29600):
            with self.assertRaises(ValueError):
                mlx_server.validate_match_port(port)

    def test_client_can_arm_without_auto_ready(self) -> None:
        argv = mlx_server.client_argv(
            Path("/opt/mlx/rtx-client"),
            Path("/opt/mlx/serverdir"),
            28600,
            29600,
            team="mlx",
            bhop=False,
            auto_ready=False,
        )

        self.assertIn("--no-auto-ready", argv)
        self.assertEqual(argv[-3:], ["+set", "rtx_bot_bhop", "0"])

if __name__ == "__main__":
    unittest.main()
