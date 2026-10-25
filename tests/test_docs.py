"""The run sheet and README only use commands, options and files that ship."""
import contextlib
import io
import re
import shlex
import unittest
from pathlib import Path

from headset import headset
from journeyman import cli

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", ROOT / "docs" / "DEMO.md"]


def code(path):
    text = path.read_text()
    blocks = re.sub(r"\s#.*", "", "\n".join(re.findall(r"```sh\n(.*?)```", text, re.S)))
    return "\n".join([blocks] + re.findall(r"`([^`\n]+)`", text))


def parses(main, argv):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            main(argv + ["--help"])
        except SystemExit as e:
            return e.code == 0
    return False


class DocsMatchCommands(unittest.TestCase):
    def test_jm_commands_parse(self):
        for doc in DOCS:
            for line in re.findall(r"\./jm ([^\n#`|&;]+)", code(doc)):
                argv = shlex.split(line)
                with self.subTest(doc=doc.name, cmd=line):
                    self.assertTrue(parses(cli.main, argv), f"./jm {line}")

    def test_script_options_exist(self):
        for doc in DOCS:
            for script, opts in re.findall(r"scripts/(thor-up|thor-down)\.sh((?: --[\w-]+)*)", code(doc)):
                text = (ROOT / "scripts" / f"{script}.sh").read_text()
                for opt in opts.split():
                    with self.subTest(doc=doc.name, script=script, opt=opt):
                        self.assertIn(f"{opt})", text.replace('"--dry-run" ]', "--dry-run)"))

    def test_headset_options_parse(self):
        for doc in DOCS:
            for opts in re.findall(r"headset/headset\.py((?: --[\w-]+(?: [^\s-][^\s]*)?)*)", code(doc)):
                with self.subTest(doc=doc.name, opts=opts):
                    self.assertTrue(parses(headset.main, opts.split()), opts)

    def test_paths_exist(self):
        for doc in DOCS:
            for rel in re.findall(r"\b((?:scripts|headset|recordings|tools|litert)/[\w./-]+)", code(doc)):
                with self.subTest(doc=doc.name, path=rel):
                    self.assertTrue((ROOT / rel.rstrip(".")).exists(), rel)


if __name__ == "__main__":
    unittest.main()
