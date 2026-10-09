import subprocess
import sys
from importlib.metadata import version

import pytest

from a13n_claw import __version__
from a13n_claw.cli import main


def test_distribution_owns_version():
    assert __version__ == version("a13n-claw")


def test_runtime_command_is_described(capsys):
    main([])
    assert "durable agent execution" in capsys.readouterr().out


def test_version(capsys):
    with pytest.raises(SystemExit) as result:
        main(["--version"])
    assert result.value.code == 0
    assert capsys.readouterr().out.strip() == f"a13n-claw {__version__}"


def test_unknown_commands_are_not_fake_runtime(capsys):
    with pytest.raises(SystemExit) as result:
        main(["start"])
    assert result.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_module_entrypoint():
    result = subprocess.run(
        [sys.executable, "-m", "a13n_claw", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == f"a13n-claw {__version__}"
